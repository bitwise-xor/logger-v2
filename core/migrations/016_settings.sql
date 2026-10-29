-- Settings of the web app (29.10.2026): which parts of the Today page show and in what order (ui_settings), and the Settings
-- page's rights to add a habit and to change a habit's label, daily count, order and whether it is active (log_types).
CREATE TABLE ui_settings (
  key        text PRIMARY KEY,
  value      jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON ui_settings TO core_rw;

GRANT INSERT ON log_types TO core_rw;
GRANT UPDATE (label, daily_target, active, sort_order) ON log_types TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE log_types_id_seq TO core_rw;
