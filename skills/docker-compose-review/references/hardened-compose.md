# Hardened compose patterns

## A web app + Postgres + Redis behind a host reverse proxy

```yaml
services:
  app:
    image: ghcr.io/example/app:2.3.1          # pinned tag (or @sha256 digest)
    restart: unless-stopped
    env_file: .env                             # chmod 600, in .gitignore
    environment:
      DATABASE_URL: postgres://app:${POSTGRES_PASSWORD}@db:5432/app
      REDIS_URL: redis://redis:6379/0
    ports:
      - "127.0.0.1:3000:3000"                  # only the host proxy can reach it
    depends_on:
      db: {condition: service_healthy}
      redis: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    mem_limit: 512m
    read_only: true
    tmpfs: [/tmp]
    security_opt: ["no-new-privileges:true"]
    cap_drop: [ALL]
    logging: &logging
      driver: json-file
      options: {max-size: "10m", max-file: "3"}

  db:
    image: postgres:16.4
    restart: unless-stopped
    environment:
      POSTGRES_USER: app
      POSTGRES_DB: app
      POSTGRES_PASSWORD_FILE: /run/secrets/pg_password
    secrets: [pg_password]
    volumes:
      - pgdata:/var/lib/postgresql/data
    # no "ports:" - reachable only on the compose network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 10s
      timeout: 5s
      retries: 5
    mem_limit: 1g
    shm_size: 256m
    logging: *logging

  redis:
    image: redis:7.4-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes: [redisdata:/data]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
    mem_limit: 256m
    logging: *logging

volumes:
  pgdata:
  redisdata:

secrets:
  pg_password:
    file: ./secrets/pg_password.txt            # chmod 600, in .gitignore
```

`cap_drop: [ALL]` and `read_only: true` break some images; add back only what fails
(e.g. `cap_add: [NET_BIND_SERVICE]`, extra `tmpfs` paths).

## Log rotation for every container at once

`/etc/docker/daemon.json`:

```json
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
```

`sudo systemctl restart docker` applies it to **new** containers only; recreate them with
`docker compose up -d --force-recreate`.

## Need a container to manage Docker?

Do not mount `/var/run/docker.sock` directly (read-only does not limit the API).
Use a socket proxy that allows only the endpoints needed:

```yaml
  socket-proxy:
    image: tecnativa/docker-socket-proxy:VERSION   # pin a released tag
    environment: {CONTAINERS: 1, POST: 0}
    volumes: ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    networks: [socket]
```

The review script still reports the socket mount on the proxy service itself; that one
finding is expected. Other services talk to `tcp://socket-proxy:2375` on the private
`socket` network and never get the socket.

## Updating pinned images

Pinned tags do not update themselves. Check for new versions monthly or use a tool such
as Renovate on the repository; then `docker compose pull && docker compose up -d`.
