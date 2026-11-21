# Runbooks

All commands in the "Investigate" blocks are read-only. Commands under "Remediate" change
the system: explain the impact and get confirmation first.

## High load

Load average counts runnable **and** uninterruptible (IO-waiting) tasks. Compare with `nproc`.

Investigate:

```bash
uptime; nproc
top -b -n 1 -o %CPU | head -25
vmstat 1 5                     # us/sy = CPU bound, wa = disk bound, st = noisy neighbour
iostat -xz 1 3                 # apt install sysstat; %util ~100 on a device = disk bound
ps -eo state,pid,ppid,etime,cmd | awk '$1 ~ /^D/'    # stuck in IO
pidstat -u 1 5                 # per-process CPU over time (sysstat)
systemd-cgtop -n 1             # CPU per service/container
```

Typical causes and next steps:

| Signal | Likely cause | Next step |
| --- | --- | --- |
| php-fpm/node/python at 100% CPU | traffic spike, bot crawl, slow endpoint | `log-investigation`: top IPs/paths in the last 15 min |
| high `wa`, `D` state tasks | disk saturated (backup, DB without index, swap) | `iotop -oPa` (needs root), check running backups/cron |
| high `st` (steal) | hypervisor contention | contact provider or resize; nothing to fix inside the VM |
| `kswapd` busy, swap in/out | memory pressure | see "Out of memory" |
| mysqld high CPU | slow queries | `mysqladmin processlist`; enable slow query log |

Remediate (confirm first): rate limit or block the abusive IP/ASN at nginx or Cloudflare;
`sudo systemctl restart <app>` only if it is wedged; postpone the heavy cron job.

## Disk full

Investigate:

```bash
df -hT -x tmpfs -x devtmpfs
df -i                                                     # inodes: 100% also means "full"
sudo du -xh --max-depth=2 / 2>/dev/null | sort -h | tail -25
sudo journalctl --disk-usage
sudo find /var/log -xdev -type f -size +100M -exec ls -lh {} +
docker system df                                          # images, containers, volumes, build cache
sudo lsof +L1 2>/dev/null | head                         # deleted files still open
ls -la /var/lib/mysql/*bin* 2>/dev/null | tail            # MySQL binlogs
```

Common culprits: one huge log without rotation, Docker json logs
(`/var/lib/docker/containers/*/*-json.log`), old images and build cache, MySQL binary logs,
journald, core dumps, backups written locally, PHP session files (inodes).

Remediate (confirm first, least destructive first):

```bash
sudo journalctl --vacuum-size=500M
sudo truncate -s 0 /var/log/app/huge.log            # instead of rm while the app has it open
docker image prune -a --filter "until=168h"           # shows what it removes; asks to confirm
docker builder prune --filter "until=168h"
sudo mysql -e "PURGE BINARY LOGS BEFORE NOW() - INTERVAL 3 DAY;"   # only if replicas are caught up
sudo apt-get clean
```

If `lsof +L1` shows a deleted file held open, restarting that one process frees the space.
Afterwards, fix the cause: logrotate config, Docker `max-size` (see `docker-compose-review`),
`SystemMaxUse=` in `/etc/systemd/journald.conf`.

## Out of memory

Investigate:

```bash
free -m
journalctl -k --since -24h | grep -iE 'out of memory|oom-kill|killed process'
ps -eo pid,user,rss,%mem,etime,cmd --sort=-rss | head -15
systemd-cgtop -m -n 1
cat /proc/pressure/memory          # PSI: "some avg10" > 10 = sustained pressure
docker stats --no-stream           # per-container memory
```

Read the OOM line: it names the killed process and its RSS. The killed process is often
the **victim** (largest), not the cause (the one that grew). Compare RSS over time.

Typical fixes (confirm first):
- php-fpm: `pm.max_children` x average child RSS must fit in RAM: `ps -C php-fpm8.3 -o rss= | awk '{s+=$1} END {print s/NR/1024 " MiB avg"}'`
- MySQL: `innodb_buffer_pool_size` too large for the box (about 50-60% of RAM on a dedicated DB host, far less when shared)
- Containers: set `mem_limit` so one service is killed instead of the host thrashing
- systemd services: `MemoryMax=` in a drop-in (`sudo systemctl edit app.service`)
- Small VPS without swap: a 1-2 GiB swap file buys time:
  `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`
  and add `/swapfile none swap sw 0 0` to `/etc/fstab`.

## 5xx and site down

Work from the outside in: DNS -> TLS -> nginx -> upstream app -> database.

```bash
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' https://example.com/
dig +short example.com                         # points at this server?
sudo systemctl status nginx --no-pager
sudo tail -n 100 /var/log/nginx/error.log
sudo ss -tlnp | grep -E ':80|:443|:3000|:8000|:9000'   # is the upstream listening?
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/health   # bypass nginx
systemctl status php8.3-fpm --no-pager; sudo tail -n 50 /var/log/php8.3-fpm.log
journalctl -u <app>.service --since -30min --no-pager | tail -50
```

| Status / log line | Meaning | Next step |
| --- | --- | --- |
| 502 + `connect() failed (111: Connection refused)` | upstream not running / wrong port | `systemctl status <app>`, check port |
| 502 + `upstream prematurely closed` | app crashed mid-request | app logs, OOM check |
| 504 + `upstream timed out` | app too slow | slow endpoint, DB locks, raise timeout only as last resort |
| 503 | rate limit (`limit_req`), maintenance, or upstream marked down | nginx error log |
| `server reached pm.max_children` | php-fpm pool exhausted | traffic analysis, then size the pool to RAM |
| `Too many open files` | ulimit | `LimitNOFILE=` in the service, `worker_rlimit_nofile` in nginx |
| `SSL_do_handshake() failed` | TLS/cert problem | `ssl-tls-check` |

Then quantify with `log-investigation`:
`python3 ../log-investigation/scripts/access_log_summary.py /var/log/nginx/access.log --last 30m`.
