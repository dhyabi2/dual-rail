"""Payment-to-request binding: a confirmed block pays for ONE request, by ONE payer.

The problem. A Nano send block is public the moment it is published, and the old
X-PAYMENT proof was just its hash. Anyone who watches the ledger - or the payer's own
request log - can present that hash first and be served what the payer paid for.
Reviewers of other x402-Nano code named this exact gap (payment not bound to the
request, replay store not persistent).

The fix, `nano-block-sig/1`. Nano has no memo field, but every block names its sender,
and only the sender holds that account's key. So the payer signs, with the key of the
account that made the payment, a message that names this block, this seller's
challenge and this resource:

    message   = b"nano-402-binding/1\\n" + block_hash(32 bytes) + b"\\n"
                + nonce(utf-8) + b"\\n" + resource(utf-8) + b"\\n" + pay_to(utf-8, nano_ form)
    signature = Ed25519-BLAKE2b(message) by the block's `account` key        (64 bytes, hex)

The seller issues `nonce` inside the 402 (`accepts[].extra.binding`) and holds NO state
for it: it is `<expiry_hex>.<HMAC-SHA256(secret, resource|expiry|pay_to)[:32]>`. The verifier
recomputes the HMAC, checks the expiry, checks the resource is the one being asked for,
recovers the block's sender from the node, and verifies the signature. A watcher who only
sees the block hash cannot produce it; a signature for resource A is useless on resource B;
a nonce issued for one seller's address is useless at another. The block itself is then
spent once, in a store that survives restarts (store.py).

What this does NOT do: it does not bind the request BODY (any caller who controls the
paying key can reuse one signature across bodies for the same resource while the nonce
lives, but the block is spent after the first accepted call, so there is no second call to
bind); and it cannot stop the payer's own key being stolen.
"""
import hashlib
import hmac
import time

import ed25519_blake2b as _ed
import nanoaddr

SCHEME = "nano-block-sig/1"
PREFIX = b"nano-402-binding/1\n"
NONCE_TTL_S = 600


class BindingError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason
        self.message = message


def message_for(block_hash: str, nonce: str, resource: str, pay_to: str) -> bytes:
    return (PREFIX + bytes.fromhex(block_hash) + b"\n" + nonce.encode("utf-8") + b"\n"
            + resource.encode("utf-8") + b"\n" + _canonical(pay_to).encode("utf-8"))


def _canonical(address: str) -> str:
    return nanoaddr.encode(nanoaddr.decode(address), "nano_")


def _mac(secret: bytes, resource: str, expiry: int, pay_to: str) -> str:
    data = ("%s|%d|%s" % (resource, expiry, _canonical(pay_to))).encode("utf-8")
    return hmac.new(secret, data, hashlib.sha256).hexdigest()[:32]


def issue_nonce(secret: bytes, resource: str, pay_to: str, now=None, ttl=NONCE_TTL_S) -> str:
    expiry = int((time.time() if now is None else now) + ttl)
    return "%x.%s" % (expiry, _mac(secret, resource, expiry, pay_to))


def check_nonce(secret: bytes, nonce: str, resource: str, pay_to: str, now=None) -> None:
    try:
        expiry_hex, mac = nonce.split(".", 1)
        expiry = int(expiry_hex, 16)
    except (AttributeError, ValueError):
        raise BindingError("binding_nonce_malformed", "binding nonce is malformed") from None
    if not hmac.compare_digest(mac, _mac(secret, resource, expiry, pay_to)):
        raise BindingError("binding_nonce_invalid",
                           "binding nonce was not issued by this seller for this resource")
    if (time.time() if now is None else now) > expiry:
        raise BindingError("binding_nonce_expired", "binding nonce has expired; fetch a new 402")


def binding_extra(secret: bytes, resource: str, pay_to: str, now=None) -> dict:
    """What the 402 carries in `accepts[].extra.binding`."""
    return {"scheme": SCHEME, "nonce": issue_nonce(secret, resource, pay_to, now),
            "resource": resource}


def verify_binding(secret: bytes, binding, block_hash: str, payer_account: str,
                   resource: str, pay_to: str, now=None) -> None:
    """Raise BindingError unless `binding` proves the presenter controls `payer_account`."""
    if not isinstance(binding, dict):
        raise BindingError("binding_missing",
                           "this seller requires a %s binding in the payment" % SCHEME)
    nonce, signature = binding.get("nonce"), binding.get("signature")
    if not isinstance(nonce, str) or not isinstance(signature, str):
        raise BindingError("binding_malformed", "binding needs a nonce and a signature")
    check_nonce(secret, nonce, resource, pay_to, now)
    try:
        sig = bytes.fromhex(signature)
        pub = nanoaddr.decode(payer_account)
        message = message_for(block_hash, nonce, resource, pay_to)
    except (ValueError, nanoaddr.InvalidAddress, TypeError):
        raise BindingError("binding_malformed", "binding signature is not 128 hex characters "
                           "or the block's sender is unreadable") from None
    if not _ed.verify(message, sig, pub):
        raise BindingError("binding_invalid",
                           "binding signature does not match the account that made the payment")
