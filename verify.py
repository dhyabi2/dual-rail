"""Does this X-PAYMENT header pay for this resource, on the Nano rail?

Four questions, all of which must answer yes, and one that must answer
no:

    the block exists and the node calls it CONFIRMED   (never accept unconfirmed)
    it is a SEND                                        (a receive paid nobody)
    its destination is exactly our payTo                (by account, not by spelling)
    its amount is >= the amount required                (integer raw, never a float)
    it has not been presented for this resource before  (one payment, one call)

Anything else - a malformed header, a block we cannot find, a node we
cannot reach - is "not paid", and the caller falls through to the
service's existing rails. Failure here must never become a 5xx for a
customer who was paying in USDC.
"""

import base64
import binascii
import json
import re

import challenge as _challenge
import money
import nanoaddr
import nanonode


def _same_account(left, right) -> bool:
    """True only when both sides decode to the same public key.

    One Nano account has two spellings - `nano_` and the legacy `xrb_` - and a
    node is free to serve either, so comparing the text can refuse a payment
    that did arrive. Two different accounts can never share a public key, so
    this widens nothing: it accepts the same account written the other way and
    nothing else. A destination that is not an address at all decodes to
    nothing and is therefore equal to nothing.
    """
    try:
        return nanoaddr.decode(left) == nanoaddr.decode(right)
    except nanoaddr.InvalidAddress:
        return False


class Unpaid(Exception):
    """Not paid, with a reason worth logging. Never surfaced to the client."""

    def __init__(self, reason: str, message: str, **detail):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.detail = detail


_ABSOLUTE_FORM = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/]*")
_SLASHES = re.compile(r"/{2,}")
_UPPER_ASCII = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def resource_key(path) -> str:
    """The replay guard's key for a (percent-decoded) request path.

    The path is the payer's to spell, and every spelling a stock server or
    framework routes to the same handler must land in the same bucket of the
    guard, or each spelling buys one more call on one payment. So: drop the
    query and fragment, drop a `scheme://authority` prefix (an absolute-form
    request line puts one there, and wsgiref passes it into PATH_INFO as is),
    collapse repeated slashes, drop a trailing slash and lowercase ASCII.

    Every step merges keys and none splits one, so this can only refuse more
    and admit less. It is idempotent, and it is also the map from a key the
    guard was given before 2026-10-11 (`scheme://host/path`) to the key it is
    given now: a persisted store rewritten through it keeps every spent block
    spent. `node/dual-rail.js` `resourceKey` is the same function, and
    tests/test_conformance.py holds the two to the same matrix.
    """
    text = "" if path is None else str(path)
    text = text.split("?", 1)[0].split("#", 1)[0]
    text = _ABSOLUTE_FORM.sub("", text, count=1)
    text = _SLASHES.sub("/", "/" + text)
    if len(text) > 1 and text.endswith("/"):
        text = text[:-1]
    return text.translate(_UPPER_ASCII)


def migrate_legacy_key(old_key, script_name="") -> str:
    """Map a key the guard stored before 2026-10-11 onto the key it uses now.

    A seller with a persisted ReplayGuard store rewrites each pre-upgrade key
    through this before serving traffic on the new version. Python keys never
    carried the WSGI mount point (SCRIPT_NAME) before, and now do, so an app
    mounted under a prefix passes it here: `migrate_legacy_key(key, "/api")`.
    Without it every migrated key misses and each old block is spendable once
    more. Run it once per stored key: with a mount prefix it is not idempotent.
    See the README.
    """
    return resource_key((script_name or "") + resource_key(old_key))


class ReplayGuard:
    """One payment buys one call, per resource.

    In-process and therefore per-process: a service running several
    workers needs a shared store, and `seen`/`remember` are the two
    methods to reimplement over one. Said plainly here rather than
    discovered in production.
    """

    def __init__(self):
        self._seen = {}

    def seen(self, resource, block_hash) -> bool:
        return block_hash in self._seen.get(resource or "", set())

    def remember(self, resource, block_hash) -> None:
        self._seen.setdefault(resource or "", set()).add(block_hash)


def parse_payment(header: str) -> dict:
    """An X-PAYMENT header, as JSON or as base64 of JSON."""
    if not header or not header.strip():
        raise Unpaid("no_payment", "no X-PAYMENT header")
    text = header.strip()
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            raise Unpaid("malformed_payment",
                         "X-PAYMENT is neither JSON nor base64 of JSON") from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise Unpaid("malformed_payment", "X-PAYMENT is not valid JSON") from None
    if not isinstance(payload, dict):
        raise Unpaid("malformed_payment", "X-PAYMENT is not an object")
    return payload


def block_hash_of(payload: dict) -> str:
    inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    for key in ("blockHash", "block_hash", "hash"):
        value = inner.get(key)
        if isinstance(value, str) and value.strip():
            candidate = value.strip().upper()
            if len(candidate) == 64 and all(c in "0123456789ABCDEF" for c in candidate):
                return candidate
            raise Unpaid("malformed_payment",
                         "block hash must be 64 hex characters, got %d" % len(candidate))
    raise Unpaid("malformed_payment", "X-PAYMENT carries no block hash")


def is_ours(payload: dict) -> bool:
    """Is this proof addressed to the Nano rail at all?

    A USDC proof is not our business: we must not look at it, and above
    all must not reject it.
    """
    network = payload.get("network")
    if network is None and isinstance(payload.get("payload"), dict):
        network = payload["payload"].get("network")
    return network == _challenge.NETWORK


def verify(header: str, pay_to: str, amount_raw: int, resource, node: nanonode.NanoNode,
           guard: ReplayGuard) -> dict:
    """Return the payment on success; raise `Unpaid` otherwise. Never raises NodeError."""
    payload = parse_payment(header)
    if not is_ours(payload):
        raise Unpaid("not_our_rail", "this proof is for %r, not %r"
                     % (payload.get("network"), _challenge.NETWORK))
    block_hash = block_hash_of(payload)
    resource = resource_key(resource)

    if guard.seen(resource, block_hash):
        raise Unpaid("replayed", "block %s has already been spent on this resource"
                     % block_hash[:12])

    try:
        info = node.block_info(block_hash)
    except nanonode.NodeError as exc:
        # Constraint 2: our rail fails open. The caller falls through and the
        # service's existing rails answer exactly as if we were not installed.
        raise Unpaid("node_unavailable", exc.message, fallthrough=True) from None

    if not info:
        raise Unpaid("unknown_block", "block %s is not known to the node" % block_hash[:12])
    if not info.get("confirmed"):
        raise Unpaid("unconfirmed", "block %s is not confirmed yet" % block_hash[:12])

    if info.get("subtype") != "send":
        # A block that is not a send paid nobody, so it cannot be a proof that
        # anybody paid us. The one that matters is a RECEIVE on our own payout
        # account: it is confirmed, it carries a real amount, its hash is public
        # in our account history, and before this check it verified - so any
        # stranger could read our ledger and be served for free. A change or an
        # epoch block carries no amount and was already refused as underpaid,
        # but it was refused for the wrong reason.
        raise Unpaid("not_a_send",
                     "block %s is a %s, not a send: it paid nobody"
                     % (block_hash[:12], info.get("subtype") or "block of an unreadable kind"))
    if not nanoaddr.is_valid(pay_to):
        # Checked before the comparison below, so a seller who misconfigured
        # their own payTo is told that, rather than the payer being told their
        # correct payment went to the wrong place.
        raise Unpaid("invalid_payto", "payTo fails its checksum")

    destination = info.get("destination")
    if not _same_account(destination, pay_to):
        raise Unpaid("wrong_destination",
                     "block %s paid %s, not %s" % (block_hash[:12], destination, pay_to))

    paid_raw = int(info.get("amount_raw", 0))
    if paid_raw < amount_raw:
        raise Unpaid("underpaid", "block %s paid %s XNO, %s XNO is required"
                     % (block_hash[:12], money.raw_to_xno(paid_raw),
                        money.raw_to_xno(amount_raw)),
                     shortfall_raw=amount_raw - paid_raw)

    guard.remember(resource, block_hash)
    return {"block_hash": block_hash, "amount_raw": paid_raw, "payTo": pay_to,
            "overpaid_raw": paid_raw - amount_raw}
