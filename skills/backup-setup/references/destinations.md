# Choosing a restic repository

| Destination | Repo string | Env vars | Notes |
| --- | --- | --- | --- |
| AWS S3 / S3-compatible (Wasabi, R2, MinIO, Hetzner Object Storage) | `s3:https://ENDPOINT/BUCKET/PREFIX` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Enable bucket versioning or Object Lock for ransomware protection. Watch egress fees on restore. |
| Backblaze B2 | `b2:BUCKET:PREFIX` | `B2_ACCOUNT_ID`, `B2_ACCOUNT_KEY` | Native backend works; many users prefer B2's S3 endpoint with `s3:`. App keys can omit `deleteFiles`. |
| SFTP (Hetzner Storage Box, another server) | `sftp:user@host:/path` | none (SSH key for root) | Use a dedicated key and, on your own box, `restic rest-server --append-only` for delete protection. |
| restic REST server | `rest:https://user:pass@host:8000/repo` | - | `--append-only` mode prevents a compromised client from deleting snapshots. |
| Google Drive, OneDrive, Dropbox, anything rclone supports | `rclone:REMOTE:path` | rclone config in `/root/.config/rclone/rclone.conf` | Configure with `sudo rclone config`. Slower; fine for small servers. |
| Local disk / mounted volume | `/mnt/backup/restic` | - | Not off-site. Only as a second copy. |

## 3-2-1 on a budget

- **3** copies: live data, restic repo A, restic repo B (or provider snapshots)
- **2** different media/providers: e.g. B2 + Hetzner Storage Box
- **1** off-site: anything not in the same datacenter as the server

A second repository can be fed with `restic copy` from the first.

## Rough sizing

- First backup = size of the selected data after compression (restic compresses since 0.14).
- Daily increments for a typical web server: 1-5% of total.
- Ask the user for `sudo du -sh /etc /var/www /home` and database dump size to estimate.

## restic vs rclone vs others

| Tool | Encryption | Dedup + snapshots | Retention policy | Use it for |
| --- | --- | --- | --- | --- |
| restic | yes (always) | yes | `forget --keep-*` | the backup itself |
| rclone | optional (crypt remote) | no (mirror) | no | transport for restic, or copying finished archives |
| borg | yes | yes | `prune` | good alternative; needs borg on the remote (or BorgBase) |
| provider snapshots | provider | whole disk | provider | fast full-server rollback, not off-site |
