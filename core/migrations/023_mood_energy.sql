-- Mood and energy check-in (02.11.2026, web item 37; plan and the owner's answers in the doc "Logger v2: status and web
-- app plan", section "Mood and energy check-in"): two measurements from 1 to 5, once a day in the evening, from the card on
-- Today or typed ("mood 4, energy 3"). Stored like weight, so the Health page, the Week page and the Sheet read them the same way.
INSERT INTO log_types (name, label, kind, category, unit, min1, max1, aliases, on_checklist, daily_target, active, sort_order, notes)
VALUES ('mood', 'Mood', 'measurement', 'daily', '/5', 1, 5, '{stimmung,laune}', false, 1, true, 13,
        '1 very low, 2 low, 3 okay, 4 good, 5 great; once a day, in the evening'),
       ('energy', 'Energy', 'measurement', 'daily', '/5', 1, 5, '{energie}', false, 1, true, 14,
        '1 drained, 2 tired, 3 okay, 4 good, 5 full of energy; once a day, in the evening')
ON CONFLICT (name) DO NOTHING;
