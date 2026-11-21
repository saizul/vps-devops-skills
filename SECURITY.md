# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | yes |

## What counts as a vulnerability

These skills are run by AI agents on real servers, so unsafe guidance is treated like a
code vulnerability:

- A script that changes system state (writes outside its `--out` directory, restarts,
  deletes, installs) or runs a command not documented as read-only
- A script that prints a secret value (compose environment secrets, private keys, passwords)
- A `SKILL.md` workflow that tells the agent to run a destructive or lockout-prone command
  (SSH, firewall, `rm`, database) without asking the user first
- Command injection: crafted input files (nginx configs, compose files, crontabs, logs)
  that make a script execute something
- Generated files (systemd units, backup scripts) that are insecure by default, e.g.
  world-readable credentials

A check that misses a misconfiguration (false negative) is a normal bug: please open an issue.

## Reporting

Please **do not** open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/saizul/vps-devops-skills/security/advisories/new)
with the affected skill, version, impact and steps to reproduce. Redact hostnames, IPs and
any secrets from examples.

You will get an answer within 5 working days. Fixes are released as soon as possible and
credited in the changelog unless you prefer otherwise.
