-- Web app: the reply to the last action, shown once on the next page (post, then redirect, then show).
BEGIN;

ALTER TABLE web_sessions ADD COLUMN flash jsonb;

COMMIT;
