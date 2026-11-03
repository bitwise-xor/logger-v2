# Body measurements mockups

Phone mockups (390 px wide, rendered at 2x) for `docs/specs/body-measurements-spec.md`. All numbers are invented examples. Dashed boxes stand for existing parts of the page that do not change.

| Image | Shows | Spec section |
|---|---|---|
| `health-card.png` | Health page: Body measurements card (table Now / Last / Start, chart with one chip per measure, waist ÷ height), "Or type it" hint | 6 |
| `health-card-form.png` | Same card with the Measure form open (protocol text, five fields, last value as placeholder) | 6 |
| `health-card-check.png` | Form with the read-back for a jump of 4 cm or more ("Yes, save" / "Fix it") | 6, 8 |
| `today-due.png` | Today: "Body measurements due" line in the To review card, Measure / Not today | 7 |

`source/` holds the Design canvas files (`.dc.html`) the images were rendered from. They need the claude.ai Design canvas runtime to run and are kept for reference only; the app's own pages follow the app's constraints (server-rendered, one stylesheet, one script, strict CSP), not this markup.

The images show layout and wording; the spec wins where they differ.
