# Outlook token keeper

[中文](README.md) · English

[![CI](https://github.com/ferretgeek/outlook-token-keeper/actions/workflows/ci.yml/badge.svg)](https://github.com/ferretgeek/outlook-token-keeper/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ferretgeek/outlook-token-keeper/actions/workflows/codeql.yml/badge.svg)](https://github.com/ferretgeek/outlook-token-keeper/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/ferretgeek/outlook-token-keeper?style=flat-square)](https://github.com/ferretgeek/outlook-token-keeper/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-168a70.svg?style=flat-square)](LICENSE)

[![Interface preview](docs/images/dashboard.png)](https://ferretgeek.github.io/outlook-token-keeper/)

[Live demo](https://ferretgeek.github.io/outlook-token-keeper/) · [Deployment](docs/DEPLOYMENT_EN.md) · [Security policy](SECURITY.md)

> Renew a batch of authorized Outlook OAuth tokens before they expire, and verify read-only that the mailboxes still connect.

## Why this exists

Microsoft OAuth refresh tokens don't last forever: leave one unused long enough and it lapses, and certain account-side changes invalidate it too.

With one or two accounts you just click renew. With dozens or hundreds of **explicitly authorized** accounts it's a different problem — you need to know which are expiring, which already failed, and which renewed successfully but whose mailbox no longer connects. And it has to run on a schedule, resume after a crash, and leave a trail when something goes wrong.

This is that schedule: **import, renew on a timer, health-check read-only, and record exceptions** — all on your own server.

> **Use it only for accounts and tokens you own or are explicitly authorized to administer.** It doesn't harvest accounts, doesn't supply tokens, and doesn't bypass Microsoft's authorization, risk controls, or terms.

## What you get

- **A recoverable workflow** — streaming TXT import, durable PostgreSQL job queue, single / selected / expiring-range job modes, and workers that resume after restart.
- **A restrained data plane** — legacy plaintext mailbox passwords are **discarded immediately** after parsing; addresses, client IDs, and refresh tokens are AES-256-GCM encrypted, and lists show masked addresses only.
- **Real but bounded checks** — refresh against fixed Microsoft OAuth endpoints; IMAP XOAUTH2 opens the inbox read-only and **never reads or displays message bodies.**
- **Built for large sets** — primary-key cursor paging, job snapshots, batching, upload / line / response ceilings, free-disk checks, and history cleanup.
- **Security checks up front** — write endpoints validate session and CSRF **before parsing the body** and cap request size per endpoint; login attempts are atomically reserved, and the expensive hash has a process-wide concurrency limit.
- **Four global themes** — Emerald, Azure, Sunset, and exact `#17191d` graphite, persisted across the login screen and the console.

## Local preview

Requires Python 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python scripts\run_qa.py
```

Open `http://127.0.0.1:8787/`. The temporary admin key is written only to a git-ignored `data/qa-*-admin-key.txt`.

Clean up afterwards:

```powershell
.\.venv\Scripts\python scripts\stop_qa.py
.\.venv\Scripts\python scripts\cleanup_qa.py
```

## Docker deployment

Generate the admin key and `.env` on a trusted machine first, then start the loopback-bound web, worker, and PostgreSQL services:

```powershell
.\.venv\Scripts\python scripts\generate_admin_key.py --output .\admin-key.txt
.\.venv\Scripts\python scripts\generate_docker_env.py --admin-key-file .\admin-key.txt --output .env
docker compose up -d --build
```

Then open `http://127.0.0.1:8787/`.

**A public server must sit behind a trusted HTTPS reverse proxy**, with `COOKIE_SECURE`, `TRUST_PROXY_HEADERS`, `TRUSTED_PROXY_IPS`, and `ALLOWED_HOSTS` set to match. The Ubuntu 24.04 systemd setup, updates, and backups are covered in the [deployment guide](docs/DEPLOYMENT_EN.md).

## Worth noting technically

**Encryption binds identity and field name.** Account ciphertexts use "account identity + field name" as authenticated context (AEAD AAD) — so even with database access, nobody can move account A's ciphertext into account B's field and have it accepted. Legacy ciphertexts are upgraded in **bounded startup batches** rather than pulling the whole table into memory.

**Legacy plaintext passwords are discarded immediately.** When an import carries a mailbox password from an old format, it's dropped right after parsing — never stored, never logged. This project only wants refresh tokens.

**There is no configurable outbound target.** OAuth and IMAP destinations are pinned to Microsoft endpoints. A tool that can be configured to send your tokens somewhere arbitrary is a backdoor.

**Security checks come before body parsing.** Write endpoints validate the session and CSRF, cap request size per endpoint, and **then** parse. In the other order, one unauthenticated large request could consume your memory.

**Login attempts are atomically reserved.** Counters reserve atomically to prevent concurrent rate-limit bypass, and the expensive password hash has a process-wide concurrency limit so the login endpoint can't be used to saturate the CPU.

**Paging designed for millions of rows.** Lists use primary-key cursor paging rather than `OFFSET`, jobs carry snapshots, imports are batched, and upload size, line length, and response size all have ceilings, alongside free-disk checks and periodic history cleanup.

Architecture and capacity boundaries are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); privacy boundaries in [PRIVACY.md](PRIVACY.md).

## What it doesn't do

- It doesn't harvest accounts, supply tokens, or obtain authorization on your behalf.
- It doesn't bypass Microsoft's authorization, risk controls, or terms.
- It doesn't read, store, or display message bodies (the health check only confirms the inbox can be opened).
- It neither accepts nor retains mailbox passwords.

## Pre-release checks

```powershell
.\.venv\Scripts\python -m ruff format --check app scripts tests
.\.venv\Scripts\python -m ruff check app scripts tests
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m pip_audit -r requirements.txt --progress-spinner off
```

## More documentation

[Deployment](docs/DEPLOYMENT_EN.md) · [Architecture and capacity](docs/ARCHITECTURE.md) · [Privacy](PRIVACY.md) · [Release audit](docs/发布审计.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md)

## License and disclaimer

[MIT](LICENSE). Direct dependency licenses are summarized in [THIRD_PARTY.md](THIRD_PARTY.md).

Independent project with no affiliation with, authorization from, or endorsement by Microsoft.
