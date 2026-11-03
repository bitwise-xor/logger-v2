# Home page: design spec for the build

Design handover (02.11.2026) for a Claude Code build session. Read `README.md`, `docs/DESIGN.md` and the Gym parts of `docs/IMPROVEMENTS.md` for the app, its rules and constraints. This spec is the "what" and "why"; Claude Code works out the "how", plans first and builds after the owner approves.

**Mockup:** four phone screens in `docs/specs/home-page-mockup/`: `Main.png` (Home · Now), `Pick.png` (15-minute pick), `Zones.png`, `Today.png` (Today card). The `.dc.html` files next to them are the design-canvas sources (they need the canvas runtime to render; read them for exact copy and spacing). Approved 02.11.2026. Ages, dates and the habit chips on the screens are sample values.

## 1. The problem

- Household chores slip between clean-ups: things are not put back and cleaning does not happen on a rhythm. Dusting, vacuuming and mopping can go undone for months, so there is a backlog that makes starting feel hard.
- Tasks must come in manageable chunks so the page never feels overwhelming.
- So the page must cope with "never done" and "months ago" calmly, pick a job for the user, and treat the backlog as short timeboxed sessions, never a "big clean first".

## 2. Principles

- Care tasks are morally neutral. No red, no overdue counts, no streaks that break. Ages are shown as plain text ("not logged yet", "3 months ago").
- Chores are not habits: they have an interval and a "last done", never the habits' "N of the last 30 days" count.
- Chunks: a time budget (5, 15, 30 min, 1 hour) and one job at a time. Stopping when the timer ends counts.
- Repeating beats re-entering; one tap to mark done; Undo bar as on every page.
- No AI cost: chat logging of chores uses a server-side word match.
- Leave out room ratings and photo logs (effort).

## 3. Where it lives

- Its own **Home** page in More, next to Gym (decided 02.11.2026). Tabs: **Now**, **Zones**, **History**.
- A small **Home card on Today**, shown or hidden via "Customize this page".
- The **evening reset** is an ordinary habit in "Daily routine" (no code; the owner can add it now). It is not on the Home page.
- One-off organizing jobs ("give things homes", zone by zone; sorting storage) go to Todoist, not the Home page.

## 4. Screens (as in the mockup)

### 4.1 Home · Now
1. **"How much time have you got?"** Time chips 5 min, 15 min (default), 30 min, 1 hour, and a button **"Pick a job for me"** that opens the pick screen.
2. **This week:** "N of 3 sessions" with a simple bar, "Last 4 weeks: 2 · 0 · 1 · 1". The weekly goal is a setting (default 3).
3. **Longest since last done:** the top 6 chores, each with a tick circle, "Name · Zone", "age · interval" and an estimated time. "Show all N chores" opens Zones.
4. **Recently done:** last few chores with date and time (dd.mm. HH:MM).
5. The usual "Log something" bar and bottom bar.

### 4.2 Pick ("Your 15 minutes")
- One job: zone chip, age chip, a title ("Dust the sleeping area"), 2 to 5 short steps, one line of how-to.
- A countdown timer for the chosen budget: Start, Pause, Resume; at zero "Time is up · well done".
- Buttons: **Done** (marks the chore or zone done, back to Now), **Another job** (next candidate), **Not today** (hides this job until the next day).
- "Also fits in N minutes": two more candidates.
- Stopping when the timer ends counts as a session, even if the job is unfinished.

### 4.3 Zones
- **Whole flat** card: Dust, Vacuum, Mop. Each has an interval, an **"All done"** button (resets every zone) and 8 zone tiles (Bath, Entrance, Dressing, Kitchen, Desk, Sleeping, Laundry, Storage) showing each zone's age. Tapping a tile marks that zone done. Recently done tiles are tinted green.
- **Zone cards**: each zone with its chores (tick circle, name, "interval · age"). Long zones collapse to a row ("2 chores · oldest not yet ›"). Storage shows "sorting jobs in Todoist".
- **Edit zones**: add, rename, retire zones and chores; set interval, estimated minutes, steps and other words. Like the Gym split editor. Retired, never deleted.

### 4.4 Today card
- "Home · this week N of 3", "Waiting longest: <chore · zone>", buttons **15 minutes** (opens Pick with 15 min) and **Open Home**.

## 5. Rules

- **Order ("longest since last done"):** by how far past its interval a chore is (time since last done ÷ interval). Chores never logged count as most overdue; among those, shorter intervals first. No colour for overdue.
- **Flat-wide chores (dust, vacuum, mop):** each zone is tracked on its own. "All done" stamps every zone; a zone tick stamps one zone. In lists the chore appears as "Dust · <oldest zone>". Natural order on a long day: dust, vacuum, mop.
- **Pick:** candidates whose estimated minutes fit the budget, ordered as above; skip jobs marked "Not today" since the last wake-up; "Another job" moves down the list. A 1-hour budget may offer a zone session (several chores in one zone) instead of one chore.
- **Session (for the weekly goal):** proposal: a day with at least one chore done or a pick timer that ran to the end. Confirm with the owner (open question 1).
- **Days:** use the logger's day rules (health day, wake-up based) so late-night chores land on the right day.
- **Chat logging:** "vacuumed the kitchen", "dusted everything", "cleaned the bathroom", "did laundry" resolve by chore name, other words and zone names, server-side, no AI. "everything" / "whole flat" stamps all zones. Unclear matches ask (needs_choice), like food names. Receipts show date and time. Messages are stored verbatim in `raw_inbox` first, as always.
- **Undo** after every tick; ticks can be removed from History.
- **Nudges:** not in the first build. Later, tie any nudge to bedtime, never a clock time.

## 6. Data (suggested; Claude Code decides)

- Zones (name, order, retired).
- Chores (name, zone or flat-wide, interval in days, estimated minutes, steps, other words, order, retired).
- Done records (chore, zone for flat-wide chores, done at, source: web, chat or pick, raw id when from chat).
- Skips ("not today": chore, until).
- Weekly goal setting.
- Additive migrations only; sandbox test, backup, install, restore test (brief section 4). Private tier untouched. Include the new tables in the Google Sheet copy and backups like other data.

## 7. Seed list (all floors are hard floors; editable on the page)

| Zone | Chore | Interval |
|---|---|---|
| Whole flat (by zone) | Dust | every 2 weeks |
| Whole flat (by zone) | Vacuum | weekly |
| Whole flat (by zone) | Mop | every 2 weeks (kitchen and bathroom weekly) |
| Bathroom | Sink and toilet | weekly |
| Bathroom | Bathtub | every 2 weeks |
| Bathroom | Window | every 3 months |
| Entrance | Tidy shoe rack and coats | monthly |
| Entrance | Wipe drawers and shelves | every 3 months |
| Dressing area | Tidy closet | monthly |
| Kitchen | Cooking area and stove | weekly |
| Kitchen | Scrub sink | weekly |
| Kitchen | Shelves and cabinet fronts | monthly |
| Kitchen | Pantry check (can add to the shopping list) | monthly |
| Kitchen | Sort cookware and utensils | every 3 months |
| Workstation | Clear and wipe desk | weekly |
| Workstation | Dust PC and electronics | monthly |
| Sleeping | Change bedding | every 2 weeks |
| Sleeping | Window | every 3 months |
| Laundry | Do laundry (wash, dry, put away) | weekly |
| Laundry | Clean machine (drum, seal, filter) | every 2 months |
| Storage | none recurring | — |

Spots per zone (for steps): Bathroom sink, bathtub, toilet, shelves, radiator, floor, window, window sill. Entrance shoe rack, coat hanger, floor, drawers, shelves. Dressing area closet. Kitchen cooking area, sink, chopping area, shelves, utensils cabinet, pantry shelves, pantry jars and bottles, cookware storage. Workstation desk, chair, bookshelf, PC, electronics. Sleeping bed, lamp, radiator, window, window sill, bedding. Laundry machine, detergent and laundry supplies, storage cabinet. Storage random shelves, boxes, old and seldom-used things.

Estimated minutes are not set yet; start with 10 to 20 and let the owner edit them.

## 8. Out of scope for the first build

Nudges, room ratings, photos, a tidy-home Insight (later: home sessions vs mood and energy), pantry check writing to the shopping list automatically (later), any AI call.

## 9. Open questions for the build session

1. What counts as a session for the weekly goal (proposal in section 5)?
2. Weekly goal default: 3 sessions?
3. Should History be a tab or just a "Recently done" list plus Undo for the first build?
4. Where exactly the Today card sits on Today (after Habits is the mockup's guess).

## 10. Paste-ready lines for Claude Code

- "Read `docs/specs/home-page-spec.md` and look at the PNGs in `docs/specs/home-page-mockup/`. Plan a Home page as specified (Now, Zones, pick with timer, Today card, chat logging by word match). Plan first, ask me the open questions in section 9, and build only after I approve."
- "Follow the Gym page's patterns and the app's constraints: server-rendered, one stylesheet, one script, strict CSP, additive migrations, sandbox test first."
