# Core: improvements and feature ideas

Written 28.10.2026 from a read of the core (`core/app`: `logbook.py`, `record.py`, `ai/`, `mcp_app.py`, `auth.py`,
`db.py`, `migrate.py`, `todos.py`, `todoist.py`, `foodfacts.py`, `service.py`), the Dockerfile, `compose.yaml`, the
tests, and counts from the live database (`audit_log`, table sizes; no entry contents). The web app has its own list
in `docs/IMPROVEMENTS.md`. Nothing here is decided: each item is an idea; plan it in the doc first before building.
The v1 files (`v1_*.py`) are copied unchanged on purpose and are left as they are.

Size: S = about an hour, M = about half a day, L = a day or more. Rough guesses.

## 1. Never lose a message

| # | Idea | Why | Size |
|---|---|---|---|
| 1 | Save the message's words first, in their own transaction, marked "not read yet"; then read and save the entries. A "try again" (button, or automatic on the next message) picks up what is left | Today, when the AI does not answer, the whole transaction is rolled back and the words are not kept anywhere (not even in `raw_inbox`, which is meant to keep every message word for word). The chat still has them; the web app loses them | M |
| 2 | Do not hold the database transaction open during calls to the AI, Todoist or Open Food Facts | One message keeps its connection, its locks and the idempotency lock for the whole AI call (several seconds; Todoist waits up to 15 s, the AI up to minutes, see 5). A second message or a page meanwhile waits. Goes together with 1 | M-L |

## 2. AI cost and speed

| # | Idea | Why | Size |
|---|---|---|---|
| 3 | Skip the Sonnet re-read when it cannot help | Measured 25.10.-27.10.: 14 Sonnet re-reads cost $0.225, 78% of all AI cost. Only 4 of them ended with everything saved; 10 still asked a question. A food that is not in the catalogue or a missing amount can only be answered by you, so a second read does not help. First record which kinds of question were open before and after, then decide | S-M |
| 4 | Record cache reads and cache writes separately in `audit_log` | Prompt caching is switched on (`cache_control` on the system prompt), but the audit log adds cached and uncached tokens together, so we cannot see whether the cache is ever used. If the prompt is below the model's minimum cache size, or messages come more than 5 minutes apart, caching saves nothing, and a cache write costs 1.25 times a normal read | S |
| 5 | Shorter wait when the AI is slow: for live messages about 20 s and one retry, then "try again" | The AI client retries 4 times with 60 s each, so in the worst case a message waits several minutes with no answer | S |
| 6 | A shorter parser prompt | `parser.md` is 2,525 words; with the output schema each message sends about 7,000 input tokens. Move rules that are seldom needed into the follow-up prompts; the golden score must stay at 180 or more of 199 | M |

## 3. Running it (goes well with the Uptime Kuma step)

| # | Idea | Why | Size |
|---|---|---|---|
| 7 | `/health` also checks the database (and a `/ready` with the time of the last message saved) | `/health` answers "ok" without touching the database, so a monitor stays green when the database is down | S |
| 8 | Error logging: one line per failed call (tool, outcome, error type; never private words or values) | The core writes no log of its own; a failure shows only as a traceback in `docker logs`, and nobody is told | S-M |
| 9 | A small database connection pool | Every function opens a new database connection; one web page opens 4 to 5 | S-M |
| 10 | A nightly clean-up: expired questions, idempotency keys older than 30 days, used sign-in rows | These tables only grow (the database is 17 MB, so not urgent) | S |
| 11 | A `.dockerignore` for `core/` | The image build copies the host's `__pycache__` files along with the code | S |
| 12 | Know when updates are out: Diun for the images (already on the homelab list), a monthly look at the pinned Python packages | Everything is pinned (good), so nothing updates by itself, security fixes included | S |

## 4. Code health

| # | Idea | Why | Size |
|---|---|---|---|
| 13 | Remove the pilot tools: `service.py`, its 9 hidden tools in `mcp_app.py` and `tests/test_service.py` (28 tests) | They have been switched off since the cutover (`PILOT_TOOLS=0`); `test_webtoday.py` and `test_webactions.py` use the module too and need a look first | S-M |
| 14 | Update old texts: the README still says "design draft and a small pilot" and that v2 does not replace v1; `config.py` says the MCP path has no login (it has had OAuth since the trial) | Misleading for the next session and for you | S |
| 15 | Run the tests on GitHub on every push (GitHub Actions with a Postgres service) | The 223 tests only run by hand (about 4 minutes). The golden tests stay private and out of it | M |
| 16 | `answer()` in `logbook.py`: one small handler per question kind instead of one long if/elif chain over 8 kinds | Safer to add a kind (Telegram, new questions) without breaking another | S-M |

## 5. New things the core could do

| # | Idea | Why | Size |
|---|---|---|---|
| 17 | A daily or weekly digest: yesterday's kcal and protein against the target, habits missed, BP and weight averages | Comes to you without opening the app or the Sheet; best with Telegram | M |
| 18 | Reminders: no weight by 10:00, a habit not ticked by the evening, a question open for a day | Needs a channel that can send to you (Telegram) | M |
| 19 | Export everything you logged (CSV or JSON, one download) | Your data in an open form, apart from the Sheet and the encrypted backup | S-M |
| 20 | Food look-up: search German words in Open Food Facts; prefer full-fat when no fat level is said ("greek yogurt" comes back low-fat) | Known gaps from HANDOVER section 6 | S-M |

Already on other lists, not repeated here: Telegram, the exchange log of the chat, the chat saving a usual meal,
token counts for food look-ups and lab reports (web list 21, 20, 29).

## Suggested order

1. Items 7 and 8 together with the Uptime Kuma monitors.
2. Items 1 and 5 (never lose a message, shorter wait), then 2.
3. Items 3 and 4 (cost), measured for a week before changing anything.
4. Items 13 and 14 (clean-up), then 15.
