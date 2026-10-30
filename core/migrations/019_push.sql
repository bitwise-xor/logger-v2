-- Nudges as phone notifications (31.10.2026, nudges.py, webpush.py): the devices that turned notifications on (a browser's
-- push address and keys) and which nudge went out on which logging day (each at most once a day).
CREATE TABLE push_subscriptions (
  id          bigserial PRIMARY KEY,
  endpoint    text NOT NULL UNIQUE,
  p256dh      text NOT NULL,
  auth        text NOT NULL,
  user_agent  text,
  session_sid uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_ok     timestamptz,
  failures    integer NOT NULL DEFAULT 0
);
CREATE TABLE push_sent (
  day     date NOT NULL,
  kind    text NOT NULL,
  sent_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, kind)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON push_subscriptions, push_sent TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE push_subscriptions_id_seq TO core_rw;
