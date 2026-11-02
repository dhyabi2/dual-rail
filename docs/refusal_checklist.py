"""Fifteen cases for a Nano-paid x402 seller (one served, twelve refused, two replay probes), run offline.

    python3 docs/refusal_checklist.py        # from a checkout; no network, no keys

Each line builds a payment the seller must NOT serve and asks verify.verify()
(the same function the adapter runs). Exit 0 only if every case is refused for
the stated reason, and the one honest payment is accepted.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fakenode, verify  # noqa: E402

PAY_TO = "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"  # public genesis account
OTHER = "nano_1111111111111111111111111111111111111111111111111111hifc8npp"
PRICE = 100000000000000000000000000  # 0.0001 XNO in raw (1 XNO = 10**30 raw)
H = lambda n: ("%064X" % n)


def proof(h, network="nano:mainnet"):
    return json.dumps({"scheme": "exact", "network": network, "payload": {"blockHash": h}})


def run(header, node, guard=None, pay_to=PAY_TO):
    try:
        verify.verify(header, pay_to, PRICE, "/r", node, guard or verify.ReplayGuard())
        return "served"
    except verify.Unpaid as e:
        return e.reason + (" (falls through to your other rails)" if e.detail.get("fallthrough") else "")


cases = []
n = fakenode.FakeNode()
n.settle(H(1), PAY_TO, PRICE)
n.settle(H(2), PAY_TO, PRICE - 1)
n.settle(H(3), OTHER, PRICE)
n.unconfirmed(H(4), PAY_TO, PRICE)
n.received(H(5), PRICE)
g = verify.ReplayGuard()
cases += [
    ("exact amount, right payTo, confirmed send", proof(H(1)), n, None, "served"),
    ("no X-PAYMENT header", "", n, None, "no_payment"),
    ("header is not JSON", "not-json", n, None, "malformed_payment"),
    ("header is JSON but not an object", "[1]", n, None, "malformed_payment"),
    ("object with no block hash", json.dumps({"network": "nano:mainnet"}), n, None, "malformed_payment"),
    ("proof for another network (not our business)", proof(H(1), "base"), n, None, "not_our_rail"),
    ("block the node has never seen", proof(H(9)), n, None, "unknown_block"),
    ("block not confirmed yet", proof(H(4)), n, None, "unconfirmed"),
    ("a receive block on your own account", proof(H(5)), n, None, "not_a_send"),
    ("send to someone else", proof(H(3)), n, None, "wrong_destination"),
    ("one raw short", proof(H(2)), n, None, "underpaid"),
    ("your own payTo fails its checksum", proof(H(1)), n, ("bad", "nano_" + "1" * 60), "invalid_payto"),
    ("node unreachable", proof(H(1)), fakenode.DeadNode(), None, "node_unavailable"),
]
failed = 0
for i, (name, header, node, extra, want) in enumerate(cases):
    pay_to = extra[1] if extra else PAY_TO
    got = run(header, node, g if want == "served" else None, pay_to)
    ok = got.split(" ")[0] == want
    failed += not ok
    print("%2d %-4s %-48s %s" % (i, "ok" if ok else "FAIL", name, got))
# the same honest block, spent twice on one resource: one payment buys one call
got = run(proof(H(1)), n, g)
ok = got == "replayed"
failed += not ok
print("%2d %-4s %-48s %s" % (len(cases), "ok" if ok else "FAIL", "the same block again, same resource", got))
# the same block on ANOTHER resource is a different bucket: bind payments to calls yourself
try:
    verify.verify(proof(H(1)), PAY_TO, PRICE, "/other", n, g)
    got = "served"
except verify.Unpaid as e:
    got = e.reason
ok = got == "served"
failed += not ok
print("%2d %-4s %-48s %s" % (len(cases) + 1, "ok" if ok else "FAIL",
      "same block, DIFFERENT resource (guard is per resource)", got))
print("\n%s" % ("all %d behaved as listed" % (len(cases) + 2) if not failed else "%d FAILED" % failed))
sys.exit(1 if failed else 0)
