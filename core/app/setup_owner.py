"""Run by the owner in their own terminal: sets the password and the authenticator secret.
    docker compose exec core python -m app.setup_owner
Nothing is printed except the authenticator secret, in that terminal."""
import getpass
import sys
import urllib.parse

from . import auth, migrate
from .db import connect


def main() -> int:
    migrate.run()
    pw = getpass.getpass("New password (at least 14 characters): ")
    if len(pw) < 14:
        print("Too short."); return 1
    if getpass.getpass("Repeat password: ") != pw:
        print("Passwords differ."); return 1
    secret = auth.new_totp_secret()
    uri = "otpauth://totp/" + urllib.parse.quote("Logger v2:owner") + "?" + urllib.parse.urlencode({"secret": secret, "issuer": "Logger v2"})
    print("\nAdd this to your authenticator app (type the secret in, or use the link):")
    print("  Secret:", secret)
    print("  Link:  ", uri)
    code = input("\nEnter the current 6-digit code from the app to confirm: ")
    step = auth.check_totp(secret, code, 0)
    if not step:
        print("Code did not match. Nothing saved."); return 1
    with connect(owner=True) as c:
        c.execute("""INSERT INTO owner_credentials (id, password_hash, totp_secret, last_totp_step) VALUES (1,%s,%s,%s)
                     ON CONFLICT (id) DO UPDATE SET password_hash=EXCLUDED.password_hash, totp_secret=EXCLUDED.totp_secret,
                     last_totp_step=EXCLUDED.last_totp_step, updated_at=now()""", (auth.hash_password(pw), secret, step))
        c.execute("DELETE FROM oauth_tokens")
        c.execute("DELETE FROM web_sessions")
    print("Saved. All connector and web sessions were signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
