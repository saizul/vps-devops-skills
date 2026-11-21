#!/usr/bin/env python3
"""Check TLS certificates and endpoints (read-only).

Endpoint mode (``example.com``, ``example.com:8443``): connects once with
certificate verification and SNI, reports days to expiry, hostname/chain
verification errors, negotiated protocol and cipher, and (--check-legacy)
whether TLS 1.0/1.1 are still accepted. This is the only mode that uses the
network, and only to the hosts you name.

File mode (``--cert-file fullchain.pem``): reads PEM certificates locally with
the ``openssl`` CLI and reports subject, issuer, SANs and expiry for each
certificate in the file. No network.

Exit codes: 0 = all ok, 1 = findings at or above --fail-on, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import warnings
from dataclasses import asdict, dataclass, field

SEVERITIES = ["info", "low", "medium", "high"]
PEM_RE = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


@dataclass
class Result:
    target: str
    ok: bool = True
    subject: str = ""
    issuer: str = ""
    sans: list[str] = field(default_factory=list)
    not_after: str = ""
    days_left: float | None = None
    protocol: str = ""
    cipher: str = ""
    legacy_protocols: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)

    def add(self, severity: str, message: str, fix: str = "") -> None:
        self.findings.append({"severity": severity, "message": message, "fix": fix})
        if severity in ("medium", "high"):
            self.ok = False


def days_until(not_after: str, now: float | None = None) -> float:
    """``not_after`` in OpenSSL format: 'Jun  1 12:00:00 2027 GMT'."""
    expires = ssl.cert_time_to_seconds(not_after)
    return (expires - (now if now is not None else time.time())) / 86400


def check_expiry(res: Result, warn_days: int, crit_days: int) -> None:
    if res.days_left is None:
        return
    d = res.days_left
    renew = "sudo certbot renew --dry-run && systemctl list-timers | grep certbot"
    if d < 0:
        res.add("high", f"certificate EXPIRED {abs(d):.0f} days ago ({res.not_after})", renew)
    elif d < crit_days:
        res.add("high", f"certificate expires in {d:.1f} days ({res.not_after})", renew)
    elif d < warn_days:
        res.add("medium", f"certificate expires in {d:.0f} days ({res.not_after})", renew)


def _name(rdns) -> str:
    parts = []
    for rdn in rdns or ():
        for k, v in rdn:
            if k in ("commonName", "organizationName"):
                parts.append(f"{'CN' if k == 'commonName' else 'O'}={v}")
    return ", ".join(parts)


# --------------------------------------------------------------------------- endpoint mode


def parse_target(target: str, default_port: int) -> tuple[str, int]:
    target = re.sub(r"^https?://", "", target).split("/", 1)[0]
    m = re.match(r"^\[([^\]]+)\](?::(\d+))?$", target)
    if m:
        return m.group(1), int(m.group(2) or default_port)
    if target.count(":") == 1:
        host, port = target.split(":")
        return host, int(port)
    return target, default_port


def openssl_decode_der(der: bytes) -> dict:
    """Decode a DER certificate with the openssl CLI (used when verification failed)."""
    if not shutil.which("openssl"):
        return {}
    pem = ssl.DER_cert_to_PEM_cert(der)
    return openssl_describe(pem)


def probe_legacy(host: str, port: int, servername: str, timeout: float) -> list[str]:
    """Return legacy protocol versions the server accepts. Empty if none or untestable."""
    accepted = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = (("TLSv1", ssl.TLSVersion.TLSv1), ("TLSv1.1", ssl.TLSVersion.TLSv1_1))
    for name, ver in legacy:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                ctx.minimum_version = ver
                ctx.maximum_version = ver
            ctx.set_ciphers("ALL:@SECLEVEL=0")
        except (ValueError, ssl.SSLError):
            continue
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=servername):
                    accepted.append(name)
        except (OSError, ssl.SSLError):
            pass
    return accepted


def check_endpoint(target: str, args) -> Result:
    host, port = parse_target(target, args.port)
    servername = args.servername or host
    res = Result(target=f"{host}:{port}" + (f" (SNI {servername})" if servername != host else ""))
    ctx = ssl.create_default_context(cafile=args.ca_file) if args.ca_file else ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=args.timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=servername) as tls:
                cert = tls.getpeercert()
                res.protocol = tls.version() or ""
                res.cipher = (tls.cipher() or ("",))[0]
        res.subject = _name(cert.get("subject"))
        res.issuer = _name(cert.get("issuer"))
        res.sans = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
        res.not_after = cert.get("notAfter", "")
        if res.not_after:
            res.days_left = round(days_until(res.not_after), 1)
    except ssl.SSLCertVerificationError as exc:
        res.add(
            "high",
            f"certificate verification failed: {exc.verify_message or exc}",
            "Serve the full chain (fullchain.pem) and a certificate whose SANs include the hostname",
        )
        # Fetch the certificate without verification so expiry can still be reported.
        try:
            raw = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            raw.check_hostname = False
            raw.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, port), timeout=args.timeout) as sock:
                with raw.wrap_socket(sock, server_hostname=servername) as tls:
                    der = tls.getpeercert(binary_form=True)
                    res.protocol = tls.version() or ""
                    res.cipher = (tls.cipher() or ("",))[0]
            info = openssl_decode_der(der) if der else {}
            res.subject, res.issuer = info.get("subject", ""), info.get("issuer", "")
            res.sans, res.not_after = info.get("sans", []), info.get("not_after", "")
            if res.not_after:
                res.days_left = round(days_until(res.not_after), 1)
        except (OSError, ssl.SSLError):
            pass
    except (OSError, ssl.SSLError) as exc:
        res.add("high", f"TLS connection failed: {exc}", "Check that the port is open and serves TLS")
        return res

    check_expiry(res, args.warn_days, args.crit_days)
    if res.protocol in ("SSLv3", "TLSv1", "TLSv1.1"):
        res.add("high", f"negotiated legacy protocol {res.protocol}", "ssl_protocols TLSv1.2 TLSv1.3;")
    if args.check_legacy:
        res.legacy_protocols = probe_legacy(host, port, servername, args.timeout)
        if res.legacy_protocols:
            res.add(
                "medium",
                f"server still accepts {', '.join(res.legacy_protocols)}",
                "ssl_protocols TLSv1.2 TLSv1.3;  (nginx) then nginx -t && systemctl reload nginx",
            )
    return res


# --------------------------------------------------------------------------- file mode


def openssl_describe(pem: str) -> dict:
    cmd = [
        "openssl",
        "x509",
        "-noout",
        "-subject",
        "-issuer",
        "-enddate",
        "-ext",
        "subjectAltName",
        "-nameopt",
        "RFC2253",
    ]
    r = subprocess.run(cmd, input=pem, capture_output=True, text=True, timeout=20, check=False)
    if r.returncode != 0:
        # Older OpenSSL without -ext: retry without SANs.
        cmd = cmd[:6] + ["-nameopt", "RFC2253"]
        r = subprocess.run(cmd, input=pem, capture_output=True, text=True, timeout=20, check=False)
        if r.returncode != 0:
            raise ValueError(r.stderr.strip() or "openssl could not read the certificate")
    info: dict = {"sans": []}
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("subject="):
            info["subject"] = line.split("=", 1)[1].strip()
        elif line.startswith("issuer="):
            info["issuer"] = line.split("=", 1)[1].strip()
        elif line.startswith("notAfter="):
            info["not_after"] = line.split("=", 1)[1].strip()
        elif "DNS:" in line or "IP Address:" in line:
            for item in line.split(","):
                item = item.strip()
                if item.startswith("DNS:"):
                    info["sans"].append(item[4:])
    return info


def check_file(path: str, args) -> list[Result]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        blocks = PEM_RE.findall(fh.read())
    if not blocks:
        raise ValueError(f"{path}: no PEM certificate found")
    results = []
    for i, pem in enumerate(blocks):
        info = openssl_describe(pem)
        res = Result(target=f"{path}#{i}" + (" (leaf)" if i == 0 else " (chain)"))
        res.subject = info.get("subject", "")
        res.issuer = info.get("issuer", "")
        res.sans = info.get("sans", [])
        res.not_after = info.get("not_after", "")
        if res.not_after:
            res.days_left = round(days_until(res.not_after), 1)
        check_expiry(res, args.warn_days, args.crit_days)
        if i == 0 and args.hostname:
            if not any(hostname_matches(args.hostname, s) for s in res.sans):
                res.add(
                    "high",
                    f"{args.hostname} is not in the certificate SANs ({', '.join(res.sans) or 'none'})",
                    f"Reissue: sudo certbot certonly -d {args.hostname} ...",
                )
        results.append(res)
    if len(blocks) == 1 and results[0].subject != results[0].issuer:
        results[0].add(
            "low",
            "file contains only the leaf certificate (no intermediate chain)",
            "Point ssl_certificate at fullchain.pem, not cert.pem",
        )
    return results


def hostname_matches(host: str, pattern: str) -> bool:
    host, pattern = host.lower().rstrip("."), pattern.lower().rstrip(".")
    if pattern.startswith("*."):
        return host.count(".") == pattern.count(".") and host.endswith(pattern[1:])
    return host == pattern


# --------------------------------------------------------------------------- CLI


def render(results: list[Result]) -> str:
    out = []
    for r in results:
        status = "OK  " if r.ok else "FAIL"
        out.append(f"[{status}] {r.target}")
        if r.subject:
            out.append(f"       subject: {r.subject}")
        if r.issuer:
            out.append(f"       issuer:  {r.issuer}")
        if r.sans:
            out.append(f"       SANs:    {', '.join(r.sans[:10])}{' ...' if len(r.sans) > 10 else ''}")
        if r.not_after:
            out.append(f"       expires: {r.not_after} ({r.days_left} days)")
        if r.protocol:
            out.append(f"       TLS:     {r.protocol} {r.cipher}")
        for f in r.findings:
            out.append(f"       {f['severity'].upper()}: {f['message']}")
            if f["fix"]:
                out.append(f"         fix: {f['fix']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check TLS certificate expiry and endpoint configuration.")
    p.add_argument("targets", nargs="*", help="host, host:port or https://host (connects to it)")
    p.add_argument(
        "--cert-file", action="append", default=[], help="local PEM file to inspect (repeatable, no network)"
    )
    p.add_argument("--hostname", help="with --cert-file: hostname that must be in the SANs")
    p.add_argument("--port", type=int, default=443, help="default port (default: 443)")
    p.add_argument("--servername", help="SNI name when it differs from the host (e.g. connecting by IP)")
    p.add_argument("--ca-file", help="trust this CA bundle instead of the system store")
    p.add_argument(
        "--warn-days", type=int, default=21, help="medium finding below this many days (default: 21)"
    )
    p.add_argument("--crit-days", type=int, default=7, help="high finding below this many days (default: 7)")
    p.add_argument("--check-legacy", action="store_true", help="also try TLS 1.0 and 1.1 handshakes")
    p.add_argument("--timeout", type=float, default=10.0, help="socket timeout in seconds")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--fail-on", choices=SEVERITIES[1:] + ["none"], default="medium")
    args = p.parse_args(argv)

    if not args.targets and not args.cert_file:
        p.error("give at least one target or --cert-file")
    if args.cert_file and not shutil.which("openssl"):
        print("error: --cert-file needs the openssl command", file=sys.stderr)
        return 2

    results: list[Result] = []
    try:
        for path in args.cert_file:
            results.extend(check_file(path, args))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for t in args.targets:
        results.append(check_endpoint(t, args))

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(render(results))
    if args.fail_on == "none":
        return 0
    threshold = SEVERITIES.index(args.fail_on)
    hit = any(SEVERITIES.index(f["severity"]) >= threshold for r in results for f in r.findings)
    return 1 if hit else 0


if __name__ == "__main__":
    sys.exit(main())
