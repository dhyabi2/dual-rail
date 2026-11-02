"""accepts_snippet: the paste-in generator. No network except a loopback server for the generated test."""
import copy
import http.server
import io
import json
import os
import sys
import threading
import types
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import accepts_snippet as snip  # noqa: E402
import testhost  # noqa: E402
import usd_quote  # noqa: E402

PAY_TO = "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"
USDC = {"scheme": "exact", "network": "base", "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "maxAmountRequired": "10000", "payTo": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "resource": "https://example.dev/report", "extra": {"name": "USD Coin", "decimals": 6}}
OTHER = {"scheme": "exact", "network": "solana", "asset": "USDC", "maxAmountRequired": "10000",
         "payTo": "So11111111111111111111111111111111111111112", "resource": "https://example.dev/report"}
MANIFEST = {"x402Version": 1, "accepts": [USDC, OTHER]}
FEEDS = [lambda: 0.32, lambda: 0.32]


def fetch_of(doc):
    text = doc if isinstance(doc, str) else json.dumps(doc)
    return lambda location: (200, {}, text)


def gen(doc=MANIFEST, **kw):
    kw.setdefault("feeds", FEEDS)
    kw.setdefault("now", 1000)
    return snip.generate("manifest.json", PAY_TO, fetch=fetch_of(doc), **kw)


class Manifest(unittest.TestCase):

    def test_exactly_one_entry_is_appended_and_nothing_else_changes(self):
        out = gen()
        acc = out["manifest"]["accepts"]
        self.assertEqual(acc[:2], MANIFEST["accepts"])
        self.assertEqual(len(acc), 3)
        self.assertEqual(acc[-1]["network"], "nano:mainnet")
        self.assertEqual(acc[-1]["payTo"], PAY_TO)
        self.assertEqual(MANIFEST["accepts"][0], USDC)            # input not mutated

    def test_the_diff_only_adds(self):
        out = gen()
        removed = [l for l in out["manifest_diff"].splitlines() if l.startswith("-") and not l.startswith("---")]
        self.assertTrue(all(l.strip() in ("-    }", "-    }") for l in removed), removed)
        self.assertIn('+      "network": "nano:mainnet"', out["manifest_diff"])

    def test_already_present_emits_nothing_to_do(self):
        doc = copy.deepcopy(MANIFEST)
        doc["accepts"].append({"scheme": "exact", "network": "nano:mainnet", "amount": "1"})
        self.assertEqual(gen(doc)["status"], "already_present")

    def test_a_bad_checksum_pay_to_is_refused_before_anything_is_read(self):
        with self.assertRaises(ValueError):
            snip.generate("m.json", PAY_TO[:-1] + "x", fetch=lambda *_: 1 / 0, feeds=FEEDS)

    def test_yaml_manifest_gets_the_entry_but_no_diff(self):
        text = json.dumps(MANIFEST)
        out = snip.generate("m.yaml", PAY_TO, fetch=fetch_of(text), feeds=FEEDS, now=1)
        self.assertIsNone(out["manifest_diff"])
        self.assertIn("nano_entry", out)

    def test_multi_resource_manifest_is_refused_not_guessed(self):
        with self.assertRaises(Exception):
            gen({"resources": [{"accepts": [USDC]}]})


class Pricing(unittest.TestCase):

    def test_usd_is_read_from_the_manifests_own_usdc_entry(self):
        p = gen()["price"]
        self.assertEqual((p["mode"], p["usd"], p["usd_cents"], p["usd_source"]), ("usd_pegged", "0.01", 1, "manifest_usdc_entry"))

    def test_conversion_is_integer_math_and_rounds_up(self):
        p = gen(usd="0.01")["price"]
        micro = 320000
        want = -(-(1 * 10 ** 6 * 10 ** 30) // (100 * micro))
        self.assertEqual(p["amount_raw"], str(want))
        self.assertEqual(int(p["amount_raw"]) * 100 * micro >= 10 ** 36, True)       # never short
        self.assertEqual(p["quoted_at"], 1000)
        self.assertEqual(p["expires_at"], 1060)

    def test_the_entry_carries_the_same_raw_amount_the_price_block_states(self):
        out = gen(usd="0.01")
        self.assertEqual(out["nano_entry"]["amount"], out["price"]["amount_raw"])
        self.assertEqual(out["nano_entry"]["maxAmountRequired"], out["price"]["amount_raw"])

    def test_two_feeds_that_disagree_refuse_to_quote(self):
        with self.assertRaises(RuntimeError):
            gen(usd="0.01", feeds=[lambda: 0.30, lambda: 0.33])

    def test_one_answering_feed_refuses(self):
        def dead():
            raise OSError("down")
        with self.assertRaises(RuntimeError):
            gen(usd="0.01", feeds=[lambda: 0.32, dead])

    def test_a_fixed_xno_amount_skips_the_feeds_entirely(self):
        out = gen(amount="0.0001", feeds=[lambda: 1 / 0])
        self.assertEqual(out["price"]["mode"], "fixed_xno")
        self.assertEqual(out["nano_entry"]["amount"], "100000000000000000000000000")

    def test_no_price_anywhere_is_its_own_exit(self):
        doc = {"accepts": [OTHER | {"asset": "EURC", "maxAmountRequired": "5"}]} if sys.version_info >= (3, 9) else \
            {"accepts": [dict(OTHER, asset="EURC", maxAmountRequired="5")]}
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = snip.main(["--manifest", "m.json", "--pay-to", PAY_TO], fetch=fetch_of(doc), feeds=FEEDS)
        self.assertEqual(code, snip.EXIT_NO_PRICE)

    def test_usd_must_be_whole_cents(self):
        for bad in ("0.001", "-1", "abc", "0"):
            with self.assertRaises(ValueError):
                snip.usd_to_cents(bad)


class Generated(unittest.TestCase):

    def serve(self, doc):
        body = json.dumps(doc).encode()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(402)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d/" % srv.server_port

    def run_generated_test(self, served_doc, out):
        url = self.serve(served_doc)
        src = out["test"].replace("<URL OF YOUR 402/MANIFEST>", url)
        src = src.replace("URL = %r" % "<URL OF YOUR 402/MANIFEST>", "URL = %r" % url)
        mod = types.ModuleType("generated_test")
        mod.__dict__["URL"] = url
        exec(compile(src.replace("URL = '<URL OF YOUR 402/MANIFEST>'", "URL = %r" % url), "gen", "exec"), mod.__dict__)
        mod.URL = url
        suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        return result.wasSuccessful()

    def test_the_generated_test_passes_against_the_patched_manifest_and_fails_against_the_original(self):
        out = gen(usd="0.01")
        self.assertTrue(self.run_generated_test(out["manifest"], out))
        self.assertFalse(self.run_generated_test(MANIFEST, out))
        changed = copy.deepcopy(out["manifest"])
        changed["accepts"][0]["maxAmountRequired"] = "1"
        self.assertFalse(self.run_generated_test(changed, out))

    def test_the_generated_python_middleware_runs_and_adds_the_nano_entry_once(self):
        out = gen(usd="0.01")
        after = [l[1:] for l in out["middleware_diff"].splitlines()
                 if l[:1] in (" ", "+") and not l.startswith("+++")]     # the file as it reads after the diff
        code = "\n".join(after)
        gate, protected = testhost.make_host()
        stub = types.ModuleType("myapp")
        stub.protected_app, stub.x402_middleware = protected, gate
        saved = (usd_quote.feed_coingecko, usd_quote.feed_kucoin, sys.modules.get("myapp"), os.environ.get("NANO_NODE_URL"))
        usd_quote.feed_coingecko, usd_quote.feed_kucoin = (lambda: 0.32), (lambda: 0.32)
        sys.modules["myapp"] = stub
        os.environ["NANO_NODE_URL"] = "http://127.0.0.1:9"
        try:
            ns = {}
            exec(compile(code, "mw", "exec"), ns)
            captured = {}

            def start(status, headers, exc=None):
                captured["status"] = status
            env = {"REQUEST_METHOD": "GET", "PATH_INFO": "/report", "wsgi.input": io.BytesIO(b"")}
            body = b"".join(ns["application"](env, start))
            self.assertTrue(captured["status"].startswith("402"))
            accepts = json.loads(body)["accepts"]
            self.assertEqual([a["network"] for a in accepts], ["base", "solana", "nano:mainnet"])
            self.assertEqual(accepts[-1]["amount"], str(-(-(10 ** 36) // (100 * 320000))))
        finally:
            usd_quote.feed_coingecko, usd_quote.feed_kucoin = saved[0], saved[1]
            if saved[2] is None:
                sys.modules.pop("myapp", None)
            else:
                sys.modules["myapp"] = saved[2]
            if saved[3] is None:
                os.environ.pop("NANO_NODE_URL", None)
            else:
                os.environ["NANO_NODE_URL"] = saved[3]

    def test_node_diff_names_the_address_and_keeps_amount_a_string(self):
        out = gen(amount="0.0001", framework="node")
        self.assertIn("payTo: '%s'" % PAY_TO, out["middleware_diff"])
        self.assertIn("amountXno: '0.0001'", out["middleware_diff"])

    def test_out_dir_writes_the_five_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = snip.main(["--manifest", "m.json", "--pay-to", PAY_TO, "--usd", "0.01", "--out-dir", tmp],
                                 fetch=fetch_of(MANIFEST), feeds=FEEDS)
            self.assertEqual(code, 0)
            self.assertEqual(sorted(os.listdir(tmp)), ["manifest.diff", "manifest.nano.json", "middleware.diff",
                                                       "price.json", "test_nano_accepts.py"])


if __name__ == "__main__":
    unittest.main()
