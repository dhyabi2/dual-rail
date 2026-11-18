"""Ed25519 signature VERIFICATION with Nano's BLAKE2b, standard library only.

Nano uses Ed25519 as RFC 8032 specifies it with SHA-512 replaced by BLAKE2b-512.
The curve code is copied from nano-wallet-xno's `ed25519_blake2b.py`. `sign` is kept
only so the tests can prove `verify`; a seller's adapter never holds or needs a
private key.

`verify` is used for one thing: checking that whoever presents a payment controls
the account that made it (see binding.py).
"""
import hashlib

# Curve25519 / Ed25519 parameters (RFC 8032, section 5.1).
Q = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, Q - 2, Q) % Q
I = pow(2, (Q - 1) // 4, Q)


def _inv(x):
    return pow(x, Q - 2, Q)


def _edwards_add(p, q):
    x1, y1 = p
    x2, y2 = q
    t = D * x1 * x2 * y1 * y2 % Q
    x3 = (x1 * y2 + x2 * y1) * _inv(1 + t) % Q
    y3 = (y1 * y2 + x1 * x2) * _inv(1 - t) % Q
    return (x3, y3)


def _scalar_mult(p, e):
    if e == 0:
        return (0, 1)
    q = _scalar_mult(p, e // 2)
    q = _edwards_add(q, q)
    if e & 1:
        q = _edwards_add(q, p)
    return q


def _x_recover(y):
    xx = (y * y - 1) * _inv(D * y * y + 1)
    x = pow(xx, (Q + 3) // 8, Q)
    if (x * x - xx) % Q != 0:
        x = x * I % Q
    if x % 2 != 0:
        x = Q - x
    return x


_BY = 4 * _inv(5) % Q
_BX = _x_recover(_BY)
B = (_BX, _BY)


def _encode_point(p):
    x, y = p
    return ((y & ~(1 << 255)) | ((x & 1) << 255)).to_bytes(32, "little")


def _clamp(h32: bytes) -> int:
    a = int.from_bytes(h32, "little")
    a &= (1 << 254) - 8            # clear the low 3 bits, clear bit 255
    a |= 1 << 254                  # set bit 254
    return a


def _blake2b512(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=64).digest()


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def public_key_from_private(private_key: bytes, hashfn=_blake2b512) -> bytes:
    """Derive the 32-byte Ed25519 public key for a 32-byte private key.

    `hashfn` defaults to BLAKE2b-512 (Nano). Pass `_sha512` to reproduce
    stock RFC 8032 behaviour, which is how the tests prove the curve code.
    """
    if len(private_key) != 32:
        raise ValueError("private key must be 32 bytes, got %d" % len(private_key))
    a = _clamp(hashfn(private_key)[:32])
    return _encode_point(_scalar_mult(B, a))


def sign(message: bytes, private_key: bytes, public_key: bytes, hashfn=_blake2b512) -> bytes:
    """Ed25519 signature over `message`. 64 bytes."""
    h = hashfn(private_key)
    a = _clamp(h[:32])
    r = int.from_bytes(hashfn(h[32:64] + message), "little") % L
    R = _scalar_mult(B, r)
    R_enc = _encode_point(R)
    k = int.from_bytes(hashfn(R_enc + public_key + message), "little") % L
    s = (r + k * a) % L
    return R_enc + s.to_bytes(32, "little")


def _decode_point(enc: bytes):
    if len(enc) != 32:
        return None
    y = int.from_bytes(enc, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    if y >= Q:
        return None
    x = _x_recover(y)
    xx = (y * y - 1) * _inv(D * y * y + 1) % Q
    if (x * x - xx) % Q != 0:
        return None
    if x == 0 and sign:
        return None
    if (x & 1) != sign:
        x = Q - x
    return (x, y)


def verify(message: bytes, signature: bytes, public_key: bytes, hashfn=_blake2b512) -> bool:
    """True only for a valid Ed25519 (BLAKE2b) signature. Never raises on bad input."""
    try:
        if len(signature) != 64 or len(public_key) != 32:
            return False
        a_point = _decode_point(public_key)
        r_point = _decode_point(signature[:32])
        if a_point is None or r_point is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= L:
            return False
        k = int.from_bytes(hashfn(signature[:32] + public_key + message), "little") % L
        left = _scalar_mult(B, s)
        right = _edwards_add(r_point, _scalar_mult(a_point, k))
        return left == right
    except Exception:
        return False
