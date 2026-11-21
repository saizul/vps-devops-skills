#!/usr/bin/env python3
"""Read-only first-five-minutes snapshot of a Linux server in trouble.

Looks at load vs CPU count, memory and swap, disk space and inodes, recent OOM
kills, failed systemd units and the top CPU/memory processes, then prints
findings ordered by severity with the next read-only command to run for each.

Live mode reads /proc and runs ``df``, ``ps``, ``journalctl -k`` and
``systemctl --failed``. Offline mode (--root and the --*-file options) reads
captured copies so you can triage from another machine or in tests.
Nothing is restarted, killed or deleted.

Exit codes: 0 = nothing at or above --fail-on, 1 = findings, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

SEVERITIES = ["info", "low", "medium", "high"]
SKIP_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "efivarfs", "proc", "sysfs", "nsfs"}
OOM_KILLED_RE = re.compile(r"Killed process (\d+) \(([^)]+)\)")
OOM_KILL_RE = re.compile(r"oom-kill:.*?task=([^,\s]+),pid=(\d+)")


@dataclass
class Finding:
    area: str
    severity: str
    title: str
    next_steps: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    loadavg: str | None = None
    meminfo: str | None = None
    cpuinfo: str | None = None
    df: str | None = None
    df_inodes: str | None = None
    kernel_log: str | None = None
    failed_units: str | None = None
    ps: str | None = None


# --------------------------------------------------------------------------- parsers


def parse_meminfo(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(\w+(?:\(\w+\))?):\s+(\d+)", line)
        if m:
            out[m.group(1)] = int(m.group(2))  # kB
    return out


def count_cpus(cpuinfo: str | None) -> int:
    if not cpuinfo:
        return 1
    n = sum(1 for line in cpuinfo.splitlines() if re.match(r"^processor\s*:", line))
    return max(n, 1)


def parse_df(text: str) -> list[dict]:
    """Parse ``df -PT`` (with type) or ``df -P`` output. Works for ``df -PTi`` too."""
    rows = []
    lines = text.strip().splitlines()
    if not lines:
        return rows
    has_type = "Type" in lines[0]
    for line in lines[1:]:
        cols = line.split()
        if len(cols) < (7 if has_type else 6):
            continue
        fs = cols[0]
        fstype = cols[1] if has_type else ""
        pct = cols[-2].rstrip("%")
        mount = cols[-1]
        if not pct.isdigit():
            continue
        rows.append({"fs": fs, "type": fstype, "use_pct": int(pct), "mount": mount})
    return rows


def parse_oom(text: str) -> Counter:
    """Count OOM victims by process name; one kill logs two lines, so key by PID."""
    by_pid: dict[str, str] = {}
    for m in OOM_KILLED_RE.finditer(text):
        by_pid[m.group(1)] = m.group(2)
    for m in OOM_KILL_RE.finditer(text):
        by_pid.setdefault(m.group(2), m.group(1))
    return Counter(by_pid.values())


def parse_ps(text: str, limit: int = 5) -> list[str]:
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    return lines[: limit + 1]


# --------------------------------------------------------------------------- analysis


def analyse(s: Snapshot) -> list[Finding]:
    f: list[Finding] = []
    ncpu = count_cpus(s.cpuinfo)

    if s.loadavg:
        parts = s.loadavg.split()
        load1, load5, load15 = (float(x) for x in parts[:3])
        ratio = load5 / ncpu
        sev = "high" if ratio >= 2 else "medium" if ratio >= 1 else None
        if sev:
            f.append(
                Finding(
                    "load",
                    sev,
                    f"Load {load1:.2f}/{load5:.2f}/{load15:.2f} on {ncpu} CPU(s) "
                    f"({ratio:.1f}x per CPU, 5 min)",
                    [
                        "top -o %CPU -b -n 1 | head -20",
                        "vmstat 1 5    # high 'wa' = waiting on disk, high 'st' = noisy neighbour",
                        "iostat -xz 1 3  # from sysstat: %util near 100 = disk bound",
                        "ps -eo state,pid,cmd | awk '$1 ~ /D/'   # tasks stuck in IO",
                    ],
                )
            )

    if s.meminfo:
        mem = parse_meminfo(s.meminfo)
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        if total:
            pct = 100 * avail / total
            sev = "high" if pct < 5 else "medium" if pct < 10 else None
            if sev:
                f.append(
                    Finding(
                        "memory",
                        sev,
                        f"Only {pct:.1f}% memory available ({avail // 1024} MiB of {total // 1024} MiB)",
                        [
                            "ps -eo pid,user,rss,%mem,cmd --sort=-rss | head -15",
                            "free -m",
                            "systemd-cgtop -m -n 1   # memory per service",
                        ],
                    )
                )
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        if swap_total:
            used = 100 * (swap_total - swap_free) / swap_total
            if used >= 50:
                f.append(
                    Finding(
                        "memory",
                        "medium" if used < 90 else "high",
                        f"Swap {used:.0f}% used: the box is likely thrashing",
                        ["vmstat 1 5   # si/so columns > 0 = actively swapping"],
                    )
                )
        elif total and total < 4 * 1024 * 1024:
            f.append(
                Finding(
                    "memory",
                    "info",
                    "No swap on a host with < 4 GiB RAM (OOM kills come with no warning)",
                    ["Consider a small swap file: fallocate -l 1G /swapfile (see references)"],
                )
            )

    for row in parse_df(s.df or ""):
        if row["type"] in SKIP_FS or row["fs"] in ("tmpfs", "devtmpfs", "overlay"):
            continue
        sev = "high" if row["use_pct"] >= 95 else "medium" if row["use_pct"] >= 85 else None
        if sev:
            m = row["mount"]
            f.append(
                Finding(
                    "disk",
                    sev,
                    f"{m} is {row['use_pct']}% full ({row['fs']})",
                    [
                        f"sudo du -xh --max-depth=2 {m} 2>/dev/null | sort -h | tail -20",
                        "sudo journalctl --disk-usage",
                        "sudo find /var/log -xdev -type f -size +100M -exec ls -lh {} +",
                        "docker system df   # if Docker is installed",
                        "sudo lsof +L1   # deleted files still held open (space not freed)",
                    ],
                )
            )
    for row in parse_df(s.df_inodes or ""):
        if row["type"] in SKIP_FS or row["fs"] in ("tmpfs", "devtmpfs", "overlay"):
            continue
        if row["use_pct"] >= 90:
            f.append(
                Finding(
                    "disk",
                    "high",
                    f"{row['mount']} inodes {row['use_pct']}% used (many small files)",
                    [
                        f"sudo find {row['mount']} -xdev -type f | cut -d/ -f2-4 "
                        "| sort | uniq -c | sort -n | tail",
                        "Typical culprits: PHP sessions, mail queue, cache directories",
                    ],
                )
            )

    if s.kernel_log:
        victims = parse_oom(s.kernel_log)
        if victims:
            names = ", ".join(f"{k} x{v}" for k, v in victims.most_common())
            f.append(
                Finding(
                    "oom",
                    "high",
                    f"OOM killer fired: {names}",
                    [
                        "journalctl -k --since -24h | grep -iE 'out of memory|oom-kill'",
                        "systemctl status <service>   # did it restart?",
                        "Set MemoryMax= on the service or a mem_limit on the container",
                    ],
                )
            )

    if s.failed_units:
        units = [
            ln.split()[0] for ln in s.failed_units.splitlines() if ln.strip() and ln.split()[0] != "UNIT"
        ]
        units = [u.lstrip("●*").strip() or u for u in units]
        units = [u for u in units if u]
        if units:
            f.append(
                Finding(
                    "systemd",
                    "medium",
                    f"Failed units: {', '.join(units)}",
                    [f"systemctl status {units[0]}", f"journalctl -u {units[0]} -n 100 --no-pager"],
                )
            )

    if s.ps:
        top = parse_ps(s.ps)
        if len(top) > 1:
            f.append(Finding("processes", "info", "Top processes by CPU", top))

    order = {sev: i for i, sev in enumerate(reversed(SEVERITIES))}
    f.sort(key=lambda x: order[x.severity])
    return f


# --------------------------------------------------------------------------- collection


def _run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 or r.stdout else None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def collect(args) -> Snapshot:
    root = Path(args.root)
    live = args.root == "/"
    s = Snapshot(
        loadavg=_read(root / "proc/loadavg"),
        meminfo=_read(root / "proc/meminfo"),
        cpuinfo=_read(root / "proc/cpuinfo"),
    )
    s.df = _read(Path(args.df_file)) if args.df_file else (_run(["df", "-PT"]) if live else None)
    s.df_inodes = (
        _read(Path(args.df_inodes_file)) if args.df_inodes_file else (_run(["df", "-PTi"]) if live else None)
    )
    if args.kernel_log_file:
        s.kernel_log = _read(Path(args.kernel_log_file))
    elif live:
        s.kernel_log = _run(["journalctl", "-k", "--since", args.since, "--no-pager", "-q"]) or _run(
            ["dmesg", "-T"]
        )
    if args.failed_units_file:
        s.failed_units = _read(Path(args.failed_units_file))
    elif live:
        s.failed_units = _run(["systemctl", "--failed", "--no-legend", "--plain"])
    if args.ps_file:
        s.ps = _read(Path(args.ps_file))
    elif live:
        s.ps = _run(["ps", "-eo", "pid,user,%cpu,%mem,rss,etime,comm", "--sort=-%cpu"])
    return s


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Read-only incident triage snapshot (load, memory, disk, OOM, failed units).",
        epilog="Exit codes: 0 nothing at/above --fail-on, 1 findings, 2 error.",
    )
    p.add_argument("--root", default="/", help="read proc/* below this directory (default: /)")
    p.add_argument("--df-file", help="captured `df -PT` output")
    p.add_argument("--df-inodes-file", help="captured `df -PTi` output")
    p.add_argument("--kernel-log-file", help="captured `journalctl -k` or `dmesg -T` output")
    p.add_argument("--failed-units-file", help="captured `systemctl --failed --no-legend --plain`")
    p.add_argument("--ps-file", help="captured `ps -eo pid,user,%%cpu,%%mem,rss,etime,comm --sort=-%%cpu`")
    p.add_argument("--since", default="-24h", help="kernel log window in live mode (default: -24h)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.add_argument("--fail-on", choices=SEVERITIES[1:] + ["none"], default="medium")
    args = p.parse_args(argv)

    if args.root == "/" and not Path("/proc/loadavg").exists():
        print("error: /proc not found (not Linux). Use --root with captured files.", file=sys.stderr)
        return 2
    snap = collect(args)
    findings = analyse(snap)
    if args.json:
        print(json.dumps({"findings": [asdict(x) for x in findings]}, indent=2))
    else:
        print("Incident triage snapshot (read-only)\n")
        if not any(x.severity != "info" for x in findings):
            print("No resource problem detected. Check the application next (logs, 5xx, upstreams).\n")
        for x in findings:
            print(f"[{x.severity.upper():6}] {x.area}: {x.title}")
            for step in x.next_steps:
                print(f"         {step}")
    if args.fail_on == "none":
        return 0
    threshold = SEVERITIES.index(args.fail_on)
    return 1 if any(SEVERITIES.index(x.severity) >= threshold for x in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
