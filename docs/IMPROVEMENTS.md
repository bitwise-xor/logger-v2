# Web app: improvements and feature ideas

Written 27.10.2026 from a read of the web app's code (`core/app/web*.py`, the page modules, `static/app.css`,
`static/app.js`) and the plan doc "Logger v2: status and web app plan". The pages were not opened in a browser for
this list, so layout points are read from the stylesheet, not seen. Nothing here is decided: each item is an idea.
Before building one, plan it in the doc first (with a clickable mockup for a new page).

Size: S = about an hour, M = about half a day, L = a day or more. Rough guesses.

## 0. Your additions (28.10.2026)

| # | Idea | What is there now | Size |
|---|---|---|---|
| 30 | Easy definition of usual meals and usual drinks: a "New usual meal" form on the Usual meals tab where you pick foods or drinks and their amounts directly, with a Meal / Drink switch | A usual meal can only be made from a meal you already logged ("Save as usual meal" on the Food log). The database already knows usual drinks (`usual_meals.is_drink`), but the web app has no way to make one | M |
| 31 | A separate Habits page: today's habits as big one-click buttons, the calendar of the last 30 days (moved off the Health page), and a click on a past day's square to tick or untick that day | Habits are ticked on Today; the 30-day calendar is on the Health page and cannot be clicked | M |
| 32 | Choose which habits show on Today (add or remove them), and their order | Today shows every habit marked `on_checklist` in the database; changing that needs SQL. To decide in the plan: does "not on Today" also hide a habit from the Habits page and the Sheet's Habits tab, or only from Today? | S-M |
| 33 | Sort the food catalogue: most eaten (all time or last 30 days), last eaten, date added, kcal per 100 g, protein per 100 g, name | The catalogue is always sorted by most eaten (all time), then by name; the filters (All, Drinks, Added in the last 14 days, Needs review) cannot be combined with another order | S |

## 1. Quick wins (small, used every day)

| # | Idea | Why | Size |
|---|---|---|---|
| 1 | Show "Saving..." and disable the Log button while the AI reads the message | A message waits a few seconds for the AI with no sign that anything is happening. The idempotency key already stops a double save, but you cannot see that it is working | S |
| 2 | Keep the typed text when nothing was saved | When the AI does not answer ("Nothing was saved; try again"), the box comes back empty and the message has to be typed again. Put it back in the box (the "Correct it" button already does this) | S |
| 3 | Stay on the page you logged from | The entry box is on every page, but every message sends you back to Today (`/web/log` always redirects to `/web/`) | S |
| 4 | An Undo button on the reply after "Saved." | The chat can undo the last entry; the web reply cannot. `commands._undo` already exists | S-M |
| 5 | Change a food entry straight from Today | Today's food rows are read-only; a change means going to Food, finding the day and opening the entry. Link each row to its edit panel on the Food log | S |
| 6 | App icon and home-screen support (web manifest, icons, favicon) | Added to the iPhone home screen, the app gets a blank icon and the browser bar. `/favicon.ico` is aborted by Caddy | S |
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
| 28 | Prompt caching for the parser prompts (check first whether it is on) | Each AI call sends about 7,000 input tokens, mostly the same prompt every time; input is about 98% of the tokens | S-M |
| 29 | Token counts for food lookups and lab reports on the Usage page | Known gap: only model and cost are recorded for them | S |

## Suggested order

1. Items 1, 2, 3 and 6 together as one small deploy.
2. The phone menu (8) and tap targets (9).
3. Food trends (10) and target lines (11).
4. The Settings page (14).
