# Core: improvements and feature ideas

Written 28.10.2026 from a read of the core (`core/app`: `logbook.py`, `record.py`, `ai/`, `mcp_app.py`, `auth.py`,
`db.py`, `migrate.py`, `todos.py`, `todoist.py`, `foodfacts.py`, `service.py`), the Dockerfile, `compose.yaml`, the
tests, and counts from the live database (`audit_log`, table sizes; no entry contents). The web app has its own list
in `docs/IMPROVEMENTS.md`. Nothing here is decided: each item is an idea; plan it in the doc first before building.
The v1 files (`v1_*.py`) are copied unchanged on purpose and are left as they are.

Size: S = about an hour, M = about half a day, L = a day or more. Rough guesses.

## Priorities (reviewed 05.11.2026, after deploy 62)

The combined top 10 with the web list is at the top of `docs/IMPROVEMENTS.md`. For the core, in order (S about an hour, M about half a
day, L a day or more):

1. **30 Safe parallel sessions** (done, deploy 64, 08.11.2026: a test database per worktree, `tools/deploy.sh` guard): its own test database per worktree, and a deploy guard. On 05.11.2026 three sessions
   worked at once and clashed in the shared test database at first.
2. **16 `answer()` handlers** (S-M): one small handler per question kind; the chain has 10 kinds since the nap answer (05.11.2026).
3. **13 Remove the pilot tools** (S-M): 9 hidden tools in `mcp_app.py`, plus `service.py` and its tests; dead code that still had to
   follow the new day rule on 05.11.2026.
4. **31 Check the watch's sleep import** (S, new): no sleep rows have arrived; Today's watch wake-up and the Activity page's sleep need them.
5. **24 Words review** (S): all learned food words by date, so a wrong one is easy to find.
6. **3 Second read** (S): look at the data again around mid-November (owner agreed on 02.11.).
7. **28 Faster tests, step 2** (M): run in parallel with one test database per worker; goes with 30.
8. **12 Update alerts** (S): Diun for the images, a monthly look at the pinned Python packages.
9. **17 + 25 Digest and Telegram** (M-L): a summary and nudges that reach the owner away from home.
10. **20 Food look-up gaps** (S-M): German words in Open Food Facts; full-fat when no fat level is said.

**Not worth it now:** 6 shorter prompt. **Open owner decisions:** GitHub's test run as the check before a deploy; whether the repo stays
public; the mini PC move; ending the v1 fallback.
**Done:** 1, most of 2 (01.11.), 4, 5 (02.11., deploy 42), 7, 8, 9 (deploy 45), 10, 11, 14, 15 (GitHub runs every push), 18 (web push),
19 export (deploy 60, 05.11.), 21, 22, 23, 26 (deploy 50), 27 and 28 step 1 (02.11.), 29 (resolved 04.11.).

## Status (01.11.2026)

- **1 (never lose a message) is done, and 2 in part** (deploy 34; the plan and the owner's answers are in the doc "Logger v2: status and web app plan", section "Core: never lose a message"). A typed message is kept word for word in `private.incoming` (migration 020, private schema) before the AI reads it; the AI reads with no transaction or connection open; the save, its audit row, the idempotency key and the removal of the kept words are one transaction (`logbook.read_held`, `held.py`). When the AI or the save fails, the words stay "not read yet": cron reads them again every 5 minutes for 2 hours (`docker exec logger-v2-core python -m app.held`), then the phone is told once (no words in the notification); Today and Recent show them with Try again and Delete (Undo). A retry keeps the message's own time. Kept until read or deleted; a deleted one goes for good after a day (daily clean-up).
- **Still inside the save transaction (the rest of 2, left on purpose, owner's answer):** Todoist, the food look-up (USDA, Open Food Facts) and change commands. If one of them fails, the save is rolled back but the kept words stay, so nothing is lost. The quick buttons (ticks, health values, usual meals, Again chips) keep the old one-transaction path: they need no AI read.

## Status (31.10.2026)

- **18 (reminders) is done on the web app side:** web-push nudges counted from the wake-up time (no weight, habits still open, nothing logged for N hours; quiet before waking, after bed, 16 hours after waking), set in Settings > Notifications, checked by cron every 5 minutes (`app.nudges`). **Telegram as a second channel and 17 (digest), 19 (export), 20 (food look-up) are still open.** Not yet verified on the user's iPhone.
- **New on the core side (31.10.):** `labelread.py` (a photo of a nutrition label read by Claude, model claude-sonnet-5-5, one call per photo, cost in `audit_log` as `label_photo`), `ai/provider.py` `image_json`, `webpush.py` and `nudges.py` (web push without new packages), `webundo.py`. Migrations 018 (undo) and 019 (push). The VAPID signing key is the env var `VAPID_PRIVATE_KEY` in `/opt/stacks/logger-v2/.env`.
- **Still open from before:** 17, 19, 20 (decisions), 24 (a page of learned words); 1 and 2 left alone on purpose (done on 01.11., see above).

## Status (29.10.2026, first build run; details in `docs/reports/core-2026-10-29.md`)

- **Done and deployed:** 3 (preparation only: the audit log now records what the second read did), 4, 5, 7, 8, 10, 11, 14, 21, 22, 23. 15 (GitHub Actions) is written but has never run.
- **Left alone on purpose:** 1 and 2 (need a decision / too risky unattended), 6, 9, 12, 13, 16.
- **Open:** 17 to 20 (need your decisions), 24 (a page of learned words).

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
| 19 | Export everything you logged (CSV or JSON, one download); done 05.11.2026, deploy 60 | Your data in an open form, apart from the Sheet and the encrypted backup | S-M |
| 20 | Food look-up: search German words in Open Food Facts; prefer full-fat when no fat level is said ("greek yogurt" comes back low-fat) | Known gaps from HANDOVER section 6 | S-M |

## 6. Food matching (found 29.10.2026, "oat cookie" logged as pasta)

What happened: the food is "Oat cookies, Oaties (Biscotto)" (N244). `v1_food._sing` turns "cookies" into "cooky" but
leaves "cookie" unchanged, so the singular "oat cookie" never matched it. The question that followed offered
unrelated "cooked" foods (Pasta, Cod, Quinoa, Peas) from the fuzzy match, N244 not among them. The pick of Pasta saved
"oat cookie" as a permanent word for it (`record._alias`), and the next messages matched pasta without asking. The
data was fixed the same day (word moved from N138 to N244).

| # | Idea | Why | Size |
|---|---|---|---|
| 21 | Singular and plural match both ways: compare a word, its singular and its plural forms (cookie / cookies, berry / berries, box / boxes) in a v2 wrapper around `_find_foods`; do not edit the copied v1 `_sing`. Tests with the real cases (cookie, cookies, berries, waffles, chips, noodles) | The same fault hits any food whose name ends in "-ies" typed in the singular | S |
| 22 | Learn a word only from a good suggestion: do not save the picked word as an alias when the suggestions came from the loose "sounds similar" match; show "saved 'oat cookie' as a word for Pasta, cooked" in the reply with an Undo button | One wrong click gave a permanent wrong word, and later messages matched it silently | S-M |
| 23 | Better suggestions when nothing matches: list foods that share a whole word first, and say "none of these? look it up or add a new food" as the first choice; never list foods that only share a word ending ("cookie" vs "cooked") | The suggestions were unrelated and pasta came first | S-M |
| 24 | A "words" review: list all learned words (aliases) by date on the web Catalogue, so a wrong one is easy to find and remove | The fault was found by searching the database | S |

Already on other lists, not repeated here: Telegram, the exchange log of the chat, the chat saving a usual meal,
token counts for food look-ups and lab reports (web list 21, 20, 29).

## 7. New after 31.10.2026

| # | Idea | Why | Size |
|---|---|---|---|
| 25 | Telegram as a second channel for the nudges, a digest and replies (links items 17 and 18; speech-to-text there is on the "decide later" list) | Notifications now reach only the phone that turned them on; Telegram would also work away from the home network and Tailscale | M-L |
| 26 | **Done 04.11.2026, deploy 50.** Show the cost of label photos and push messages on the Usage page | `label_photo` rows are in `audit_log`; the Usage page does not list them as a kind yet | S |

## 8. New after 02.11.2026

| # | Idea | Why | Size |
|---|---|---|---|
| 27 | Disk headroom: `docker builder prune` (keep a little cache) at the end of `tools/deploy.sh`, and a disk-use check (warn above 90%) in `uptime-kuma/push-checks.sh`. **Done 02.11.2026:** cleaned up from 86% (5.4 GB free) to 80% (7.4 GB free): build cache trimmed to 1 GB, unused images removed (hello-world and its exited container, traefik/whoami, alpine:3, alpine:latest, python:3.13-slim), three old Claude Code versions in `~/.local/share/claude/versions` (2.1.306-308). `deploy.sh` now ends with a clean-up: it removes `trial-fixes<N-3>` and trims the build cache to 1 GB. The disk check was already in `push-checks.sh` (down above 90%); it alerts only once Kuma has an alert channel (the owner's task). Left: `sudo apt clean` (145 MB) and old journal logs need sudo (the owner) | The disk was at 86% (5.3 GB free); the build cache held 2.2 GB that could be reclaimed, and every deploy adds a 309 MB image (three are kept) | S |
| 28 | A faster test run: find the slowest tests (`pytest --durations=25`), share set-up where tests only read, and look at running in parallel with one test database per worker. **Step 1 done 02.11.2026: 15 min 15 s → 10 min 13 s** (405 tests): the tests hash passwords with a tiny scrypt cost (`auth.SCRYPT_N`, lowered in `conftest.py`; the live app keeps 2^15; sign-in cost 2 × 0.4 s per signed-in test), and the test database runs in memory without syncing (`tests/test-db.sh`: tmpfs, fsync off, scram_iterations 1; emptying the tables 340 → 220 ms per test). **Measured next:** opening a connection costs about 45 ms on this VM and a page opens 4 to 8 (the Today page 2.3 s in a test, 1.6 s of it connections; live, Today takes about 0.6 s to build, half of it 2 connections). A small connection pool (item 9) would speed up the tests and every live page. GitHub's runners take about 3 minutes for the whole suite | A full run takes about 15 to 17 minutes before every deploy; it was about 4 minutes at 223 tests | M |
| 29 | **Resolved 04.11.2026, no code change** (see Priorities). Night-time test failures: 4 GitHub test runs failed, all between about 03:00 and 06:00 Berlin time (seen 02.11.2026); find the tests that depend on the 05:00 day change and fix their clock | GitHub's run can then be the check before a deploy (an open owner decision) | S |

## 9. New after 05.11.2026 (three sessions in parallel; the sleep-cycle work)

| # | Idea | Why | Size |
|---|---|---|---|
| 30 | Safe parallel sessions: `tests/test-db.sh` and `conftest.py` take the port and container name from the environment (one test database per worktree, e.g. derived from the folder name), and `tools/deploy.sh` refuses to run when the checkout has uncommitted changes or another deploy holds a lock file | Deploy builds from the checkout, so a session's unfinished code would ship with another's deploy; tests in two sessions empty each other's tables (seen 05.11.2026 until the water session moved to its own test database) | S-M |
| 31 | Check the watch's sleep import: no `sleep_analysis` rows in `watch_metrics` yet. Check that the Health Auto Export automation sends Sleep Analysis, then confirm a night arrives and Today offers the watch's wake-up | Today's "Your watch says you got up" (deploy 59) and the Activity page's sleep card stay empty without it; mostly a setting on the owner's phone | S |

## Suggested order

Replaced on 02.11.2026 by "Priorities" at the top (the old order is done except 13 and 15).
