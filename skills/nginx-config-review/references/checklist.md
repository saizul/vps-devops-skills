# nginx review checklist

## Checked by `nginx_lint.py`

| Check | Severity | Fix |
| --- | --- | --- |
| `server_tokens` not off | low | `server_tokens off;` in `http {}` |
| `tls.protocols` SSLv3/TLSv1/TLSv1.1 | high | `ssl_protocols TLSv1.2 TLSv1.3;` |
| `tls.ciphers` RC4/DES/3DES/MD5/NULL/EXPORT | medium | Mozilla intermediate list, or drop `ssl_ciphers` to use the default |
| `headers.hsts` missing on TLS server | medium | `add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;` |
| `headers.x-content-type-options` | low | `add_header X-Content-Type-Options nosniff always;` |
| `headers.referrer-policy` | low | `add_header Referrer-Policy "strict-origin-when-cross-origin" always;` |
| `headers.frame` (no XFO / frame-ancestors) | low | `add_header X-Frame-Options "SAMEORIGIN" always;` |
| `headers.always` | low | Without `always`, headers are not sent on 4xx/5xx responses |
| `headers.inheritance` | medium | A `location` with any `add_header` loses every inherited one; repeat them |
| `autoindex` on | medium | `autoindex off;` |
| `hidden_files` | low | `location ~ /\.(?!well-known) { deny all; }` |
| `alias_traversal` | high | `location /x/ { alias /path/x/; }` (both with trailing slash) |
| `open_proxy` | high | Never build the upstream host from `$host`, `$http_*` or `$arg_*` |
| `http_redirect` | low | Port-80 server: `return 301 https://$host$request_uri;` |

## Review manually

- **Reverse proxy headers**
  ```nginx
  proxy_set_header Host $host;
  proxy_set_header X-Real-IP $remote_addr;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
  ```
- **WebSockets**: `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";`
- **Upload size**: `client_max_body_size 20m;` (default 1m returns 413)
- **Timeouts**: `proxy_read_timeout 60s;` raise only for known slow endpoints
- **Catch-all**: reject unknown hosts so the server IP does not serve a real site
  ```nginx
  server { listen 80 default_server; listen 443 ssl default_server; server_name _;
           ssl_reject_handshake on; return 444; }
  ```
- **Rate limiting** for login/API
  ```nginx
  limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;   # http {}
  location = /wp-login.php { limit_req zone=login burst=3 nodelay; ... }
  ```
- **PHP-FPM**: use `include snippets/fastcgi-php.conf;` (it has `try_files $fastcgi_script_name =404;`);
  deny PHP in upload dirs: `location ~* /uploads/.*\.php$ { deny all; }`
- **Real client IP behind Cloudflare/load balancer**: `set_real_ip_from <proxy range>; real_ip_header CF-Connecting-IP;`
  Otherwise rate limits and logs see only the proxy IP.
- **OCSP stapling / session tickets**: `ssl_stapling on; ssl_stapling_verify on;` (needs `resolver`),
  `ssl_session_tickets off;` unless keys are rotated.
- **Logs**: separate `access_log`/`error_log` per site make incident work easier.
