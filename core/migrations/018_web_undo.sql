-- Undo for the web app's buttons (31.10.2026): the last change made with a button on a signed-in device, kept until that
-- device's next action (web.py done, webundo.py): the rows as they were before a delete or a correction, or the message to undo.
-- core_rw's table-wide grants on web_sessions cover the new column.
ALTER TABLE web_sessions ADD COLUMN undo jsonb;
