-- Web app devices (29.10.2026): the list of signed-in devices with "sign out", and a sign-in lockout that a stranger's
-- failed attempts cannot use against the owner's own devices.
--   web_sessions.sid         a public handle for a session (the token hash itself is never shown or sent)
--   web_sessions.user_agent  what the browser called itself, for the list ("iPhone, Safari")
--   web_sessions.device_hash the device cookie the session was made with
--   trusted_devices          devices that signed in once (hash of a long random cookie); their failures are counted per device,
--                            every other failed attempt goes into one shared pool (device_hash NULL), as before
ALTER TABLE web_sessions ADD COLUMN sid uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE web_sessions ADD COLUMN user_agent text;
ALTER TABLE web_sessions ADD COLUMN device_hash text;
CREATE UNIQUE INDEX web_sessions_sid_key ON web_sessions (sid);

ALTER TABLE login_failures ADD COLUMN device_hash text;
CREATE INDEX login_failures_device_idx ON login_failures (device_hash, at);

CREATE TABLE trusted_devices (
  device_hash text PRIMARY KEY,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_seen   timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON trusted_devices TO core_rw;
