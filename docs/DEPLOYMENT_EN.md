# Outlook token keeper — deployment

Every runtime mode requires a valid Argon2id admin-key hash, session secret, and 32-byte field-encryption key. There are no usable default secrets. A public endpoint must use trusted HTTPS, while the application remains bound to loopback or a private container network.

## Docker Compose

With Python 3.11+, Docker Engine, and Compose v2 installed:

```text
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/generate_admin_key.py --output ./admin-key.txt
.venv/bin/python scripts/generate_docker_env.py --admin-key-file ./admin-key.txt --output .env
docker compose up -d --build
docker compose ps
```

On Windows PowerShell, use `.\.venv\Scripts\python`. The published port is loopback-only by default. For a public reverse proxy, set:

```text
COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_IPS=private proxy IP
ALLOWED_HOSTS=your.example.invalid,127.0.0.1,localhost
```

Replace the reserved example domain. The proxy must overwrite, not pass through, client-supplied `X-Forwarded-Proto` and `X-Real-IP`. Never expose PostgreSQL or port 8787 directly to the internet.

## Ubuntu 24.04 and systemd

Generate one-time bootstrap material on a trusted machine:

```text
python scripts/generate_admin_key.py --output /safe/path/admin-key.txt
python scripts/build_bootstrap.py \
  --admin-key-file /safe/path/admin-key.txt \
  --output /safe/path/token-admin-bootstrap.env
```

Transfer the source and bootstrap securely, then run as root:

```text
bash deploy/install.sh /path/to/source /path/to/token-admin-bootstrap.env
systemctl status token-admin token-admin-worker
curl --fail http://127.0.0.1:8787/healthz
```

The installer provisions PostgreSQL, a least-privilege service account, `/etc/token-admin.env`, Web and Worker units, health-checked releases, and bounded rollback candidates. It removes the bootstrap after a successful first install.

Copy `deploy/token-admin-nginx.example.conf`, replace its example host and certificate paths, add the proxy settings above to `/etc/token-admin.env`, and restart both services. Port 8787 must stay private.

## Backup and operations

Back up the PostgreSQL database, runtime environment secrets, and original admin key as one protected set. The database is unusable without `ENCRYPTION_KEY`; encrypt backup media and test restores in isolation. Run one Worker only. Start external checks at low concurrency and comply with Microsoft terms, tenant limits, and the authorization scope.

Rotating `SESSION_SECRET` revokes every session. Version 1.0 does not implement online `ENCRYPTION_KEY` rotation; replacing it directly makes encrypted fields unreadable.

## Upgrade, health, troubleshooting, and removal

For Docker, take the paired backup first, then rebuild/recreate and verify `/healthz` plus login, import, and one low-risk check. For systemd, rerun `deploy/install.sh` with the new source; its health gate retains bounded rollback candidates and preserves the environment/database.

- A failed `/healthz` requires checking Web, Worker, PostgreSQL, environment-file permissions, and free disk without exposing the environment file.
- Login failures require checking the Argon2id hash and rate-limit state; the original admin key cannot be recovered from the server.
- Decryption failures mean the database and `ENCRYPTION_KEY` are not a matched backup set. Never overwrite data by repeatedly trying new keys.
- Stalled work requires exactly one Worker, lease checks, Microsoft connectivity/tenant limits, and a low-concurrency retry.
- Proxy loops or missing cookies require correct `COOKIE_SECURE`, trusted proxy IPs, overwritten forwarding headers, and an exact `ALLOWED_HOSTS` list.

Before uninstalling, stop traffic and both services and verify a complete encrypted backup. `docker compose down` preserves volumes unless they are explicitly removed; volume removal permanently erases the database. For systemd, disable/remove both units, the proxy site, and release directories. Remove PostgreSQL data/roles, `/etc/token-admin.env`, and the service account only after deciding restoration is unnecessary. Revoke any secret that may have leaked; deleting files does not invalidate it.
