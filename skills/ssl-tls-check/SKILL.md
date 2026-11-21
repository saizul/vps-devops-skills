---
name: ssl-tls-check
description: Read-only TLS/SSL certificate and endpoint check - days until expiry, hostname/SAN match, chain verification, incomplete chains, negotiated TLS version and cipher, and whether legacy TLS 1.0/1.1 is still accepted - for live hosts (Python ssl) or local PEM files (openssl CLI), plus Let's Encrypt/certbot renewal troubleshooting. Use when the user asks if a certificate is expired or about to expire, sees browser certificate warnings or "certificate verify failed", sets up or debugs HTTPS, certbot renewals, or wants to audit TLS settings of a domain or mail server.
license: MIT
compatibility: Python 3.8+. File mode needs the openssl command. Endpoint mode connects only to hosts the user names.
metadata:
  author: Saizul Amin
  version: "0.1.0"
---

# SSL/TLS check

Paths below are relative to this skill's folder (in Claude Code: `${CLAUDE_SKILL_DIR}`).

## When to use

- "Is my certificate expiring?", "check SSL for example.com", "NET::ERR_CERT_..." warnings
- `certificate verify failed`, `unable to get local issuer certificate` from curl/clients
- After setting up HTTPS or changing nginx TLS settings
- certbot renewal failures, or a monitoring alert about expiry
- Checking several domains at once (one command, many targets)

## When not to use

- Full TLS scoring (cipher ordering, every protocol/cipher combination): use testssl.sh or
  SSL Labs; this skill covers the checks that cause real outages
- Client certificate / mTLS setups and private PKI design
- Reviewing nginx TLS directives in config files: use `nginx-config-review`

## Workflow

1. **Ask which hosts** to check. Endpoint mode makes a network connection to each host;
   only connect to hosts the user named.
2. **Check endpoints.**
   ```bash
   python3 scripts/tls_check.py example.com www.example.com api.example.com:8443
   python3 scripts/tls_check.py example.com --check-legacy          # also try TLS 1.0/1.1
   python3 scripts/tls_check.py 203.0.113.5 --servername example.com  # by IP, before DNS cutover
   python3 scripts/tls_check.py mail.example.com:465 --json
   ```
   Defaults: medium below `--warn-days 21`, high below `--crit-days 7` or on any
   verification failure. Exit 1 when any finding is at or above `--fail-on` (default medium).
   `--check-legacy` may report nothing if the local OpenSSL refuses old protocols itself.
3. **Check files on the server** (no network):
   ```bash
   sudo python3 scripts/tls_check.py --cert-file /etc/letsencrypt/live/example.com/fullchain.pem \
       --hostname example.com
   ```
   This lists every certificate in the file (leaf first) and warns when only the leaf is
   present (the most common cause of "works in Chrome, fails in curl/Android").
4. **Diagnose** with [references/troubleshooting.md](references/troubleshooting.md):
   expired but renewed (nginx not reloaded), wrong certificate served (SNI/default_server),
   missing chain, renewal failing (port 80 blocked, DNS, rate limits).
5. **Propose the fix** and the verification command. Renewals and reloads
   (`sudo certbot renew`, `sudo systemctl reload nginx`) run only after the user confirms.

## Output

```
example.com:443      OK    expires 2027-02-14 (85 days), TLSv1.3, SANs example.com, www.example.com
api.example.com:8443 FAIL  certificate verification failed: unable to get local issuer certificate
                           fix: ssl_certificate must point to fullchain.pem
old.example.com:443  WARN  expires in 12 days; certbot timer inactive
```

Then the cause and the exact fix for each failing host.

## Monitoring tip

For a cron or CI check across many domains:
`python3 scripts/tls_check.py $(cat domains.txt) --fail-on medium --json > tls.json`
(exit code 1 triggers the alert).
