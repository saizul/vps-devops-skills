# Contributing to vps-devops-skills

Thank you for helping! New checks, new skills, fixes, test fixtures from real-world
configs (redacted) and clearer docs are all welcome.

- [Ways to contribute](#ways-to-contribute)
- [Good first issues](#good-first-issues)
- [Development setup](#development-setup)
- [Repository layout](#repository-layout)
- [Write a new skill](#write-a-new-skill)
- [Quality bar](#quality-bar)
- [Pull request process](#pull-request-process)

## Ways to contribute

| Size | Idea |
| --- | --- |
| 10 minutes | Fix a typo, clarify a workflow step, add a FAQ entry |
| 30 minutes | Add a missing test case or a fixture that triggers a false positive |
| 1-2 hours | Add a check to an existing script (see below) |
| Half a day | Write a new skill from the template |

Comment on an issue before you start so nobody else picks the same one.

## Good first issues

Each of these is self-contained and touches one script and its test file.

1. **nginx-config-review: `client_max_body_size` hint.** Report `info` when a server with
   `proxy_pass` or `fastcgi_pass` has no `client_max_body_size` (default 1m causes 413 on uploads).
2. **nginx-config-review: `ssl_session_tickets`.** Report `low` when TLS servers do not set
   `ssl_session_tickets off;`. Add fixtures for http-level and server-level settings.
3. **docker-compose-review: `no-new-privileges`.** Report `info` when a service has no
   `security_opt: ["no-new-privileges:true"]`, and `low` for `user: root` / `user: "0"`.
4. **docker-compose-review: `.env` interpolation.** Accept `--env-file` and report variables
   used in the compose file but missing from it (names only, never values).
5. **server-hardening-audit: authorized_keys review.** Add an optional `authorized_keys.txt`
   capture and report keys with `from=`-less root access or weak types (`ssh-rsa` < 3072 bits,
   `ssh-dss`).
6. **server-hardening-audit: sysctl checks.** Parse `sysctl -a` output for
   `net.ipv4.conf.all.accept_redirects`, `kernel.kptr_restrict`, `fs.protected_symlinks`.
7. **log-investigation: JSON log format.** Support nginx `escape=json` log lines (one JSON
   object per line) in addition to the combined format.
8. **log-investigation: per-status-code time series.** Add `--per-minute 5xx` that prints a
   count per minute for a status class, for quick spike graphs.
9. **cron-systemd-timers: `--check` mode.** Compare an existing `.timer` file's `OnCalendar`
   with a cron line and report whether they match.
10. **ssl-tls-check: STARTTLS.** Add `--starttls smtp|imap` for mail servers on 25/587/143.
11. **incident-triage: PSI.** Read `/proc/pressure/{cpu,memory,io}` and report sustained
    pressure (`avg60 > 10`).
12. **New skill: `logrotate-review`** (see the roadmap in the README).

## Development setup

```bash
git clone https://github.com/saizul/vps-devops-skills.git
cd vps-devops-skills
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt

python tools/validate_skills.py   # SKILL.md frontmatter, sections, links, marketplace listing
ruff check . && ruff format --check .
pytest
```

The ssl-tls-check tests need the `openssl` command (they generate throwaway certificates at
test time and skip when it is missing).

To try your changes in Claude Code, either copy a skill to `~/.claude/skills/` or add your
checkout as a local marketplace:

```bash
claude plugin marketplace add ./vps-devops-skills
claude plugin install vps-devops-skills@vps-devops-skills
```

For Codex, copy the skill folder to `~/.agents/skills/`.

## Repository layout

```
.claude-plugin/
  marketplace.json      marketplace + plugin entry listing every skill
  plugin.json           plugin manifest (name, version, author)
skills/<name>/
  SKILL.md              frontmatter + workflow (the part agents read)
  scripts/*.py          standalone helpers, Python 3.8+ stdlib only
  references/*.md       detail loaded on demand
templates/skill/        copy this to start a new skill
tests/
  test_<skill>.py       one test module per skill
  fixtures/<skill>/     inputs for those tests
tools/validate_skills.py
```

## Write a new skill

1. Open a "New skill proposal" issue first so we can agree on scope.
2. Copy the template:
   ```bash
   cp -r templates/skill skills/my-new-skill
   mv skills/my-new-skill/scripts/your_script.py skills/my-new-skill/scripts/my_check.py
   ```
3. Edit `SKILL.md`:
   - `name` equals the folder name: lowercase letters, digits, single hyphens, max 64 chars.
   - `description` (max 1024 chars) says **what** it does with concrete keywords and **when**
     to use it, starting the second part with "Use when ...". This text decides whether
     the agent picks the skill, so list the phrases users actually type.
   - Required sections: `## When to use`, `## When not to use`, `## Workflow`, `## Output`.
   - Workflow steps are imperative and numbered, with exact commands, decision points and
     the point where the agent must ask before changing anything.
   - Keep it under 400 lines; move tables, runbooks and long configs to `references/`.
4. Write the script: `argparse` with a useful `--help`, `--json` output, exit codes
   0 (ok) / 1 (findings) / 2 (error), read-only by default, no network unless that is the
   skill's purpose (then only to hosts the user passes).
5. Add `tests/test_my_new_skill.py` and fixtures in `tests/fixtures/my-new-skill/`. Cover
   a "bad" input, a "good" input that produces no findings, edge cases and the CLI exit codes.
6. Add the skill to `.claude-plugin/marketplace.json` (`plugins[0].skills`), the README
   skills table, the table in `AGENTS.md` and `CHANGELOG.md` under "Unreleased".
7. Run the validator, ruff and pytest.

## Quality bar

- **Safety first.** A skill must never tell the agent to change a server without showing
  the command and getting confirmation. SSH and firewall changes need a lockout plan.
- **Deterministic where it matters.** If a step is "parse this and find X", write a script
  and test it rather than relying on the model reading a 2,000-line file.
- **Real-world fixtures.** Redact real configs: use `example.com`, documentation IP ranges
  (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) and obviously fake secrets.
- **Low false positives.** If a check is often wrong, make it `info` or drop it, and document
  known false positives in the skill's references.
- **Ubuntu 24.04 first,** Debian 12 and Ubuntu 22.04 second. Note version differences.

## Pull request process

1. Fork, create a branch, make focused commits.
2. Make sure CI passes locally (validator, ruff, pytest).
3. Fill in the pull request template.
4. A maintainer reviews within a few days. Small, focused pull requests are merged fastest.

By contributing you agree that your contributions are licensed under the MIT License.
