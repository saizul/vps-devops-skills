#!/usr/bin/env python3
"""Lint an nginx configuration for common security and correctness mistakes.

Input is either a config file (includes are followed on disk) or the output of
``nginx -T`` (``--dump``), which contains every included file. The linter only
reads; it never reloads or edits nginx.

Checks: server_tokens, missing security headers / HSTS, add_header inheritance,
headers without ``always``, legacy TLS protocols and weak ciphers, open-proxy
``proxy_pass``, ``autoindex on``, alias path traversal (missing trailing
slash), hidden files (.git/.env) not denied, plain-HTTP servers that do not
redirect.

Exit codes: 0 = no findings at or above --fail-on, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import json
import os
import re
import sys
from dataclasses import asdict, dataclass

SEVERITIES = ["info", "low", "medium", "high"]


@dataclass
class Directive:
    name: str
    args: list[str]
    file: str
    line: int
    children: list[Directive] | None = None  # None = simple directive, list = block

    def find(self, name: str) -> list[Directive]:
        return [c for c in (self.children or []) if c.name == name]

    def first(self, name: str) -> Directive | None:
        found = self.find(name)
        return found[-1] if found else None


@dataclass
class Finding:
    check: str
    severity: str
    title: str
    file: str
    line: int
    fix: str = ""


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------- tokenizer


def tokenize(text: str, filename: str):
    """Yield (token, line, is_quoted). Tokens: words, '{', '}', ';'."""
    i, line, n = 0, 1, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{};":
            yield c, line, False
            i += 1
        elif c in "\"'":
            quote, start_line, i = c, line, i + 1
            buf = []
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == "\n":
                    line += 1
                buf.append(text[i])
                i += 1
            if i >= n:
                raise ParseError(f"{filename}:{start_line}: unterminated quote")
            i += 1
            yield "".join(buf), start_line, True
        else:
            buf = []
            while i < n and not text[i].isspace() and text[i] not in "{};":
                # ${var} inside a word must not end the token at '{'
                if text[i] == "$" and i + 1 < n and text[i + 1] == "{":
                    end = text.find("}", i)
                    if end == -1:
                        break
                    buf.append(text[i : end + 1])
                    i = end + 1
                    continue
                buf.append(text[i])
                i += 1
            yield "".join(buf), line, False


def parse(text: str, filename: str) -> list[Directive]:
    tokens = list(tokenize(text, filename))
    pos = 0

    def block(depth: int) -> list[Directive]:
        nonlocal pos
        items: list[Directive] = []
        words: list[str] = []
        start = 0
        while pos < len(tokens):
            tok, ln, quoted = tokens[pos]
            pos += 1
            if not quoted and tok == ";":
                if words:
                    items.append(Directive(words[0], words[1:], filename, start))
                words = []
            elif not quoted and tok == "{":
                if not words:
                    raise ParseError(f"{filename}:{ln}: block without a name")
                d = Directive(words[0], words[1:], filename, start, [])
                d.children = block(depth + 1)
                items.append(d)
                words = []
            elif not quoted and tok == "}":
                if depth == 0:
                    raise ParseError(f"{filename}:{ln}: unexpected '}}'")
                if words:
                    raise ParseError(f"{filename}:{ln}: missing ';' before '}}'")
                return items
            else:
                if not words:
                    start = ln
                words.append(tok)
        if depth != 0:
            raise ParseError(f"{filename}: unexpected end of file (missing '}}')")
        if words:
            raise ParseError(f"{filename}:{start}: missing ';' at end of file")
        return items

    return block(0)


# --------------------------------------------------------------------------- loading


class Loader:
    """Resolves include directives against files on disk or an ``nginx -T`` dump."""

    def __init__(self, files: dict[str, str] | None, prefix: str):
        self.files = files  # None = read from disk
        self.prefix = prefix
        self.warnings: list[str] = []

    def read(self, path: str) -> str:
        if self.files is not None:
            return self.files[path]
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def resolve(self, pattern: str, base_dir: str) -> list[str]:
        if not os.path.isabs(pattern):
            pattern = os.path.join(self.prefix or base_dir, pattern)
        if self.files is not None:
            return sorted(p for p in self.files if fnmatch.fnmatch(p, pattern))
        return sorted(glob.glob(pattern))

    def load(self, path: str, seen: tuple[str, ...] = ()) -> list[Directive]:
        if path in seen:
            self.warnings.append(f"include loop at {path}")
            return []
        tree = parse(self.read(path), path)
        return self._expand(tree, os.path.dirname(path), seen + (path,))

    def _expand(self, items: list[Directive], base: str, seen) -> list[Directive]:
        out: list[Directive] = []
        for d in items:
            if d.name == "include" and d.args and d.children is None:
                matches = self.resolve(d.args[0], base)
                if not matches and not any(ch in d.args[0] for ch in "*?["):
                    self.warnings.append(f"{d.file}:{d.line}: include not found: {d.args[0]}")
                for m in matches:
                    out.extend(self.load(m, seen))
                continue
            if d.children is not None:
                d.children = self._expand(d.children, base, seen)
            out.append(d)
        return out


def split_dump(text: str) -> tuple[dict[str, str], str | None]:
    """Split ``nginx -T`` output into {path: content}; return (files, main_file)."""
    files: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.splitlines(keepends=True):
        m = re.match(r"^# configuration file (.+?):\s*$", line)
        if m:
            if current:
                files[current] = "".join(buf)
            current, buf = m.group(1), []
        elif current:
            buf.append(line)
    if current:
        files[current] = "".join(buf)
    main = next(iter(files), None)
    return files, main


# --------------------------------------------------------------------------- checks

SECURITY_HEADERS = {
    "x-content-type-options": "add_header X-Content-Type-Options nosniff always;",
    "referrer-policy": 'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
}
FRAME_FIX = 'add_header X-Frame-Options "SAMEORIGIN" always;  # or CSP frame-ancestors'
HSTS_FIX = 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;'
PROXY_HOST_VAR_RE = re.compile(r"\$(host|http_\w+|arg_\w+)\b")
WEAK_CIPHER_RE = re.compile(r"(?<![!\-+])\b(RC4|DES|3DES|MD5|NULL|EXPORT|aNULL|eNULL|SEED|IDEA)\b")


def headers_of(block: Directive) -> dict[str, Directive]:
    return {d.args[0].lower(): d for d in block.find("add_header") if d.args}


def effective_headers(chain: list[Directive]) -> dict[str, Directive]:
    """add_header is inherited only if the current level defines none."""
    for level in reversed(chain):
        h = headers_of(level)
        if h:
            return h
    return {}


def is_tls_server(server: Directive) -> bool:
    for listen in server.find("listen"):
        if "ssl" in listen.args or "quic" in listen.args:
            return True
        if listen.args and re.search(r"(^|:)443$", listen.args[0]):
            return True
    ssl = server.first("ssl")
    return bool(ssl and ssl.args and ssl.args[0] == "on")


def walk_locations(block: Directive, chain: list[Directive]):
    for loc in block.find("location"):
        yield loc, chain + [loc]
        yield from walk_locations(loc, chain + [loc])


def lint(tree: list[Directive]) -> list[Finding]:
    f: list[Finding] = []
    root = Directive("main", [], "", 0, tree)
    https = [h for h in root.find("http")]
    if not https:
        # A bare server/site file: treat the file itself as the http block.
        https = [root]
    servers_by_name: dict[str, list[Directive]] = {}

    for http in https:
        tokens = http.first("server_tokens")
        if tokens is None or (tokens.args and tokens.args[0] != "off"):
            where = tokens or http
            f.append(
                Finding(
                    "server_tokens",
                    "low",
                    "server_tokens is not off (nginx version shown in headers and error pages)",
                    where.file,
                    where.line,
                    "server_tokens off;  # in the http block",
                )
            )
        http_protocols = http.first("ssl_protocols")
        http_ciphers = http.first("ssl_ciphers")
        for server in http.find("server"):
            names = [a for d in server.find("server_name") for a in d.args]
            for n in names:
                servers_by_name.setdefault(n, []).append(server)
            f.extend(check_server(http, server, http_protocols, http_ciphers))

    # Plain-HTTP servers whose names also have a TLS server should redirect.
    for name, servers in servers_by_name.items():
        if name in ("_", "") or not any(is_tls_server(s) for s in servers):
            continue
        for s in servers:
            if is_tls_server(s):
                continue
            if not redirects_to_https(s):
                f.append(
                    Finding(
                        "http_redirect",
                        "low",
                        f"HTTP server for {name} serves content instead of redirecting to HTTPS",
                        s.file,
                        s.line,
                        "return 301 https://$host$request_uri;",
                    )
                )
    unique: dict[tuple, Finding] = {}
    for x in f:
        unique.setdefault((x.check, x.file, x.line, x.title), x)
    return list(unique.values())


def redirects_to_https(server: Directive) -> bool:
    def has(block: Directive) -> bool:
        for d in block.find("return"):
            if any(a.startswith("https://") for a in d.args):
                return True
        for d in block.find("rewrite"):
            if any(a.startswith("https://") for a in d.args):
                return True
        return False

    if has(server):
        return True
    return any(has(loc) for loc in server.find("location") if loc.args == ["/"])


def proxy_host(target: str) -> str:
    """Host part of a proxy_pass target: 'http://$host$request_uri' -> '$host$request_uri'."""
    rest = re.sub(r"^(\$scheme|https?)://", "", target)
    return rest.split("/", 1)[0]


def check_server(http: Directive, server: Directive, http_protocols, http_ciphers) -> list[Finding]:
    f: list[Finding] = []
    label = " ".join(a for d in server.find("server_name") for a in d.args) or "(no server_name)"
    tls = is_tls_server(server)
    redirect_only = redirects_to_https(server) and not server.find("root")

    # TLS protocols and ciphers
    if tls:
        protocols = server.first("ssl_protocols") or http_protocols
        if protocols:
            legacy = [p for p in protocols.args if p in ("SSLv2", "SSLv3", "TLSv1", "TLSv1.1")]
            if legacy:
                f.append(
                    Finding(
                        "tls.protocols",
                        "high",
                        f"{label}: legacy TLS protocols enabled: {' '.join(legacy)}",
                        protocols.file,
                        protocols.line,
                        "ssl_protocols TLSv1.2 TLSv1.3;",
                    )
                )
        ciphers = server.first("ssl_ciphers") or http_ciphers
        if ciphers and ciphers.args:
            weak = sorted(set(WEAK_CIPHER_RE.findall(ciphers.args[0])))
            if weak:
                f.append(
                    Finding(
                        "tls.ciphers",
                        "medium",
                        f"{label}: ssl_ciphers allows weak algorithms: {', '.join(weak)}",
                        ciphers.file,
                        ciphers.line,
                        "Use the Mozilla 'intermediate' cipher list (ssl-config.mozilla.org)",
                    )
                )

    # Security headers (effective at server level)
    if not redirect_only:
        hdrs = effective_headers([http, server])
        if tls and "strict-transport-security" not in hdrs:
            f.append(
                Finding(
                    "headers.hsts",
                    "medium",
                    f"{label}: no Strict-Transport-Security header",
                    server.file,
                    server.line,
                    HSTS_FIX,
                )
            )
        for name, fix in SECURITY_HEADERS.items():
            if name not in hdrs:
                f.append(
                    Finding(
                        f"headers.{name}",
                        "low",
                        f"{label}: missing {name} header",
                        server.file,
                        server.line,
                        fix,
                    )
                )
        csp = hdrs.get("content-security-policy")
        has_frame = "x-frame-options" in hdrs or (
            csp is not None and "frame-ancestors" in " ".join(csp.args).lower()
        )
        if not has_frame:
            f.append(
                Finding(
                    "headers.frame",
                    "low",
                    f"{label}: no clickjacking protection (X-Frame-Options or frame-ancestors)",
                    server.file,
                    server.line,
                    FRAME_FIX,
                )
            )
        for name, d in hdrs.items():
            if (
                name in ("strict-transport-security", "x-content-type-options", "x-frame-options")
                and "always" not in d.args
            ):
                f.append(
                    Finding(
                        "headers.always",
                        "low",
                        f"{label}: add_header {d.args[0]} has no 'always' (missing on 4xx/5xx)",
                        d.file,
                        d.line,
                        "Append 'always' to the add_header directive",
                    )
                )

    # autoindex
    for d in iter_all(server):
        if d.name == "autoindex" and d.args[:1] == ["on"]:
            f.append(
                Finding(
                    "autoindex",
                    "medium",
                    f"{label}: autoindex on lists directory contents",
                    d.file,
                    d.line,
                    "autoindex off;  # or remove it",
                )
            )

    # Hidden files
    if server.find("root") and not redirect_only:
        denies_hidden = any(
            re.search(r"/\\\.|\\\./|/\.(git|env|ht)", " ".join(loc.args))
            for loc, _ in walk_locations(server, [server])
        )
        if not denies_hidden:
            f.append(
                Finding(
                    "hidden_files",
                    "low",
                    f"{label}: no location denies hidden files (.git, .env)",
                    server.file,
                    server.line,
                    "location ~ /\\.(?!well-known) { deny all; }",
                )
            )

    server_hdrs = effective_headers([http, server])
    for loc, chain in walk_locations(server, [server]):
        # add_header inheritance
        own = headers_of(loc)
        if own and server_hdrs:
            parent = effective_headers([http] + chain[:-1])
            lost = sorted(set(parent) - set(own))
            if lost:
                f.append(
                    Finding(
                        "headers.inheritance",
                        "medium",
                        f"{label}: location {' '.join(loc.args)} has add_header, so it drops "
                        f"inherited headers: {', '.join(lost)}",
                        loc.file,
                        loc.line,
                        "Repeat the security headers in this location (or use an include snippet)",
                    )
                )
        # alias traversal
        alias = loc.first("alias")
        if alias and alias.args and loc.args:
            path = loc.args[-1]
            modifier = loc.args[0] if len(loc.args) > 1 else ""
            if modifier not in ("~", "~*") and not path.endswith("/") and alias.args[0].endswith("/"):
                f.append(
                    Finding(
                        "alias_traversal",
                        "high",
                        f"{label}: location {path} without trailing slash uses alias "
                        f"{alias.args[0]} (path traversal: {path}../)",
                        alias.file,
                        alias.line,
                        f"location {path}/ {{ alias {alias.args[0]}; }}",
                    )
                )
        # open proxy
        for d in loc.find("proxy_pass"):
            target = d.args[0] if d.args else ""
            if PROXY_HOST_VAR_RE.search(proxy_host(target)):
                f.append(
                    Finding(
                        "open_proxy",
                        "high",
                        f"{label}: proxy_pass {target} takes the upstream host from the request "
                        "(open proxy / SSRF)",
                        d.file,
                        d.line,
                        "Proxy to a fixed upstream or validate the host against a map",
                    )
                )
    return f


def iter_all(block: Directive):
    for c in block.children or []:
        yield c
        if c.children is not None:
            yield from iter_all(c)


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Lint an nginx config for security and correctness mistakes (read-only).",
        epilog="Tip: sudo nginx -T > nginx-dump.txt && nginx_lint.py --dump nginx-dump.txt",
    )
    p.add_argument("path", help="nginx.conf / site file, or an `nginx -T` dump with --dump")
    p.add_argument("--dump", action="store_true", help="input is the output of `nginx -T`")
    p.add_argument(
        "--prefix",
        default="",
        help="directory relative includes resolve against (default: the file's directory)",
    )
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument(
        "--fail-on",
        choices=SEVERITIES[1:] + ["none"],
        default="medium",
        help="lowest severity that makes the exit code 1 (default: medium)",
    )
    args = p.parse_args(argv)

    try:
        if args.dump:
            with open(args.path, encoding="utf-8", errors="replace") as fh:
                files, main_file = split_dump(fh.read())
            if not main_file:
                print("error: no '# configuration file' markers; is this nginx -T output?", file=sys.stderr)
                return 2
            loader = Loader(files, args.prefix or os.path.dirname(main_file))
            tree = loader.load(main_file)
        else:
            loader = Loader(None, args.prefix)
            tree = loader.load(args.path)
    except (OSError, ParseError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    findings = lint(tree)
    order = {s: i for i, s in enumerate(reversed(SEVERITIES))}
    findings.sort(key=lambda x: (order[x.severity], x.file, x.line))

    if args.json:
        print(json.dumps({"findings": [asdict(x) for x in findings], "warnings": loader.warnings}, indent=2))
    else:
        for w in loader.warnings:
            print(f"warning: {w}")
        for x in findings:
            print(f"[{x.severity.upper():6}] {x.file}:{x.line}  {x.title}")
            if x.fix:
                print(f"         fix: {x.fix}")
        counts = {s: sum(1 for x in findings if x.severity == s) for s in SEVERITIES}
        print(
            "Summary: "
            + ", ".join(f"{counts[s]} {s}" for s in reversed(SEVERITIES))
            + ". Test changes with `sudo nginx -t` before `sudo systemctl reload nginx`."
        )

    if args.fail_on == "none":
        return 0
    threshold = SEVERITIES.index(args.fail_on)
    return 1 if any(SEVERITIES.index(x.severity) >= threshold for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
