# AGENTS.md

Guidance for Codex CLI and other coding agents that use or work on this repository.

## Using the skills

The skills live in `skills/<name>/SKILL.md` and follow the Agent Skills format. Codex
discovers them when they are copied to `~/.agents/skills/` or `.agents/skills/` (see
README "Install"). If you are reading this file inside the repository instead:

1. Pick the skill whose `description` matches the user's request:

   | Request is about | Skill |
   | --- | --- |
   | securing / auditing a server, SSH, firewall, open ports | `server-hardening-audit` |
   | nginx config files or `nginx -T` output | `nginx-config-review` |
   | docker-compose.yml / compose.yaml | `docker-compose-review` |
   | backups, restic, rclone, restore tests | `backup-setup` |
   | server slow/down, disk full, OOM, 502/5xx | `incident-triage` |
   | access logs, journalctl, who is hitting the server | `log-investigation` |
   | certificate expiry, HTTPS errors, certbot | `ssl-tls-check` |
   | cron jobs, systemd timers, OnCalendar | `cron-systemd-timers` |
   | php-fpm pm.max_children, pool memory, 502 under load | `php-fpm-pool-sizing` |

2. Read that `SKILL.md` completely and follow its Workflow section in order.
3. Paths such as `scripts/x.py` and `references/y.md` are relative to the skill folder.
   `${CLAUDE_SKILL_DIR}` in the text means that folder; Codex does not expand it.
4. Read files in `references/` only when a workflow step points to them.

## Safety rules (apply to every skill)

- Run only the read-only commands and scripts a workflow lists without asking.
- Before any command that edits files on a server, restarts or stops services, deletes
  data, installs packages or changes firewall/SSH settings: show the exact command, explain
  the impact, and wait for the user's explicit confirmation.
- Never connect to a host the user did not name. Never type passwords or sudo prompts for
  the user.
- Never print or paste secrets (private keys, `.env` values, tokens, `/etc/shadow`). Scripts
  hide secret values; keep it that way when summarising.
- For SSH and firewall changes, keep an existing session open and test a new one before
  closing it.

## Working on this repository

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python tools/validate_skills.py   # frontmatter, sections, links, marketplace listing
ruff check . && ruff format --check .
pytest
```

- Scripts: Python 3.8+ standard library only, `#!/usr/bin/env python3`, `argparse` with
  `--help`, `--json` where output is structured, exit 0/1/2 (ok / findings / error).
- Every script needs tests in `tests/test_<skill_with_underscores>.py` with fixtures in
  `tests/fixtures/<skill>/`. Use documentation IPs (192.0.2.0/24, 198.51.100.0/24,
  203.0.113.0/24) and `example.com`; never commit keys or real secrets.
- Keep each `SKILL.md` under 400 lines; move detail to `references/`.
- A new skill must be added to `.claude-plugin/marketplace.json` (`plugins[0].skills`), the
  README table, the table above and `CHANGELOG.md`. The validator enforces the manifest.
