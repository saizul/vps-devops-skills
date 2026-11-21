---
name: incident-triage
description: Step-by-step, read-only triage for a Linux server or website in trouble - high load or CPU, disk full (space or inodes), out-of-memory kills, failed systemd units, 502/503/504 and other 5xx errors, or the site being slow or down - with a snapshot script that flags the likely cause and exact next commands. Use when the user says the server is slow, down, unreachable, out of disk, out of memory, returning 502 Bad Gateway or 5xx errors, a service keeps crashing, or asks what to check first during an outage.
license: MIT
compatibility: Linux with systemd and Python 3.8+ (Ubuntu/Debian). Offline mode reads captured command outputs on any OS.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# Incident triage

Goal: find the cause quickly **without making it worse**. Observe first; every
remediation (restart, kill, delete) is proposed, explained and confirmed separately.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "The site is down / slow / returning 502", "server is unresponsive", "load is 30"
- "No space left on device", "disk full", writes failing
- A process was "Killed", MySQL keeps restarting, OOM messages
- A systemd service is in `failed` state or flapping

## When not to use

- Planned performance tuning with no active incident (profile instead)
- Security incidents (suspected break-in): preserve evidence, do not clean up; isolate the
  server and follow an incident-response process. This skill can still collect read-only data.
- Kubernetes/cluster-wide incidents (use cluster tooling); this skill is for single hosts

## Workflow

1. **Stabilise the conversation, not the server.** Ask: what is the symptom, since when,
   what changed recently (deploy, update, traffic spike, cron job)? Note the time; it scopes
   every log query.
2. **Take a snapshot (read-only, about 2 seconds).** On the server:
   ```bash
   sudo python3 scripts/triage.py              # text report, worst first
   sudo python3 scripts/triage.py --json
   ```
   From a laptop: `scp scripts/triage.py HOST:/tmp/` and ask the user to run
   `ssh HOST 'sudo python3 /tmp/triage.py'`. If you cannot run anything, ask for the outputs of
   `uptime; free -m; df -hT; df -i; systemctl --failed; journalctl -k --since -2h | tail -50`
   and analyse them offline with `--root` and the `--*-file` options (see `--help`).
3. **Follow the matching runbook** in [references/runbooks.md](references/runbooks.md):
   - High load / CPU -> section "High load"
   - Disk full -> "Disk full" (also check inodes and deleted-but-open files)
   - OOM kills / low memory -> "Out of memory"
   - 5xx / site down while the box looks healthy -> "5xx and site down"
   Run the read-only commands it lists; read outputs before the next step.
4. **Form one hypothesis and test it** with a single command. Write down the evidence
   (log line, metric) for the user. Avoid shotgun restarts: they destroy evidence and
   often only delay the problem.
5. **Propose the smallest safe remediation** and wait for confirmation, e.g.
   `sudo systemctl restart php8.3-fpm`, `sudo journalctl --vacuum-size=500M`, rotating a
   runaway log. Say what it will affect (dropped connections, brief downtime).
   Never `rm` files under `/var/lib/mysql`, `/var/lib/docker` or `/var/lib/postgresql`;
   never `kill -9` a database.
6. **Verify recovery** (rerun the snapshot, curl the site, watch `journalctl -f` for a minute)
   and write the short incident note (see Output).

## Output

During triage, short updates: finding -> evidence -> next command. At the end:

```
Incident: 502s on example.com, 09:12-09:40 UTC
Cause: php-fpm pool exhausted (pm.max_children=5 reached), triggered by bot traffic on /search
Evidence: "server reached pm.max_children setting" in php8.3-fpm log; 4,100 req/min from one ASN
Fix applied: rate limit on /search (limit_req), max_children 5 -> 12 (RAM checked)
Follow-ups: alert on 5xx rate; review cache headers for /search
```

## Quick reference: read-only first commands

```bash
uptime; nproc                                   # load vs CPU count
free -m; vmstat 1 5                             # memory, swap, IO wait (wa), steal (st)
df -hT -x tmpfs -x devtmpfs; df -i              # space and inodes
systemctl --failed; journalctl -p err -b --since -1h --no-pager | tail -50
journalctl -k --since -24h | grep -iE 'out of memory|oom-kill'
ss -s; ss -tnp state established | wc -l        # connection counts
sudo tail -n 50 /var/log/nginx/error.log
```
