---
name: backup-setup
description: Plan and generate encrypted, off-site VPS backups with restic (S3, Backblaze B2, SFTP, or any rclone remote) - what to back up, consistent MySQL/PostgreSQL dumps, retention, a systemd service and timer, monitoring - and walk through a restore drill so the backup is proven to work. Use when the user asks to set up, automate, schedule or fix server backups, back up a VPS, database, Docker volumes or WordPress site, choose between restic and rclone, or test that backups can be restored.
license: MIT
compatibility: Generator needs Python 3.8+ and writes files only. Target server - Ubuntu/Debian with systemd and the restic package; rclone only for rclone remotes.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# Backup setup (restic + systemd)

A backup that has never been restored is a hope, not a backup. This skill always ends
with a restore drill.

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "Set up backups for my VPS / database / Docker volumes / WordPress"
- Replacing ad-hoc `tar` + cron scripts or provider snapshots with off-site backups
- "restic or rclone?", "how long should I keep backups?", "how do I restore?"
- Verifying existing backups (jump to the restore drill)

## When not to use

- Managed databases (RDS, Cloud SQL): use the provider's point-in-time recovery
- Block-level disaster recovery of the whole disk: use provider snapshots *in addition*
- Large Postgres clusters needing PITR: use pgBackRest or WAL-G instead of nightly dumps

## Workflow

1. **Inventory what matters** (ask; do not guess). Typical list:
   - `/etc` (configs), `/home`, `/root`, `/var/www` or `/srv` (apps), `/opt/<app>`
   - Databases: **dump** them; never copy live database files
   - Docker named volumes: `/var/lib/docker/volumes/<name>/_data` (stop or dump DB containers)
   - Let's Encrypt: `/etc/letsencrypt` (covered by `/etc`)
   Write down RPO (how much data loss is OK: 24h for nightly) and RTO (how fast a restore must be).
2. **Choose the repository** with [references/destinations.md](references/destinations.md).
   Rule of thumb: restic is the backup tool (encryption, dedup, snapshots, retention);
   rclone is only a transport when restic has no native backend for the storage
   (`rclone:remote:path`). Never use `rclone sync` alone as a backup: it mirrors deletions
   and ransomware.
3. **Generate the files** (nothing is installed):
   ```bash
   python3 scripts/make_restic_units.py --repo s3:s3.eu-central-1.amazonaws.com/BUCKET/web1 \
     --path /etc --path /var/www --path /home --mysql --out ./restic-out
   python3 scripts/make_restic_units.py --repo sftp:backup@storage.example:/srv/restic/web1 \
     --postgres --schedule '*-*-* 02:30:00' --dry-run        # print instead of writing
   ```
   Options: `--name`, `--exclude`, `--keep-daily/weekly/monthly`, `--check-subset`,
   `--on-failure UNIT`, `--force`. Review every generated file with the user.
4. **Install on the server** - the script prints the exact steps; the user (or you, after
   explicit confirmation) runs them. Critical points:
   - The restic password file is the only key to the data. Store a copy **off the server**
     (password manager). Lost password = lost backups.
   - Credentials live in `/etc/restic/*.env`, mode 600, owned by root. Never echo them.
   - Use a storage key that can write but not delete if the provider supports it
     (B2 application keys, S3 Object Lock), so a compromised server cannot wipe backups.
5. **First run and monitoring.**
   `sudo systemctl start restic-system.service && journalctl -u restic-system -n 50`,
   then `sudo systemctl enable --now restic-system.timer`. Add failure alerts via
   `--on-failure` or a heartbeat ping (see [references/monitoring.md](references/monitoring.md)).
6. **Restore drill - do not skip.** Follow [references/restore-drill.md](references/restore-drill.md):
   restore one file, one directory and the database dump to a scratch location, and record
   the time it took. Schedule the drill quarterly.

## Output

1. A one-paragraph backup plan: what, where, schedule, retention, RPO/RTO, cost estimate.
2. The generated files (or a diff against existing ones).
3. The install commands for the user to run.
4. The restore drill checklist with results filled in once done.

## Retention defaults

`--keep-daily 7 --keep-weekly 4 --keep-monthly 6` keeps about 17 snapshots spanning six
months. Deduplication makes this cheap: typically 1.2-2x the size of one full backup.
