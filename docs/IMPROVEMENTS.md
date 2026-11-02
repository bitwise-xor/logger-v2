# Web app: improvements and feature ideas

Written 27.10.2026 from a read of the web app's code (`core/app/web*.py`, the page modules, `static/app.css`,
`static/app.js`) and the plan doc "Logger v2: status and web app plan". The pages were not opened in a browser for
this list, so layout points are read from the stylesheet, not seen. Nothing here is decided: each item is an idea.
Before building one, plan it in the doc first (with a clickable mockup for a new page).

Size: S = about an hour, M = about half a day, L = a day or more. Rough guesses.

## Priorities (reviewed 02.11.2026, after the Gym section and deploy 41)

Re-ranked against the app as it is now: the daily logging is fast and safe (never lose a message, Again chips, suggestions,
scanner, label photos), the Gym section is complete (Workout, Plan, Progress, Activity, History, Exercises with rename), the
watch data waits for the owner's watch. What is missing most is the *meaning* of the data (coaching, mood) and a few small
daily frictions. The core list (`docs/CORE-IMPROVEMENTS.md`) has its own ranking; the two together give the order below.
Nothing here is decided: plan each one in the doc first.

**Claude's recommendations (most value next):**

1. **52 Coaching** (M-L): the data is now rich enough (food, health, sleep, habits, gym) to say something useful each day and week. Needs the owner's answers to its 4 open questions first.
2. **37 Mood and energy check-in** (M): the one daily signal still missing; it feeds Insights (50) and the coach (52). Decide the scales and the time of day.
3. **Core 28 Faster test run, and core 15 tests on GitHub** (M): a full run takes about 15 minutes before every deploy; most of that is waiting. Faster runs make every later item cheaper.
4. **42 Lab report upload with trends** (L): lab reports still come only through the chat; the label-photo flow is the pattern (read, check, add).
5. **45 Export of everything** (with core 19) (S-M): your data in an open form, apart from the Sheet and the encrypted backup; useful before a mini PC move.

**Quick wins (small, about an hour each):**

1. **Core 27 Disk headroom** (S): 86% used; 2.2 GB of Docker build cache can go; each deploy adds a 309 MB image. Prune the build cache in `deploy.sh` and add a disk check to the Kuma push checks.
2. **Core 5 Shorter AI wait** (S): since 01.11. a typed message is kept and retried by cron, so a live message no longer needs to wait up to 60 s × 3; about 20 s, then "kept, trying again".
3. **46 One-tap usual meals** (S): a tap logs the meal with its saved weights; the editor stays for changes.
4. **40 Water target** (S): a daily water bar on Today from the drinks already logged.
5. **29 + core 26 Usage page complete** (S): label photos and token counts for food look-ups and lab reports; worth having before coaching adds Opus calls.

**Next after those:** 48 "All look right" on the review box (S), 26 error page in the app's layout (S), 38 minutes for practices (M), 43 medication schedule (M, only if wanted), 49 more nudges (S-M), 50 more Insights (M, after 37), 18 link thoughts (M), gym workouts in the Search page (S).
**Parked or dropped:** 35 swipe (not needed), 41 weight trend (dropped), 44 photo on a food entry (disk), 47 home-screen shortcuts (iOS limits), 51 server-side voice (only if Safari's fails), 20/21 chat usual meal and exchange log (no ask since 29.10.).
**Done since 31.10.:** 36 in part (sleep hours; no colours, the owner's choice), 39 mostly (watch data and the Gym Activity tab, deploy 40; typed steps not planned), the Gym section (deploys 37 to 41).

## Status (31.10.2026; the first build run of 29.10. is described in `docs/reports/web-app-2026-10-29.md`)

- **02.11.2026 (deploy 36, commit a35becf):** 12 Again chips on the PC (8 on phones); a food's words under its name in the catalogue and a Close button on the open food's box; suggestions treat ü/ö/ä/ß as ue/oe/ae/ss, show the word a food was found by, most recently eaten first; **36 (sleep hours) done in part:** "slept 7 h 05 min" with Woke up on Today and a 7-day average line on the Health Sleep chart (no colours, no target: the owner's choice; the Week page already had the average night). **41 (weight trend) dropped:** the Health page already has the 7-day average. Answers: artifact https://claude.ai/artifact/9pxSQY2DD55er5y51jMm51 (db `answers`).
- **31.10.2026, the three "frictionless" rounds (details in HANDOVER section 000):** the whole web app restyled (Inter, soft cards, icons, light/dark switch, phone tab bar with the entry box pinned above it); Today for any day (date picker, earlier days read-only) with the macro donut and share table; Undo bar; food entries change at once (no "Yes" question); food editor in place on Today; Again chips; food suggestions in the entry box; a 2-line entry box on the PC; the Recent page; notifications ("nudges") through web push (Settings > Notifications); voice input (microphone in the entry box); the barcode scanner (zxing) with Open Food Facts look-up and **photos of nutrition labels read by Claude**; the Insights page. Item 17 (barcode by camera) is done by this.
- **Open ideas after those rounds:** section 8 below (items 35 to 52; 52 = coaching, added 02.11.2026). The user's answers on 31.10. are in the private artifact https://claude.ai/artifact/9rHgvRxZWG7tV2LdXxdQhg (swipe: not needed; sleep hours and mood/energy: "save it for the next version").
- **Not yet checked on the user's real iPhone:** a real notification (needs Logger on the home screen, iOS 16.4+), the microphone in the home-screen app. The barcode scanner and the label photo were tried by the user and work.

## Status (29.10.2026, end of the first build run)

- **Done and deployed:** 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 19, 22, 23, 26 (the unknown-page screen; the error page is not done), 27 (titles, skip link, labelled menus), 30, 31, 32, 33, 34.
- **Open:** 16 (lab upload on the web), 18 (link thoughts): no answers yet. (17, barcode by camera, was built on 31.10.) 25 (one helper for the host check) and the error page of 26 are code tidying, not started.
- **Habits page, 30.10.2026:** the page lists every habit with a Track switch, Retire / Bring back, Delete (never-used only) and a New habit form; Wake-up and Bedtime are times, not habits (migration 017; Health card "Now" button). See `docs/reports/habits-page-2026-10-30.md`.
- **On the core list, not here:** 20, 21, 24, 28, 29 (see `docs/CORE-IMPROVEMENTS.md`).

## 0. Your additions (28.10.2026)

| # | Idea | What is there now | Size |
|---|---|---|---|
| 30 | Easy definition of usual meals and usual drinks: a "New usual meal" form on the Usual meals tab where you pick foods or drinks and their amounts directly, with a Meal / Drink switch | A usual meal can only be made from a meal you already logged ("Save as usual meal" on the Food log). The database already knows usual drinks (`usual_meals.is_drink`), but the web app has no way to make one | M |
| 31 | A separate Habits page: today's habits as big one-click buttons, the calendar of the last 30 days (moved off the Health page), and a click on a past day's square to tick or untick that day | Habits are ticked on Today; the 30-day calendar is on the Health page and cannot be clicked | M |
| 32 | Choose which habits show on Today (add or remove them), and their order | Today shows every habit marked `on_checklist` in the database; changing that needs SQL. To decide in the plan: does "not on Today" also hide a habit from the Habits page and the Sheet's Habits tab, or only from Today? | S-M |
| 33 | Sort the food catalogue: most eaten (all time or last 30 days), last eaten, date added, kcal per 100 g, protein per 100 g, name | The catalogue is always sorted by most eaten (all time), then by name; the filters (All, Drinks, Added in the last 14 days, Needs review) cannot be combined with another order | S |
| 34 | Customize the Today page: add and remove its items (habits, the Health card's rows such as blood sugar or wake-up, the usual-meal buttons, the review box), reorder them, and bring them back later. Saved per user in the database, with "Reset to default" | Today shows a fixed set. Widens item 32 (which habits show) to the whole page; plan the two together, and decide whether a hidden item is also hidden elsewhere (Habits page, Sheet) or only on Today | M |

## 1. Quick wins (small, used every day)

| # | Idea | Why | Size |
|---|---|---|---|
| 1 | Show "Saving..." and disable the Log button while the AI reads the message | A message waits a few seconds for the AI with no sign that anything is happening. The idempotency key already stops a double save, but you cannot see that it is working | S |
| 2 | Keep the typed text when nothing was saved | When the AI does not answer ("Nothing was saved; try again"), the box comes back empty and the message has to be typed again. Put it back in the box (the "Correct it" button already does this) | S |
| 3 | Stay on the page you logged from | The entry box is on every page, but every message sends you back to Today (`/web/log` always redirects to `/web/`) | S |
| 4 | An Undo button on the reply after "Saved." | The chat can undo the last entry; the web reply cannot. `commands._undo` already exists | S-M |
| 5 | Change a food entry straight from Today | Today's food rows are read-only; a change means going to Food, finding the day and opening the entry. Link each row to its edit panel on the Food log | S |
| 6 | Make the web app behave like an iPhone app (a progressive web app): its own icon on the home screen, opening full-screen without Safari's bars. Needs: an app icon set and favicon; a web app manifest (name, colours, "standalone"); the iPhone meta tags (apple-touch-icon, status bar); `manifest-src 'self'` in the content security policy (today `default-src 'none'` blocks it); the files served under `/web/static/` (Caddy passes only `/` and `/web/*`, and `web.py` serves only `app.css` and `app.js`). Best together with items 7 to 9 (chart values by tap, a shorter phone menu, bigger tap targets). Asked for 28.10.2026, not now. Plan in the doc first, with a small icon design | Added to the iPhone home screen today, the app gets a generic icon and may open inside a Safari-style tab. Limits to state in the plan: no push notifications (the app is only on the home network and tailnet), it opens only on home Wi-Fi or with Tailscale on, the home-screen app keeps its own 30-day sign-in | S-M |
| 7 | Tap to see a chart value on the phone | Chart values show only on mouse hover (SVG `<title>`), so on the phone a point cannot be read | S-M |

## 2. Phone layout

| # | Idea | Why | Size |
|---|---|---|---|
| 8 | A shorter menu on the phone: Today, Food, Health, Ideas and "More" (Questions with its badge, Private, Labs, Usage); or a bottom tab bar | Eight links wrap into two or three rows above the page on a narrow screen | S-M |
| 9 | Bigger tap targets for the small buttons (the small Save buttons, icons, chips) | Several buttons are about 24 px high; about 44 px is the usual minimum for touch | S |

## 3. New views of data you already have

| # | Idea | Why | Size |
|---|---|---|---|
| 10 | Food trends chart on the Food log: kcal and protein per day against the target over 7 / 30 / 90 days, with the weekly average | The Food log date range is a table only; the Sheet has charts, the web app has none for food | M |
| 11 | Target lines on the Health charts (BP 130/80 and 140/90, sugar 100 and 126, sleep 7 h and 6 h, the Sheet's colours) | Left out on 26.10. "for now"; the limits are already decided for the Sheet | S |
| 12 | A Weekly summary page like the Sheet's: averages against targets, habits, weight change, sleep, top foods | One place for the week without opening the Sheet | M-L |
| 13 | One search box for everything (food entries, thoughts, labs, tasks; private entries only on the Private page) | Today each page has its own filter or search | M |

## 4. Settings and editing on the web (instead of NocoDB)

| # | Idea | Why | Size |
|---|---|---|---|
| 14 | A Settings page: daily food targets (`food_targets`), checklist habits (add, rename, daily count, retire; which ones show on Today is item 32) | These can only be changed in the database today | M-L |
| 15 | Todoist: set or change a due date | The page shows due dates but cannot change them | S-M |
| 16 | Labs: paste a lab report or upload a PDF on the Labs page (the chat's `lab_report`, shown first, then Yes) | Lab reports can only come in through the chat | M |
| 17 | Barcode from the phone camera on the Catalogue | Left for later on 26.10.; typing 13 digits on the phone is slow | M |
| 18 | Link thoughts to each other | Receipts already show v1's "link candidates", but linking is not built | M |

## 5. Backlog already agreed (HANDOVER section 6)

| # | Idea | Size |
|---|---|---|
| 19 | Date picker in the Private page's Gratitude tab (`privatepage._gratitude` already takes `day=`) | S |
| 20 | The chat saving a usual meal (plan in the doc first) | M |
| 21 | An exchange log of the chat, with a web page to read it (plan in the doc first) | M |

## 6. Safety and housekeeping (under the hood)

| # | Idea | Why | Size |
|---|---|---|---|
| 22 | A list of signed-in devices with "sign out this device" and "sign out all others" | A session lasts 30 days; a lost phone stays signed in until then | S-M |
| 23 | Lock out failed sign-ins per device, not for everyone | After too many failed attempts the sign-in is locked for every device, so anyone on the home network or tailnet could lock you out for a while | S |
| 24 | Run page reads and the session check off the server's main loop | While a page is built, a chat message waits. Fine for one user; matters more once Telegram comes | S |
| 25 | One helper for the 12 copies of "app host only, signed in, else to the login" in `web.py` | Less code, and a new page cannot forget a check | S |
| 26 | A friendly "not found" page and an error page inside the app's layout | Unknown `/web/` paths show a plain "Not Found" | S |
| 27 | Small accessibility fixes: a label on the main menu, a "skip to content" link | Screen readers and keyboard use | S |

## 7. Cost (outside the web app, found 27.10.)

| # | Idea | Why | Size |
|---|---|---|---|
| 28 | Check that prompt caching works (it is switched on, but the audit log cannot show whether it is used; see `docs/CORE-IMPROVEMENTS.md` items 3 to 6 for AI cost) | Each AI call sends about 7,000 input tokens, mostly the same prompt every time; input is about 98% of the tokens | S |
| 29 | Token counts for food lookups and lab reports on the Usage page | Known gap: only model and cost are recorded for them | S |

## 8. Open ideas after the 31.10.2026 rounds

Collected from the user's list of 30.10.2026 and the answers on 31.10.2026. Nothing here is started. Plan each one in the doc first (a clickable mockup for a new page).

| # | Idea | Notes | Size |
|---|---|---|---|
| 35 | Swipe left/right to change the day on Today and the Food log (phone) | The user answered "not needed" on 31.10.; parked. Today already has arrows and a date field | S |
| 36 | Sleep hours: from Bed to Woke up, on the Today Health card ("7 h 35 min"), as a Health page chart with a 7-day average (amber under 7 h, red under 6 h, like the Sheet), and on the Week page | User: "save it for the next version". The data exists (bed and wake events; the Health page already shows the bed time of the night before) | M |
| 37 | Mood and energy check-in: 1 to 5 buttons in a small card on Today and in chat ("mood 4, energy 3") | User: "next version". Undecided: which scales (mood and energy, or also stress), once in the evening or morning and evening. Would feed the Insights page and could be a nudge | M |
| 38 | Minutes for practices (Shoonya, meditation, study) as minutes, not only a tick | A twice-a-day count already works as a habit target | M |
| 39 | Activity: steps or walk minutes, typed or from the phone health export | The `health-export` channel already carried 23 messages | M |
| 40 | Water as its own daily target with a bar on Today | Drinks are logged already; needs a target per day | S |
| 41 | Weight trend: 7-day average, weekly change, an optional goal weight | Smooths day-to-day jumps; the Health chart already has a 7-day average line | S-M |
| 42 | Lab report upload with trends (item 16): photo or PDF read by Claude, shown to check, then saved | The label-photo flow (`labelread.py`, `webfood.label_add`) is the pattern: read, check, add | L |
| 43 | Medication schedule: dose and time with a reminder | The nudge code (`nudges.py`, web push) can carry it | M |
| 44 | A photo attached to a food entry (a memory aid when the amount was a guess) | Needs somewhere to keep photos (disk is at about 82%) | M-L |
| 45 | A daily or weekly digest, and an export of everything logged (CSV or JSON) | Same as core items 17 and 19 | M |
| 46 | One-tap usual meals: a tap logs the meal with its saved weights, the editor stays for changes | Today has "+ meal" buttons that open an editor first | S |
| 47 | Phone home-screen shortcuts that open straight at Log food or Weight | iOS limits what a web app can offer; check what works | S |
| 48 | "All look right" on the review box | One button for a whole list of items that are fine | S |
| 49 | More nudges: evening blood pressure (offered, not chosen), a question open for a day, a habit-specific time; Telegram as a second channel (core item 18) | Today's nudges: no weight, habits open, nothing logged for N hours, all counted from the wake-up time | S-M |
| 50 | Insights: more questions as the data grows (mood, steps, sleep once item 36 exists), and a longer range | Today's three cards need about 4 days in each group; the user's data had 1 day with a long gap after food | M |
| 51 | Voice without the browser's speech recognition (record on the phone, convert on the server) if Safari's recognition is missing in the home-screen app | Only if the microphone button does not show or does not work there | M-L |
| 52 | Coaching: "Coach me" on Today (any day of the date picker) and "Review this week" on the Week page, by Claude Opus 5.5 at high effort, on demand | The user's idea of 02.11.2026; details below. The chat can already coach when asked; this makes it one tap and keeps each coaching | M |

### 52. Coaching (02.11.2026, not started; plan in the doc and a clickable mockup first)

- **Day coach** (button on Today, for the day shown): what went well, what to improve, the next meal (from foods the user actually eats,
  fitted to the protein, carbs, fat and kcal left against that day's targets, and the time of day), and gaps in the log (no weight yet, ...).
- **Week review** (button on the Week page): the week against the targets, trends in weight, blood pressure, fasting sugar and sleep,
  habits kept and missed, best and hardest days, one or two focus points for next week. It may also read that week's daily coachings.
- **Model:** claude-opus-5-5, effort high (the user's choice), only when a button is pressed. Estimates, not measured: 20 to 60 s per
  coaching (show "Coaching..." while it works; the provider's 60 s timeout needs to be longer for this), about 5 to 20 cents a day coaching,
  10 to 40 cents a week review. Cost shown on the Usage page as its own kind (audit_log).
- **Saved:** each coaching kept in a new table (day or week, text, model, cost, time), shown again without a new call; "Coach me again" asks
  for a fresh one.
- **Health caution:** comment only on what was logged, no medical advice; say when something is worth raising with the doctor.
- **Goals:** a short "My goals" text in Settings that the coach reads every time (without it, it can only coach against the targets).
- **Undecided (ask the user):** (1) buttons on Today and Week, or a Coach page under More with all past coachings; (2) whether the coach sees
  thoughts and ideas (private and gratitude entries left out by default); (3) on demand only, or also a Sunday-evening week review as a
  notification (that would also cover the digest, item 45 / core 17); (4) the user's goals, or the Settings field for them.
- **Data to give it:** the day's (or week's) food entries by meal with macros and targets, habits, health values (weight, BP, sugar, bed and
  wake times), the last 7 days for context, the catalogue's frequently eaten foods for the next-meal suggestion.

## Suggested order

Replaced on 02.11.2026 by "Priorities" at the top (the old order, items 1 to 14, is all done).
