# vps-devops-skills

[![CI](https://github.com/saizul/vps-devops-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/saizul/vps-devops-skills/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/saizul/vps-devops-skills?sort=semver)](https://github.com/saizul/vps-devops-skills/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/saizul/vps-devops-skills?style=social)](https://github.com/saizul/vps-devops-skills/stargazers)

**Agent Skills that let Claude Code and Codex run your Linux servers safely: audit first, change only with your confirmation.**

Eight skills for the jobs that come up on every Ubuntu/Debian VPS: hardening, nginx and
Docker Compose review, backups that are actually restorable, outage triage, log digging,
TLS certificates and cron jobs. Each skill is a step-by-step workflow plus a tested,
standard-library-only Python script, so the agent gets deterministic answers instead of guessing.

- **Read-only by default.** Scripts inspect and report. Every command that changes a server is
  shown to you first, and the skill tells the agent to wait for a yes.
- **Tested.** 140+ tests with fixtures for every script, run on Python 3.11-3.13 in CI.
- **No dependencies.** Python 3.8+ standard library only (PyYAML optional for YAML compose files).
  Copy a script to a server with `scp` and run it.
- **Ubuntu 24.04 aware.** sshd drop-in ordering, `ssh.socket` activation, nginx 1.24, `php8.3-fpm`.

## Skills

| Skill | What it does | Try asking |
| --- | --- | --- |
| [server-hardening-audit](skills/server-hardening-audit/SKILL.md) | Read-only audit of `sshd -T`, UFW, fail2ban, unattended-upgrades, sudo/UID 0 users, public listening ports; findings with exact fixes | "Audit my new Ubuntu 24.04 VPS before I go live" |
| [nginx-config-review](skills/nginx-config-review/SKILL.md) | Parses nginx config (follows includes or reads `nginx -T`): headers, add_header inheritance, TLS, open proxy, alias traversal, autoindex, .git/.env exposure | "Review this nginx site config and fix the security headers" |
| [docker-compose-review](skills/docker-compose-review/SKILL.md) | Pinned tags, healthchecks, restart, memory limits, log rotation, ports on 0.0.0.0 (UFW bypass), literal secrets, docker.sock | "Is this docker-compose.yml production ready?" |
| [backup-setup](skills/backup-setup/SKILL.md) | restic plan (S3, B2, SFTP, rclone), DB dumps, generated script + systemd service/timer, restore drill checklist | "Set up nightly encrypted backups of /var/www and MySQL to B2" |
| [incident-triage](skills/incident-triage/SKILL.md) | Snapshot of load, memory, disk/inodes, OOM kills, failed units; runbooks for high load, disk full, OOM, 5xx | "My site is returning 502 and the server feels slow" |
| [log-investigation](skills/log-investigation/SKILL.md) | Access log summary (status, IPs, paths, 5xx/404, busiest minute, scanner probes, slow endpoints; .gz ok) plus journalctl/grep/awk recipes | "Who is hammering my server and what's behind the 5xx spike?" |
| [ssl-tls-check](skills/ssl-tls-check/SKILL.md) | Expiry, SAN match, chain verification, TLS version, legacy TLS 1.0/1.1, PEM file inspection, certbot troubleshooting | "Check SSL expiry for example.com, www and api" |
| [cron-systemd-timers](skills/cron-systemd-timers/SKILL.md) | Converts crontab lines to `.service` + `.timer` (OnCalendar, `CRON_TZ` time zones, Persistent, User=, OnFailure), explains schedules, debugs jobs that do not run | "Convert my crontab to systemd timers" |
| [php-fpm-pool-sizing](skills/php-fpm-pool-sizing/SKILL.md) | Measures php-fpm child RSS, computes `pm.max_children` (p90, reserve for MySQL/Redis, several pools), spare servers and pm mode; flags pools that can overcommit RAM | "We keep hitting pm.max_children, what should it be on this 4 GB VPS?" |

## Install

### Claude Code: plugin marketplace (recommended)

```text
/plugin marketplace add saizul/vps-devops-skills
/plugin install vps-devops-skills@vps-devops-skills
```

Or from your shell:

```bash
claude plugin marketplace add saizul/vps-devops-skills
claude plugin install vps-devops-skills@vps-devops-skills
```

Plugin skills are namespaced, e.g. `/vps-devops-skills:incident-triage`. Claude also picks them
up automatically when your request matches a skill's description. Update later with
`/plugin marketplace update vps-devops-skills`.

### Claude Code: copy the skills

```bash
git clone https://github.com/saizul/vps-devops-skills.git
# all projects (personal skills)
mkdir -p ~/.claude/skills && cp -r vps-devops-skills/skills/* ~/.claude/skills/
# or just one project
mkdir -p .claude/skills && cp -r vps-devops-skills/skills/ssl-tls-check .claude/skills/
```

Each release also has one zip per skill and an all-skills zip under
[Releases](https://github.com/saizul/vps-devops-skills/releases).

### Codex CLI

Codex reads skills from `~/.agents/skills` (all repositories) and `.agents/skills` in a
repository (from the current directory up to the repository root):

```bash
git clone https://github.com/saizul/vps-devops-skills.git
mkdir -p ~/.agents/skills && cp -r vps-devops-skills/skills/* ~/.agents/skills/
```

Codex also ships a `$skill-installer` skill that can install skills from a GitHub repository;
ask it to install from `saizul/vps-devops-skills`. Skill locations and the installer are
described in the [Codex skills documentation](https://developers.openai.com/codex/skills);
check it if your Codex version behaves differently. [AGENTS.md](AGENTS.md) explains how
Codex and other agents should use these skills.

### Any other agent

The skills follow the open [Agent Skills format](https://agentskills.io/specification):
a folder with `SKILL.md` (YAML frontmatter + instructions), `scripts/` and `references/`.
Point your agent at the folder, or run the scripts yourself; every script has `--help`.

## Usage examples

```text
> Audit web1 for security problems. I'm on my laptop; the server is web1.example.com.
  (Claude copies audit_server.py over scp, asks you to run it with sudo, then explains
   the findings and proposes fixes one by one, starting with key-only SSH.)

> nginx -T output is in nginx-dump.txt. Why is HSTS missing on /api?
  (Runs nginx_lint.py, finds the add_header inheritance trap, proposes a snippet include.)

> Our checkout is throwing 502s since 09:10. Logs are in /var/log/nginx.
  (incident-triage snapshot -> log-investigation summary of the last 30 minutes ->
   correlates "upstream prematurely closed" with OOM kills of php-fpm.)
```

Run the scripts directly too:

```bash
python3 skills/ssl-tls-check/scripts/tls_check.py example.com www.example.com --warn-days 30
python3 skills/log-investigation/scripts/access_log_summary.py /var/log/nginx/access.log* --last 1h
python3 skills/cron-systemd-timers/scripts/cron_to_timer.py --line '*/15 * * * * /usr/local/bin/sync'
python3 skills/docker-compose-review/scripts/compose_review.py compose.json --json
```

## How skills work

At startup the agent loads only each skill's `name` and `description` (about 100 tokens
each). When your request matches a description, it reads that skill's `SKILL.md`: the
workflow, decision points and safety rules. Longer material lives in `references/` and is
read only when a step needs it. Scripts run as normal commands, so their output is exact
and repeatable.

```
skills/<skill-name>/
  SKILL.md          frontmatter (name, description) + workflow
  scripts/          standalone Python 3 helpers (stdlib only, --help, --json)
  references/       checklists, runbooks, known-good configs
```

## FAQ

**Will the agent change my server on its own?**
The skills instruct it not to. Scripts are read-only (the two generators only write files to
a local output directory). Each SKILL.md requires showing commands and waiting for your
confirmation before anything that edits, restarts, deletes or installs. Your agent's own
permission settings still apply on top.

**Does anything leave my machine?**
No script sends telemetry. Only `ssl-tls-check` connects to the network, and only to hosts
you name.

**Do I need Python on the server?**
Ubuntu and Debian ship Python 3. Every script is a single file that runs with the system
`python3`. Several also work offline on captured command output (`--from-dir`, `--dump`,
`--root`), so nothing needs to run on the server.

**Other distributions?**
The checks target Ubuntu/Debian paths and tools. Most of nginx, compose, TLS, logs and cron
are distribution independent; RHEL-family support for the hardening audit is on the roadmap.

**Why are some findings "low"?**
Severity reflects risk on an internet-facing server: high = remote compromise or data
exposure, medium = likely problem, low = hardening or reliability, info = context.

## Roadmap

Ideas for new skills (great first contributions; see [CONTRIBUTING.md](CONTRIBUTING.md)):

- `postgres-health-check` - connections, bloat, long transactions, replication lag (read-only SQL)
- `mysql-tuning-review` - parse `SHOW VARIABLES` / `SHOW STATUS` for buffer pool, connections, slow log
- `logrotate-review` - find logs without rotation and validate `/etc/logrotate.d/*`
- `ufw-rules-review` - parse `ufw status numbered` and spot shadowed or overly broad rules
- `dns-records-check` - A/AAAA/MX/SPF/DMARC/CAA sanity for a domain
- `systemd-service-hardening` - score units with `systemd-analyze security` and suggest sandboxing
- `server-hardening-audit` for RHEL/Rocky/Alma (firewalld, dnf-automatic, SELinux)

## Contributing

Contributions are welcome: new checks, new skills, fixes and better docs. Read
[CONTRIBUTING.md](CONTRIBUTING.md) for the skill template, the quality bar and
how to run the validator and tests:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python tools/validate_skills.py && ruff check . && ruff format --check . && pytest
```

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md) and report security issues
privately as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) (c) Saizul Amin
