"""A hosted Nano x402 facilitator: verify-only, no treasury, no key, no wallet.

A seller adds ONE accepts[] entry (payTo = the seller's own nano_ address) and
points its x402 middleware at this service's /verify and /settle. The payer
sends XNO straight to the seller's address; this service only reads a public
node and answers whether that block pays what the requirement says. It holds
nothing, signs nothing and cannot move a raw: there is no second treasury and
no seller-side verifier to run.

Routes (the shapes x402 facilitators use):
    GET  /health      -> {"ok": true}
    GET  /supported   -> {"kinds": [{"x402Version": 1|2, "scheme": "exact", "network": "nano:mainnet"}]}
    POST /verify      body {paymentPayload, paymentRequirements} -> {"isValid", "invalidReason", "payer"}
    POST /settle      same body -> {"success", "transaction", "network", "payer", "errorReason"}

/verify is idempotent. /settle also claims the block for the resource so a second
/settle of the same block is refused ("replayed"). That claim lives in the guard
you pass in: the default is per-process memory, which on serverless is only a
best effort - a seller that needs hard one-payment-one-call keeps its own ledger
or uses the binding (see binding.py). Said here, not discovered in production.

A node outage is answered 200 with isValid false and fallthrough true, never a 5xx,
so the seller's other rails keep selling.

The operator sets only NANO_RPC_URL (a public node is fine). Nothing here reads
a seed or key, and the module has no code path that could.
"""

import json
import os

import challenge as _challenge
import money
import nanoaddr
import nanonode
import verify as _verify

NETWORK = "nano:mainnet"
MAX_BODY = 64 * 1024


class _ReadOnlyGuard:
    """Wraps a guard so /verify can ask 'was it spent?' without spending it."""

    def __init__(self, inner):
        self._inner = inner

    def seen(self, resource, block_hash):
        return self._inner.seen(resource, block_hash)

    def remember(self, resource, block_hash):
        return None

    def claim(self, resource, block_hash):
        return not self._inner.seen(resource, block_hash)


def _requirement_amount(req):
    for key in ("amount", "maxAmountRequired", "max_amount_required"):
        value = req.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text.isascii() and text.isdigit():
            return int(text)
        return None
    return None


def _payer(node, payload):
    try:
        h = _verify.block_hash_of(payload)
        return (node.block_info(h) or {}).get("account")
    except Exception:
        return None


def _decide(body, node, guard):
    """Returns (ok, reason, payer, block_hash, detail)."""
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except (ValueError, UnicodeDecodeError):
        return False, "malformed_request", None, None, {}
    if not isinstance(data, dict):
        return False, "malformed_request", None, None, {}
    payload = data.get("paymentPayload")
    req = data.get("paymentRequirements")
    if not isinstance(payload, dict) or not isinstance(req, dict):
        return False, "malformed_request", None, None, {}
    if req.get("network", NETWORK) != NETWORK:
        return False, "not_our_rail", None, None, {}
    pay_to = req.get("payTo")
    if not isinstance(pay_to, str) or not nanoaddr.is_valid(pay_to):
        return False, "invalid_payto", None, None, {}
    amount = _requirement_amount(req)
    if amount is None or amount <= 0:
        return False, "invalid_amount", None, None, {}
    try:
        got = _verify.verify(json.dumps(payload), pay_to, amount,
                             req.get("resource") or "", node, guard)
    except _verify.Unpaid as exc:
        return False, exc.reason, None, None, dict(exc.detail)
    return True, None, _payer(node, payload), got["block_hash"], {}


PAID_PATH = "/v1/block-check"


def paid_config():
    """(pay_to, price_xno) from the environment, or (None, None) when the paid route is off.

    PAY_TO is the OWNER's own nano_ address, set by the owner; there is no default and
    nothing here can derive one. Unset or invalid = the route is off (404).
    """
    pay_to = os.environ.get("PAY_TO", "").strip()
    price = os.environ.get("PRICE_XNO", "0.0001").strip()
    try:
        money.check_price(price)
    except money.AmountError:
        return None, None
    return (pay_to, price) if nanoaddr.is_valid(pay_to) else (None, None)


def _paid_block_check(path, query_hash, header, node, guard, pay_to, price, base):
    """A tiny paid product: GET /v1/block-check?hash=H returns what the node says about block H.

    No payment -> 402 with a nano:mainnet accepts[] entry (payTo = the owner's address).
    Payment -> verified the same way as /settle, then answered. The point is a live,
    cheap thing an outsider can pay in XNO without our box (any host that runs this file).
    """
    resource = base + PAID_PATH
    if not header:
        entry = _challenge.nano_entry(pay_to, price, resource)
        return 402, {"x402Version": 2, "error": "payment required", "accepts": [entry]}
    try:
        _verify.verify(header, pay_to, money.check_price(price), PAID_PATH, node, guard)
    except _verify.Unpaid as exc:
        entry = _challenge.nano_entry(pay_to, price, resource)
        return 402, {"x402Version": 2, "error": exc.reason, "accepts": [entry]}
    try:
        info = node.block_info(query_hash.upper()) if query_hash else {}
    except nanonode.NodeError:
        info = {}
    return 200, {"hash": query_hash, "block": info}


def handle(method, path, body, node, guard, header="", host="", query=""):
    """Pure core: (status, json-able). `node` is a NanoNode, `guard` a ReplayGuard."""
    if method and method.upper() == "GET" and (path or "").split("?", 1)[0].rstrip("/") == "/.well-known/x402":
        pay_to, price = paid_config()
        if not pay_to:
            return 404, {"error": "not found"}
        return 200, {"x402Version": 2, "accepts": [_challenge.nano_entry(pay_to, price, "https://%s%s" % (host, PAID_PATH))]}
    if method and method.upper() == "GET" and (path or "").split("?", 1)[0].rstrip("/") == PAID_PATH:
        pay_to, price = paid_config()
        if not pay_to:
            return 404, {"error": "not found"}
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        return _paid_block_check(path, q.get("hash", ""), header, node, guard, pay_to, price, "https://" + host)
    path = (path or "/").split("?", 1)[0].rstrip("/") or "/"
    method = (method or "GET").upper()
    if method == "GET" and path in ("/", "/health"):
        return 200, {"ok": True, "service": "nano-x402-facilitator", "network": NETWORK,
                     "custody": "none: verify-only, funds go from payer to the seller's own address"}
    if method == "GET" and path == "/supported":
        return 200, {"kinds": [{"x402Version": 1, "scheme": "exact", "network": NETWORK},
                               {"x402Version": 2, "scheme": "exact", "network": NETWORK}]}
    if method == "POST" and path in ("/verify", "/settle"):
        if body is not None and len(body) > MAX_BODY:
            return 413, {"error": "body too large"}
        if path == "/verify":
            ok, reason, payer, _h, detail = _decide(body, node, _ReadOnlyGuard(guard))
            out = {"isValid": ok, "payer": payer}
            if not ok:
                out["invalidReason"] = reason
                if reason == "node_unavailable":
                    out["fallthrough"] = True
            return 200, out
        ok, reason, payer, block, detail = _decide(body, node, guard)
        out = {"success": ok, "network": NETWORK, "payer": payer,
               "transaction": block if ok else ""}
        if not ok:
            out["errorReason"] = reason
            if reason == "node_unavailable":
                out["fallthrough"] = True
        return 200, out
    return 404, {"error": "not found"}


_GUARD = _verify.ReplayGuard()


def default_node():
    return nanonode.HttpNanoNode(os.environ.get("NANO_RPC_URL", "https://rpc.nano.to"))


def wsgi(environ, start_response, node=None, guard=None):
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        size = 0
    body = environ["wsgi.input"].read(min(size, MAX_BODY + 1)) if size else b""
    status, out = handle(environ.get("REQUEST_METHOD"), environ.get("PATH_INFO"), body,
                         node or default_node(), guard or _GUARD,
                         environ.get("HTTP_X_PAYMENT", ""), environ.get("HTTP_HOST", ""),
                         environ.get("QUERY_STRING", ""))
    blob = json.dumps(out).encode("utf-8")
    start_response("%d %s" % (status, {200: "OK", 402: "Payment Required"}.get(status, "Error")),
                   [("Content-Type", "application/json"), ("Content-Length", str(len(blob))),
                    ("Cache-Control", "no-store"), ("Access-Control-Allow-Origin", "*")])
    return [blob]


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    port = int(os.environ.get("PORT", "8080"))
    print("nano x402 facilitator on :%d (node %s)" % (port, nanonode.host_of(default_node().url)))
    make_server("127.0.0.1", port, wsgi).serve_forever()
