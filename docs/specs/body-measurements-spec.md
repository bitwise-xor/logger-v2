# Body measurements: build spec

Source: body-measurements design chat, 02.11.2026. Audience: Claude Code build session for this repo. Not written for humans; terse on purpose.

Read first: `README.md` and `docs/DESIGN.md` (rules and constraints). The owner keeps a longer app brief in the Claude project "Logger v2" (`claude/logger-app-brief.md`, not in this repo); "brief section N" below refers to it. If unavailable, the rules this spec relies on are restated where used. This spec = what and why. Claude Code = how. Plan first, ask the owner the open questions (section 12), build only after approval.

Status: designed, not approved. Owner is reviewing the mockup.
Mockup: images in `docs/specs/mockups/body-measurements/` (see its README: Health card, form, read-back, Today due line). Example numbers only. Original Design canvas "Body measurements mockup" is private to the owner on claude.ai. Sections 6 to 8 describe every element.

No personal health values in this spec, in code, in tests or in commits. Test fixtures use invented values.

## 1. Goal

- Track body shape change alongside weight, because weight alone is noisy and hides fat loss vs muscle gain.
- Constraint: low effort; a log that must be kept up is a chore.
- Effort budget: about 3 minutes every 4 weeks for tape measurements. Everything else is either already tracked, derived, imported from a device, or done rarely.

## 2. Scope by tier

| Tier | What | When |
|---|---|---|
| T1 | Core tape measurements (5), Health card, Today due line, chat/Log bar parsing, derived ratios | This build |
| T2 | Optional tape measurements (neck, calf), body-fat estimate, settings for interval and which measures show | This build if cheap, else next |
| T3 | Fitness tests (quarterly) as a Tests tab on Gym | After the gym routine is settled (owner decision 02.11.2026) |
| T4 | Device metrics via Apple Health import (resting HR, HRV, VO2max estimate, steps, smart-scale body fat) | With the Apple Health import (brief section 10), not this build |
| T5 | Labs (existing `lab_report` flow) | No code; list in section 10 is guidance only |
| Parked | Progress photos | Not planned; see section 11 |

## 3. Already tracked (do not duplicate)

weight (daily, 7-day average on Health), blood pressure (morning, evening), fasting blood sugar, bed and wake-up (sleep hours), mood and energy (evening check-in), food and macros vs targets, habits, gym sessions.

## 4. Metric catalogue

### 4.1 T1 core tape (cm, one decimal, every 28 days)

Register each as a `log_types` row (health v3 schema), stored in `measurements`. Do not add columns to the daily Entries table.

| key | label | landmark (show as hint in form) | plausible range cm | aliases (lowercase, server-side match) |
|---|---|---|---|---|
| waist | Waist | at the navel, after a normal breath out | 50–200 | waist, belly, tummy, waistline |
| hips | Hips | widest point of the buttocks | 60–200 | hips, hip, butt |
| chest | Chest | across the nipples, arms down | 60–200 | chest |
| upper_arm | Upper arm | halfway shoulder to elbow, relaxed, right side | 15–70 | arm, upper arm, bicep, biceps |
| thigh | Thigh | halfway hip to knee, standing, right side | 30–110 | thigh, leg |

Measurement protocol text (show once at the top of the form): "Morning, before eating, relaxed, tape level and snug. Right side for arm and thigh. Leave a field empty to skip it."

### 4.2 T2 optional tape (off by default; enable per measure in Settings)

| key | label | landmark | range cm | aliases | why |
|---|---|---|---|---|---|
| neck | Neck | just below the larynx, tape sloping slightly down to the front | 25–70 | neck | needed for the body-fat estimate (4.3) |
| calf | Calf | widest point, standing, right side | 20–70 | calf, calves | leg muscle change, cheap to add |

Do not add more tape sites (forearm, shoulders, wrist): low value for this owner's goals, more effort.

### 4.3 Derived (compute on read; never store; recompute when inputs change)

| key | formula | display | notes |
|---|---|---|---|
| waist_to_height | waist / height | 2 decimals; "under 0.5 is the common target" | needs `height_cm` setting (4.4). Hide if unset; show "Add your height in Settings". Best single metric for this owner. |
| waist_to_hip | waist / hips | 2 decimals, no target line | published cut-offs differ by sex; show the value only (open question 3) |
| body_fat_est | US Navy tape method | %, labelled "estimate" | needs height, waist, neck (+ hips in one variant). Formula has sex-specific variants: ask the owner which, or skip (open question 3). T2. |
| weight_rate | (7-day avg weight now − 7-day avg 28 days ago) / 4 | kg per week and % of body weight per week | shown on the Body card under the table; no colour judgement |
| bmi | weight 7-day avg / (height m)² | 1 decimal | low priority; show only if the owner wants it (open question 4) |

### 4.4 Settings additions

- `height_cm` (single value, range 120–230, dated so a later change does not rewrite history; reuse the dated-period pattern of `food_targets` if convenient).
- `measure_interval_days` (default 28, range 7–90).
- Which tape measures show in the form and card (T1 on by default, T2 off).

## 5. Input paths

All paths store the message verbatim in `raw_inbox` first (brief principle 1). Receipts show date and time.

1. **Health card form** (section 6). Save = the owner's yes, like the Entries table.
2. **Log bar and chat** via `log`: "waist 123.5", "waist 123.5 hips 128 chest 125 arm 41.5 thigh 72", "measured: waist 123,5 neck 44".
   - Parse deterministically on the server: tokens `<alias> <number>` with optional "cm", decimal comma or point. No Haiku/Sonnet call when every token matches (brief section 9 item 5: avoid escalation for simple values).
   - Unit other than cm ("in", "inch", `"`): convert to cm, say so in the receipt ("49 in = 124.5 cm"), round to 0.1.
   - Unknown token next to known ones: save the known, return needs_choice/needs_data for the rest. Never guess silently.
   - "arm" and "leg" map to right side; there is no left/right field.
3. **Today due line** links to the Health card with the form open.

## 6. Health page: Body measurements card (mockup `Main.dc.html`)

Position: after the existing charts (weight, BP, sugar, sleep, mood/energy), before the Entries table. Always shown; before the first measurement it shows an empty state ("No measurements yet" + Measure button).

Elements, top to bottom:
1. Title "Body measurements"; subline "Last measured dd.mm.yyyy · next due in about N weeks" (or "due now").
2. Button **Measure** (primary, ≥44 px) toggles the inline form.
3. Inline form: protocol text (4.1), one row per enabled measure: label, landmark hint, numeric input (`inputmode="decimal"`), placeholder = last value, unit "cm". Buttons Save, Cancel. Empty fields skipped. At least one value required ("Nothing to save. Fill at least one field.").
4. Table: columns `cm | Now | Last | Start`. Now = latest value; Last = change vs previous session; Start = change vs first session. Signs "+1.5", "−2.0", "±0". Neutral grey, no red/green (brief section 2: modest health claims; no judgement).
5. Chip row, one chip per enabled measure (Waist selected by default) + a small line chart of that measure over all sessions in the selected range (7/30/90 days selector already on Health; for measurements use all sessions if the range holds fewer than 2). Points labelled with value and dd.mm.
6. Derived lines (4.3): waist ÷ height; body-fat estimate if enabled; weight rate.
7. Hint card "Or type it" with the chat example (mockup shows it; optional in the build).

Undo bar after save, as on every page. Editing past values: tap a value in the table → same form pre-filled for that session; clearing a value deletes it (asked once), like the Entries table.

## 7. Today: due line (mockup `Today.dc.html`)

- Lives in the existing **To review** card. No new card.
- Shown when: days since the last session ≥ `measure_interval_days` AND a wake-up exists for the current health day (do not show before waking).
- Text: "Body measurements due" / "Last dd.mm. · N days ago · takes about 3 minutes". Never measured: "Body measurements · not measured yet".
- Buttons: **Measure** (opens Health with the form open), **Not today** (hide until the next health day).
- Disappears as soon as any measurement is saved.
- No push nudge in this build. If added later: tie to wake-up, not clock time (brief decision 31.10.2026 pattern).

## 8. Rules

- **Session:** all tape values in one health day (brief section 6: local time minus 5 h) form one session. Display dates per session.
- **Second value for the same measure in the same health day:** read-back ("Waist already 124.0 today. Replace?"), like a second weight.
- **Odd jump:** |new − last| ≥ 4 cm for any measure → needs_confirmation with read-back text ("Waist 118.0 cm is 6.0 cm away from last time (124.0). Right?"). Out of plausible range → needs_confirmation too. An AI never confirms for the owner.
- **Units:** store cm with one decimal.
- **Private tier:** untouched. Measurements are normal health data.
- **Sheet copy, backups, Week, Search, Recent:** include measurements like other `measurements` rows. Recent shows "Body measurements: waist, hips, … (5 values)".
- **Week page:** add one line only when a session falls in the period: "Measured dd.mm.: waist −1.5 cm since last". Nothing otherwise.
- **Insights:** none in this build. Later candidate: waist change vs average daily kcal over the same 4 weeks (patterns, not proof).

## 9. T3 fitness tests (later; design only, do not build now)

Gym page, new tab **Tests**. Every ~90 days. Same due-line pattern as section 7 but on the Gym page, not Today. Each test = `log_types` row.

| key | label | unit | protocol (hint text) | range |
|---|---|---|---|---|
| grip_right / grip_left | Grip strength | kg | hand dynamometer, standing, arm by side, best of 3 per hand | 5–120 |
| dead_hang | Dead hang | s | overhand grip on a bar, feet off the floor, until grip fails | 0–600 |
| farmer_carry | Farmer's carry | s + load kg | walk holding equal weights; record load and time | 0–600 |
| sit_to_rise | Sit-to-rise | score 0–10 | sit on the floor and stand without hands; start at 10, −1 per hand/knee/forearm support, −0.5 per wobble | 0–10 |
| one_leg_stand | One-leg stand, eyes closed | s | best of 3, either leg, stop at 60 | 0–60 |
| walk_test_1mile | 1-mile walk test | min:s + heart rate at finish | flat route, walk as fast as possible (Rockport test); app can estimate VO2max later | 8–40 min |

Do not add lifting maxes yet; Gym History already shows progression.

## 10. T4/T5 reference (no code here)

- T4 via Apple Health import (brief section 8, 31.10.2026): resting heart rate (daily), HRV (daily), VO2max estimate (watch), steps and active minutes, body fat % from a smart scale if one is bought (show as trend only; bioimpedance is noisy). Display on Health as trends; never typed.
- T5 via the existing `lab_report` flow, once or twice a year: HbA1c, fasting glucose, fasting insulin, lipid panel, ApoB, Lp(a) (once in a lifetime), ALT and GGT, hsCRP. Optional self-pay DEXA scan (body fat, visceral fat, bone density) as an imaging report. No new code; Labs page already shows them.

## 11. Out of scope

Progress photos (effort, storage, privacy; if ever: server only, never in the Sheet, never shown in Recent or Search), left/right sides, more tape sites, AI calls, push nudges, coaching text, any colour judgement of changes.

## 12. Open questions for the owner (ask in the build session)

1. Interval: 28 days ok?
2. Build T2 (neck, calf, body-fat estimate) now or later?
3. Waist-to-hip and the body-fat estimate have sex-specific reference values and formula variants. Which to use, or show waist ÷ height only?
4. Show BMI, yes or no?
5. Should the weight-rate line live on the Body card, the Week page, or both?
6. Card position on Health: after the charts (mockup), or first?

## 13. Data (suggested; Claude Code decides after reading the v2 schema)

- New `log_types` rows for 4.1 (and 4.2 if T2). Reuse `measurements`; add a session notion only if needed (health day + type may suffice).
- Settings: `height_cm` (dated), `measure_interval_days`, enabled measures.
- "Not today" skip: per health day, can reuse whatever Today uses for dismissals.
- Additive migrations only; sandbox test on the same Postgres major, backup, install, restore test (brief section 4 and 11).

## 14. Paste-ready lines for Claude Code

- "Read `docs/specs/body-measurements-spec.md` (and the Logger v2 project brief if you can reach it). Plan the T1 body measurements build (Health card, Today due line, deterministic chat parsing, derived waist ÷ height). Ask me the open questions in section 12. Build only after I approve."
- "Follow the existing Health page and Entries table patterns and the app's constraints: server-rendered, one stylesheet, one script, strict CSP, additive migrations, sandbox test first."
