"""The fourteen numbered tests of the dual-rail design spec,
in spec order and under their spec names, then the error table and the money.

Nothing here opens a socket to anything but 127.0.0.1, and nothing reaches a
Nano node: `fakenode.FakeNode` answers block_info from memory and
`fakenode.DeadNode` raises the way an unreachable node does.
"""

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from wsgiref.simple_server import WSGIRequestHandler, make_server
from wsgiref.util import setup_testing_defaults

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adapter as _adapter
import challenge as _challenge
import cli
import fakenode
import money
import nanoaddr
import testhost
import verify as _verify

PAY_TO = "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"
BURN = "nano_1111111111111111111111111111111111111111111111111111hifc8npp"
AMOUNT = "0.0001"
AMOUNT_RAW = 10 ** 26
BLOCK = "9F2C" + "0" * 60
RESOURCE = "https://example.dev/report"


def build(node=None, pay_to=PAY_TO, amount=AMOUNT, **extra):
    return _adapter.dual_rail(payTo=pay_to, amountXno=amount, node=node, **extra)


def request(app, headers=None, path="/report"):
    """One WSGI round trip. Returns (status, headers, body)."""
    environ = {}
    setup_testing_defaults(environ)
    environ["PATH_INFO"] = path
    environ["HTTP_HOST"] = "example.dev"
    for name, value in (headers or {}).items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value
    captured = {}

    def start_response(status, response_headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = list(response_headers)
        return lambda data: None

    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def proof(block_hash=BLOCK, network=_challenge.NETWORK):
    return json.dumps({"scheme": "exact", "network": network,
                       "payload": {"blockHash": block_hash}})


def mounted(node):
    gate, protected = testhost.make_host()
    return build(node=node).wsgi(protected, gate=gate)


# ------------------------------------------------------- the fourteen tests

class SpecTests(unittest.TestCase):

    def test_existing_accepts_unchanged(self):
        """1. the first two elements are deeply equal to the originals, in order."""
        original = testhost.challenge()
        before = copy.deepcopy(original["accepts"])
        patched = build().patch_challenge(original)
        self.assertEqual(patched["accepts"][0], before[0])
        self.assertEqual(patched["accepts"][1], before[1])
        self.assertEqual(patched["accepts"][:2], before)
        self.assertEqual(original["accepts"], before)       # the input is not mutated

    def test_nano_entry_appended_last(self):
        """2. the Nano entry is at index len-1."""
        patched = build().patch_challenge(testhost.challenge())
        accepts = patched["accepts"]
        self.assertEqual(_challenge.find_nano(accepts), len(accepts) - 1)
        self.assertEqual(accepts[-1]["network"], "nano:mainnet")
        self.assertEqual(accepts[-1]["asset"], "XNO")
        self.assertEqual(accepts[-1]["resource"], RESOURCE)
        self.assertEqual(accepts[-1]["extra"], {"decimals": 30, "adapter": "dual-rail/1"})

    def test_construction_rejects_bad_payto(self):
        """3. the burn address with its last char altered -> invalid_address, and
        nothing is ever mounted."""
        broken = BURN[:-1] + ("3" if BURN[-1] != "3" else "4")
        self.assertEqual(len(broken), len(BURN))            # well-formed but for the checksum
        self.assertFalse(nanoaddr.is_valid(broken))

        # Boot the service the way a service boots: construct, then bind. The
        # construction must raise, so the bind line is never reached and no port
        # can have been opened.
        bound = []

        def boot(pay_to):
            rail = build(pay_to=pay_to)                     # must raise for `broken`
            bound.append(live_server(rail.wsgi(lambda e, s: [b""])).__enter__())
            return rail

        with self.assertRaises(_adapter.ConfigurationError) as caught:
            boot(broken)
        self.assertEqual(caught.exception.reason, "invalid_address")
        self.assertEqual(bound, [], "a port was bound despite an unpayable payTo")
        self.assertIn("can never receive a payment", caught.exception.message)

        # ...and the same call with a good address does get past construction.
        self.assertIsNotNone(build(pay_to=PAY_TO))

    def test_node_outage_falls_through(self):
        """4. every node call raises; an unpaid request still gets the host's 402
        with all entries, a USDC proof still succeeds, and no 5xx anywhere."""
        node = fakenode.DeadNode()
        app = mounted(node)
        seen = []

        status, headers, body = request(app, {"X-PAYMENT": proof()})
        seen.append(status)
        accepts = json.loads(body)["accepts"]
        self.assertTrue(status.startswith("402"))
        self.assertEqual(len(accepts), 3)

        status, _, _ = request(app)
        seen.append(status)

        status, _, body = request(app, {"X-PAYMENT": json.dumps({"network": "base"})})
        seen.append(status)
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(json.loads(body)["report"], "the goods")

        for status in seen:
            self.assertLess(int(status.split(" ")[0]), 500, "a 5xx escaped: %s" % status)
        self.assertTrue(node.calls, "the node was never even tried")

    def test_valid_nano_payment_admits_request(self):
        """5. a confirmed block to payTo for the exact amount is served as paid."""
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW)
        status, _, body = request(mounted(node), {"X-PAYMENT": proof()})
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(json.loads(body)["report"], "the goods")

    def test_unconfirmed_payment_rejected(self):
        """6. confirmed: false -> 402."""
        node = fakenode.FakeNode()
        node.unconfirmed(BLOCK, PAY_TO, AMOUNT_RAW)
        status, _, _ = request(mounted(node), {"X-PAYMENT": proof()})
        self.assertTrue(status.startswith("402"), status)

    def test_wrong_destination_rejected(self):
        """7. confirmed block to a different address -> 402."""
        node = fakenode.FakeNode()
        node.settle(BLOCK, BURN, AMOUNT_RAW)
        status, _, _ = request(mounted(node), {"X-PAYMENT": proof()})
        self.assertTrue(status.startswith("402"), status)

    def test_underpayment_rejected(self):
        """8. one raw below required -> 402. One raw, not one cent."""
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW - 1)
        status, _, _ = request(mounted(node), {"X-PAYMENT": proof()})
        self.assertTrue(status.startswith("402"), status)

    def test_overpayment_accepted(self):
        """9. above required -> served as paid."""
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW + 1)
        status, _, _ = request(mounted(node), {"X-PAYMENT": proof()})
        self.assertTrue(status.startswith("200"), status)

    def test_replay_rejected(self):
        """10. the same block twice -> first served, second 402."""
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW)
        app = mounted(node)
        first, _, _ = request(app, {"X-PAYMENT": proof()})
        second, _, _ = request(app, {"X-PAYMENT": proof()})
        self.assertTrue(first.startswith("200"), first)
        self.assertTrue(second.startswith("402"), second)

    def test_patch_is_additive_only(self):
        """11. five fixture shapes, and none yields a patch that removes a
        semantic element."""
        directory = tempfile.mkdtemp()
        try:
            for name, blob in fixtures().items():
                path = os.path.join(directory, name)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(blob)
                before = load_manifest(path)

                code, out, err = run_cli(["add", "--manifest", path,
                                          "--pay-to", PAY_TO, "--amount", AMOUNT])
                self.assertEqual(code, 0, "%s: %s%s" % (name, out, err))
                entry = json.loads(out)
                self.assertEqual(entry["network"], "nano:mainnet")

                # Re-parsing the manifest with the entry appended gives the
                # original entries, unchanged, plus exactly one.
                after = before["accepts"] + [entry]
                self.assertEqual(after[:len(before["accepts"])], before["accepts"], name)
                self.assertEqual(len(after), len(before["accepts"]) + 1, name)

                code, out, err = run_cli(["add", "--manifest", path, "--pay-to", PAY_TO,
                                          "--amount", AMOUNT, "--diff"])
                if name.endswith((".yaml", ".yml")):
                    # A YAML round trip rewrites lines this tool did not author, so
                    # the tool refuses rather than emit a diff it cannot prove.
                    self.assertEqual(code, 3, name)
                    self.assertIn("refusing to emit a non-additive patch", err)
                    continue
                self.assertEqual(code, 0, "%s: %s" % (name, err))
                removals = [line for line in out.splitlines()
                            if line.startswith("-") and not line.startswith("---")]
                for line in removals:
                    self.assertIn(line[1:].strip(), ("}", "},", "]", "],"),
                                  "%s: the patch deletes a semantic line: %r" % (name, line))
        finally:
            import shutil
            shutil.rmtree(directory)

    def test_add_is_idempotent(self):
        """12. add on an already-patched manifest exits 0, already_present, no patch."""
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "patched.json")
            patched = build().patch_challenge(testhost.challenge())
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(patched, handle, indent=2)
            code, out, err = run_cli(["add", "--manifest", path,
                                      "--pay-to", PAY_TO, "--amount", AMOUNT])
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["status"], "already_present")
            self.assertNotIn("maxAmountRequired", out)
        finally:
            import shutil
            shutil.rmtree(directory)

    def test_verify_reports_both_rails(self):
        """13. against a local server with the adapter mounted: pass, 7 checks,
        and rails_before == rails_after for the pre-existing entries."""
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW)
        with live_server(mounted(node)) as base:
            code, out, err = run_cli(["verify", base + "/report",
                                      "--payment", BLOCK, "--json"])
            report = json.loads(out)
        self.assertTrue(report["pass"], json.dumps(report, indent=2))
        self.assertEqual(len(report["checks"]), 7)
        self.assertEqual(code, 0)
        self.assertEqual(report["rails_after"][:len(report["rails_before"])],
                         report["rails_before"])
        self.assertEqual(len(report["rails_after"]), len(report["rails_before"]) + 1)

    def test_402_headers_byte_identical_apart_from_accepts(self):
        """14. every header and every field is identical except accepts[]'s length."""
        node = fakenode.FakeNode()
        gate, protected = testhost.make_host()
        bare_status, bare_headers, bare_body = request(gate)
        app = build(node=node).wsgi(protected, gate=gate)
        with_status, with_headers, with_body = request(app)

        self.assertEqual(bare_status, with_status)

        def without_length(headers):
            return sorted((name.lower(), value) for name, value in headers
                          if name.lower() != "content-length")

        self.assertEqual(without_length(bare_headers), without_length(with_headers))

        bare, patched = json.loads(bare_body), json.loads(with_body)
        self.assertEqual(set(bare), set(patched))
        for field in bare:
            if field != "accepts":
                self.assertEqual(bare[field], patched[field], field)
        self.assertEqual(len(patched["accepts"]), len(bare["accepts"]) + 1)
        self.assertEqual(patched["accepts"][:len(bare["accepts"])], bare["accepts"])


# ------------------------------------------------------------- error table

class ErrorTable(unittest.TestCase):

    def test_price_out_of_range(self):
        for bad in ("0.0000001", "101", "0"):
            with self.assertRaises(_adapter.ConfigurationError) as caught:
                build(amount=bad)
            self.assertEqual(caught.exception.reason, "price_out_of_range", bad)

    def test_a_float_price_is_refused_at_construction(self):
        with self.assertRaises(_adapter.ConfigurationError) as caught:
            build(amount=0.0001)
        self.assertEqual(caught.exception.reason, "price_out_of_range")

    def test_on_verify_error_must_be_fallthrough(self):
        for bad in ("reject", "503", "", None):
            with self.assertRaises(_adapter.ConfigurationError) as caught:
                build(onVerifyError=bad)
            self.assertEqual(caught.exception.reason, "invalid_on_verify_error")

    def test_network_is_fixed(self):
        with self.assertRaises(_adapter.ConfigurationError) as caught:
            build(network="nano:beta")
        self.assertEqual(caught.exception.reason, "invalid_network")

    def test_node_outage_is_logged_once_not_once_per_request(self):
        lines = []
        gate, protected = testhost.make_host()
        rail = build(node=fakenode.DeadNode(), logger=lines.append)
        app = rail.wsgi(protected, gate=gate)
        for _ in range(5):
            request(app, {"X-PAYMENT": proof()})
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("falling through", lines[0])

    def test_a_usdc_proof_is_never_examined_as_ours(self):
        node = fakenode.FakeNode()
        request(mounted(node), {"X-PAYMENT": json.dumps({"network": "base"})})
        self.assertEqual(node.calls, [], "a USDC proof reached the Nano node")

    def test_malformed_payment_falls_through_not_errors(self):
        node = fakenode.FakeNode()
        app = mounted(node)
        for header in ("not json", "", "e30=", json.dumps({"network": "nano:mainnet"}),
                       json.dumps({"network": "nano:mainnet", "payload": {"blockHash": "xy"}})):
            status, _, _ = request(app, {"X-PAYMENT": header})
            self.assertTrue(status.startswith("402"), "%r -> %s" % (header, status))
            self.assertLess(int(status.split(" ")[0]), 500)

    def test_base64_payment_is_accepted(self):
        import base64
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW)
        encoded = base64.b64encode(proof().encode()).decode()
        status, _, _ = request(mounted(node), {"X-PAYMENT": encoded})
        self.assertTrue(status.startswith("200"), status)

    def test_a_402_we_do_not_understand_is_left_alone(self):
        node = fakenode.FakeNode()

        def gate(environ, start_response):
            start_response("402 Payment Required", [("Content-Type", "text/html")])
            return [b"<html>pay up</html>"]

        def protected(environ, start_response):            # pragma: no cover
            start_response("200 OK", [])
            return [b""]

        app = build(node=node).wsgi(protected, gate=gate)
        status, _, body = request(app)
        self.assertTrue(status.startswith("402"))
        self.assertEqual(body, b"<html>pay up</html>")

    def test_a_non_402_response_is_never_touched(self):
        node = fakenode.FakeNode()

        def gate(environ, start_response):
            blob = json.dumps({"accepts": []}).encode()
            start_response("200 OK", [("Content-Type", "application/json")])
            return [blob]

        app = build(node=node).wsgi(lambda e, s: [b""], gate=gate)
        status, _, body = request(app)
        self.assertEqual(json.loads(body), {"accepts": []})

    def test_replay_is_scoped_per_resource(self):
        """One payment buys one call - of one resource. A different resource is a
        different purchase, and must not silently reuse the block."""
        guard = _verify.ReplayGuard()
        guard.remember("https://a/one", BLOCK)
        self.assertTrue(guard.seen("https://a/one", BLOCK))
        self.assertFalse(guard.seen("https://a/two", BLOCK))

    def test_verify_fails_without_a_payment_proof(self):
        node = fakenode.FakeNode()
        with live_server(mounted(node)) as base:
            code, out, _ = run_cli(["verify", base + "/report", "--json"])
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertFalse(report["pass"])
        failed = [c["name"] for c in report["checks"] if not c["pass"]]
        self.assertEqual(failed, ["nano rail settles"])

    def test_inspect_is_read_only_and_reports_the_rails(self):
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "m.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(testhost.challenge(), handle, indent=2)
            with open(path, "rb") as handle:
                before = handle.read()
            code, out, _ = run_cli(["inspect", path])
            self.assertEqual(code, 0)
            report = json.loads(out)
            self.assertEqual(report["entries"], 2)
            self.assertFalse(report["nano_entry_present"])
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), before, "inspect wrote to the manifest")
        finally:
            import shutil
            shutil.rmtree(directory)

    def test_add_rejects_a_bad_payto_before_reading_anything(self):
        code, out, _ = run_cli(["add", "--manifest", "/does/not/exist.json",
                                "--pay-to", BURN[:-1] + "3", "--amount", AMOUNT])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"], "invalid_address")


# ---------------------------------------------------------------- the money

class Money(unittest.TestCase):

    def test_one_raw_below_is_below(self):
        self.assertEqual(money.xno_to_raw(AMOUNT), AMOUNT_RAW)
        self.assertLess(AMOUNT_RAW - 1, money.xno_to_raw(AMOUNT))

    def test_a_float_amount_is_refused(self):
        with self.assertRaises(money.AmountError) as caught:
            money.xno_to_raw(0.1)
        self.assertEqual(caught.exception.reason, "float_amount")

    def test_the_hardest_decimals_round_trip(self):
        """Every one of these is a value a float gets wrong."""
        for value in ("0.000001", "0.1", "0.2", "0.3", "100", "99.999999",
                      "0." + "0" * 29 + "1", "0.000000000000000000000000000003"):
            raw = money.xno_to_raw(value)
            self.assertEqual(money.xno_to_raw(money.raw_to_xno(raw)), raw, value)
            self.assertEqual(_canonical(money.raw_to_xno(raw)), _canonical(value), value)

    def test_point_one_plus_point_two_is_exactly_point_three(self):
        self.assertEqual(money.xno_to_raw("0.1") + money.xno_to_raw("0.2"),
                         money.xno_to_raw("0.3"))


# ----------------------------------------------------------------- helpers

def _canonical(text):
    """Strip trailing fractional zeros only - "100" must stay 100, not become 1."""
    if "." not in text:
        return text
    whole, frac = text.split(".", 1)
    frac = frac.rstrip("0")
    return "%s.%s" % (whole, frac) if frac else whole


def fixtures():
    two = testhost.challenge()
    one = testhost.challenge(testhost.USDC_ENTRIES[:1])
    three = testhost.challenge(testhost.USDC_ENTRIES + [dict(
        testhost.USDC_ENTRIES[0], network="polygon", maxAmountRequired="0.02")])
    return {
        "two-entry.json": json.dumps(two, indent=2),
        "one-entry.json": json.dumps(one, indent=2),
        "three-entry.json": json.dumps(three, indent=2),
        "minified.json": json.dumps(two, separators=(",", ":")),
        "manifest.yaml": _to_yaml(two),
    }


def _to_yaml(document):
    import yaml
    return yaml.safe_dump(document, sort_keys=False)


def load_manifest(path):
    with open(path, encoding="utf-8") as handle:
        return cli._challenge_of(handle.read(), path)


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cli(args):
    """The real CLI, as a subprocess. Returns (code, stdout, stderr)."""
    result = subprocess.run([sys.executable, os.path.join(HERE, "cli.py")] + args,
                            capture_output=True, text=True, cwd=HERE, timeout=120)
    return result.returncode, result.stdout, result.stderr


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        return


class live_server:
    """A real HTTP server on loopback, for the verifier to verify."""

    def __init__(self, app):
        self.app = app

    def __enter__(self):
        self.server = make_server("127.0.0.1", 0, self.app, handler_class=_QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        return False


if __name__ == "__main__":
    unittest.main()
