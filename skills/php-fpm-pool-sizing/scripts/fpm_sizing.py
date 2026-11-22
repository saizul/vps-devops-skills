#!/usr/bin/env python3
"""Size a php-fpm pool from RAM and measured per-child memory.

    pm.max_children = floor((RAM - reserve) / pools / child RSS)

The child RSS comes from --child-mb, or from measured values (KiB, one per line, as
printed by ``ps --no-headers -o rss -C php-fpm8.3``) via --rss-file (``-`` for stdin).
Measured values use a high percentile (--percentile, default 90) instead of the mean,
because a few heavy requests (imports, image resizing) are what push a server into OOM.

Optionally compares with an existing pool file (--pool-conf www.conf) and reports
when its pm.max_children can use more memory than the budget.

Reads nothing on its own except /proc/meminfo when --ram-mb is not given. Writes nothing.

Exit codes: 0 ok, 1 the current pool can exceed the memory budget, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

PM_MODES = ("dynamic", "static", "ondemand")


class SizingError(ValueError):
    """Invalid input."""


def parse_rss(text: str) -> list[float]:
    """Parse RSS values in KiB (one per line, ``ps -o rss`` style) into MiB.

    A header line (``RSS``) and blank lines are skipped; zero values (kernel threads,
    the master between forks) are ignored.
    """
    values = []
    for raw in text.splitlines():
        token = raw.strip().split()[0] if raw.strip() else ""
        if not token or token.upper() == "RSS":
            continue
        if not re.fullmatch(r"\d+", token):
            raise SizingError(f"not an RSS value in KiB: {raw.strip()!r}")
        kib = int(token)
        if kib > 0:
            values.append(kib / 1024)
    if not values:
        raise SizingError("no RSS values found")
    return values


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def meminfo_total_mb(path: str = "/proc/meminfo") -> float:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SizingError("cannot read /proc/meminfo; pass --ram-mb") from exc
    match = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.M)
    if not match:
        raise SizingError("MemTotal not found in /proc/meminfo; pass --ram-mb")
    return int(match.group(1)) / 1024


def parse_pool_conf(text: str) -> dict[str, str]:
    """Return the pm* settings of a php-fpm pool file (last value wins)."""
    settings: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split(";", 1)[0].strip()
        match = re.fullmatch(r"(pm(?:\.[a-z_]+)?)\s*=\s*(\S+)", line)
        if match:
            settings[match.group(1)] = match.group(2)
    return settings


def size_pool(
    ram_mb: float,
    reserve_mb: float,
    child_mb: float,
    *,
    pools: int = 1,
    cpus: int = 2,
    pm: str = "dynamic",
) -> dict:
    """Compute pool settings. Raises SizingError when the inputs leave no room."""
    if ram_mb <= 0 or child_mb <= 0:
        raise SizingError("RAM and child memory must be positive")
    if reserve_mb < 0:
        raise SizingError("reserve must not be negative")
    if pools < 1 or cpus < 1:
        raise SizingError("pools and cpus must be at least 1")
    if pm not in PM_MODES:
        raise SizingError(f"pm must be one of {', '.join(PM_MODES)}")
    budget = ram_mb - reserve_mb
    if budget <= 0:
        raise SizingError(
            f"reserve ({reserve_mb:.0f} MiB) leaves no memory for php-fpm ({ram_mb:.0f} MiB RAM)"
        )
    per_pool = budget / pools
    max_children = math.floor(per_pool / child_mb)
    if max_children < 1:
        raise SizingError(f"one child needs {child_mb:.0f} MiB but each pool only has {per_pool:.0f} MiB")

    settings: dict[str, int | str] = {"pm": pm, "pm.max_children": max_children}
    if pm == "dynamic":
        min_spare = max(1, min(cpus * 2, max_children))
        max_spare = max(min_spare, min(cpus * 4, max_children))
        settings["pm.start_servers"] = min_spare + (max_spare - min_spare) // 2
        settings["pm.min_spare_servers"] = min_spare
        settings["pm.max_spare_servers"] = max_spare
    elif pm == "ondemand":
        settings["pm.process_idle_timeout"] = "10s"
    settings["pm.max_requests"] = 500
    return {
        "ram_mb": round(ram_mb),
        "reserve_mb": round(reserve_mb),
        "budget_mb": round(budget),
        "pools": pools,
        "child_mb": round(child_mb, 1),
        "worst_case_mb": round(max_children * child_mb * pools),
        "settings": settings,
    }


def check_current(current: dict[str, str], child_mb: float, budget_per_pool: float) -> list[str]:
    """Findings for an existing pool configuration."""
    findings = []
    raw = current.get("pm.max_children")
    if raw is None:
        return ["pool file has no pm.max_children (php-fpm refuses to start without it)"]
    if not raw.isdigit():
        return [f"pm.max_children = {raw} is not a number (environment variables cannot be checked)"]
    value = int(raw)
    need = value * child_mb
    if need > budget_per_pool:
        findings.append(
            f"current pm.max_children = {value} can use {need:.0f} MiB, more than the "
            f"{budget_per_pool:.0f} MiB budget: under load the OOM killer may stop php-fpm or MySQL"
        )
    if current.get("pm.max_requests", "0") == "0":
        findings.append("pm.max_requests is 0 (unlimited): set 500 so leaking extensions are recycled")
    return findings


def render_ini(result: dict, pool: str) -> str:
    lines = [
        f"; [{pool}] sized for {result['ram_mb']} MiB RAM, {result['reserve_mb']} MiB reserved,",
        f"; {result['child_mb']} MiB per child, {result['pools']} pool(s): worst case "
        f"{result['worst_case_mb']} MiB",
    ]
    lines += [f"{key} = {value}" for key, value in result["settings"].items()]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fpm_sizing.py",
        description="Compute pm.max_children and spare-server settings for a php-fpm pool from RAM "
        "and measured child memory. Read-only.",
        epilog="Example: ps --no-headers -o rss -C php-fpm8.3 | python3 fpm_sizing.py --rss-file - "
        "--reserve-mb 1024 --pool-conf /etc/php/8.3/fpm/pool.d/www.conf",
    )
    p.add_argument("--ram-mb", type=float, help="total RAM in MiB (default: MemTotal from /proc/meminfo)")
    p.add_argument(
        "--reserve-mb",
        type=float,
        default=512,
        help="memory kept for the OS and other services (MySQL, Redis, nginx), MiB (default 512)",
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--child-mb", type=float, help="memory per php-fpm child, MiB")
    source.add_argument("--rss-file", help="RSS values in KiB, one per line ('-' for stdin)")
    p.add_argument(
        "--percentile", type=float, default=90, help="percentile of measured RSS to use (default 90)"
    )
    p.add_argument(
        "--pools", type=int, default=1, help="number of pools sharing the budget equally (default 1)"
    )
    p.add_argument("--cpus", type=int, default=2, help="CPU cores, for the spare-server settings (default 2)")
    p.add_argument("--pm", choices=PM_MODES, default="dynamic", help="process manager mode (default dynamic)")
    p.add_argument("--pool-conf", help="existing pool file to compare against")
    p.add_argument("--pool-name", default="www", help="pool name for the ini snippet (default www)")
    p.add_argument("--json", action="store_true", help="print JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not 50 <= args.percentile <= 100:
            raise SizingError("--percentile must be between 50 and 100")
        measured = None
        if args.rss_file:
            text = (
                sys.stdin.read() if args.rss_file == "-" else Path(args.rss_file).read_text(encoding="utf-8")
            )
            values = parse_rss(text)
            child_mb = percentile(values, args.percentile)
            measured = {
                "samples": len(values),
                "mean_mb": round(sum(values) / len(values), 1),
                "max_mb": round(max(values), 1),
                f"p{args.percentile:g}_mb": round(child_mb, 1),
            }
        else:
            child_mb = args.child_mb
        ram_mb = args.ram_mb if args.ram_mb is not None else meminfo_total_mb()
        result = size_pool(ram_mb, args.reserve_mb, child_mb, pools=args.pools, cpus=args.cpus, pm=args.pm)
        findings: list[str] = []
        current = None
        if args.pool_conf:
            current = parse_pool_conf(Path(args.pool_conf).read_text(encoding="utf-8"))
            findings = check_current(current, child_mb, result["budget_mb"] / args.pools)
    except (SizingError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if measured:
        result["measured"] = measured
    if args.json:
        print(json.dumps({**result, "current": current, "findings": findings}, indent=2))
    else:
        if measured:
            print(
                f"Measured {measured['samples']} children: mean {measured['mean_mb']} MiB, "
                f"max {measured['max_mb']} MiB, using p{args.percentile:g} = {result['child_mb']} MiB"
            )
        print(render_ini(result, args.pool_name), end="")
        for finding in findings:
            print(f"WARN  {finding}")
    overcommitted = any(f.startswith("current pm.max_children") for f in findings)
    return 1 if overcommitted else 0


if __name__ == "__main__":
    sys.exit(main())
