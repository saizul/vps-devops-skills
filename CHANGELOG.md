# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `cron-systemd-timers`: `cron_to_timer.py` turns `CRON_TZ=Area/City` lines into a time zone
  suffix on every `OnCalendar=` expression (systemd 235+), with a new `--timezone` option to
  set or override it. Invalid zone names are reported as conversion errors.

### Fixed

- `cron-systemd-timers`: `CRON_TZ` is no longer emitted as an `Environment=` line, where it
  had no effect on the schedule.

### Added

- `php-fpm-pool-sizing`: new skill with `fpm_sizing.py`, which computes `pm.max_children`
  from RAM, a reserve for other services and measured child RSS (90th percentile by
  default), suggests spare-server values for `dynamic`, `ondemand` or `static`, splits the
  budget across pools and exits 1 when an existing pool file can overcommit memory.

## [0.1.0] - 2026-11-21

### Added

- `server-hardening-audit`: read-only audit of sshd (`sshd -T`), UFW, fail2ban,
  unattended-upgrades, UID 0 and sudo accounts, public listening ports; live or `--from-dir`.
- `nginx-config-review`: nginx parser with include resolution and `nginx -T` dump support;
  checks for headers, add_header inheritance, TLS protocols/ciphers, open proxy, alias
  traversal, autoindex, hidden files and HTTP redirects.
- `docker-compose-review`: pinned images, healthchecks, restart, memory limits, log rotation,
  public ports (UFW bypass), literal secrets, docker.sock and host mounts, privileged mode.
- `backup-setup`: restic script + systemd service/timer generator with MySQL/PostgreSQL dumps,
  destination guide, monitoring and a restore drill checklist.
- `incident-triage`: read-only snapshot (load, memory, swap, disk, inodes, OOM, failed units)
  and runbooks for high load, disk full, OOM and 5xx.
- `log-investigation`: access log summariser (.gz, filters, slow paths, scanner probes) and
  journalctl/grep/awk recipes.
- `ssl-tls-check`: endpoint and PEM file checks for expiry, SANs, chain, TLS version and
  legacy protocols; certbot troubleshooting.
- `cron-systemd-timers`: cron to OnCalendar/systemd unit converter with DOM/DOW OR semantics,
  specials, `%` handling and `/etc/cron.d` user columns.
- Claude Code plugin marketplace manifest, `tools/validate_skills.py`, CI and release workflows.

[Unreleased]: https://github.com/saizul/vps-devops-skills/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saizul/vps-devops-skills/releases/tag/v0.1.0
