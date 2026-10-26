-- Web app sign-in: one row per signed-in device. Only the hash of the cookie value is stored.
BEGIN;

CREATE TABLE web_sessions (
  token_hash text PRIMARY KEY,
  csrf       text NOT NULL,                       -- checked on every form post
  created_at timestamptz NOT NULL DEFAULT now(),
  last_seen  timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);

GRANT SELECT, INSERT, UPDATE, DELETE ON web_sessions TO core_rw;

COMMIT;
