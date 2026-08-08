# TokenLoom project rules

- Read the workspace root `README.md`, this file, and the project README before changing the project.
- Never add real accounts, email addresses, passwords, Client IDs, refresh/access tokens, server addresses, deployment identities, databases, logs, import files, screenshots, or environment files.
- Keep outbound OAuth and IMAP destinations fixed and documented. Any change to external data flow requires security tests and `PRIVACY.md` updates.
- Preserve the three light palettes and exact `#17191d` graphite mode across login, console, and demo; keep SVG, PNG, and ICO favicons synchronized.
- Run Ruff, pytest, pip-audit, Bandit, detect-secrets, Gitleaks tree/history scans, JavaScript syntax checks, and desktop/mobile visual QA before a public release.
- Every public change must also update the workspace root `README.md` and the profile repository `ferretgeek/README.md` when its visible project facts change.
