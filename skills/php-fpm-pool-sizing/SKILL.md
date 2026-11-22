---
name: php-fpm-pool-sizing
description: Size php-fpm pools to the server's RAM - measure per-child memory with ps, compute pm.max_children with a safety percentile and a reserve for MySQL/Redis/the OS, pick pm dynamic/static/ondemand and spare-server values, and flag an existing pool that can overcommit memory and trigger the OOM killer. Use when the user sees "server reached pm.max_children setting", 502/504 errors from nginx with php-fpm, OOM kills of php-fpm or MySQL, a slow WordPress/Laravel site under load, or asks what pm.max_children should be.
license: MIT
compatibility: Python 3.8+. Measuring needs ps on the server; the calculation itself runs anywhere.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# php-fpm pool sizing

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "server reached pm.max_children setting" in the php-fpm log
- nginx 502/504 with php-fpm upstreams, requests queueing under traffic
- The OOM killer stopped php-fpm or MySQL (`dmesg`, `journalctl -k | grep -i oom`)
- A new VPS or a RAM upgrade: "what should pm.max_children be?"
- Several sites (pools) sharing one server

## When not to use

- The bottleneck is slow PHP code or the database: more children only add load. Check
  slow endpoints first with `log-investigation` and the php-fpm `slowlog`
- Opcache, JIT or `memory_limit` tuning for a single script
- Containers with a memory limit: use the container limit as `--ram-mb`, not the host RAM

## Workflow

1. **Find the pools and the version.**
   ```bash
   ls /etc/php/*/fpm/pool.d/          # Debian/Ubuntu; one .conf per pool
   systemctl list-units 'php*-fpm*' --no-pager
   free -m; nproc
   ```
2. **Measure child memory under normal traffic** (not right after a restart, children grow).
   ```bash
   ps --no-headers -o rss -C php-fpm8.3 > rss.txt     # KiB per process, master included
   ```
   For one pool only: `ps --no-headers -o rss,cmd -C php-fpm8.3 | grep 'pool www' | awk '{print $1}'`.
3. **Decide the reserve**: memory for everything that is not php-fpm. Add up MySQL
   (`innodb_buffer_pool_size` + about 20%), Redis `maxmemory`, nginx, the OS (300-500 MiB) and
   any workers. When unsure, look at `ps -eo rss,comm --sort -rss | head` and add the non-PHP
   processes.
4. **Compute.**
   ```bash
   python3 scripts/fpm_sizing.py --rss-file rss.txt --reserve-mb 1536 --cpus "$(nproc)" \
       --pool-conf /etc/php/8.3/fpm/pool.d/www.conf
   python3 scripts/fpm_sizing.py --child-mb 70 --ram-mb 4096 --reserve-mb 1024 --pools 3 --json
   ```
   The script uses the 90th percentile of the measured RSS (`--percentile`) so heavy requests
   are covered. With `--pools N` the budget is split equally; size busy pools separately if
   traffic is uneven. Exit 1 means the current `pm.max_children` can use more memory than the
   budget.
5. **Choose the mode** (`--pm`):
   - `dynamic` (default): steady traffic; spare servers scale with the CPU count.
   - `ondemand`: many low-traffic sites on one server; children exit after 10 s idle.
   - `static`: a dedicated PHP server with predictable load; all children always run.
6. **Show the diff** of the pool file and wait for confirmation. Then test and reload:
   ```bash
   sudo php-fpm8.3 -t && sudo systemctl reload php8.3-fpm
   ```
7. **Verify** after a busy period: no new `max_children` warnings in the php-fpm log, no OOM
   kills, and swap not growing (`vmstat 5`). Re-measure after plugin or PHP upgrades.

## Output

```
Measured 24 children: mean 57.8 MiB, max 96.4 MiB, using p90 = 85.9 MiB
; [www] sized for 3931 MiB RAM, 1536 MiB reserved,
; 85.9 MiB per child, 1 pool(s): worst case 2320 MiB
pm = dynamic
pm.max_children = 27
pm.start_servers = 6
pm.min_spare_servers = 4
pm.max_spare_servers = 8
pm.max_requests = 500
WARN  current pm.max_children = 50 can use 4297 MiB, more than the 2395 MiB budget: ...
WARN  pm.max_requests is 0 (unlimited): set 500 so leaking extensions are recycled
```

Then explain the numbers in one paragraph, show the pool file change, and list what to
watch after the reload. If the budget allows fewer than about 5 children, say so: the server
needs more RAM, a smaller reserve (MySQL buffer pool) or fewer heavy plugins, not a higher
`pm.max_children`.
