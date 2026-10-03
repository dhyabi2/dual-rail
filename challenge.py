"""The accepts[] array, and the one entry this adapter is allowed to add.

Hard constraint 1 of the spec, stated as code: the adapter must be
INCAPABLE of removing or reordering an existing entry. `append_nano`
therefore never mutates its input, never touches an existing element,
and appends last - so a client that takes the first acceptable entry
keeps its current behaviour byte for byte.

`assert_additive` is the belt to that braces: it compares a before and
after array and raises unless the prefix is deeply equal and exactly one
element was added. Every path that emits a patched challenge runs
through it, including the CLI's `--diff`.
"""

import copy

import money
import network as _network

NETWORK = "nano:mainnet"
ASSET = "XNO"
SCHEME = "exact"
ADAPTER_ID = "dual-rail/1"
DECIMALS = 30
# x402 requires a payment window on every accepts[] entry, in BOTH protocol
# versions (see `nano_entry`). It is how long the server will still take a
# payment built against this quote. A Nano send confirms in about a second, so
# the window is there for the client's own round trip, not for the ledger; 60s
# is the value the x402 ecosystem uses, and it is not a price, so it is a
# constant rather than one more thing an operator can get wrong.
MAX_TIMEOUT_SECONDS = 60

DESCRIPTION = "Feeless native-coin settlement. Optional - the entries above are unchanged."


class NotAdditive(AssertionError):
    """Raised rather than emit a patch that changes anything existing."""


def nano_entry(pay_to: str, amount_xno: str, resource) -> dict:
    """The single element this adapter appends. Nothing else is ever written.

    The amount is in the asset's ATOMIC unit - raw, of which there are 10**30 to
    the XNO, which is why `extra.decimals` says 30. It used to carry the
    configured decimal XNO string verbatim ("0.0001"), and every x402 Nano client
    reads the field as an integer count of raw: feeless402 0.2.12's
    `nano_pay.x402.offer_amount_raw` is `int(offer[field])` over
    `("amount", "maxAmountRequired", "max_amount_required")` in that order, so a
    patched 402 raised `ValueError: invalid literal for int() with base 10:
    '0.0001'` and the agent could not pay at all. The decimal figure is kept beside it in `maxAmountRequiredFormatted`,
    which is where that client already looks for something human-readable, so
    nothing is lost from the operator's view.

    The amount is advertised under BOTH names, because x402 renamed the field
    between protocol versions and this entry is appended to somebody else's
    challenge, whose version we do not choose:

      * `maxAmountRequired` is the x402 **v1** name, and v1 requires it;
      * `amount` is the x402 **v2** name, and v2 requires it.

    Measured against the official packages - `@x402/core` 2.28.0's
    `PaymentRequirementsV1Schema` and `PaymentRequirementsV2Schema`, and
    `@x402nano/exact` 0.3.0, whose `parsePrice("0.0001", "nano:mainnet")` returns
    exactly `{"amount": "100000000000000000000000000"}`, the same integer this
    emits - carrying both names is accepted by v1, by v2 and by the
    `PaymentRequirements` union, because each version's schema strips the other
    version's field rather than rejecting it. Carrying only one is rejected
    outright by the other version, so no single name is correct for an entry we
    append blind. `tests/test_dual_rail.py` pins both required sets.

    `maxTimeoutSeconds` is required by v1 AND v2, so without it this entry was
    never a valid `PaymentRequirements` object under either version - which is
    why it is here even though no Nano client was reading it.

    The configured `amount_xno` is unchanged, and so is everything this adapter
    accepts: `Adapter.amount_raw` is still `money.check_price(amount_xno)`, so
    the amount a payment is checked against is exactly what it always was.
    """
    raw = str(money.xno_to_raw(amount_xno))
    return {
        "scheme": SCHEME,
        "network": NETWORK,
        "asset": ASSET,
        "amount": raw,
        "maxAmountRequired": raw,
        "maxAmountRequiredFormatted": "%s XNO" % amount_xno,
        "payTo": pay_to,
        "resource": resource,
        "description": DESCRIPTION,
        "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
        "extra": {"decimals": DECIMALS, "adapter": ADAPTER_ID},
    }


def find_nano(accepts) -> int:
    """Index of an existing Nano mainnet entry, or -1.

    Matches every spelling of Nano mainnet, not just the one we emit.
    Comparing against NETWORK alone missed a seller who already accepted XNO
    and spelled the network `nano-mainnet` - x402 v1's colon-free form - so
    `append_nano` appended a SECOND Nano entry beside theirs. The array then
    carried two Nano prices and two payout addresses, and which one got paid
    depended on the payer's protocol version: a v1 client takes the first
    entry it can pay, a v2 client refuses `nano-mainnet` outright (its
    NetworkSchemaV2 requires a colon) and takes ours. See network.py.
    """
    for index, entry in enumerate(accepts or []):
        if isinstance(entry, dict) and _network.is_nano_mainnet(entry.get("network")):
            return index
    return -1


def resource_of(accepts):
    """The resource string the existing entries already use.

    Taken from them rather than invented, so the appended entry names the
    same resource - an entry with a different resource string is a second
    resource, not a second rail.
    """
    for entry in accepts or []:
        if isinstance(entry, dict) and entry.get("resource"):
            return entry["resource"]
    return None


def assert_additive(before, after) -> None:
    if len(after) != len(before) + 1:
        raise NotAdditive("expected exactly one added entry, went from %d to %d"
                          % (len(before), len(after)))
    for index, entry in enumerate(before):
        if after[index] != entry:
            raise NotAdditive(
                "entry %d changed. The adapter may only append; this patch would alter "
                "a rail that already works." % index
            )
    if find_nano([after[-1]]) != 0:
        raise NotAdditive("the appended entry is not the Nano entry")


def append_nano(challenge: dict, pay_to: str, amount_xno: str) -> dict:
    """A copy of `challenge` with one Nano entry appended to accepts[].

    Returns the challenge UNCHANGED if a Nano entry is already present, so
    running this twice is the same as running it once.
    """
    patched = copy.deepcopy(challenge)
    accepts = patched.get("accepts")
    if not isinstance(accepts, list):
        raise NotAdditive("this 402 challenge has no accepts[] array to append to")
    if find_nano(accepts) != -1:
        return patched
    before = copy.deepcopy(accepts)
    accepts.append(nano_entry(pay_to, amount_xno, resource_of(accepts)))
    assert_additive(before, accepts)
    return patched
