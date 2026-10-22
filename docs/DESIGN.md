# Logger v2 design draft

A rebuild of the personal logging app as one core with thin clients, so the iPhone AI app, the desktop AI app, the web app and a future bot all share the same rules. Draft 2, 21 Oct 2026, revised after reading nine days of real v1 usage. Nothing is built, and the running app stays untouched until v2 has earned the switch.

Basis for this draft: the v1 repo docs, and the v1 database read on 21 Oct 2026 (153 chat messages from 13 to 21 Oct, 782 food entries, 241 foods, 6 usual meals, 87 measurements, 135 events, 62 thoughts). Where a decision rests on that data it says so. Private entries were counted, not read.

## 1. Purpose and scope

A single-user system for food and drinks, habits, health measurements, labs, thoughts and private entries that:

- runs on the home server under `/opt/stacks/`, with one compose file and data beside it
- takes entries from the iPhone AI app, the desktop AI app and a web app, and from Telegram later if wanted
- works without any AI for correcting, reviewing and repeating entries
- keeps all entries in one Postgres database
- shows reports in the web app and in Google Sheets
- makes private entries and lab results reachable from the iPhone, behind a stricter check

What the data says you actually do:

- **Food and drinks dominate:** about 28 entries a day, most of them one-liners with grams, often dictated by voice.
- **Your meals repeat almost exactly:** the same morning bowl, the same snack combinations, the same drinks. Today every ingredient and gram is re-sent each time.
- **A day also has habits and numbers:** wake and bed times, meds, meditation, weight, blood pressure and blood sugar, often several in one message.
- **A quarter of the chat is notes, ideas and to-dos,** including a list of improvements for the app itself.

In scope for v2 beyond v1: notes on meals and foods, a per-day summary and coaching text, an exercise tracker, and date and time shown in confirmations. Tasks and shopping items stay in Todoist, as today, through the AI app's own Todoist connector.

Non-goals: multiple users, sharing, third-party analysis.

## 2. Principles

Kept from v1:

1. Capture, storage and presentation are separate. Every log input is stored verbatim and append-only first.
2. Derived rows keep the raw id and an extractor version, so anything can be re-extracted. This matters more for dictated text, which a better model may read more accurately later.
3. Migrations are additive only.
4. Unknown is not zero.
5. Nothing reaches production before it passed a sandbox test on the same Postgres major version.
6. Sheets, the web app and NocoDB are views, never data stores.

New in v2:

7. AI is a client, not a dependency.
8. The core never guesses silently. It returns a structured outcome and the client asks the human.
9. Rules live in the server, not in the prompt, so a different AI needs only a short prompt.
10. Sensitive tiers are separate in the code and the API, not only in the schema.
11. Repeating beats re-entering. The common case, "the usual again, with small changes", takes one short sentence or one tap.
12. Follow-ups are anchored explicitly. A receipt returns the meal id, the client passes it back for a follow-up such as "25g whey", and server-side context is only a fallback with a short expiry.
13. Only calls that carry structured entries are stored as log input. What the client chooses to send is the client's job, and the prompt asks it to send only log messages.

## 3. Architecture

- **db**: Postgres 17, the same major as v1 so its dump restores directly. Bind-mounted, no host port.
- **core**: Python (FastAPI). Owns every rule, name resolution, validation, context and the audit log. Described by an OpenAPI file, which is the single source of truth for behaviour.
- **mcp**: a thin adapter exposing the core as MCP tools over Streamable HTTP, with no business rules.
- **web**: a mobile-first, installable web app, server-rendered (proposed: FastAPI, Jinja and HTMX, no JavaScript build). It is mainly for seeing, correcting and repeating, not for typing entries (section 6).
- **sheets-sync**: as in v1, a read-only database role, values only, change detection, plus a "refresh now" trigger from the web app.
- **jobs**: backups, reports, and generation of day summaries when an AI client asks for one.
- **telegram** (later): one more thin client.

Traffic reaches the MCP adapter through a Cloudflare Tunnel. The core is the only component that writes to the database. Language is Python everywhere, because speed is not a constraint for one user.

What changed from draft 1: explicit follow-up anchoring with a small context fallback, a repeat mechanism built on versioned meal templates, a log-only input rule, and a web app redesigned around review and repeat instead of free-text entry.

## 4. The core contract

Every write returns exactly one outcome:

| Outcome | Meaning | Client behaviour |
| --- | --- | --- |
| saved | Stored, with a receipt of ids and the date and time used | Show the receipt |
| needs_choice | Several candidates | Show options, send the chosen one back |
| needs_data | Information is missing | Ask for values, or offer a lookup |
| needs_confirmation | Possible but odd | Show a read-back, confirm or cancel |
| rejected | Will not save, with a reason | Show the reason |

Every outcome except `saved` carries a `pending_id`, stored in the database with an expiry (proposed: 24 hours). The answer goes back with `resolve(pending_id, answer)`. A question can therefore start in the iPhone chat and finish in the web app, because nothing depends on a chat session. Receipts always show date and time, which was one of your own requests.

**Rules the core owns, ported from v1.** Health day is local time minus 5 hours. Food day is the day of the last wake-up within 18 hours before the food, and the core asks if there is none. Each type has a plausible range. A second weight in a day asks for confirmation, and so does a future time. Names resolve by id, exact name, alias, default term, then close candidates, and the close candidates always ask. Estimated weights are flagged. Correcting a food value that past entries use asks whether the past changes too. Lab reports need a content code. Private entries are routed to the private store, fail closed, and are never linked or shown elsewhere.

**Time is the hardest rule set.** Your messages often describe earlier events ("at 18:00", "last night at 3:10", "log at 14:00") and arrive after midnight. The AI client resolves the words into a local date and time and sends them with a flag saying whether the time was stated or inferred. The core applies the day rules and stores `time_known` accordingly. About a quarter of v1 food entries have no known time today.

**Follow-ups and context.** Every receipt returns the meal id it used. A follow-up fragment such as "25g whey" or "1.5 tbsp ketchup, 2 tbsp remoulade" carries that meal id back, and the core attaches it there. In a chat the previous receipt is already in the conversation, so this needs no hidden state. For clients that cannot pass the id, the core keeps a small fallback context (the last meal, expiring after a short time, proposed two hours), and the receipt always says which meal an entry attached to, so a wrong attachment is visible at once. If the context is stale or unclear, the core asks instead of guessing.

**Repeat.** `repeat(template_or_entry, changes)` logs the last version of a named usual meal with edits, for example "the usual bowl, 20g muesli, no walnuts". A template is created from history: when the same set of items recurs, the core offers to name it. The template then updates as you change it, so it always holds your latest version.

**Operations** (the pilot subset is `record`, `repeat`, `query`, `amend`, `remove` and `resolve`):

- `record(entries[], text, source, idempotency_key)`: typed entries such as measurement, event, food, thought, note or exercise. One call can hold several domains, as in a morning line with wake time, weight, meds and meditation. The caller supplies the structure and the core validates and resolves it.
- `repeat(ref, changes)`: described above.
- `query(kind, filters)`: read access to the views, with small results and no raw SQL.
- `amend(id, changes)`: edit one entry, for example move a food entry to another meal, change its amount or date, or add a note.
- `remove(id)` and `undo(receipt_id)`.
- `resolve(pending_id, answer)`.
- `lookup_food(text)` and `create_food(values, source)`: Open Food Facts search and creation.
- `day(date)`: the day summary and checklist status.
- `summary(date, text)`: stores a day summary or coaching text written by whichever AI client is in use. Which model writes it is the client's choice, not the core's.

A quick-add parser (not AI) turns terse typed lines into `record` calls, for example `up 8:30, 127.5, meds, shoonya` or `bp 120/80`. It is meant for habits and measurements, where you already write in that style. It is not meant for food, which you dictate in natural sentences. Anything it cannot parse is shown back, never guessed.

**Safety properties.** `record` takes an idempotency key, so a retry or an accidentally sent duplicate creates no second entry. Raw text is stored before processing and stays marked unprocessed on failure. Every call is written to an audit log, which matters because the endpoint is public. Each raw entry records its channel: mcp, web, rest, telegram or import-v1. Only text passed to `record` or `repeat` is stored as log input.

## 5. Data model

Reuse the v1 schemas (thoughts, health, labs, food, drinks) as the starting point, so the v1 `appdata` dump restores into v2 without transformation. Additions are all additive:

- `pending`: pending id, kind, payload, created, expires, resolved
- `session_context`: current meal, last entry, last day, per client session
- `meal_templates` and `meal_template_items`: your usual meals, updated from the last version used, with a version history so a template cannot drift unnoticed. The six v1 usual meals migrate into them, even though none is linked to an entry today.
- `notes` on meals and on individual food entries
- `day_summaries`: date, text, author client, created
- an exercise type in the type registry, with its own plausible ranges
- `audit_log` and `idempotency_keys`
- `raw_inbox.source`, defaulting to `import-v1` for migrated rows

Roles and per-table grants stay as in v1: CONNECT is revoked from PUBLIC and the private schema stays separate. Roles are `core_rw` as the only writer, `sheets_reader`, and `web_ro` if the web app reads views directly. For the data migration, dump v1 `appdata`, restore it into v2 in a sandbox, compare row counts for every table, and only then do the real cutover.

## 6. Clients

**Web app: rethought.** v1 usage shows that entry happens by voice in the AI app, and that food is dictated in whole sentences. A web form would rarely beat that. The web app's job is to see the day, fix mistakes, repeat what you repeat, and read reports. It is designed phone-first and installable on the home screen.

| Page | What it is for |
| --- | --- |
| Today | The day by meal, drinks, the habit checklist (wake, bed, meds, meditation), running calories, protein and caffeine against targets, and anything flagged for review. |
| Repeat bar | Your usual meals as buttons. One tap logs the last version; a small editor changes grams before saving. |
| Meal editor | Move an entry to another meal, change an amount or time, add a note, undo. |
| Questions | Every pending question from any client, ready to answer. |
| Trends | Weight, blood pressure, blood sugar, sleep and caffeine over time, with tables and charts. |
| Food catalogue | Foods with estimated or flagged values, so they can be fixed once. |
| Thoughts and tasks | A readable layout for notes and ideas, which you asked for. |
| Labs and private | Behind the step-up check. |
| Reports | Weekly and monthly summaries, and a button that refreshes the Google Sheet now. |

A small quick-add line for habits and measurements stays at the top of Today.

| Other client | How it connects | Notes |
| --- | --- | --- |
| iPhone and desktop AI apps | Remote MCP server added as a custom connector | The main entry path. The AI provider's cloud makes the connection, so the endpoint must be public. Six or seven tools with short descriptions and a written token budget. |
| REST with token | Same core, narrow-scope token | For scripts, iOS Shortcuts and a future bot. |
| Google Sheets | Read-only role, two views, hashed change detection | Same pattern as v1, so the existing sheet keeps its tabs and layout. |
| Telegram (later) | Polling bot calling the core | Needs a server-side LLM for free text. Private and lab tools are not exposed there. |

v1 spends about 3,760 tokens on tool definitions and 3,760 words on instructions in every chat. In v2 the prompt only explains how to turn natural language into `record` and `repeat` calls, to send only log messages to the tools, and to ask the user when the core returns a question. Everything else travels in tool results, so a different AI needs a different short prompt. An optional server-side LLM for free text in the web app and for Telegram sits behind one interface, `parse(text)` returning entries, and is off by default.

To verify in the pilot: whether the iPhone app shows connector tool descriptions and results the way the desktop app does, and whether tool-result length limits matter.

## 7. Access, auth and exposure

Only the MCP hostname is public. Everything else stays reachable at home or over Tailscale, as today.

- **Exposure (proposed):** a Cloudflare Tunnel from the home server for a hostname such as `log.19hospitalroad.fyi`, with no port forwarding on the router. Whether the web app is also public is open (section 13).
- **Connector auth (to verify):** the AI apps expect OAuth 2.1 with discovery metadata, probably dynamic client registration, and a fixed callback URL. I am not certain of the details, so the pilot checks them against current documentation.
- **Authorization server (proposed, to be proven):** a minimal single-user OAuth server inside the gateway, with a passkey or password plus a second factor. Many identity products lack the registration features the connector flow needs. The alternative is a dedicated product that also gives single sign-on for the rest of the homelab, and a spike decides.
- **Two tiers of tools:** standard (food, habits, measurements, thoughts) and sensitive (private entries and labs). Both are reachable from the iPhone, but sensitive calls need a short-lived step-up first, and their results are never written to logs.
- **Hardening:** allow only the AI provider's published outbound IP ranges where possible (to verify that they are published and stable), rate limiting, token expiry and revocation, the audit log, and no admin endpoints on the public hostname.
- **Secrets:** `.env` files stay out of git, are copied into Vaultwarden and are included in backups.

GDPR probably does not apply to purely personal processing (the household exemption), but the data is handled as special-category health data regardless. The real exposure is to you: a public endpoint, and the AI provider seeing what appears in chats and tool results. The private tier is where the line is drawn.

## 8. Layout and operations

```
/opt/stacks/logger-v2/
  compose.yaml   .env (not in git)
  core/  mcp/  web/  sheets-sync/  jobs/
  pgdata/  backups/   (not in git)
  DESIGN.md  tests/  migrations/
```

- Caddy serves the web app internally, and the tunnel serves the MCP hostname.
- Images are pinned by digest as in v1, with healthchecks, a Homepage tile and an Uptime Kuma monitor.
- Backups: a nightly `pg_dump -Fc` plus globals, picked up by the homelab restic job, with a restore test after every migration. The off-site copy is the restic repository.
- **Uptime assumption:** v1 runs in a VMware VM on a Windows PC (the VM is still `vmware`, checked 21.10.2026), so it works only while the PC and the VM are on. The pilot is therefore valid only while the host is on. The always-on target is the planned dedicated home server.
- Code location is open: a new GitHub repository for v2 code with this folder holding deployment files only, or everything in one repository.

## 9. Testing

- Port the v1 sandbox tests first. They run against a throwaway Postgres 17.
- **A golden set from real use.** The 153 chat messages become test cases, each with the entries it should produce: fragments, dictated sentences, after-midnight times, multi-domain lines, duplicates. It contains personal data, so it lives outside git, in `/opt/stacks/logger-v2/tests/golden/` on the VM (readable by Claude Code, covered by the encrypted backups) and is never committed to a shared repository.
- Contract tests cover every outcome type for every write operation, plus repeat and context attachment.
- A rule is not ported until its v1 test passes against the core.
- Migration test: restore a real v1 dump into a sandbox, compare counts and spot-check the views.

## 10. Migration and cutover

1. v1 keeps running. v2 is built beside it on a different hostname and port.
2. The pilot uses test data only. No real data goes into v2 until the pilot criteria pass.
3. Port the domains in this order: habits and measurements, food and drinks, thoughts, labs. Food moves earlier than in draft 1 because it is most of your daily use.
4. Dry-run the migration from the v1 dump into a sandbox and then v2, comparing counts and view outputs such as food day totals and the glucose view.
5. On cutover day: snapshot the VM, take the last v1 backup, make a final dump, restore it into v2 and verify. Then switch the AI app connector and make v1 read-only. Keep v1 for about two weeks.
6. During the dual period, log in one place only. After cutover, v1 is never written to again.

## 11. Pilot

Decided 22.10.2026: v1 gets its new features first, and v2 copies them afterwards. The v2 pilot is a small proof of concept with three entry types: weight, blood pressure and wake-up time. It proves the riskiest things and nothing else.

1. **Remote MCP from the iPhone and desktop AI apps:** `record` and `query` through the tunnel, with OAuth.
2. **The core contract on real rules:** plausible ranges for weight and blood pressure, a second weight in a day asking for confirmation, wake-up time with the day rule (health day is local time minus 5 hours, so a wake-up logged at 01:30 lands on the right day), an idempotent retry, a receipt with date and time, and `amend` and `undo`.
3. **A minimal web page:** Today shows the three values and has a form for them, and pending questions can be answered there.

Out of the pilot: food, repeat, labs, private entries and the sensitive tier, Telegram, and Sheets. Only Claude is tested; REST contract tests keep a second AI app cheap to add later.

Success criteria:

- All of it works from the iPhone on mobile data, not Wi-Fi.
- A dropped connection followed by a retry creates no duplicate.
- The odd-value read-back works.
- A wake-up time logged after midnight lands on the correct day.
- The token can be revoked.
- The whole stack comes up from `docker compose up -d`.

Two spikes of about a day each come first, with no commitment: the connector auth flow, and tunnel latency and stability. The pilot is valid only while the host is on (section 8).

## 12. Phases

1. **v1 first (decided):** build the new features in v1 (repeat templates, follow-up anchoring, date and time in receipts, notes, day summaries, and more from the backlog). Keep the schema change additive, and write each feature's tests so they can become v2 contract tests. This avoids doing the work twice.
2. Review this document and decide the remaining open questions.
3. Spikes, then the small pilot (weight, blood pressure, wake-up time) in a new repository.
4. Go or no-go on v2, based on the pilot.
5. If go: copy the v1 features into the core domain by domain, with tests from the golden set. Habits and measurements first, then food and drinks, then thoughts, then labs and the sensitive tier.
6. Web views, reports and the Sheets sync, including refresh now.
7. Dry-run migration, cutover, and two weeks of dual running.
8. Optional: Telegram, exercise tracking extras, and a server-side LLM for free text.

## 13. Open questions

- [ ] Is the web app public for use on the iPhone without Tailscale, or is only the MCP endpoint public with the web app on the tailnet? The second is safer, and Tailscale already works on the iPhone.
- [x] Where does the code live? Decided 22.10.2026: a separate repository for v2, and `collab/` moves there later.
- [ ] Which step-up method for the sensitive tier: a one-time code, a second passphrase, or a passkey prompt?
- [ ] Is the existing Google Sheet kept as it is, with the same tabs and layout, so the sync is only repointed?
- [ ] Todoist: should tasks keep going through the AI app's own connector, or should the core call Todoist so every client can add items?
- [ ] Is NocoDB kept as a bulk-edit view over v2, or dropped now that the web app has a meal editor and a food catalogue page?
- [x] Which AI apps must be supported first? Decided 22.10.2026: Claude only for the pilot, with REST contract tests so a second AI app is cheap later.
- [ ] Is anything in the v1 chat instructions model-specific that you would not want to lose when the prompt is shortened, such as house rules, wording or languages?
- [ ] Should day summaries and coaching text be written automatically at the end of each day, or only when you ask?
