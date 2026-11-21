---
name: docker-compose-review
description: Review a docker-compose.yml / compose.yaml for production readiness and security on a single VPS - unpinned image tags, missing healthchecks, restart policies, memory limits and log rotation, ports published on 0.0.0.0 (which bypass UFW), literal secrets in environment, docker.sock and host-root mounts, privileged mode, dangerous capabilities and host networking. Use when the user shares a compose file, asks to review, harden or "productionize" a Docker Compose stack, deploys containers to a VPS, or asks why a database port is reachable despite the firewall.
license: MIT
compatibility: Python 3.8+. JSON input needs only the standard library; YAML input needs PyYAML (apt install python3-yaml) or convert with `docker compose config --format json`.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# Docker Compose review

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- The user shares `docker-compose.yml`, `compose.yaml` or `compose.override.yml`
- "Is this compose file production ready?", "harden my Docker stack", "review before deploy"
- "My Postgres/Redis is reachable from the internet even though UFW blocks it"
- Containers fill the disk with logs, restart loops, or OOM-kill the host

## When not to use

- Kubernetes manifests or Helm charts (different model; use kube-linter / kubeconform)
- Dockerfile review only (use hadolint), image CVE scanning (use Trivy or Docker Scout)
- Docker Swarm-specific deploy settings beyond `deploy.resources` and `restart_policy`

## Workflow

1. **Get the resolved config when possible.** In the project directory:
   `docker compose config --format json > compose.json`. This merges overrides and `.env`
   interpolation, and needs no PyYAML. Warning: it inlines resolved secret values, so
   never paste `compose.json` into the chat; only run the script on it. If Docker is not
   available, run the script directly on the YAML file.
2. **Run the review (read-only).**
   ```bash
   python3 scripts/compose_review.py compose.json
   python3 scripts/compose_review.py docker-compose.yml            # needs PyYAML
   python3 scripts/compose_review.py compose.json --json --fail-on high
   ```
   Exit code 1 = findings at or above `--fail-on` (default `medium`), 2 = unreadable file.
   Secret values are never printed, only variable names.
3. **Add context the script cannot see.** Ask or check:
   - Is there a reverse proxy (nginx, Caddy, Traefik) on the host or in the stack? Then app
     ports should be `127.0.0.1:PORT:PORT` or not published at all.
   - Does the image define `HEALTHCHECK` already (`docker inspect --format '{{json .Config.Healthcheck}}' IMAGE`)?
   - Are volumes backed up? Named volumes live in `/var/lib/docker/volumes/` (see `backup-setup`).
   - Is `/etc/docker/daemon.json` already setting log rotation (`log-opts`)?
4. **Explain the UFW bypass** whenever a port is published on all interfaces: Docker writes
   its own iptables rules ahead of UFW, so `ufw deny 5432` does not block a published
   container port. Fix it in compose (bind to `127.0.0.1`), not in UFW.
5. **Propose a corrected compose file** using
   [references/hardened-compose.md](references/hardened-compose.md). Keep the user's
   service names, volumes and networks. Move literal secrets to `${VAR}` with an untracked
   `.env` (chmod 600) or Compose `secrets:`.
6. **Validate** with `docker compose config -q` and deploy with `docker compose up -d` only
   when the user confirms. Check `docker compose ps` shows `healthy`.

## Output

```
compose review: 8 high, 4 medium, 16 low (4 services)
HIGH  db: PostgreSQL 5432 published on all interfaces (Docker bypasses UFW)
      fix: "127.0.0.1:5432:5432" or remove ports and use the internal network
HIGH  db: POSTGRES_PASSWORD is a literal value in the file -> ${POSTGRES_PASSWORD} + .env
...
Proposed compose.yaml diff: ...
```

Group repeated low findings ("4 services have no memory limit") instead of listing each.

## Severity guide

| Severity | Meaning |
| --- | --- |
| high | Remote compromise or data exposure: public DB port, docker.sock, privileged, literal secrets |
| medium | Unpinned images, host networking, public app ports without a proxy |
| low | Reliability: no healthcheck, restart policy, memory limit or log rotation |
| info | Style: obsolete `version:` key |
