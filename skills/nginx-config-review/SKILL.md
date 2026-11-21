---
name: nginx-config-review
description: Lint and review nginx configuration for security and correctness - server_tokens, missing HSTS and security headers, the add_header inheritance trap, legacy TLS protocols and weak ciphers, open proxies, alias path traversal, autoindex, exposed .git/.env files and missing HTTP to HTTPS redirects - using a parser that follows includes or reads `nginx -T` output. Use when the user shares an nginx.conf or site file, asks to review, harden or debug an nginx config, set up a reverse proxy, fix security headers, or before reloading nginx in production.
license: MIT
compatibility: Python 3.8+. Works offline on config files or `nginx -T` output; nginx itself is only needed for the final `nginx -t` syntax check.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# nginx config review

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- The user pastes or points to `nginx.conf`, `sites-available/*`, `conf.d/*.conf` or snippets
- "Review my nginx config", "why are my security headers missing on /api?", "harden nginx"
- Setting up a reverse proxy for Node, Python, PHP-FPM or a Docker container
- Before `systemctl reload nginx` on a production server

## When not to use

- Apache, Caddy, Traefik or HAProxy configs (different syntax; review by hand)
- Performance tuning only (worker counts, buffers): the linter does not score performance
- Live certificate checks: use `ssl-tls-check`
- Reading access logs: use `log-investigation`

## Workflow

1. **Get the full effective config.** Best source, on the server:
   `sudo nginx -T > nginx-dump.txt` (all included files, one dump). If you only have
   files, keep the directory layout so `include` lines resolve.
2. **Run the linter (read-only).**
   ```bash
   python3 scripts/nginx_lint.py --dump nginx-dump.txt          # nginx -T output
   python3 scripts/nginx_lint.py /etc/nginx/nginx.conf           # follows includes on disk
   python3 scripts/nginx_lint.py site.conf --prefix /etc/nginx   # single file, includes relative to prefix
   python3 scripts/nginx_lint.py --dump nginx-dump.txt --json    # machine-readable
   ```
   Exit code 1 = findings at or above `--fail-on` (default `medium`), 2 = parse error.
   A parse error usually means a missing `;` or `}`: report the file and line.
3. **Review what the linter cannot know.** Read the config yourself for:
   - `proxy_set_header` for `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`; `proxy_http_version 1.1`
     plus `Upgrade`/`Connection` headers for WebSockets
   - `client_max_body_size` suited to uploads; timeouts for slow upstreams
   - PHP: `fastcgi_pass` only for files that exist (`try_files $uri =404;`), never pass
     uploaded files to PHP
   - `default_server` catch-all that returns 444 for unknown Host headers
   - Rate limiting (`limit_req`) on login and API endpoints
   See [references/checklist.md](references/checklist.md) for the full list and fixes.
4. **Explain each finding** in plain words with the corrected snippet. The most common
   surprise: `add_header` in a `location` drops **all** headers inherited from `server`/`http`.
   Recommend a shared snippet (`include snippets/security-headers.conf;`) repeated where needed.
5. **Propose a patch**, then tell the user to validate before reload:
   `sudo nginx -t && sudo systemctl reload nginx`. Never reload or restart nginx yourself
   without confirmation. Use [references/secure-defaults.md](references/secure-defaults.md)
   for a known-good server block.

## Output

```
nginx review: 3 high, 4 medium, 9 low
HIGH  sites-enabled/example.conf:14  alias traversal: location /static + alias /var/www/static/
      fix: location /static/ { alias /var/www/static/; }
...
Not checked automatically: rate limiting, upload size, upstream timeouts (reviewed manually: ...)
Patch: (diff)
Validate with: sudo nginx -t && sudo systemctl reload nginx
```

## Notes

- The linter treats a file without an `http {}` block (a site file) as if it were inside one.
- Findings on headers are per `server`; HSTS is only expected on TLS servers.
- `ssl_protocols` default in nginx >= 1.23.4 is `TLSv1.2 TLSv1.3`, so a missing directive is fine.
