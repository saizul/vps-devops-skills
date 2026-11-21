#!/usr/bin/env python3
"""Describe what the script checks or generates. Read-only by default.

Exit codes: 0 = ok, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import sys


def check(text: str) -> list[dict]:
    """Return findings as dicts with severity, title and fix."""
    findings = []
    if "TODO" in text:
        findings.append({"severity": "low", "title": "input contains TODO", "fix": "resolve it"})
    return findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", help="file to check, or - for stdin")
    p.add_argument("--json", action="store_true", help="print JSON")
    args = p.parse_args(argv)
    try:
        if args.path == "-":
            text = sys.stdin.read()
        else:
            with open(args.path, encoding="utf-8") as fh:
                text = fh.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    findings = check(text)
    if args.json:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        for f in findings:
            print(f"[{f['severity'].upper()}] {f['title']}  fix: {f['fix']}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
