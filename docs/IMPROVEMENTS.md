# Web app: improvements and feature ideas

Written 27.10.2026 from a read of the web app's code (`core/app/web*.py`, the page modules, `static/app.css`,
`static/app.js`) and the plan doc "Logger v2: status and web app plan". The pages were not opened in a browser for
this list, so layout points are read from the stylesheet, not seen. Nothing here is decided: each item is an idea.
Before building one, plan it in the doc first (with a clickable mockup for a new page).

Size: S = about an hour, M = about half a day, L = a day or more. Rough guesses.

## Status (29.10.2026, end of the first build run; details in `docs/reports/web-app-2026-10-29.md`)

- **Done and deployed:** 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 19, 22, 23, 26 (the unknown-page screen; the error page is not done), 27 (titles, skip link, labelled menus), 30, 31, 32, 33, 34.
- **Open:** 16 (lab upload on the web), 17 (barcode by camera), 18 (link thoughts): no answers yet. 25 (one helper for the host check) and the error page of 26 are code tidying, not started.
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

## Suggested order

1. Items 1, 2, 3 and 6 together as one small deploy.
2. The phone menu (8) and tap targets (9).
3. Food trends (10) and target lines (11).
4. The Settings page (14).
