"""The adapter: one entry appended to accepts[], and nothing else changed.

Read this paragraph first, because it is the objection an operator will
have: **if our rail fails, yours keeps selling.** Every error on the
Nano side - an unreachable node, a timeout, a malformed proof, a block
we cannot find - is handled by falling through to the service's existing
rails exactly as if this adapter were not installed. There is no code
path here that can turn a Nano problem into a 5xx for a customer paying
in USDC. `test_node_outage_falls_through` asserts no 5xx anywhere in a
run where every node call raises.

What changes in a 402 response: one additional element at the end of
`accepts[]`. Same status code, same headers, same ordering, same
`x-402-version`. `test_402_headers_byte_identical_apart_from_accepts`
captures the whole response with and without the adapter and asserts it.

Mounting. `dual_rail(...)` builds the adapter; `.wsgi(app)` wraps a WSGI
application, which is where the 402 is patched on its way out. Admitting
a Nano-paid request additionally needs `gate=` - the middleware that is
the paywall - because bypassing the paywall is the one thing admission
means, and an adapter cannot bypass a middleware it was never shown. The
Node binding takes the same two forms: `dualRail(opts)` as Express
middleware, and `opts.gate` to admit.
"""

import json

import challenge as _challenge
import money
import nanoaddr
import nanonode
import verify as _verify

PAYMENT_HEADER = "X-PAYMENT"
WSGI_PAYMENT_KEY = "HTTP_X_PAYMENT"
PAID_ENV_KEY = "dual_rail.payment"

ONLY_PERMITTED_ON_VERIFY_ERROR = "fallthrough"


class ConfigurationError(ValueError):
    """A configuration this adapter refuses to construct under.

    Always fatal at construction: a service must never be able to boot
    advertising an address nobody can pay.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class DualRail:
    def __init__(self, pay_to, amount_xno, node_url=None, network=_challenge.NETWORK,
                 on_verify_error=ONLY_PERMITTED_ON_VERIFY_ERROR, node=None,
                 timeout=nanonode.DEFAULT_TIMEOUT, logger=None):
        verdict = nanoaddr.validate(pay_to)
        if not verdict["valid"]:
            raise ConfigurationError(
                "invalid_address",
                "payTo %r fails its checksum (%s). A service must not boot advertising "
                "an address that can never receive a payment."
                % (pay_to, verdict["reason"]),
            )
        try:
            self.amount_raw = money.check_price(amount_xno)
        except money.AmountError as exc:
            raise ConfigurationError("price_out_of_range", exc.message) from None
        if on_verify_error != ONLY_PERMITTED_ON_VERIFY_ERROR:
            raise ConfigurationError(
                "invalid_on_verify_error",
                "onVerifyError must be %r and nothing else. Failing closed on our rail "
                "would let our outage cost you a sale on yours."
                % ONLY_PERMITTED_ON_VERIFY_ERROR,
            )
        if network != _challenge.NETWORK:
            raise ConfigurationError("invalid_network",
                                     "network is fixed at %r" % _challenge.NETWORK)

        self.pay_to = verdict["normalised"]
        self.amount_xno = str(amount_xno)
        self.node = node if node is not None else (
            nanonode.HttpNanoNode(node_url, timeout) if node_url else None)
        self.guard = _verify.ReplayGuard()
        self._log = logger
        self._warned = set()

    # -- the two things it does ------------------------------------------

    def patch_challenge(self, challenge_body: dict) -> dict:
        """Append our entry to a 402 challenge. Never alters an existing one."""
        return _challenge.append_nano(challenge_body, self.pay_to, self.amount_xno)

    def check_payment(self, header, resource):
        """The verified payment, or None. NEVER raises - falling through is the
        answer to every failure, and a caller that has to catch exceptions to
        stay up is a caller that will one day 500."""
        if self.node is None:
            return None
        try:
            return _verify.verify(header, self.pay_to, self.amount_raw, resource,
                                  self.node, self.guard)
        except _verify.Unpaid as exc:
            self._warn_once(exc)
            return None
        except Exception as exc:                        # pragma: no cover - defensive
            self._warn_once(_verify.Unpaid("adapter_error", str(exc), fallthrough=True))
            return None

    def _warn_once(self, exc):
        """Log a node outage ONCE, not once per request.

        A node that is down produces one line per request otherwise, which
        is how an outage on our side becomes an incident on theirs.
        """
        if exc.reason in ("no_payment", "not_our_rail"):
            return
        key = exc.reason
        if exc.detail.get("fallthrough"):
            if key in self._warned:
                return
            self._warned.add(key)
        if self._log is not None:
            self._log("dual-rail: %s (%s) - falling through to the existing rails"
                      % (exc.message, exc.reason))

    # -- WSGI -------------------------------------------------------------

    def wsgi(self, app, gate=None):
        """Wrap a WSGI app. With `gate`, a verified payment bypasses it."""
        adapter = self

        def middleware(environ, start_response):
            resource = _resource_of(environ)
            payment = adapter.check_payment(environ.get(WSGI_PAYMENT_KEY, ""), resource)
            if payment is not None and gate is not None:
                environ[PAID_ENV_KEY] = payment
                return app(environ, start_response)

            downstream = gate if gate is not None else app
            captured = {}

            def capture(status, headers, exc_info=None):
                captured["status"] = status
                captured["headers"] = list(headers)
                captured["exc_info"] = exc_info
                return lambda data: None

            chunks = list(downstream(environ, capture))
            body = b"".join(chunks)
            status = captured.get("status", "500 Internal Server Error")
            headers = captured.get("headers", [])

            if status.split(" ", 1)[0] == "402":
                body, headers = adapter._patched(body, headers)
            start_response(status, headers, captured.get("exc_info"))
            return [body]

        return middleware

    def _patched(self, body: bytes, headers):
        """Append our entry to a 402 body, or return it untouched.

        Anything unexpected about the body - not JSON, no accepts[], a
        Nano entry already there - means we leave it exactly as it was.
        A 402 we do not understand is a 402 we must not rewrite.
        """
        try:
            parsed = json.loads(body.decode("utf-8"))
            patched = self.patch_challenge(parsed)
        except Exception:
            return body, headers
        if patched == parsed:
            return body, headers
        new_body = json.dumps(patched).encode("utf-8")
        headers = [(name, value) for name, value in headers
                   if name.lower() != "content-length"]
        headers.append(("Content-Length", str(len(new_body))))
        return new_body, headers


def dual_rail(**options) -> DualRail:
    """`dualRail({...})` from the spec, in Python spelling.

    Accepts the spec's camelCase keys and their snake_case equivalents, so
    the same configuration can be pasted between the two bindings.
    """
    mapping = {"payTo": "pay_to", "amountXno": "amount_xno", "nodeUrl": "node_url",
               "onVerifyError": "on_verify_error"}
    normalised = {mapping.get(key, key): value for key, value in options.items()}
    return DualRail(**normalised)


def _resource_of(environ) -> str:
    scheme = environ.get("wsgi.url_scheme", "http")
    host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "")
    return "%s://%s%s" % (scheme, host, environ.get("PATH_INFO", ""))
