# Known-good reverse proxy site (Ubuntu 24.04, nginx 1.24, certbot)

`/etc/nginx/snippets/security-headers.conf`:

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
```

In `/etc/nginx/nginx.conf` inside `http {}`: `server_tokens off;`

`/etc/nginx/sites-available/app.example.com`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name app.example.com;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl http2;          # nginx >= 1.25.1: "listen 443 ssl;" plus "http2 on;"
    listen [::]:443 ssl http2;
    server_name app.example.com;

    ssl_certificate     /etc/letsencrypt/live/app.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    include snippets/security-headers.conf;
    client_max_body_size 20m;

    location ~ /\.(?!well-known) { deny all; }

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location /static/ {
        alias /srv/app/static/;
        expires 7d;
        include snippets/security-headers.conf;   # add_header here would otherwise drop them
        add_header Cache-Control "public";
    }
}
```

Enable and test:

```bash
sudo ln -s /etc/nginx/sites-available/app.example.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
curl -sI https://app.example.com | grep -iE 'strict-transport|x-content|x-frame|server:'
```

Ubuntu 24.04 ships nginx 1.24, where `http2` is a `listen` parameter. From 1.25.1 the
separate `http2 on;` directive replaces it (the old form still works with a warning).

For apps without WebSockets you can drop the `Upgrade`/`Connection` lines.
