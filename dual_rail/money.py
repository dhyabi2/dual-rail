"""Exact XNO <-> raw. Integers only.

1 XNO = 10**30 raw. A double holds 53 bits of mantissa; 10**30 needs
about 100. Every amount in this package is therefore an int of raw, and
every decimal amount is a string that is parsed, never a float that is
read. The Node binding uses BigInt for the same reason, and the
conformance suite asserts the two agree on values chosen to break a
float.
"""

import re

RAW_PER_XNO = 10 ** 30
DECIMALS = 30

#: An amount is ASCII digits with at most one dot - the same grammar the Node
#: binding tests and the same one `@x402nano/typescript-common` declares for an
#: integer amount (`/^\d+$/`, where JavaScript's \d is ASCII). Written out
#: rather than `\d`, which in Python's `re` also matches Unicode digits.
_DECIMAL = re.compile(r"^[0-9]*(\.[0-9]*)?$")

#: The spec's price band. Below the floor is dust; above the ceiling is a
#: price nobody meant to type.
MIN_PRICE_XNO = "0.000001"
MAX_PRICE_XNO = "100"


class AmountError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def xno_to_raw(amount) -> int:
    """Exact decimal XNO string -> integer raw. A float is refused outright."""
    if isinstance(amount, float):
        raise AmountError(
            "float_amount",
            "an amount must be a decimal STRING, not a float: %r cannot represent "
            "raw exactly and would round real money away" % amount,
        )
    text = str(amount).strip()
    if not text:
        raise AmountError("invalid_amount", "amount is empty")
    if text.startswith("-"):
        raise AmountError("invalid_amount", "amount must not be negative")
    if not _DECIMAL.match(text) or text == ".":
        # `str.isdigit()` was here and is true for every Unicode digit, which
        # is not what any client reads the amount back with. The Node binding's
        # /^\d+$/ is ASCII-only, so "٣" was 3 XNO in Python and refused in
        # Node, and "²" - isdigit() true, int() ValueError - crashed out of
        # this module past every `except AmountError` in the package. The
        # schema that does the real rejecting is ASCII, so this is too.
        raise AmountError("invalid_amount", "amount is not a decimal number: %r" % amount)
    whole, _, frac = text.partition(".")
    whole = whole or "0"
    if len(frac) > DECIMALS:
        raise AmountError("invalid_amount",
                          "Nano has %d decimal places; %d were given" % (DECIMALS, len(frac)))
    return int(whole) * RAW_PER_XNO + int(frac.ljust(DECIMALS, "0") or 0)


def raw_to_xno(raw: int) -> str:
    if isinstance(raw, float) or not isinstance(raw, int):
        raise AmountError("invalid_raw", "raw must be an integer, got %s" % type(raw).__name__)
    if raw < 0:
        raise AmountError("invalid_raw", "raw must not be negative")
    whole, frac = divmod(raw, RAW_PER_XNO)
    if frac == 0:
        return str(whole)
    return "%d.%s" % (whole, str(frac).zfill(DECIMALS).rstrip("0"))


def check_price(amount) -> int:
    """Validate a price against the band and return it in raw, or raise."""
    try:
        raw = xno_to_raw(amount)
    except AmountError as exc:
        raise AmountError("price_out_of_range", exc.message) from None
    if not xno_to_raw(MIN_PRICE_XNO) <= raw <= xno_to_raw(MAX_PRICE_XNO):
        raise AmountError(
            "price_out_of_range",
            "amountXno must be a decimal string between %s and %s XNO, got %r"
            % (MIN_PRICE_XNO, MAX_PRICE_XNO, amount),
        )
    return raw
