# TLS troubleshooting

## Read-only openssl commands

```bash
# What the server actually sends (chain, SNI)
openssl s_client -connect example.com:443 -servername example.com -showcerts </dev/null 2>/dev/null \
  | grep -E 's:|i:|Verify return code'
# Expiry of the served certificate
echo | openssl s_client -connect example.com:443 -servername example.com 2>/dev/null \
  | openssl x509 -noout -enddate -subject -ext subjectAltName
# Will it still be valid in 14 days? (exit 1 = no)
openssl x509 -in /etc/letsencrypt/live/example.com/fullchain.pem -noout -checkend $((14*86400))
# Does a key match a certificate?
openssl x509 -in cert.pem -noout -pubkey | sha256sum
openssl pkey -in privkey.pem -pubout | sha256sum
# Protocol support
openssl s_client -connect example.com:443 -tls1_1 </dev/null    # should fail
openssl s_client -connect example.com:443 -tls1_3 </dev/null    # should succeed
# STARTTLS services
openssl s_client -connect mail.example.com:587 -starttls smtp -servername mail.example.com </dev/null
```

## Symptoms and causes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Expired, but certbot says it renewed | nginx/Apache never reloaded | `sudo systemctl reload nginx`; add a deploy hook (below) |
| `unable to get local issuer certificate` in curl, fine in Chrome | only the leaf is served | `ssl_certificate .../fullchain.pem` (not `cert.pem`) |
| Wrong certificate / wrong name | no `server_name` match, SNI falls to `default_server` | add the name to the right server block; check `nginx -T` |
| `hostname mismatch` for `www.` | certificate lacks the www SAN | `sudo certbot --nginx -d example.com -d www.example.com` |
| Works by domain, fails by IP | expected: certificates name hosts | use `--servername` when testing by IP |
| Renewal fails `Connection refused/Timeout` | port 80 blocked or redirected badly | `sudo ufw allow 80/tcp`; keep `/.well-known/acme-challenge/` reachable over HTTP |
| Renewal fails `NXDOMAIN` / wrong IP | DNS points elsewhere (Cloudflare proxy, old IP) | fix DNS, or use the DNS-01 challenge plugin |
| `too many certificates already issued` | Let's Encrypt rate limit | wait; test with `--dry-run` (staging) first |
| Clients fail after Let's Encrypt chain changes | outdated client trust store | update CA certificates on the client |

## certbot health on the server

```bash
sudo certbot certificates                       # names, expiry, paths
systemctl list-timers | grep -i certbot         # snap: snap.certbot.renew.timer
sudo certbot renew --dry-run                    # safe: uses staging, changes nothing
ls /etc/letsencrypt/renewal-hooks/deploy/
```

Reload nginx automatically after each renewal (confirm before creating):

```bash
printf '#!/bin/sh\nsystemctl reload nginx\n' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
```

## Expiry thresholds

Let's Encrypt certificates last 90 days (shorter lifetimes are being phased in) and
certbot renews when about 30 days or a third of the lifetime remain. A certificate with
less than 21 days left therefore means renewal is already failing: investigate, do not wait.
