-- Pilot additions (additive): pending questions, idempotency keys, audit log.
BEGIN;

CREATE TABLE pending (
  id          text PRIMARY KEY,                 -- 'p' + random hex
  tool        text NOT NULL,
  payload     jsonb NOT NULL,
  reason      text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  resolved_at timestamptz,
  resolution  text CHECK (resolution IN ('confirmed','cancelled'))
);

CREATE TABLE idempotency_keys (
  key          text PRIMARY KEY,
  tool         text NOT NULL,
  request_hash text NOT NULL,
  response     jsonb NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Who called what, with which outcome. No health values here, only ids.
CREATE TABLE audit_log (
  id      bigserial PRIMARY KEY,
  at      timestamptz NOT NULL DEFAULT now(),
  tool    text NOT NULL,
  outcome text NOT NULL,
  client  text NOT NULL,
  detail  jsonb NOT NULL DEFAULT '{}'
);

GRANT SELECT, INSERT, UPDATE ON pending TO core_rw;
GRANT SELECT, INSERT ON idempotency_keys, audit_log TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO core_rw;

COMMIT;
