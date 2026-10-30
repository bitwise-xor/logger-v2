-- Habits page (30.10.2026): every habit can be tracked or not (log_types.on_checklist), and a habit that was never used can be deleted.
-- Wake-up is a time (it starts the logging day and drives the sleep), not a tick-box habit: it leaves the checklist. The Health card and
-- the Health page keep recording it.
GRANT UPDATE (on_checklist) ON log_types TO core_rw;
GRANT DELETE ON log_types TO core_rw;
UPDATE log_types SET on_checklist = false WHERE name = 'wake';
