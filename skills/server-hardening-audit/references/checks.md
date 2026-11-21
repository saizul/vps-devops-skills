# What each check means

| Check id | Severity | Why it matters | Common false positive |
| --- | --- | --- | --- |
| `ssh.permitrootlogin` | high (`yes`), low (`prohibit-password`) | Root is the first account bots try. A named sudo user leaves an audit trail. | Automation that needs root over SSH with a key: `prohibit-password` is acceptable. |
| `ssh.passwordauthentication` | high | Passwords can be brute-forced; keys cannot in practice. | None on internet-facing servers. |
| `ssh.kbdinteractive` | medium | With `UsePAM yes`, keyboard-interactive can still accept passwords. | 2FA via PAM (Google Authenticator) needs it; then it is intentional. |
| `ssh.permitemptypasswords` | high | Accounts without a password can log in. | None. |
| `ssh.pubkey` | medium | Key login disabled means passwords are the only option. | None. |
| `ssh.x11forwarding` | low | Extra attack surface servers do not need. | Desktop-in-the-cloud setups. |
| `ssh.maxauthtries` | low | More attempts per connection = faster guessing. | None. |
| `ssh.allowlist` | info | `AllowUsers`/`AllowGroups` limit who can log in at all. | Optional. |
| `firewall.ufw` | high | No host firewall: every listening service is reachable. | A cloud firewall (Hetzner, AWS SG, DO) may already filter. Ask. |
| `firewall.default` | high | Default allow makes UFW pointless. | None. |
| `firewall.rule` | high | Database/cache port allowed from `Anywhere`. | Replication between servers: restrict to their IPs instead. |
| `firewall.ssh_rate_limit` | low | No `ufw limit` and no fail2ban. | None. |
| `fail2ban`, `fail2ban.sshd` | medium | Bans IPs after repeated failures; cuts log noise and risk. | Key-only SSH makes this less critical but still useful. |
| `updates` | medium/low | Security patches are not applied automatically. | Managed patching (Landscape, Ansible) - ask. |
| `updates.reboot` | low | Kernel/libc updates are installed but not running. | None. |
| `accounts.uid0` | high | A second UID 0 account is a classic backdoor. | Rare legacy setups (`toor`). Investigate anyway. |
| `accounts.sudo` | info | List of admins to confirm. | - |
| `ports.risky` | high | Database, cache or admin API on a public interface. | Bound publicly but blocked by a cloud firewall: still fix the bind address. |
| `ports.public` | info | Everything reachable from outside. | - |

## Ubuntu 24.04 notes

- The SSH service is `ssh` (not `sshd`): `sudo systemctl reload ssh`.
- 24.04 uses **socket activation** for SSH (`ssh.socket`). Changing `Port` in sshd_config is
  not enough: also run `sudo systemctl daemon-reload && sudo systemctl restart ssh.socket`.
  Keep the old port open in UFW until the new one is confirmed working.
- Cloud images ship `/etc/ssh/sshd_config.d/50-cloud-init.conf`, often with
  `PasswordAuthentication yes`. sshd keeps the **first** value it reads and includes files in
  lexical order, so put overrides in `00-hardening.conf`.
- `sshd -T` shows the effective config after all includes; trust it over reading files.

## Things the script does not check

- Kernel sysctl hardening, AppArmor profiles, auditd
- SSH keys in `~/.ssh/authorized_keys` (review them manually: `sudo cat /home/*/.ssh/authorized_keys`)
- Cloud provider firewalls and security groups
- Running container images or application dependencies
