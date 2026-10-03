"""The Node and Python bindings must behave identically. This checks it.

The definition of done says "Node and Python bindings behave identically on the
whole test matrix - run the same fixture suite against both". A promise that
two implementations agree is worth nothing without the thing that runs them
side by side, so: one matrix file, both bindings, deep equality.

Where they disagree, this fails and prints which case. Node is skipped, loudly,
if no `node` is on PATH - never passed silently, because a skipped conformance
check is exactly the state in which the two drift.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adapter as _adapter
import challenge as _challenge
import declaration as _declaration
import fakenode
import money
import nanoaddr
import network as _network
import testhost
import verify as _verify

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MATRIX = os.path.join(HERE, "tests", "conformance-matrix.json")

PAY_TO = "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"
BURN = "nano_1111111111111111111111111111111111111111111111111111hifc8npp"
AMOUNT = "0.0001"
BLOCK = "9F2C" + "0" * 60


def build_matrix():
    """The cases both bindings must agree on. Written once, run twice."""
    two = testhost.challenge()
    one = testhost.challenge(testhost.USDC_ENTRIES[:1])
    three = testhost.challenge(testhost.USDC_ENTRIES + [dict(
        testhost.USDC_ENTRIES[0], network="polygon", maxAmountRequired="0.02")])
    patched = dict(two, accepts=two["accepts"] + [
        _challenge.nano_entry(PAY_TO, AMOUNT, "https://example.dev/report")])
    return {
        "addresses": [
            BURN, PAY_TO, "xrb_" + BURN[5:], BURN[:-1] + "3", PAY_TO[:-1] + "1",
            PAY_TO[:-5], "nano_", "", "notanaddress", "nano_0" + PAY_TO[6:],
            PAY_TO.replace("nano_3t6", "nano_2t6", 1), " " + PAY_TO + " ",
        ],
        "amounts": [
            "0.0001", "0.000001", "0.1", "0.2", "0.3", "100", "99.999999",
            "0." + "0" * 29 + "1", "0." + "0" * 30 + "1", "1e-3", "-1", "", "1.2.3", "abc",
        ],
        "prices": ["0.0001", "0.000001", "100", "0.0000001", "101", "0", "-0.1", "1e-3"],
        "challenges": [two, one, three, patched, {"accepts": []}, {"nope": 1}],
        "proofs": [
            json.dumps({"scheme": "exact", "network": "nano:mainnet",
                        "payload": {"blockHash": BLOCK}}),
            json.dumps({"network": "base"}),
            json.dumps({"network": "nano:mainnet", "payload": {"blockHash": "xy"}}),
            json.dumps({"network": "nano:mainnet"}),
            "not json", "", "e30=", "[]",
        ],
        "networks": [
            "nano:mainnet", "nano-mainnet", "NANO:MAINNET", " nano:mainnet ",
            "Nano-Mainnet", "nano", "xno", "XNO", "nano:testnet", "nano-beta",
            "xno:mainnet", "xno-mainnet", "nano:", "nanomainnet", "base",
            "solana:mainnet", "eip155:8453", "", "   ", None, 42, True, [], {},
            "nano:mainnet:extra", "nano_mainnet", "nano:main", "a:b",
        ],
        "declarations": _declaration_cases(),
        "payments": [
            {"header": _proof(BLOCK), "block": _block(BLOCK, PAY_TO, 10 ** 26, True)},
            {"header": _proof(BLOCK), "block": _block(BLOCK, PAY_TO, 10 ** 26 - 1, True)},
            {"header": _proof(BLOCK), "block": _block(BLOCK, PAY_TO, 10 ** 26 + 1, True)},
            {"header": _proof(BLOCK), "block": _block(BLOCK, PAY_TO, 10 ** 26, False)},
            {"header": _proof(BLOCK), "block": _block(BLOCK, BURN, 10 ** 26, True)},
            {"header": _proof(BLOCK), "block": None},
            {"header": _proof(BLOCK), "block": _block(BLOCK, PAY_TO, 10 ** 26, True),
             "replay": True},
            {"header": json.dumps({"network": "base"}), "block": None},
            {"header": "", "block": None},
            {"header": _proof(BLOCK), "block": _block(BLOCK, PAY_TO, 10 ** 30, True),
             "required": "100"},
        ],
    }


def _declaration_cases():
    """402 documents both bindings must read the same way.

    Deliberately heavy on the near-misses: a document that is wrong in one
    field is where two implementations drift, and a document that is wrong in
    every field is one they both reject for free.
    """
    def entry(**over):
        base = {"scheme": "exact", "network": "nano:mainnet", "asset": "XNO",
                "amount": "100000000000000000000000000", "payTo": PAY_TO,
                "maxTimeoutSeconds": 60}
        base.update(over)
        return {key: value for key, value in base.items() if value is not _DROP}

    def doc(*entries, **over):
        body = {"x402Version": 2, "resource": {"url": "https://example.dev/report"},
                "accepts": list(entries)}
        body.update(over)
        return {key: value for key, value in body.items() if value is not _DROP}

    v1 = dict(entry(amount=_DROP), maxAmountRequired="0.0001",
              resource="https://example.dev/report", description="a rail")
    return [
        doc(entry()),
        doc(entry(amount="0.0001")),
        doc(entry(amount="0")),
        doc(entry(amount="9" * 40)),
        doc(entry(amount=123)),
        doc(entry(amount=_DROP, maxAmountRequired="0.0001")),
        doc(entry(maxTimeoutSeconds="60")),
        doc(entry(maxTimeoutSeconds=0)),
        doc(entry(maxTimeoutSeconds=True)),
        doc(entry(maxTimeoutSeconds=60.5)),
        doc(entry(network="nano-mainnet")),
        doc(entry(network="nano")),
        doc(entry(network="nano:testnet")),
        doc(entry(network="NANO:MAINNET")),
        doc(entry(network=42)),
        doc(entry(payTo=PAY_TO[:-1] + "1")),
        doc(entry(payTo="xrb_" + PAY_TO[5:])),
        doc(entry(payTo=" " + PAY_TO + " ")),
        doc(entry(payTo="")),
        doc(entry(payTo=None)),
        doc(entry(payTo=42)),
        doc(entry(asset="USDC")),
        doc(entry(asset="xno")),
        doc(entry(scheme="upto")),
        doc(entry(extra={"decimals": 18})),
        doc(entry(extra={"decimals": 30})),
        doc(entry(extra="nano-mainnet")),
        doc(entry(extra=None)),
        doc(entry(), resource=_DROP),
        doc(entry(), resource={"url": ""}),
        doc(entry(), resource="https://example.dev/report"),
        doc(),
        doc(entry(network="nano-mainnet", payTo=BURN), entry()),
        doc(entry(), entry()),
        doc({"scheme": "exact", "network": "base", "asset": "USDC",
             "amount": "10000", "payTo": "0x" + "1" * 40,
             "maxTimeoutSeconds": 60}, entry()),
        doc({"scheme": "exact", "network": "base", "asset": "USDC",
             "maxAmountRequired": "0.01", "payTo": "0x" + "1" * 40}, entry()),
        doc(None, 5, "x", entry()),
        {"x402Version": 1, "accepts": [v1]},
        {"x402Version": 1, "accepts": [dict(v1, mimeType=None)]},
        {"x402Version": 1, "accepts": [dict(v1, outputSchema="x")]},
        {"x402Version": 1, "accepts": [dict(v1, description=None)]},
        {"x402Version": 1, "accepts": [entry()]},
        {"x402Version": 2.0, "resource": {"url": "u"}, "accepts": [entry()]},
        {"x402Version": True, "accepts": [entry()]},
        {"x402Version": 3, "accepts": [entry()]},
        {"accepts": [entry()]},
        {"x402Version": 2, "accepts": "nope"},
        {"x402Version": 2},
        [], "nope", None, 7, {},
    ]


_DROP = object()


def _proof(block_hash):
    return json.dumps({"scheme": "exact", "network": "nano:mainnet",
                       "payload": {"blockHash": block_hash}})


def _block(block_hash, destination, amount_raw, confirmed):
    return {"hash": block_hash, "destination": destination,
            "amount_raw": str(amount_raw), "confirmed": confirmed}


# ----------------------------------------------------- the Python half

def python_result(matrix):
    return {
        "addresses": [_address(value) for value in matrix["addresses"]],
        "amounts": [_amount(value) for value in matrix["amounts"]],
        "prices": [_price(value) for value in matrix["prices"]],
        "challenges": [_challenge_case(shape) for shape in matrix["challenges"]],
        "networks": [_network.classify(value) for value in matrix["networks"]],
        "declarations": [_declaration.inspect(value) for value in matrix["declarations"]],
        "proofs": [_proof_case(header) for header in matrix["proofs"]],
        "payments": [_payment_case(case) for case in matrix["payments"]],
    }


def _address(value):
    verdict = nanoaddr.validate(value)
    if verdict["valid"]:
        return {"valid": True, "normalised": verdict["normalised"],
                "public_key": verdict["public_key"]}
    return {"valid": False, "reason": verdict["reason"]}


def _amount(value):
    try:
        raw = money.xno_to_raw(value)
    except money.AmountError as exc:
        return {"ok": False, "reason": exc.reason}
    return {"ok": True, "raw": str(raw), "back": money.raw_to_xno(raw)}


def _price(value):
    try:
        return {"ok": True, "raw": str(money.check_price(value))}
    except money.AmountError as exc:
        return {"ok": False, "reason": exc.reason}


def _challenge_case(shape):
    rail = _adapter.dual_rail(payTo=PAY_TO, amountXno=AMOUNT)
    try:
        return {"ok": True, "patched": rail.patch_challenge(shape)}
    except _challenge.NotAdditive:
        return {"ok": False, "reason": "NotAdditive"}


def _proof_case(header):
    try:
        payload = _verify.parse_payment(header)
    except _verify.Unpaid as exc:
        return {"ok": False, "reason": exc.reason}
    ours = _verify.is_ours(payload)
    try:
        block = _verify.block_hash_of(payload) if ours else None
    except _verify.Unpaid as exc:
        return {"ok": False, "reason": exc.reason}
    return {"ok": True, "ours": ours, "block": block}


def _payment_case(case):
    node = fakenode.FakeNode()
    block = case.get("block")
    if block:
        node.settle(block["hash"], block["destination"], int(block["amount_raw"]),
                    block["confirmed"])
    rail = _adapter.dual_rail(payTo=PAY_TO, amountXno=case.get("required") or AMOUNT, node=node)
    resource = case.get("resource") or "https://example.dev/report"
    if case.get("replay"):
        rail.check_payment(case["header"], resource)
    try:
        payment = _verify.verify(case["header"], rail.pay_to, rail.amount_raw, resource,
                                 node, rail.guard)
        verdict = {"paid": True, "block_hash": payment["block_hash"],
                   "overpaid_raw": str(payment["overpaid_raw"])}
    except _verify.Unpaid as exc:
        verdict = {"paid": False, "reason": exc.reason}
    verdict["node_calls"] = len(node.calls)
    return verdict


class Conformance(unittest.TestCase):

    def test_the_two_bindings_agree_on_the_whole_matrix(self):
        node_binary = shutil.which("node")
        if node_binary is None:                          # pragma: no cover
            self.fail("node is not on PATH, so the conformance matrix cannot run. "
                      "This check is never skipped silently: an unrun conformance "
                      "suite is exactly the state in which the two bindings drift.")

        matrix = build_matrix()
        with open(MATRIX, "w", encoding="utf-8") as handle:
            json.dump(matrix, handle, indent=2)

        result = subprocess.run(
            [node_binary, os.path.join(HERE, "node", "conformance.js"), MATRIX],
            capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        from_node = json.loads(result.stdout)
        from_python = python_result(matrix)

        self.assertEqual(sorted(from_node), sorted(from_python))
        for section in sorted(from_python):
            self.assertEqual(len(from_node[section]), len(from_python[section]), section)
            for index, (js, py) in enumerate(zip(from_node[section], from_python[section])):
                self.assertEqual(
                    js, py,
                    "the two bindings disagree on %s[%d]\n  input:  %s\n  node:   %s\n"
                    "  python: %s" % (section, index,
                                      json.dumps(matrix[section][index])[:200],
                                      json.dumps(js), json.dumps(py)))

    def test_the_matrix_is_not_trivially_small(self):
        """A matrix that shrank to nothing would pass the test above silently."""
        matrix = build_matrix()
        self.assertGreaterEqual(sum(len(v) for v in matrix.values()), 50)
        for section, cases in matrix.items():
            self.assertGreaterEqual(len(cases), 6, section)


if __name__ == "__main__":
    unittest.main()
