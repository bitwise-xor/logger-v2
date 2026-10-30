"""Web push for the nudges (31.10.2026): a message is encrypted for one browser (RFC 8291, aes128gcm), signed with the app's
VAPID key (RFC 8292, env VAPID_PRIVATE_KEY: the private number, base64url) and posted to that browser's push service
(Apple, Google, Mozilla, Microsoft; nothing else is ever posted to). Only the cryptography and PyJWT packages the app
already has are used."""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import APP_HOST

SERVICES = ("push.apple.com", "googleapis.com", "mozilla.com", "mozaws.net", "notify.windows.com")
RECORD_SIZE = 4096


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _point(key) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _private():
    raw = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    return ec.derive_private_key(int.from_bytes(unb64u(raw), "big"), ec.SECP256R1()) if raw else None


def public_key() -> Optional[str]:
    """The applicationServerKey a browser subscribes with (None when the server has no key)."""
    k = _private()
    return b64u(_point(k.public_key())) if k else None


def allowed(endpoint: str) -> bool:
    """Only https addresses of the known push services."""
    u = urlsplit(str(endpoint))
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == s or host.endswith("." + s) for s in SERVICES)


def valid_keys(p256dh: str, auth: str) -> bool:
    try:
        pub, secret = unb64u(p256dh), unb64u(auth)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)
    except (ValueError, TypeError):
        return False
    return len(pub) == 65 and len(secret) == 16


def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """One aes128gcm record for the browser's key (p256dh) and secret (auth), with a new key pair and salt each time."""
    ua_pub, secret = unb64u(p256dh), unb64u(auth)
    as_key = ec.generate_private_key(ec.SECP256R1())
    as_pub = _point(as_key.public_key())
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hkdf(secret, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02: the last (only) record
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_pub)]) + as_pub + body


def vapid(endpoint: str) -> str:
    u = urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": f"https://{APP_HOST}"}
    return f"vapid t={jwt.encode(claims, _private(), algorithm='ES256')}, k={public_key()}"


def send(sub: dict, message: dict, ttl: int = 3600) -> int:
    """Post one message to one subscription. The push service's status: 201 taken, 404/410 the subscription is gone;
    0 when nothing could be sent (no key, an unknown service, no network)."""
    if not allowed(sub["endpoint"]) or not _private():
        return 0
    data = encrypt(json.dumps(message).encode(), sub["p256dh"], sub["auth"])
    req = urllib.request.Request(sub["endpoint"], data=data, method="POST",
                                 headers={"Authorization": vapid(sub["endpoint"]), "TTL": str(ttl), "Urgency": "normal",
                                          "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - https to a known push service only (allowed)
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0
