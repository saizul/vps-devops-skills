# Log recipes (read-only)

## nginx access log (combined format)

Field positions with the default `combined` format, split on spaces:
`$1` IP, `$4` time, `$6` method, `$7` path, `$9` status, `$10` bytes.

```bash
L=/var/log/nginx/access.log
# Status codes
awk '{print $9}' $L | sort | uniq -c | sort -rn
# Requests per minute (busiest 10)
awk '{print substr($4, 2, 17)}' $L | sort | uniq -c | sort -rn | head
# Top IPs returning 5xx
awk '$9 ~ /^5/ {print $1}' $L | sort | uniq -c | sort -rn | head
# Everything one IP did
grep '^203\.0\.113\.10 ' $L | tail -50
# Requests in a time window
awk '$4 >= "[21/Nov/2026:09:00" && $4 < "[21/Nov/2026:10:00"' $L | wc -l
# Rotated files too
zcat -f $L* | awk '$9 == 502 {print $7}' | sort | uniq -c | sort -rn | head
# User agents of 404s (bot spotting)
awk -F'"' '$3 ~ / 404 / {print $6}' $L | sort | uniq -c | sort -rn | head
```

Add timings to the log format (then the summary script shows slowest paths):

```nginx
log_format timed '$remote_addr - $remote_user [$time_local] "$request" $status '
                 '$body_bytes_sent "$http_referer" "$http_user_agent" '
                 '$upstream_response_time $request_time';
access_log /var/log/nginx/access.log timed;
```

## nginx error log

```bash
sudo tail -n 200 /var/log/nginx/error.log
sudo grep -E 'upstream (timed out|prematurely closed)|connect\(\) failed' /var/log/nginx/error.log | tail
sudo awk '{print $3}' /var/log/nginx/error.log | sort | uniq -c        # by level: [error] [warn] [crit]
```

## systemd journal

```bash
journalctl -u nginx --since "2026-11-21 09:00" --until "2026-11-21 10:00" --no-pager
journalctl -u myapp -n 200 --no-pager -o short-iso
journalctl -p warning -b --no-pager | tail -100       # warnings and worse since boot
journalctl -b -1 -p err                                # previous boot (after a crash)
journalctl -k --since -24h                             # kernel: OOM, disk errors, segfaults
journalctl --list-boots | tail                         # unexpected reboots?
journalctl -u myapp -o json --since -1h | python3 -c 'import sys,json; [print(json.loads(l)["MESSAGE"]) for l in sys.stdin]'
journalctl -f -u myapp                                 # follow live (Ctrl-C to stop)
```

## SSH and auth

```bash
# Failed logins by IP (Ubuntu 24.04 logs to the journal and /var/log/auth.log)
journalctl -u ssh --since -24h | grep -E 'Failed|Invalid user' | grep -oE 'from [0-9a-f.:]+' | sort | uniq -c | sort -rn | head
# Successful logins (who got in)
journalctl -u ssh --since -7d | grep 'Accepted'
sudo last -n 20; sudo lastb -n 20
# sudo usage
journalctl _COMM=sudo --since -24h
# fail2ban bans
sudo fail2ban-client status sshd
```

## PHP-FPM, MySQL, Docker

```bash
sudo tail -n 100 /var/log/php8.3-fpm.log
sudo grep -c 'max_children' /var/log/php8.3-fpm.log
sudo tail -n 100 /var/log/mysql/error.log
sudo mysql -e "SHOW GLOBAL STATUS LIKE 'Threads_connected';"
docker compose logs --since 30m --tail 200 app
docker logs --since 2026-11-21T09:00:00 --until 2026-11-21T10:00:00 CONTAINER 2>&1 | grep -i error
docker inspect --format '{{.State.OOMKilled}} {{.RestartCount}}' CONTAINER
```

## Cron and timers

```bash
journalctl -u cron --since today | tail -50           # Ubuntu cron service name: cron
systemctl list-timers --all
journalctl -u mytask.service --since -2d
```
