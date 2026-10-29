# logger-v2

Rebuild of the personal Logger app: one core with thin clients (AI apps over remote MCP, a web app, later Telegram). Private, single user.

Status (29.10.2026): this is the real logger since 26.10.2026. It takes free-text messages from Claude (a remote MCP connector with
sign-in), from the web app (`/web/` on the app address, home network and tailnet only) and feeds a Google Sheet. The old v1 app
(repo `logger-app`) only stays as a fallback and is not written to.

- `docs/DESIGN.md`: the design (draft 2, decisions of 22.10.2026 in sections 11 to 13); parts of it are history.
- `docs/IMPROVEMENTS.md` (web app) and `docs/CORE-IMPROVEMENTS.md` (core): ideas and what is done; `docs/reports/`: reports of what was built.
- Deployment: this folder is cloned to `/opt/stacks/logger-v2` on the homelab VM. Data and secrets stay out of git (`.gitignore`).
- Rules: no passwords, keys, `.env` files, dumps or personal health data in this repo. Test cases built from real messages stay private.
