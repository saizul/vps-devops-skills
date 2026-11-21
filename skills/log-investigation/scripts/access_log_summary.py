#!/usr/bin/env python3
"""Summarise nginx (or Apache) access logs in the combined format.

Reports totals, status classes, top IPs, paths, user agents, the paths behind
5xx and 404 responses, the busiest minute, vulnerability-scanner probes, and
(when the log has $request_time as the last field) the slowest paths.

Reads plain or .gz files (rotated logs work: access.log.2.gz) or stdin ('-').
Filters: --since/--until (ISO time), --last (window ending at the newest
entry, e.g. 15m, 2h), --status (404, 5xx), --path-prefix, --ip.

Exit codes: 0 = ok, 1 = --max-5xx-pct exceeded, 2 = error.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

LINE_RE = re.compile(
    r"^(?P<ip>\S+) \S+ (?P<user>\S+) \[(?P<time>[^\]]+)\] "
    r'"(?P<request>(?:[^"\\]|\\.)*)" (?P<status>\d{3}) (?P<bytes>\d+|-)'
    r'(?: "(?P<referer>(?:[^"\\]|\\.)*)" "(?P<ua>(?:[^"\\]|\\.)*)")?'
    r"(?P<rest>.*)$"
)
TIME_FMT = "%d/%b/%Y:%H:%M:%S %z"
PROBE_RE = re.compile(
    r"(/\.env|/\.git/|/wp-login\.php|/xmlrpc\.php|/phpmyadmin|/pma/|/vendor/phpunit|"
    r"/cgi-bin/|/boaform|/actuator|/\.aws/|/config\.json|/server-status|/HNAP1|/owa/|"
    r"\.\./|/etc/passwd|/shell|/wp-content/plugins/.+\.php)",
    re.I,
)


def parse_duration(text: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if not m:
        raise argparse.ArgumentTypeError("use a number followed by s, m, h or d, e.g. 15m")
    n, unit = int(m.group(1)), m.group(2)
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    return timedelta(**{units[unit]: n})


def parse_iso(text: str) -> datetime:
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO time: {text}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def open_log(path: str):
    if path == "-":
        return sys.stdin
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def parse_line(line: str) -> dict | None:
    m = LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    try:
        ts = datetime.strptime(m.group("time"), TIME_FMT)
    except ValueError:
        return None
    req = m.group("request")
    parts = req.split(" ")
    method, path = (parts[0], parts[1]) if len(parts) >= 2 else ("-", req or "-")
    rt = None
    rest = (m.group("rest") or "").split()
    if rest:
        try:
            rt = float(rest[-1])
        except ValueError:
            rt = None
    return {
        "ip": m.group("ip"),
        "time": ts,
        "method": method,
        "path": path,
        "status": int(m.group("status")),
        "bytes": 0 if m.group("bytes") == "-" else int(m.group("bytes")),
        "ua": m.group("ua") or "-",
        "request_time": rt,
    }


def status_matches(status: int, wanted: list[str]) -> bool:
    for w in wanted:
        if w.endswith("xx") and str(status)[0] == w[0]:
            return True
        if w == str(status):
            return True
    return False


def summarise(entries: list[dict], unparsed: int, top: int, keep_query: bool) -> dict:
    def norm(path: str) -> str:
        return path if keep_query else path.split("?", 1)[0]

    statuses = Counter(e["status"] for e in entries)
    classes = Counter(f"{e['status'] // 100}xx" for e in entries)
    ips = Counter(e["ip"] for e in entries)
    paths = Counter(norm(e["path"]) for e in entries)
    uas = Counter(e["ua"] for e in entries)
    err5 = Counter(norm(e["path"]) for e in entries if e["status"] >= 500)
    err404 = Counter(norm(e["path"]) for e in entries if e["status"] == 404)
    per_minute = Counter(e["time"].strftime("%Y-%m-%d %H:%M") for e in entries)
    probes_by_ip = Counter(e["ip"] for e in entries if PROBE_RE.search(e["path"]))
    timed = defaultdict(list)
    for e in entries:
        if e["request_time"] is not None:
            timed[norm(e["path"])].append(e["request_time"])
    slow = sorted(
        (
            {"path": p, "count": len(v), "avg_s": round(sum(v) / len(v), 3), "max_s": max(v)}
            for p, v in timed.items()
        ),
        key=lambda x: x["avg_s"],
        reverse=True,
    )[:top]
    total = len(entries)
    return {
        "total": total,
        "unparsed_lines": unparsed,
        "first": entries[0]["time"].isoformat() if entries else None,
        "last": entries[-1]["time"].isoformat() if entries else None,
        "bytes": sum(e["bytes"] for e in entries),
        "status_classes": dict(sorted(classes.items())),
        "status_codes": dict(statuses.most_common()),
        "pct_5xx": round(100 * classes.get("5xx", 0) / total, 2) if total else 0.0,
        "top_ips": ips.most_common(top),
        "top_paths": paths.most_common(top),
        "top_user_agents": uas.most_common(top),
        "top_5xx_paths": err5.most_common(top),
        "top_404_paths": err404.most_common(top),
        "busiest_minutes": per_minute.most_common(min(top, 5)),
        "probe_ips": probes_by_ip.most_common(top),
        "slowest_paths": slow,
    }


def render(s: dict) -> str:
    out = [
        f"Requests: {s['total']}  (unparsed lines: {s['unparsed_lines']})",
        f"Window:   {s['first']} .. {s['last']}",
        f"Bytes:    {s['bytes']:,}",
        "Status:   "
        + "  ".join(f"{k}={v}" for k, v in s["status_classes"].items())
        + f"   5xx rate {s['pct_5xx']}%",
    ]

    def table(title: str, rows, fmt=lambda k: k):
        if not rows:
            return
        out.append("")
        out.append(title)
        for k, v in rows:
            out.append(f"  {v:>8}  {fmt(k)}")

    table("Top IPs", s["top_ips"])
    table("Top paths", s["top_paths"])
    table("Paths returning 5xx", s["top_5xx_paths"])
    table("Paths returning 404", s["top_404_paths"])
    table("Busiest minutes", s["busiest_minutes"])
    table("IPs sending scanner probes (.env, wp-login, phpmyadmin, ...)", s["probe_ips"])
    table("Top user agents", s["top_user_agents"], lambda k: k[:100])
    if s["slowest_paths"]:
        out.append("")
        out.append("Slowest paths (avg request_time)")
        for r in s["slowest_paths"]:
            out.append(f"  {r['avg_s']:>7.3f}s avg  {r['max_s']:>7.3f}s max  {r['count']:>6}x  {r['path']}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Summarise nginx/Apache combined-format access logs (plain or .gz).",
    )
    p.add_argument("files", nargs="+", help="log files (.gz ok) or - for stdin")
    p.add_argument("--top", type=int, default=10, help="rows per table (default: 10)")
    p.add_argument("--since", type=parse_iso, help="ISO time, e.g. 2026-11-21T09:00:00+00:00")
    p.add_argument("--until", type=parse_iso, help="ISO time")
    p.add_argument("--last", type=parse_duration, help="window ending at the newest entry: 15m, 2h, 1d")
    p.add_argument("--status", action="append", default=[], help="only these statuses: 404, 5xx (repeatable)")
    p.add_argument("--path-prefix", help="only paths starting with this prefix")
    p.add_argument("--ip", action="append", default=[], help="only this client IP (repeatable)")
    p.add_argument("--keep-query", action="store_true", help="keep ?query strings when grouping paths")
    p.add_argument("--max-5xx-pct", type=float, help="exit 1 if the 5xx rate is above this percentage")
    p.add_argument("--json", action="store_true", help="print JSON")
    args = p.parse_args(argv)

    entries: list[dict] = []
    unparsed = 0
    try:
        for path in args.files:
            with open_log(path) as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    e = parse_line(line)
                    if e is None:
                        unparsed += 1
                        continue
                    entries.append(e)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    entries.sort(key=lambda e: e["time"])
    if args.last and entries:
        cutoff = entries[-1]["time"] - args.last
        entries = [e for e in entries if e["time"] >= cutoff]
    if args.since:
        entries = [e for e in entries if e["time"] >= args.since]
    if args.until:
        entries = [e for e in entries if e["time"] <= args.until]
    if args.status:
        entries = [e for e in entries if status_matches(e["status"], args.status)]
    if args.path_prefix:
        entries = [e for e in entries if e["path"].startswith(args.path_prefix)]
    if args.ip:
        entries = [e for e in entries if e["ip"] in set(args.ip)]

    summary = summarise(entries, unparsed, args.top, args.keep_query)
    print(json.dumps(summary, indent=2) if args.json else render(summary))
    if args.max_5xx_pct is not None and summary["pct_5xx"] > args.max_5xx_pct:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
