"""Hosted facilitator laws. No socket: FakeNode / DeadNode stand in for the node."""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fakenode, hosted_facilitator as hf, money, verify

PAY_TO = "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7"
H = "B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F"
AMOUNT = money.xno_to_raw("0.0005")          # derived, never restated


def body(block=H, pay_to=PAY_TO, amount=AMOUNT, network="nano:mainnet", resource="/r"):
    return json.dumps({"paymentPayload": {"network": "nano:mainnet", "payload": {"blockHash": block}},
                       "paymentRequirements": {"network": network, "payTo": pay_to,
                                               "amount": str(amount), "resource": resource}}).encode()


def call(path, b, node, guard=None):
    return hf.handle("POST", path, b, node, guard or verify.ReplayGuard())


class T(unittest.TestCase):
    def node(self, **kw):
        n = fakenode.FakeNode()
        n.settle(H, kw.get("dest", PAY_TO), kw.get("raw", AMOUNT), confirmed=kw.get("confirmed", True),
                 account="nano_3payer")
        return n

    def test_valid(self):
        s, o = call("/verify", body(), self.node())
        self.assertEqual((s, o["isValid"], o["payer"]), (200, True, "nano_3payer"))

    def test_verify_does_not_spend_settle_does(self):
        g, n = verify.ReplayGuard(), self.node()
        self.assertTrue(call("/verify", body(), n, g)[1]["isValid"])
        self.assertTrue(call("/verify", body(), n, g)[1]["isValid"])
        s1 = call("/settle", body(), n, g)[1]
        self.assertTrue(s1["success"]); self.assertEqual(s1["transaction"], H)
        s2 = call("/settle", body(), n, g)[1]
        self.assertFalse(s2["success"]); self.assertEqual(s2["errorReason"], "replayed")
        self.assertEqual(call("/verify", body(), n, g)[1]["invalidReason"], "replayed")

    def test_refusals(self):
        cases = [("underpaid", dict(raw=AMOUNT - 1), {}), ("unconfirmed", dict(confirmed=False), {}),
                 ("wrong_destination", dict(dest="nano_3" + "1" * 59), {})]
        for reason, nk, _ in cases:
            s, o = call("/verify", body(), self.node(**nk))
            self.assertFalse(o["isValid"], reason)
        self.assertEqual(call("/verify", body(), self.node(raw=AMOUNT - 1))[1]["invalidReason"], "underpaid")
        self.assertEqual(call("/verify", body(), self.node(confirmed=False))[1]["invalidReason"], "unconfirmed")
        self.assertEqual(call("/verify", body(), fakenode.FakeNode())[1]["invalidReason"], "unknown_block")

    def test_receive_block_is_not_a_payment(self):
        n = fakenode.FakeNode(); n.received(H, AMOUNT)
        self.assertEqual(call("/verify", body(), n)[1]["invalidReason"], "not_a_send")

    def test_node_outage_is_200_with_fallthrough(self):
        s, o = call("/verify", body(), fakenode.DeadNode())
        self.assertEqual(s, 200); self.assertTrue(o["fallthrough"]); self.assertFalse(o["isValid"])
        s, o = call("/settle", body(), fakenode.DeadNode())
        self.assertEqual(s, 200); self.assertTrue(o["fallthrough"])

    def test_bad_requests_never_5xx(self):
        n = self.node()
        for b in (b"", b"{", b"[]", b"{}", body(pay_to="nano_bad"), body(amount="1.5"), body(amount="0"),
                  body(amount="١٢٣"), body(network="base"), body(block="zz")):
            s, o = call("/verify", b, n)
            self.assertEqual(s, 200); self.assertFalse(o["isValid"])
        self.assertEqual(hf.handle("POST", "/verify", b"x" * (hf.MAX_BODY + 1), n, verify.ReplayGuard())[0], 413)
        self.assertEqual(hf.handle("GET", "/nope", b"", n, None)[0], 404)

    def test_paid_route(self):
        import base64
        os.environ["PAY_TO"] = PAY_TO; os.environ["PRICE_XNO"] = "0.0005"
        try:
            n, g = self.node(), verify.ReplayGuard()
            s, o = hf.handle("GET", "/v1/block-check", b"", n, g, "", "h.example", "hash=" + H)
            self.assertEqual(s, 402)
            e = o["accepts"][0]
            self.assertEqual((e["network"], e["payTo"], e["amount"]), ("nano:mainnet", PAY_TO, str(AMOUNT)))
            pay = json.dumps({"network": "nano:mainnet", "payload": {"blockHash": H}})
            s, o = hf.handle("GET", "/v1/block-check", b"", n, g, pay, "h.example", "hash=" + H)
            self.assertEqual(s, 200)
            s, o = hf.handle("GET", "/v1/block-check", b"", n, g, pay, "h.example", "hash=" + H)
            self.assertEqual(s, 402)                       # replayed: one payment, one call
            s, o = hf.handle("GET", "/.well-known/x402", b"", n, g, "", "h.example")
            self.assertEqual(o["accepts"][0]["resource"], "https://h.example/v1/block-check")
            os.environ["PAY_TO"] = "nano_bad"
            self.assertEqual(hf.handle("GET", "/v1/block-check", b"", n, g, "", "h", "")[0], 404)
        finally:
            os.environ.pop("PAY_TO", None); os.environ.pop("PRICE_XNO", None)

    def test_supported_and_health(self):
        self.assertEqual(hf.handle("GET", "/supported", b"", None, None)[1]["kinds"][0]["network"], "nano:mainnet")
        self.assertTrue(hf.handle("GET", "/health", b"", None, None)[1]["ok"])

    def test_amount_is_integer_exact(self):
        # one raw short of 10**30*0.0005 must fail; exact must pass (no float anywhere)
        self.assertTrue(call("/verify", body(amount=AMOUNT), self.node())[1]["isValid"])
        self.assertFalse(call("/verify", body(amount=AMOUNT + 1), self.node())[1]["isValid"])

    def test_no_secret_surface(self):
        import re
        src = open(hf.__file__).read()
        self.assertEqual(set(re.findall(r'environ\.get\("([A-Z_]+)"', src)),
                         {"NANO_RPC_URL", "PORT", "PAY_TO", "PRICE_XNO", "CONTENT_LENGTH", "REQUEST_METHOD", "PATH_INFO", "HTTP_X_PAYMENT", "HTTP_HOST", "QUERY_STRING"})
        for bad in ("private_key", "sign(", "send_block", '"process"'):
            self.assertNotIn(bad, src)

if __name__ == "__main__":
    unittest.main()
