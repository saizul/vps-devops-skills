---
name: cron-systemd-timers
description: Convert crontab entries to systemd timers and services (OnCalendar expressions, Persistent catch-up, RandomizedDelaySec, User=, Environment=, OnFailure alerts, journal logging), explain cron and OnCalendar schedules, and debug jobs that do not run. Handles cron's day-of-month OR day-of-week rule, @reboot/@daily specials, % escaping and /etc/cron.d user columns. Use when the user wants to migrate cron jobs to systemd timers, write or explain an OnCalendar or cron expression, asks why a cron job or timer did not run, or needs logging, retries or missed-run handling for scheduled jobs.
license: MIT
compatibility: Converter needs Python 3.8+ and only prints or writes unit files. Target - Linux with systemd 239+ (Ubuntu 20.04+, Debian 11+).
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# Cron to systemd timers

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "Convert my crontab to systemd timers", "make this cron job a timer"
- "What does `*/15 9-17 * * 1-5` mean?", "write OnCalendar for every weekday at 2:30"
- "My cron job doesn't run" / "the timer never fired"
- Jobs that need logs in the journal, failure alerts, catch-up after downtime, or a lock
  against overlapping runs

## When not to use

- Systems without systemd (Alpine/OpenRC, containers without an init): keep cron
- Per-second or event-driven work: use a long-running service, a path unit, or a queue
- Distributed schedules across many servers: use the scheduler of your orchestrator

## Workflow

1. **Collect the entries**: `crontab -l` (user), `sudo crontab -l -u USER`, `/etc/crontab`
   and `/etc/cron.d/*` (these have a user column: pass `--system`).
2. **Convert (prints only, installs nothing).**
   ```bash
   python3 scripts/cron_to_timer.py --line '*/15 * * * * /usr/local/bin/sync-feeds'
   crontab -l > my.crontab && python3 scripts/cron_to_timer.py --file my.crontab --out ./units
   python3 scripts/cron_to_timer.py --file /etc/cron.d/app --system --persistent --json
   python3 scripts/cron_to_timer.py --line '0 2 * * * /usr/local/bin/backup' --name nightly-backup \
       --user backup --random-delay 10min --on-failure 'notify-failure@%n.service'
   ```
   Exit code 1 = at least one entry could not be converted (reported on stderr).
3. **Review every warning** the converter prints and decide with the user:
   - **Persistent=true** runs a missed job at boot (cron does not). Good for backups and
     cleanups; bad for "send the 9:00 report" jobs.
   - **DOM + DOW both set** (`0 4 1,15 * 0`): cron runs on the 1st, the 15th **and** every
     Sunday. The converter emits two `OnCalendar=` lines to keep that meaning.
   - `%` in cron is a newline; `\%` is a literal percent. In units `%` must be `%%`.
   - Output redirection (`>/dev/null 2>&1`) is unnecessary: output goes to the journal.
   - `MAILTO` has no equivalent: use `OnFailure=` (see the reference).
   - Environment: cron's minimal `PATH` differs from systemd's; use absolute paths.
4. **Verify the schedule on the server** for each expression:
   `systemd-analyze calendar --iterations=5 'Mon..Fri *-*-* 02:30:00'`.
5. **Install (user runs or confirms):** copy units to `/etc/systemd/system/` (or
   `~/.config/systemd/user/` with `systemctl --user` for user jobs), `sudo systemctl daemon-reload`,
   `sudo systemctl enable --now NAME.timer`, test once with `sudo systemctl start NAME.service`,
   check `journalctl -u NAME.service`. Then **remove the cron line** so the job does not run twice.
6. For "job did not run" questions, follow the debugging section in
   [references/timers.md](references/timers.md).

## Output

For each job: the original cron line, the OnCalendar expression(s) in plain words
("every 15 minutes", "weekdays at 02:30"), both unit files, warnings with the decision
taken, and the install + verify commands. Example:

```
cron:  30 2 * * 1-5 /home/deploy/bin/report.sh
timer: OnCalendar=Mon..Fri *-*-* 02:30:00   (weekdays at 02:30 server time)
```

## Cheat sheet

| cron | OnCalendar |
| --- | --- |
| `* * * * *` | `*-*-* *:*:00` (or `minutely`) |
| `*/5 * * * *` | `*-*-* *:00/5:00` |
| `0 * * * *` | `hourly` = `*-*-* *:00:00` |
| `0 0 * * *` | `daily` = `*-*-* 00:00:00` |
| `0 0 * * 0` | `Sun *-*-* 00:00:00` (`weekly` in systemd is **Monday**) |
| `0 0 1 * *` | `monthly` = `*-*-01 00:00:00` |
| `30 2 * * 1-5` | `Mon..Fri *-*-* 02:30:00` |
| `0 9-17 * * *` | `*-*-* 09..17:00:00` (the converter writes the equivalent list `09,10,...,17`) |
