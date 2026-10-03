"""Nano network identifiers: one canonical spelling, several in the wild.

An outside agent with a budget states the problem in its own words: there are
"at least three incompatible network identifiers" for Nano in x402
declarations. A seller that spells it one way is invisible to a payer that
matches the other, and nothing in either tool says so - the entry is simply
skipped, silently, and the seller concludes nobody wants to pay in XNO.

The two spellings that are provably live, both checked against installed
packages rather than guessed:

* ``nano:mainnet`` - the canonical one. ``x402-nano-exact`` 0.1.0 declares
  ``NETWORK_NANO_MAINNET = "nano:mainnet"`` and ``@x402nano/exact`` 0.3.0
  carries the same literal.
* ``nano-mainnet`` - no colon. Legal in x402 **v1**, where
  ``@x402/core`` 2.28.0's ``NetworkSchemaV1`` is ``z.string().min(1)``, and
  rejected by **v2**, whose ``NetworkSchemaV2`` is
  ``z.string().min(3).refine(v => v.includes(":"))``. A seller emitting it is
  payable by old clients and invisible to new ones.

A bare ``nano`` (or ``xno``) is a third thing in the wild and is deliberately
NOT canonicalised to mainnet: it names the family without naming the network,
and Nano's test networks use the same ``nano_`` address prefix, so reading it
as mainnet would be a guess about where money goes. It is reported as
``network_unspecified`` and the caller decides. Likewise ``nano:testnet`` is
recognised as Nano and refused as ``not_mainnet`` - a distinct answer from
"this is not Nano at all", because the two want different fixes.

Nothing here touches money, a key or the network. It reads a string.
"""

import json
import re


def show(value) -> str:
    """A value as it would appear in JSON.

    Used in every message instead of a language's own repr, so the Python and
    Node bindings produce byte-identical text and the conformance suite can
    compare whole verdicts rather than just their machine-readable halves.
    """
    try:
        return json.dumps(value)
    except (TypeError, ValueError):                      # pragma: no cover
        return "null"


def kind(value) -> str:
    """A value's JSON type name: object, array, string, number, boolean, null.

    Python's `type(x).__name__` and JavaScript's `typeof` disagree on every
    one of these; JSON's vocabulary is the one both bindings share.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"                                     # pragma: no cover


#: The one spelling this package emits, and the only one x402 v2 accepts.
CANONICAL = "nano:mainnet"

#: Spellings folded to CANONICAL. Each is here because it was found in a
#: shipped package or named by a payer we have evidence from - never invented.
MAINNET_SPELLINGS = ("nano:mainnet", "nano-mainnet")

#: Names that mean Nano but not which Nano network.
FAMILY_ONLY = ("nano", "xno")

#: CAIP-2 (https://chainagnostic.org/CAIP-2): a lowercase namespace, a colon,
#: a reference. `x402-validator` 0.3.1 gates on ^[a-z0-9-]+:[a-zA-Z0-9_-]+$;
#: the spec itself bounds the two halves, so the stricter form is used here and
#: the looser one cannot disagree about any identifier in MAINNET_SPELLINGS.
CAIP2_PATTERN = re.compile(r"^[-a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$")

#: x402 v2's own gate, read off @x402/core 2.28.0's NetworkSchemaV2.
V2_MIN_LENGTH = 3


class UnknownNetwork(ValueError):
    """Raised by `canonical` when an identifier is not Nano mainnet.

    `reason` is a stable machine-readable code, safe to put in an API
    response body: callers switch on it, humans read `str(exc)`.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def _fold(value: str) -> str:
    """Whitespace and case are not part of an identifier.

    A namespace must be lowercase to be CAIP-2, so folding case here loses
    nothing a payer could have used - and `classify` reports the fold so a
    seller learns their emitted string is not the canonical one.
    """
    return value.strip().lower()


def classify(value) -> dict:
    """Never raises. Returns a JSON-serialisable verdict about one identifier.

    Nano mainnet, under any accepted spelling::

        {"nano": true, "mainnet": true, "canonical": "nano:mainnet",
         "as_given": "nano-mainnet", "folded": false,
         "x402_v1_legal": true, "x402_v2_legal": false, "caip2": false}

    Nano, but not usable as mainnet::

        {"nano": true, "mainnet": false, "canonical": null,
         "reason": "network_unspecified" | "not_mainnet", "message": "..."}

    Not Nano at all::

        {"nano": false, "mainnet": false, "canonical": null,
         "reason": "not_nano" | "not_a_string" | "empty", "message": "..."}

    `x402_v2_legal` is the one that costs a seller money today: an entry whose
    network fails it is dropped by a v2 client before any payment is attempted.
    """
    if not isinstance(value, str):
        return {
            "nano": False, "mainnet": False, "canonical": None,
            "reason": "not_a_string",
            "message": "a network identifier must be a string, got %s" % kind(value),
        }

    folded = _fold(value)
    if not folded:
        return {"nano": False, "mainnet": False, "canonical": None,
                "reason": "empty", "message": "network identifier is empty"}

    if folded in MAINNET_SPELLINGS:
        return {
            "nano": True, "mainnet": True, "canonical": CANONICAL,
            "as_given": value, "folded": folded != value,
            "x402_v1_legal": True,
            "x402_v2_legal": len(folded) >= V2_MIN_LENGTH and ":" in folded,
            "caip2": bool(CAIP2_PATTERN.match(value)),
        }

    if folded in FAMILY_ONLY:
        return {
            "nano": True, "mainnet": False, "canonical": None,
            "reason": "network_unspecified",
            "message": "%s names Nano but not which Nano network, and the test "
                       "networks share the nano_ address prefix - say %s if you "
                       "mean mainnet" % (show(value), show(CANONICAL)),
        }

    for prefix in ("nano:", "nano-", "xno:", "xno-"):
        if folded.startswith(prefix):
            return {
                "nano": True, "mainnet": False, "canonical": None,
                "reason": "not_mainnet",
                "message": "%s is a Nano network identifier but not mainnet; this "
                           "package settles on %s only"
                           % (show(value), show(CANONICAL)),
            }

    return {
        "nano": False, "mainnet": False, "canonical": None,
        "reason": "not_nano",
        "message": "%s is not a Nano network identifier" % show(value),
    }


def canonical(value) -> str:
    """`CANONICAL` for any accepted spelling of Nano mainnet, else raise.

    Fails closed: an identifier this module does not recognise raises rather
    than being assumed to be mainnet.
    """
    verdict = classify(value)
    if verdict["mainnet"]:
        return verdict["canonical"]
    raise UnknownNetwork(verdict["reason"], verdict["message"])


def is_nano_mainnet(value) -> bool:
    """True for any accepted spelling of Nano mainnet. Never raises."""
    return classify(value)["mainnet"]
