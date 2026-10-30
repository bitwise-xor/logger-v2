"""Round 2 (31.10.2026): nudges as phone notifications (web push encryption and signing, the rules counted from waking up,
quiet hours, once a day, Settings > Notifications, the subscribe route, the service worker) and the entry box's microphone."""
from datetime import date, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app import nudges, webpush
from app.db import connect
from app.when import BERLIN
from conftest import owner_sql
from test_web import ORIGIN, act, web  # noqa: F401
from test_webhealth import event, measure

DAY = date(2027, 6, 10)  # later than every real message in the test database, so "nothing logged since" counts from waking
APPLE = "https://web.push.apple.com/QGuQyavXutnMH6mz"


def at(hm: str) -> datetime:
    return datetime.combine(DAY, datetime.strptime(hm, "%H:%M").time(), BERLIN)


def browser():
    """A browser's push keys: (private key, p256dh, auth)."""
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return k, webpush.b64u(pub), webpush.b64u(b"0123456789abcdef")


@pytest.fixture(autouse=True)
def push_clean(monkeypatch):  # (not named "clean": that would hide conftest's database reset)
    k = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv("VAPID_PRIVATE_KEY", webpush.b64u(k.private_numbers().private_value.to_bytes(32, "big")))
    wipe = ("DELETE FROM push_subscriptions", "DELETE FROM push_sent", "DELETE FROM ui_settings WHERE key = 'nudges'",
            f"DELETE FROM events WHERE day = '{DAY}'", f"DELETE FROM measurements WHERE day = '{DAY}'")
    for sql in wipe:
        owner_sql(sql)
    yield
    for sql in wipe:
        owner_sql(sql)


def due(now):
    with connect() as c, c.cursor() as cur:
        return {n["kind"] for n in nudges.due(cur, now)}


# ---- web push --------------------------------------------------------------------------------------

def test_a_message_is_encrypted_so_only_that_browser_can_read_it():
    key, p256dh, auth = browser()
    blob = webpush.encrypt(b'{"title": "Weight"}', p256dh, auth)
    salt, rs, n = blob[:16], int.from_bytes(blob[16:20], "big"), blob[20]
    as_pub, body = blob[21:21 + n], blob[21 + n:]
    assert rs == 4096 and n == 65
    shared = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))  # the browser's side (RFC 8291)
    h = lambda s, k, i, ln: HKDF(algorithm=hashes.SHA256(), length=ln, salt=s, info=i).derive(k)  # noqa: E731
    ikm = h(webpush.unb64u(auth), shared, b"WebPush: info\x00" + webpush.unb64u(p256dh) + as_pub, 32)
    plain = AESGCM(h(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(h(salt, ikm, b"Content-Encoding: nonce\x00", 12), body, None)
    assert plain == b'{"title": "Weight"}\x02'
    assert webpush.encrypt(b"x", p256dh, auth)[:16] != salt  # a new salt every time


def test_the_signature_names_the_push_service_and_checks_with_the_public_key():
    header = webpush.vapid(APPLE + "/x")
    token, k = header.removeprefix("vapid t=").split(", k=")
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), webpush.unb64u(k))
    claims = jwt.decode(token, pub, algorithms=["ES256"], audience="https://web.push.apple.com")
    assert claims["sub"].startswith("https://") and k == webpush.public_key()


def test_only_known_push_services_and_real_keys_are_accepted():
    _, p256dh, auth = browser()
    for ok in (APPLE, "https://fcm.googleapis.com/fcm/send/abc", "https://updates.push.services.mozilla.com/wpush/v2/x"):
        assert webpush.allowed(ok), ok
    for bad in ("http://web.push.apple.com/x", "https://evil.example/x", "https://push.apple.com.evil.example/x", "https://127.0.0.1/x"):
        assert not webpush.allowed(bad), bad
    assert webpush.valid_keys(p256dh, auth) and not webpush.valid_keys("abc", auth) and not webpush.valid_keys(p256dh, "short")


# ---- the rules -------------------------------------------------------------------------------------

def test_nothing_before_waking_up():
    assert due(at("09:30")) == set()


def test_weight_after_two_hours_awake_and_not_once_it_is_logged():
    event("wake", DAY, "07:00")
    assert due(at("08:30")) == set()
    assert due(at("09:30")) == {"weight"}
    measure("weight", DAY, "09:40", 82.4)
    assert due(at("09:45")) == set()


def test_habits_after_fourteen_hours_and_nothing_logged_for_five_hours():
    event("wake", DAY, "07:00")
    measure("weight", DAY, "07:30", 82.4)
    assert due(at("12:30")) == {"idle-0700"}  # nothing from the web app or Claude since waking
    assert due(at("21:30")) == {"idle-0700", "habits"}  # medication is not ticked


def test_quiet_after_bed_and_late_at_night_and_each_nudge_once_a_day():
    event("wake", DAY, "07:00")
    assert "weight" in due(at("10:00"))
    owner_sql("INSERT INTO push_sent (day, kind) VALUES (%s, 'weight')", DAY)
    assert "weight" not in due(at("10:05"))
    assert due(at("23:30")) == set()  # more than 16 hours after waking
    event("bed", DAY, "21:00")
    assert due(at("21:30")) == set()


def test_settings_turn_nudges_off_or_change_the_hours():
    event("wake", DAY, "07:00")
    nudges.save({**nudges.DEFAULTS, "weight_after": 4.0, "idle": False})
    assert due(at("10:00")) == set() and due(at("11:30")) == {"weight"}
    nudges.save({**nudges.DEFAULTS, "on": False})
    assert due(at("11:30")) == set()


def test_run_sends_to_every_device_drops_gone_ones_and_marks_the_day(monkeypatch):
    _, p256dh, auth = browser()
    for i, e in enumerate((APPLE, APPLE + "gone")):
        owner_sql("INSERT INTO push_subscriptions (endpoint, p256dh, auth) VALUES (%s, %s, %s)", e, p256dh, auth)
    calls = []
    monkeypatch.setattr(webpush, "send", lambda sub, msg: calls.append((sub["endpoint"], msg)) or (410 if sub["endpoint"].endswith("gone") else 201))
    event("wake", DAY, "07:00")
    out = nudges.run(at("09:30"))
    assert out == {"due": 1, "sent": 1, "devices": 2} and calls[0][1]["title"] == "Weight"
    assert [r["endpoint"] for r in owner_sql("SELECT endpoint FROM push_subscriptions")] == [APPLE]
    assert nudges.run(at("09:35"))["due"] == 0  # once a day


# ---- Settings, the subscribe route, the service worker, the microphone -----------------------------

def test_the_notifications_tab_has_the_key_and_the_nudges(web):
    page = web[0].get("/web/settings?tab=notify").text
    assert f'data-key="{webpush.public_key()}"' in page and "data-push-on" in page and 'name="weight_after"' in page
    assert 'aria-current="page">Notifications' in page and "Add to Home Screen" in page


def test_saving_the_nudges_keeps_hours_in_range(web):
    page = act(web, "/web/settings/nudges", on="1", weight="1", weight_after="2,5", habits_after="99", idle_hours="x", back="/web/settings?tab=notify")
    assert "Nudges are on." in page.text
    v = owner_sql("SELECT value FROM ui_settings WHERE key = 'nudges'")[0]["value"]
    assert (v["weight_after"], v["habits_after"], v["idle_hours"], v["habits"], v["idle"]) == (2.5, 20, 5.0, False, False)


def test_a_device_subscribes_with_the_csrf_header_only(web):
    c, csrf = web
    _, p256dh, auth = browser()
    body = {"endpoint": APPLE, "keys": {"p256dh": p256dh, "auth": auth}}
    assert c.post("/web/push/subscribe", json=body, headers=ORIGIN).status_code == 403
    assert c.post("/web/push/subscribe", json={**body, "endpoint": "https://evil.example/x"}, headers={**ORIGIN, "X-CSRF": csrf}).status_code == 400
    assert c.post("/web/push/subscribe", json=body, headers={**ORIGIN, "X-CSRF": csrf}).json() == {"ok": True}
    assert c.post("/web/push/subscribe", json=body, headers={**ORIGIN, "X-CSRF": csrf}).json() == {"ok": True}  # again: still one
    assert [r["endpoint"] for r in owner_sql("SELECT endpoint FROM push_subscriptions")] == [APPLE]
    pid = owner_sql("SELECT id FROM push_subscriptions")[0]["id"]
    assert "Notifications are off" in act(web, "/web/settings/push_off", id=str(pid)).text
    assert owner_sql("SELECT count(*) AS n FROM push_subscriptions")[0]["n"] == 0


def test_the_service_worker_is_served_under_web_and_the_microphone_waits_for_the_browser(web):
    r = web[0].get("/web/sw.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript") and "showNotification" in r.text
    page = web[0].get("/web/").text
    assert "data-mic hidden" in page and 'aria-label="Speak instead of typing"' in page
