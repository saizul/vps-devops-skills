# Safe fix order

Apply fixes in this order. Each step is reversible and each one is confirmed with the
user first. Keep one SSH session open the whole time as a lifeline.

## 1. Make sure key login works

```bash
# On the laptop
ssh-copy-id -i ~/.ssh/id_ed25519.pub deploy@HOST
ssh -o PreferredAuthentications=publickey -o PasswordAuthentication=no deploy@HOST 'echo key-ok'
```

Do not continue until `key-ok` prints.

## 2. Create a named sudo user (if only root exists)

```bash
sudo adduser deploy
sudo usermod -aG sudo deploy
sudo rsync -a --chown=deploy:deploy /root/.ssh/ /home/deploy/.ssh/
```

Test `ssh deploy@HOST` and `sudo -v` in a new terminal.

## 3. SSH daemon

```bash
sudo tee /etc/ssh/sshd_config.d/00-hardening.conf >/dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
X11Forwarding no
MaxAuthTries 3
EOF
sudo sshd -t && sudo systemctl reload ssh
sudo sshd -T | grep -Ei 'permitrootlogin|passwordauthentication|kbdinteractive'
```

Open a **new** session to confirm before closing the old one.
Rollback: `sudo rm /etc/ssh/sshd_config.d/00-hardening.conf && sudo systemctl reload ssh`.

## 4. Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw limit OpenSSH          # or: sudo ufw limit 2222/tcp for a custom port
sudo ufw allow 80,443/tcp       # only if this is a web server
sudo ufw enable
sudo ufw status verbose
```

Remove a dangerous rule: `sudo ufw status numbered`, then `sudo ufw delete N`.
Rollback: `sudo ufw disable`.

## 5. Bind databases to localhost

- MySQL/MariaDB: `bind-address = 127.0.0.1` in `/etc/mysql/mysql.conf.d/mysqld.cnf`
  (MariaDB: `/etc/mysql/mariadb.conf.d/50-server.cnf`), then `sudo systemctl restart mysql`.
- PostgreSQL: `listen_addresses = 'localhost'` in `/etc/postgresql/*/main/postgresql.conf`.
- Redis: `bind 127.0.0.1 -::1` and `protected-mode yes` in `/etc/redis/redis.conf`.
- Docker: change `"6379:6379"` to `"127.0.0.1:6379:6379"` in compose and `docker compose up -d`.

Confirm with `sudo ss -tlnp | grep -E '3306|5432|6379'`.

## 6. fail2ban

```bash
sudo apt install fail2ban
printf '[sshd]\nenabled = true\nmaxretry = 5\nbantime = 1h\n' | sudo tee /etc/fail2ban/jail.d/sshd.local
sudo systemctl enable --now fail2ban
sudo fail2ban-client status sshd
```

## 7. Automatic security updates

```bash
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades     # answer Yes
cat /etc/apt/apt.conf.d/20auto-upgrades
sudo unattended-upgrade --dry-run --debug | tail -20
```

Optional automatic reboot at a quiet hour, in `/etc/apt/apt.conf.d/52unattended-upgrades-local`:

```
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
```
