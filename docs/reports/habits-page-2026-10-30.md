# Habits page: report of 30.10.2026

The Habits page now lists every habit of the database, with a Track switch, Edit, Retire / Bring back, Delete (never-used habits only) and a
New habit form. Wake-up is a time and no longer a habit. Planned in a clickable mockup (Claude Design canvas "Habits page mockup"), the owner
answered the questions in chat; build, deploy, commit and push were authorised in advance for this task.

## What changed

| Where | Change |
|---|---|
| `/web/habits` | New list **Your habits** between Today and the 30-day calendar: filters Tracked (default) / Not tracked / Retired / All (`?show=`), grouped by category (Intake, Practice, Learning, Daily routine, Behaviour, Other), per row: how often it was ticked in the last 30 days and the last day, a Track switch, Edit (name, times a day), Retire or Bring back, Delete (only when the habit has no events or measurements, after a yes). A **New habit** form (name, group, times a day, "Track it now") at the bottom, reachable from the "+ New habit" link at the top. The calendar draws tracked habits only. Settings > Habits stays as it was. |
| States | tracked = `on_checklist` and `active`; not tracked = `active` only (still loggable in the chat); retired = not `active` (hidden everywhere, history stays). Retire keeps `on_checklist`, so Bring back returns a habit to the state it had. |
| Wake-up and Bedtime | Not listed, cannot be changed by these forms (category `sleep`). Migration 017 takes Wake-up off the checklist, so it has no tick button on Today or the Habits page, is not in the Today page's habit list and not in the "x of y done" count. It stays an event type: it starts the logging day and drives the sleep. The Health card's **Woke up** row gets a **Now** button (fills in the current time; Save is still the yes). |
| Today page / Settings > Today page | No change in code: both read `on_checklist AND active AND kind = 'event'`, so a habit switched to Track appears on Today (last in the order) and in the Today tab's habit list; hidden/ordered choices are stored by habit name. The Week page and the chat's checklist follow the same flag. |
| Database | Migration 017: `GRANT UPDATE (on_checklist)` and `GRANT DELETE` on `log_types` to `core_rw`; `UPDATE log_types SET on_checklist = false WHERE name = 'wake'`. |
| Code | `habitspage.py` (list and forms), new `webhabits.py` (track, retire, restore, edit, delete, new; routes `/web/habits/<action>` in `web.py`), `app.css` (`.hm*`), `app.js` (the Now button), `todaypage.py` (Now button markup). |
| Tests | New `tests/test_webhabits.py`; `tests/test_websettings.py` adjusted (Wake-up is no longer a habit button). |

## Decisions taken without asking (neutral defaults)

- A new habit gets its lower-case name as an alias (`aliases = [label.lower()]`) so the chat can find it by that word; not verified against the parser with a real message.
- New habits go last in the global event order; a habit switched to Track goes last on Today.
- Editing a habit here changes name and times a day only; the order is still set in Settings (Habits tab or Today page).
- The Sleep group is not offered for new habits (sleep times are recorded, not ticked).
- The Google Sheet was left alone (owner's decision); no Sheet script mentions `on_checklist`.

## Not verified

- A click-through in a real browser on the owner's data, and the look on the phone (CSS written from the mockup).
- Whether the Sheet's Habits tab still looks the same (it should: nothing there reads `on_checklist`).
- Whether the chat's "what's left today" no longer lists Wake-up (it follows `checklist_status`, which reads the flag).

## Rollback

`docker tag logger-v2-core:trial-fixes24 logger-v2-core:pilot`, `docker compose up -d core` in `/opt/stacks/logger-v2`. Migration 017 only adds
two rights and clears one flag; an older image runs on it (Wake-up then has no tick button but keeps its Health row). To get the
tick button back: `UPDATE log_types SET on_checklist = true WHERE name = 'wake'`. The dump taken before the deploy is
`private-dumps/v2-live-before-habits-page-20261030.dump`.
