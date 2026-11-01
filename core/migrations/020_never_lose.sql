-- Never lose a message (01.11.2026, core items 1 and 2; held.py): a typed message is kept here, word for word, before the
-- AI reads it, and deleted in the same transaction that saves what it said. When the AI or the save fails, it stays here
-- "not read yet" and is read again (automatically for 2 hours, or with Try again on the web app). It lives in the private
-- schema because a message may turn out to be private, and nobody knows that before the AI has read it: the Sheet's
-- reader and NocoDB never see this schema. last_error keeps only the kind of error, never the words.
CREATE TABLE private.incoming (
  id              bigserial PRIMARY KEY,
  received_at     timestamptz NOT NULL DEFAULT now(),
  channel         text NOT NULL,
  content         text NOT NULL,
  idempotency_key text,
  tries           integer NOT NULL DEFAULT 0,
  reading_since   timestamptz,  -- a reader has it now; after 10 minutes it counts as dropped (the container stopped mid-read)
  last_try_at     timestamptz,
  last_error      text,
  notified_at     timestamptz,  -- the phone was told once that it is still not read
  deleted_at      timestamptz   -- deleted on the web app (Undo brings it back); removed for good a day later
);
CREATE INDEX incoming_key ON private.incoming (idempotency_key) WHERE idempotency_key IS NOT NULL;
GRANT SELECT, INSERT, UPDATE, DELETE ON private.incoming TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE private.incoming_id_seq TO core_rw;
