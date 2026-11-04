-- Home page round 2, step B (04.11.2026; plan and the owner's answers in the doc "Logger v2: current state and plans", tab Current).
-- A whole-flat chore (Dust, Vacuum, Mop) gets settings per zone next to zone_days (its interval there): zone_minutes ({"<zone id>": 5},
-- blank = the chore's minutes) and zone_off ([<zone id>, ...]: not done there, e.g. a Maintenance zone). A done row can be a starting
-- point (source 'start': "When did you last do these?" on Zones; it gives the job an age but is not a session, not under Recently done,
-- not in coaching) and can be dated (a job done earlier: the date is what counts, the time is 12:00, so History shows the date only).
ALTER TABLE home_chores ADD COLUMN zone_minutes jsonb NOT NULL DEFAULT '{}',
                        ADD COLUMN zone_off jsonb NOT NULL DEFAULT '[]';

ALTER TABLE home_done DROP CONSTRAINT home_done_source_check;
ALTER TABLE home_done ADD CONSTRAINT home_done_source_check CHECK (source IN ('web', 'chat', 'pick', 'start'));
ALTER TABLE home_done ADD COLUMN dated boolean NOT NULL DEFAULT false;
