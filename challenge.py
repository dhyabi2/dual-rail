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

import network as _network

NETWORK = "nano:mainnet"
ASSET = "XNO"
SCHEME = "exact"
ADAPTER_ID = "dual-rail/1"
DECIMALS = 30

DESCRIPTION = "Feeless native-coin settlement. Optional - the entries above are unchanged."


class NotAdditive(AssertionError):
    """Raised rather than emit a patch that changes anything existing."""


def nano_entry(pay_to: str, amount_xno: str, resource) -> dict:
    """The single element this adapter appends. Nothing else is ever written."""
    return {
        "scheme": SCHEME,
        "network": NETWORK,
        "asset": ASSET,
        "maxAmountRequired": str(amount_xno),
        "payTo": pay_to,
        "resource": resource,
        "description": DESCRIPTION,
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
