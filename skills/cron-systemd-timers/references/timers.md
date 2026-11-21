# systemd timers in practice

## Minimal pair

`/etc/systemd/system/sync-feeds.service`

```ini
[Unit]
Description=Sync feeds

[Service]
Type=oneshot
User=deploy
ExecStart=/usr/local/bin/sync-feeds --quiet
Nice=10
```

`/etc/systemd/system/sync-feeds.timer`

```ini
[Unit]
Description=Run sync-feeds every 15 minutes

[Timer]
OnCalendar=*-*-* *:00/15:00
RandomizedDelaySec=30s
AccuracySec=1min

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sync-feeds.timer
systemctl list-timers sync-feeds.timer
```

## Useful options

| Option | Where | Effect |
| --- | --- | --- |
| `Persistent=true` | timer | Run once at boot if a scheduled run was missed while off |
| `RandomizedDelaySec=5min` | timer | Spread start times (avoid every server hitting S3 at 03:00:00) |
| `AccuracySec=1s` | timer | Default 1min coalesces wakeups; lower it for exact times |
| `OnBootSec=2min` + `OnUnitActiveSec=1h` | timer | Monotonic schedule: 2 min after boot, then hourly |
| `OnFailure=notify-failure@%n.service` | service `[Unit]` | Alert when the job fails |
| `TimeoutStartSec=30min` | service | Kill a hung oneshot job |
| `Restart=on-failure` + `RestartSec=1min` | service | Retries (oneshot services support it on systemd 244+) |
| `MemoryMax=512M`, `CPUQuota=50%` | service | Resource limits |
| `ProtectSystem=strict`, `ReadWritePaths=/srv/data`, `PrivateTmp=true`, `NoNewPrivileges=true` | service | Sandboxing |
| `EnvironmentFile=/etc/app/job.env` | service | Secrets/config out of the unit (chmod 600) |

Overlapping runs: a timer never starts a service that is still running, so the cron
`flock` trick is not needed.

## OnCalendar syntax

`DayOfWeek Year-Month-Day Hour:Minute:Second`, each part may use `*`, lists `a,b`,
ranges `a..b` and repetition `start/step`. Weekday names: `Mon..Sun`. Time zone: append
`Europe/Berlin` (systemd 235+) or the server's local time is used.

```bash
systemd-analyze calendar --iterations=5 'Mon..Fri *-*-* 09..17:00:00'
systemd-analyze calendar 'Sat *-*-1..7 04:00:00'        # first Saturday of the month
systemd-analyze calendar '*-*~01 23:00:00'               # last day of the month
```

## Debugging "it did not run"

```bash
systemctl list-timers --all | grep NAME        # NEXT/LAST columns; n/a = never scheduled
systemctl status NAME.timer NAME.service
journalctl -u NAME.service -n 100 --no-pager
systemd-analyze verify /etc/systemd/system/NAME.*   # syntax errors
```

Common causes: timer not enabled (`enable --now` the **timer**, not the service), forgot
`daemon-reload` after editing, `OnCalendar` typo (`systemd-analyze calendar` rejects it),
service fails because of relative paths or a different `PATH`, `User=` lacks permissions.

For cron jobs that do not run: `journalctl -u cron --since today`, check the file ends with
a newline, files in `/etc/cron.d/` must not contain dots in their names, `%` must be escaped,
and cron's `PATH` is only `/usr/bin:/bin`.

## User timers (no root)

Put units in `~/.config/systemd/user/`, use `systemctl --user daemon-reload` and
`systemctl --user enable --now NAME.timer`. To run while logged out:
`sudo loginctl enable-linger USER`.
