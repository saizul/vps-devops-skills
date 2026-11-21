#!/usr/bin/env python3
"""Convert crontab entries to systemd .service + .timer units.

Handles the 5-field syntax (lists, ranges, steps, month and weekday names),
@hourly/@daily/@weekly/@monthly/@yearly/@reboot, VAR=value lines, the /etc/cron.d
user column (--system), '%' escaping, and the cron rule that a job with both
day-of-month and day-of-week restricted runs when EITHER matches (emitted as
two OnCalendar= lines, which systemd ORs).

Input: --line "*/5 * * * * /usr/local/bin/job", or a crontab file (``crontab -l``
output, /etc/crontab, /etc/cron.d/*). Output: unit files printed to stdout, or
written to --out. Nothing is installed or enabled.

Verify every generated expression on the server with:
    systemd-analyze calendar --iterations=5 'EXPR'

Exit codes: 0 = converted, 1 = some lines could not be converted, 2 = error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DOW_CRON = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
DOW_SYSTEMD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
FIELD_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 7),
}
SPECIALS = {
    "@yearly": "*-01-01 00:00:00",
    "@annually": "*-01-01 00:00:00",
    "@monthly": "*-*-01 00:00:00",
    "@weekly": "Sun *-*-* 00:00:00",  # cron's @weekly is Sunday; systemd 'weekly' is Monday
    "@daily": "*-*-* 00:00:00",
    "@midnight": "*-*-* 00:00:00",
    "@hourly": "*-*-* *:00:00",
}
SHELL_CHARS = re.compile(r"[|&;<>()$`\\\"'*?\[\]#~{}]")


class CronError(ValueError):
    pass


@dataclass
class Job:
    schedule: str
    command: str
    user: str = ""
    env: dict[str, str] = field(default_factory=dict)
    line: int = 0


@dataclass
class Converted:
    name: str
    schedule: str
    command: str
    on_calendar: list[str]
    on_boot: bool
    service: str
    timer: str
    warnings: list[str]


# --------------------------------------------------------------------------- field expansion


def _value(token: str, kind: str) -> int:
    t = token.lower()
    if kind == "month" and t[:3] in MONTHS and not t.isdigit():
        return MONTHS.index(t[:3]) + 1
    if kind == "dow" and t[:3] in DOW_CRON and not t.isdigit():
        return DOW_CRON.index(t[:3])
    if not t.isdigit():
        raise CronError(f"invalid {kind} value '{token}'")
    return int(t)


def expand(field_text: str, kind: str) -> set[int]:
    lo, hi = FIELD_RANGES[kind]
    values: set[int] = set()
    for part in field_text.split(","):
        if not part:
            raise CronError(f"empty item in {kind} field '{field_text}'")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"invalid step in {kind} field '{field_text}'")
            step = int(step_text)
        if part == "*":
            start, end = lo, (6 if kind == "dow" else hi)
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _value(a, kind), _value(b, kind)
        else:
            start = _value(part, kind)
            end = hi if step > 1 else start  # "5/10" means 5-max/10
        if not (lo <= start <= hi and lo <= end <= hi) or start > end:
            raise CronError(f"{kind} value out of range in '{field_text}'")
        values.update(range(start, end + 1, step))
    if kind == "dow" and 7 in values:
        values.discard(7)
        values.add(0)
    return values


def render(values: set[int], kind: str, width: int = 2) -> str:
    """Render a value set in systemd syntax: '*', 'start/step', or a comma list."""
    lo, hi = FIELD_RANGES[kind]
    if kind == "dow":
        hi = 6
    full = set(range(lo, hi + 1))
    if values == full:
        return "*"
    ordered = sorted(values)
    if len(ordered) >= 3:
        step = ordered[1] - ordered[0]
        if all(b - a == step for a, b in zip(ordered, ordered[1:])):
            # systemd 'a/s' repeats to the end of the range; only use it when that matches.
            if ordered[-1] + step > hi:
                return f"{ordered[0]:0{width}d}/{step}"
    return ",".join(f"{v:0{width}d}" for v in ordered)


def render_dow(values: set[int]) -> str:
    if values == set(range(0, 7)):
        return ""
    ordered = sorted(values, key=lambda d: (d + 6) % 7)  # Monday first
    names = [DOW_SYSTEMD[d] for d in ordered]
    idx = [(d + 6) % 7 for d in ordered]
    if len(idx) >= 3 and idx == list(range(idx[0], idx[-1] + 1)):
        return f"{names[0]}..{names[-1]}"
    return ",".join(names)


def to_on_calendar(schedule: str) -> tuple[list[str], bool, list[str]]:
    """Return (OnCalendar expressions, is_reboot, warnings)."""
    warnings: list[str] = []
    s = schedule.strip()
    if s.startswith("@"):
        key = s.lower()
        if key == "@reboot":
            return (
                [],
                True,
                ["@reboot became OnBootSec=1min; consider a normal service with WantedBy=multi-user.target"],
            )
        if key not in SPECIALS:
            raise CronError(f"unknown special schedule {s}")
        return [SPECIALS[key]], False, warnings
    fields = s.split()
    if len(fields) != 5:
        raise CronError(f"expected 5 time fields, got {len(fields)}: '{s}'")
    minute, hour, dom, month, dow = fields
    mins = render(expand(minute, "minute"), "minute")
    hours = render(expand(hour, "hour"), "hour")
    doms = render(expand(dom, "dom"), "dom")
    months = render(expand(month, "month"), "month")
    dows = render_dow(expand(dow, "dow"))
    time_part = f"{hours}:{mins}:00"
    # Vixie/cronie: a field starting with '*' (including */N) is "unrestricted".
    dom_restricted = not dom.startswith("*")
    dow_restricted = not dow.startswith("*")
    if dom_restricted and dow_restricted and dows:
        warnings.append(
            "cron runs this job when day-of-month OR day-of-week matches; "
            "emitted two OnCalendar= lines (systemd ORs them)"
        )
        return (
            [f"*-{months}-{doms} {time_part}", f"{dows} *-{months}-* {time_part}"],
            False,
            warnings,
        )
    date_part = f"*-{months}-{doms}"
    expr = f"{dows} {date_part} {time_part}" if dows else f"{date_part} {time_part}"
    return [expr], False, warnings


# --------------------------------------------------------------------------- crontab parsing


ENV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def parse_crontab(text: str, system: bool) -> tuple[list[Job], list[str]]:
    jobs: list[Job] = []
    errors: list[str] = []
    env: dict[str, str] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = ENV_RE.match(line)
        if m and not line.startswith("@") and not line[0].isdigit() and line[0] != "*":
            env[m.group(1)] = m.group(2).strip().strip("\"'")
            continue
        parts = line.split()
        if line.startswith("@"):
            need = 2 + (1 if system else 0)
            if len(parts) < need:
                errors.append(f"line {n}: missing command")
                continue
            schedule = parts[0]
            rest = parts[1:]
        else:
            need = 6 + (1 if system else 0)
            if len(parts) < need:
                errors.append(f"line {n}: expected 5 time fields{' + user' if system else ''} + command")
                continue
            schedule = " ".join(parts[:5])
            rest = parts[5:]
        user = rest.pop(0) if system else ""
        # Keep the command text exactly as written after the schedule/user columns.
        idx = 0
        for tok in parts[: len(parts) - len(rest)]:
            idx = raw.index(tok, idx) + len(tok)
        command = raw[idx:].strip()
        jobs.append(Job(schedule, command, user, dict(env), n))
    return jobs, errors


# --------------------------------------------------------------------------- unit generation


def slug(command: str) -> str:
    command = re.sub(r"^cd\s+\S+\s*&&\s*", "", command.strip())
    words = command.split()
    if len(words) > 1 and words[0] in ("php", "python3", "python", "node", "bash", "sh", "perl"):
        words = words[1:]
    first = words[0] if words else "job"
    base = Path(first).name if first not in ("/bin/sh", "sh", "bash", "/bin/bash") else "job"
    base = re.sub(r"\.(sh|py|php|pl|rb)$", "", base)
    s = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return s or "job"


def cron_command_to_exec(command: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    cmd = command
    if re.search(r"(?<!\\)%", cmd):
        warnings.append(
            "unescaped % in cron means newline/stdin; the text after the first % was dropped - check it"
        )
        cmd = re.split(r"(?<!\\)%", cmd, maxsplit=1)[0].rstrip()
    cmd = cmd.replace("\\%", "%")
    if re.search(r">\s*/dev/null|2>&1", cmd):
        warnings.append("output redirection is no longer needed: stdout/stderr go to the journal")
    exec_cmd = cmd.replace("%", "%%")  # systemd specifier escaping
    if SHELL_CHARS.search(cmd):
        exec_cmd = exec_cmd.replace("$", "$$")  # systemd expands $VAR in ExecStart
        quoted = exec_cmd.replace("\\", "\\\\").replace('"', '\\"')
        exec_cmd = f'/bin/sh -c "{quoted}"'
    elif not exec_cmd.startswith("/"):
        warnings.append(
            "command is not an absolute path; systemd searches a fixed PATH, prefer /usr/local/bin/..."
        )
    return exec_cmd, warnings


def build_units(job: Job, name: str, args) -> Converted:
    on_cal, on_boot, warnings = to_on_calendar(job.schedule)
    exec_cmd, w2 = cron_command_to_exec(job.command)
    warnings += w2
    mailto = job.env.pop("MAILTO", None)
    if mailto is not None:
        warnings.append("MAILTO has no systemd equivalent; use OnFailure= with a notify unit")
    job.env.pop("SHELL", None)
    user = args.user or job.user
    service = [
        "[Unit]",
        f"Description={args.description or f'{name} (converted from cron: {job.schedule})'}",
    ]
    if args.on_failure:
        service.append(f"OnFailure={args.on_failure}")
    service += ["", "[Service]", "Type=oneshot"]
    if user and user != "root":
        service.append(f"User={user}")
    for k, v in job.env.items():
        service.append(f'Environment="{k}={v}"')
    if args.workdir:
        service.append(f"WorkingDirectory={args.workdir}")
    service.append(f"ExecStart={exec_cmd}")
    service += ["Nice=10", ""]

    timer = ["[Unit]", f"Description=Timer for {name}", "", "[Timer]"]
    if on_boot:
        timer.append("OnBootSec=1min")
    for expr in on_cal:
        timer.append(f"OnCalendar={expr}")
    if args.persistent and not on_boot:
        timer.append("Persistent=true")
    if args.random_delay:
        timer.append(f"RandomizedDelaySec={args.random_delay}")
    timer.append("AccuracySec=1s" if args.precise else "AccuracySec=1min")
    timer += ["", "[Install]", "WantedBy=timers.target", ""]
    return Converted(
        name, job.schedule, job.command, on_cal, on_boot, "\n".join(service), "\n".join(timer), warnings
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Convert cron entries to systemd timer + service units.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--line", help='one cron entry, e.g. "*/15 * * * * /usr/local/bin/sync"')
    src.add_argument(
        "--file", help="crontab file (crontab -l output, /etc/crontab, /etc/cron.d/x), - for stdin"
    )
    p.add_argument(
        "--system", action="store_true", help="entries have a user column (/etc/crontab, /etc/cron.d)"
    )
    p.add_argument("--name", help="unit name for a single entry (default: derived from the command)")
    p.add_argument("--user", help="run as this user (User=); overrides the cron.d user column")
    p.add_argument("--workdir", help="WorkingDirectory= for the service")
    p.add_argument("--description", help="Description= for the service")
    p.add_argument(
        "--persistent",
        action="store_true",
        help="Persistent=true: run missed jobs after downtime (cron does not)",
    )
    p.add_argument("--random-delay", help="RandomizedDelaySec=, e.g. 5min, to spread load")
    p.add_argument("--precise", action="store_true", help="AccuracySec=1s instead of the 1min default")
    p.add_argument("--on-failure", help="OnFailure= unit, e.g. notify-failure@%%n.service")
    p.add_argument("--out", type=Path, help="write units into this directory instead of printing")
    p.add_argument("--json", action="store_true", help="print JSON")
    args = p.parse_args(argv)

    if args.line:
        text = args.line
    elif args.file == "-":
        text = sys.stdin.read()
    else:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    jobs, errors = parse_crontab(text, args.system)
    if not jobs and not errors:
        print("error: no cron entries found", file=sys.stderr)
        return 2

    results: list[Converted] = []
    used: dict[str, int] = {}
    for job in jobs:
        base = args.name if (args.name and len(jobs) == 1) else slug(job.command)
        used[base] = used.get(base, 0) + 1
        name = base if used[base] == 1 else f"{base}-{used[base]}"
        try:
            results.append(build_units(job, name, args))
        except CronError as exc:
            errors.append(f"line {job.line}: {exc}")

    if args.json:
        print(json.dumps({"units": [asdict(r) for r in results], "errors": errors}, indent=2))
    elif args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for r in results:
            (args.out / f"{r.name}.service").write_text(r.service, encoding="utf-8")
            (args.out / f"{r.name}.timer").write_text(r.timer, encoding="utf-8")
            print(f"wrote {args.out}/{r.name}.service and {r.name}.timer")
            for w in r.warnings:
                print(f"  warning: {w}")
    else:
        for r in results:
            print(f"# ---- {r.name}: cron '{r.schedule}' ----")
            for w in r.warnings:
                print(f"# warning: {w}")
            print(f"# /etc/systemd/system/{r.name}.service")
            print(r.service)
            print(f"# /etc/systemd/system/{r.name}.timer")
            print(r.timer)
    if results and not args.json:
        names = " ".join(f"{r.name}.timer" for r in results)
        print("\nCheck, then install (you run these):")
        for r in results:
            for expr in r.on_calendar:
                print(f"  systemd-analyze calendar --iterations=3 '{expr}'")
        print("  sudo cp *.service *.timer /etc/systemd/system/ && sudo systemctl daemon-reload")
        print(f"  sudo systemctl enable --now {names}")
        print("  # then remove the old cron line so the job does not run twice")
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
