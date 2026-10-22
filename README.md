# logger-v2

Rebuild of the personal Logger app: one core with thin clients (AI apps over remote MCP, a web app, later Telegram). Private, single user.

Status: design draft and a small pilot (weight, blood pressure, wake-up time). Nothing here replaces the running v1 app (repo `logger-app`).

- `docs/DESIGN.md`: the design (draft 2, decisions of 22.10.2026 in sections 11 to 13).
- Deployment: this folder is cloned to `/opt/stacks/logger-v2` on the homelab VM. Data and secrets stay out of git (`.gitignore`).
- Rules: no passwords, keys, `.env` files, dumps or personal health data in this repo. Test cases built from real messages stay private.
