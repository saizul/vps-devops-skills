# Restore drill checklist

Run quarterly and after any change to the backup setup. Restore to a scratch path,
never over live data. Record results in the table at the end.

Load the environment first (as root):

```bash
sudo -i
set -a; . /etc/restic/restic-system.env; set +a
```

## 1. Snapshots exist and are recent

```bash
restic snapshots --latest 3
restic snapshots --json --latest 1 | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["time"])'
```

- [ ] Newest snapshot is younger than the schedule interval (e.g. < 26 hours for nightly)

## 2. Repository integrity

```bash
restic check --read-data-subset=10%
```

- [ ] `no errors were found`

## 3. Restore a single file

```bash
restic find /etc/nginx/nginx.conf
restic restore latest --target /tmp/restore-test --include /etc/nginx/nginx.conf
diff /etc/nginx/nginx.conf /tmp/restore-test/etc/nginx/nginx.conf && echo identical
```

- [ ] File restored and identical (or differs only by expected recent edits)

## 4. Restore a directory and time it

```bash
time restic restore latest --target /tmp/restore-test --include /var/www
du -sh /tmp/restore-test/var/www /var/www
```

- [ ] Size roughly matches; note the duration for the RTO estimate

## 5. Restore and load the database dump into a scratch database

MySQL/MariaDB:

```bash
restic restore latest --target /tmp/restore-test --include /var/backups/db/mysql-all.sql.gz
gunzip -t /tmp/restore-test/var/backups/db/mysql-all.sql.gz && echo gzip-ok
# Full test: load into a throwaway container, never the live server
docker run -d --name restore-mysql -e MYSQL_ALLOW_EMPTY_PASSWORD=yes mysql:8.4
sleep 30
gunzip -c /tmp/restore-test/var/backups/db/mysql-all.sql.gz | docker exec -i restore-mysql mysql
docker exec restore-mysql mysql -e 'SHOW DATABASES;'
docker rm -f restore-mysql
```

PostgreSQL:

```bash
restic restore latest --target /tmp/restore-test --include /var/backups/db/postgres-all.sql.gz
docker run -d --name restore-pg -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16
sleep 15
gunzip -c /tmp/restore-test/var/backups/db/postgres-all.sql.gz | docker exec -i restore-pg psql -U postgres
docker exec restore-pg psql -U postgres -c '\l'
docker rm -f restore-pg
```

- [ ] Dump loads without errors; expected databases and a recent row are present

## 6. Disaster scenario (yearly)

Pretend the server is gone. On a fresh VPS, with only the password manager entry:

- [ ] Install restic, recreate the env file from the password manager, `restic snapshots` works
- [ ] Restore `/etc`, app files and database; the site comes up

## 7. Clean up

```bash
rm -rf /tmp/restore-test
```

## Record

| Date | Snapshot age | check | file | dir (time) | DB load | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| | | | | | | |
