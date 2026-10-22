-- OAuth for the connector: one owner, registered clients, short-lived requests and codes, hashed tokens.
BEGIN;

CREATE TABLE owner_credentials (
  id             int PRIMARY KEY CHECK (id = 1),   -- exactly one owner
  password_hash  text NOT NULL,                    -- scrypt$n$r$p$salt$hash
  totp_secret    text NOT NULL,                    -- base32; needed to verify codes
  last_totp_step bigint NOT NULL DEFAULT 0,        -- a code works once
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE oauth_clients (
  client_id  text PRIMARY KEY,
  info       jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- An /authorize request waiting for the owner to log in.
CREATE TABLE oauth_requests (
  id         text PRIMARY KEY,
  client_id  text NOT NULL,
  params     jsonb NOT NULL,
  expires_at timestamptz NOT NULL
);

CREATE TABLE oauth_codes (
  code_hash  text PRIMARY KEY,
  data       jsonb NOT NULL,
  expires_at timestamptz NOT NULL
);

CREATE TABLE oauth_tokens (
  token_hash text PRIMARY KEY,
  kind       text NOT NULL CHECK (kind IN ('access','refresh')),
  client_id  text NOT NULL,
  scopes     text[] NOT NULL,
  resource   text,
  expires_at timestamptz NOT NULL
);

CREATE TABLE login_failures (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, UPDATE ON owner_credentials TO core_rw;
GRANT SELECT, INSERT, DELETE ON oauth_clients, oauth_requests, oauth_codes, oauth_tokens TO core_rw;
GRANT SELECT, INSERT, DELETE ON login_failures TO core_rw;
GRANT USAGE, SELECT ON SEQUENCE login_failures_id_seq TO core_rw;

COMMIT;
