---
name: log-investigation
description: Investigate server logs fast - summarise nginx/Apache access logs (status codes, top IPs, paths, user agents, 5xx and 404 hot spots, busiest minutes, scanner probes, slowest endpoints) with a script that reads rotated .gz files, plus journalctl, grep and awk recipes for systemd services, auth.log/SSH, kernel, PHP-FPM, MySQL and Docker logs. Use when the user asks what is in the logs, why errors or 5xx spiked, who is hammering the site, which IP or bot to block, what happened at a certain time, or to find the error behind a failing service.
license: MIT
compatibility: Python 3.8+ for the summary script. Recipes assume Linux with systemd (journalctl), GNU grep/awk.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# Log investigation

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "What's in the logs?", "why did 5xx spike at 14:00?", "who is hitting my server?"
- Deciding which IPs, bots or paths to rate limit or block
- Finding the error behind a crashing systemd service or container
- SSH brute-force questions (auth.log / journal)

## When not to use

- Centralised logging already exists (Loki, ELK, Datadog): query it there instead
- Live outage with unknown cause: start with `incident-triage`, then come here
- Application-level tracing/APM questions

## Workflow

1. **Scope the question**: which service, which time window (with timezone), which
   symptom. Log times in nginx are server local time; journalctl takes `--since`/`--until`.
2. **Access logs: summarise before grepping.**
   ```bash
   python3 scripts/access_log_summary.py /var/log/nginx/access.log
   python3 scripts/access_log_summary.py /var/log/nginx/access.log* --last 1h     # .gz rotated files ok
   python3 scripts/access_log_summary.py access.log --status 5xx --top 20
   python3 scripts/access_log_summary.py access.log --since 2026-11-21T09:00:00+00:00 \
       --until 2026-11-21T10:00:00+00:00 --json
   sudo cat /var/log/nginx/access.log | python3 scripts/access_log_summary.py -
   ```
   Filters: `--status 404|5xx`, `--ip`, `--path-prefix /api/`, `--keep-query`.
   `--max-5xx-pct 2` exits 1 when the 5xx rate is higher (handy in cron/CI).
   If the log format has `$request_time` as the last field, the slowest paths table appears;
   see [references/recipes.md](references/recipes.md) for a log_format that adds it.
3. **Drill into what the summary shows** with the recipes in
   [references/recipes.md](references/recipes.md): one IP's requests, a path's error lines,
   correlated `error.log` entries, upstream response times.
4. **Service logs**: `journalctl -u SERVICE --since "-1h" --no-pager | tail -100`,
   `journalctl -p err -b` for errors since boot, `docker compose logs --since 1h SERVICE`.
5. **Conclude with evidence**: quote at most a few log lines (redact tokens, cookies,
   emails and session IDs), give counts, and state the time window analysed.
6. **Recommend actions** (rate limit, block, fix an endpoint, add log rotation). Blocking
   or config changes are proposed, not applied, until the user confirms.

## Output

```
Window: 2026-11-21 09:00-10:00 UTC, 48,210 requests, 5xx 3.1% (normal < 0.5%)
5xx: 1,402 x POST /api/checkout (502) from 09:12, matching "upstream prematurely closed" in error.log
Top IP: 203.0.113.10 (21% of traffic, UA "python-requests/2.31", 4,100 req/min)
Scanner probes: 192.0.2.66 (.env, wp-login.php, phpmyadmin) - all 404, harmless but noisy
Suggested: limit_req on /api/, block 203.0.113.10 at Cloudflare, investigate checkout worker crash
```

## Privacy

Access logs contain IP addresses and sometimes personal data in URLs. Summarise; do not
paste large raw excerpts into the conversation, and never copy logs to third-party services.
