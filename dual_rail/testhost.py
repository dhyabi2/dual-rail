"""A host service that already speaks x402, for the adapter to be mounted on.

It is written as if we had never heard of Nano: two USDC entries, its own
402, its own headers. Nothing in this file knows the adapter exists, which
is the only way to test that the adapter leaves it alone.
"""

import json

USDC_ENTRIES = [
    {"scheme": "exact", "network": "base", "asset": "USDC",
     "maxAmountRequired": "0.01", "payTo": "0x1111111111111111111111111111111111111111",
     "resource": "https://example.dev/report", "description": "USDC on Base"},
    {"scheme": "exact", "network": "solana", "asset": "USDC",
     "maxAmountRequired": "0.01", "payTo": "So11111111111111111111111111111111111111112",
     "resource": "https://example.dev/report", "description": "USDC on Solana"},
]

X402_VERSION = "2"


def challenge(entries=None):
    return {"x402Version": 2, "error": "payment required",
            "accepts": [dict(entry) for entry in (entries or USDC_ENTRIES)]}


def make_host(entries=None, body=b'{"report": "the goods"}'):
    """Returns (gate, protected). The gate is the paywall; protected is what
    a paid request gets."""

    def protected(environ, start_response):
        start_response("200 OK", [("Content-Type", "application/json"),
                                  ("X-402-Version", X402_VERSION)])
        return [body]

    def gate(environ, start_response):
        header = environ.get("HTTP_X_PAYMENT", "")
        if header:
            try:
                payload = json.loads(header)
            except ValueError:
                payload = {}
            if payload.get("network") in ("base", "solana"):
                return protected(environ, start_response)
        blob = json.dumps(challenge(entries)).encode("utf-8")
        start_response("402 Payment Required", [
            ("Content-Type", "application/json"),
            ("X-402-Version", X402_VERSION),
            ("Cache-Control", "no-store"),
            ("Content-Length", str(len(blob))),
        ])
        return [blob]

    return gate, protected
