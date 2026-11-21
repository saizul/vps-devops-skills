# Knowing when backups fail

A silent failure is the usual way backups die. Use at least one of these.

## 1. OnFailure unit (local alert)

`/etc/systemd/system/notify-failure@.service`:

```ini
[Unit]
Description=Failure notification for %i

[Service]
Type=oneshot
ExecStart=/usr/local/bin/notify-failure %i
```

`/usr/local/bin/notify-failure` (chmod 755) can send mail, a Slack/Telegram webhook, or
write to a monitored log. Keep webhook URLs in a root-only file, not in the unit.

Generate units with `--on-failure 'notify-failure@%n.service'`.

## 2. Heartbeat (dead man's switch)

A heartbeat service (Healthchecks.io, Uptime Kuma push monitor, Better Stack) alerts when
it does **not** hear from the job. This also catches "the timer never ran". Add to the end
of the generated backup script, after `restic check`:

```sh
curl -fsS -m 10 --retry 3 "$HEARTBEAT_URL" >/dev/null
```

and put `HEARTBEAT_URL=` in the env file. Ping `$HEARTBEAT_URL/fail` from the
OnFailure unit if the service supports it.

## 3. Check from the outside weekly

```bash
systemctl list-timers 'restic-*'
journalctl -u restic-system.service --since -8d | grep -E 'snapshot .* saved|Fatal|error'
```

## What to alert on

- Job failed (exit code)
- No successful snapshot within 26 hours
- `restic check` errors
- Repository size dropped sharply (possible deletion)
