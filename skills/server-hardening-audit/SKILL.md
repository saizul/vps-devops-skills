---
name: server-hardening-audit
description: Read-only security audit of an Ubuntu/Debian VPS - SSH daemon settings (sshd -T), UFW firewall, fail2ban, unattended-upgrades, sudo and UID 0 accounts, and services listening on public interfaces - with a prioritised list of findings and exact fix commands. Use when the user asks to audit, harden, secure or review a Linux server, VPS or droplet, asks "is my server secure", sets up a new Ubuntu 24.04 box, or worries about SSH brute force or open ports. Changes nothing without explicit confirmation.
license: MIT
compatibility: Linux server with Python 3.8+ (Ubuntu 22.04/24.04, Debian 12). Needs sudo on the server for sshd -T, ufw and fail2ban status. Offline mode works anywhere.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# Server hardening audit

Audit first, change later. This skill produces a report; every fix is a separate,
confirmed step because a wrong SSH or firewall change can lock the user out.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "Audit / harden / secure my server", "is my VPS secure?", "check my droplet"
- After provisioning a new Ubuntu 24.04 server, before putting it in production
- Seeing SSH brute-force noise in `auth.log`, or unsure which ports are open
- Periodic review (monthly) of an existing server

## When not to use

- Compliance frameworks (CIS benchmark scoring, PCI): suggest Lynis or OpenSCAP instead
- Container images or Kubernetes nodes: use image scanners (Trivy) and the cluster's own tooling
- Web app vulnerabilities (XSS, SQLi): out of scope; this is the OS/network layer
- nginx or docker-compose specifics: use `nginx-config-review` / `docker-compose-review`

## Workflow

1. **Find out where the agent runs.** If you are on the server itself, use live mode.
   If you are on the user's laptop, do not SSH anywhere until the user names the host
   and agrees. Then choose one:
   - Copy and run: `scp scripts/audit_server.py HOST:/tmp/` and ask the user to run
     `ssh -t HOST 'sudo python3 /tmp/audit_server.py'` (sudo may prompt for a password, which
     the user types, never you).
   - Capture and audit offline: print the capture commands with
     `python3 scripts/audit_server.py --print-collect-commands`, have the user run them on the
     server, copy the folder back, then `python3 scripts/audit_server.py --from-dir ./audit`.
2. **Run the audit (read-only).**
   ```bash
   sudo python3 scripts/audit_server.py            # text report
   sudo python3 scripts/audit_server.py --json     # for further processing
   ```
   Exit code 1 means findings at or above `--fail-on` (default `medium`).
3. **Interpret.** Read [references/checks.md](references/checks.md) for what each check means,
   false positives (e.g. a database intentionally public behind a cloud firewall) and why it matters.
   Ask about context you cannot see: cloud provider firewalls, VPN-only access, bastion hosts.
4. **Present the report** (see Output). Group by severity; lead with anything that allows
   remote login without a key or exposes a database.
5. **Offer fixes one at a time and wait for a yes before each.** Follow
   [references/safe-fix-order.md](references/safe-fix-order.md). Non-negotiable rules:
   - Before disabling password login, confirm key login works **in a second, still-open session**.
   - Put sshd overrides in `/etc/ssh/sshd_config.d/00-hardening.conf`. sshd uses the **first**
     value it reads, so a `99-` file loses to cloud-init's `50-cloud-init.conf`.
   - Always `sudo sshd -t` before `sudo systemctl reload ssh`.
   - Run `sudo ufw allow OpenSSH` (or the custom SSH port) **before** `sudo ufw enable`.
   - Docker-published ports bypass UFW; fix those in the compose file, not with ufw rules.
6. **Re-run the audit** after changes and show the before/after summary line.

## Output

Return a short report:

```
Server: web1 (Ubuntu 24.04)  -  7 high, 3 medium, 4 low
HIGH
  1. SSH password login enabled -> PasswordAuthentication no (after testing key login)
  2. MySQL 3306 listening on 0.0.0.0 and allowed in UFW -> bind-address = 127.0.0.1
MEDIUM ...
LOW / INFO ... (one line each)
Proposed next step: fix #1. Shall I prepare the exact commands?
```

Never claim the server is "secure"; say which checks passed and what was not checked
(e.g. "sshd -T needed sudo and was skipped").

## Safety

- The script only reads files and runs `sshd -T`, `ufw status verbose`, `fail2ban-client status`
  and `ss -tulpnH`. It never edits, restarts or installs anything.
- Never print or copy `/etc/shadow`, private keys or tokens into the conversation.
- Never run fix commands on a server without the user's explicit confirmation for that step.
