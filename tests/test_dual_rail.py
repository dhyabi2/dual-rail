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
import nanonode
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
        semantic element.

        The fifth is YAML and needs PyYAML, which is not a dependency of this
        package - see HAVE_YAML. The count is asserted rather than assumed, so
        a run that covers four shapes says four and a machine that has PyYAML
        cannot quietly cover fewer than five.
        """
        blobs = fixtures()
        self.assertEqual(len(blobs), 5 if HAVE_YAML else 4,
                         "fixture shapes: %s" % sorted(blobs))
        directory = tempfile.mkdtemp()
        try:
            for name, blob in blobs.items():
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

class AdvertisedAmountIsPayable(unittest.TestCase):
    """`maxAmountRequired` must be the ATOMIC amount - raw - or no x402 Nano
    client can pay the entry this adapter appends.

    It carried the configured decimal XNO string verbatim ("0.0001"). Every
    x402 client reads that field as an integer count of the asset's atomic
    unit, which is what `extra.decimals: 30` declares it to be; the reference
    Nano client, feeless402, reads it as literally `int(offer[field])`. So a
    402 this adapter had patched raised

        ValueError: invalid literal for int() with base 10: '0.0001'

    inside the payer, and the agent could not buy at all - the one thing this
    adapter exists to make possible. The seller was never at risk (the adapter
    checks a payment against `money.check_price(amount_xno)`, which never
    changed, so a short payment was still refused as underpaid); the loss was
    the sale.
    """

    def entry(self, amount=AMOUNT):
        patched = _challenge.append_nano(
            {"accepts": [{"scheme": "exact", "network": "base", "asset": "USDC",
                          "maxAmountRequired": "10000", "payTo": "0xabc",
                          "resource": RESOURCE}]},
            PAY_TO, amount)
        return patched["accepts"][-1]

    def test_the_advertised_amount_is_an_integer_of_raw(self):
        field = self.entry()["maxAmountRequired"]
        self.assertTrue(field.isdigit(),
                        "maxAmountRequired must be an integer string of raw, got %r" % field)
        self.assertEqual(int(field), AMOUNT_RAW)

    def test_a_client_reading_it_the_x402_way_gets_the_configured_price(self):
        """Exactly what feeless402's `offer_amount_raw` does: int(the field)."""
        entry = self.entry()
        self.assertEqual(int(entry["maxAmountRequired"]), money.xno_to_raw(AMOUNT))

    def test_what_the_adapter_accepts_is_what_it_advertises(self):
        """The advertised figure and the figure a payment is checked against
        must be the same number, or the rail either overcharges or undersells."""
        a = build(node=fakenode.FakeNode())
        self.assertEqual(int(self.entry()["maxAmountRequired"]), a.amount_raw)

    def test_the_decimal_figure_is_still_there_for_a_human(self):
        self.assertEqual(self.entry()["maxAmountRequiredFormatted"], "%s XNO" % AMOUNT)

    def test_the_hardest_prices_survive_the_wire(self):
        """A price a float would round must come back exactly, through the
        string that actually goes on the wire."""
        for amount in ("0.000001", "0.1", "0.3", "100", "99.999999",
                       "0.000000000000000000000000000003"):
            field = self.entry(amount)["maxAmountRequired"]
            self.assertTrue(field.isdigit(), amount)
            self.assertEqual(int(field), money.xno_to_raw(amount), amount)
            self.assertEqual(money.raw_to_xno(int(field)).rstrip("."),
                             amount.rstrip(".") if "." in amount else amount, amount)


class EntryIsAValidPaymentRequirements(unittest.TestCase):
    """The appended entry must satisfy the official x402 `PaymentRequirements`
    schema, not merely be readable by one lenient client.

    Getting the unit right is not enough. Measured against the published
    packages in a session that had npm (`@x402/core` 2.28.0,
    `@x402nano/exact` 0.3.0), the entry this adapter emitted was **rejected by
    both protocol versions' schemas** for two reasons that have nothing to do
    with raw:

        PaymentRequirementsV1Schema: REJECTED  maxTimeoutSeconds: invalid_type
        PaymentRequirementsV2Schema: REJECTED  amount: invalid_type
                                               maxTimeoutSeconds: invalid_type

    x402 renamed the amount field between versions - v1 `maxAmountRequired`,
    v2 `amount` - and this adapter appends to somebody else's challenge, whose
    version it does not choose, so it has to carry both. Each version's schema
    strips the other version's field rather than rejecting it, so carrying both
    is accepted by v1, by v2 and by the `PaymentRequirements` union; carrying
    one is rejected outright by the other version.

    `maxTimeoutSeconds` was simply absent, and both versions require it.

    The two sets below are the measurement, taken by deleting one key at a time
    from an otherwise-valid entry and recording which schema then refused it.
    They are hard-coded on purpose: this suite must not reach the network, and a
    JS dependency is not available to a Python test. The end-to-end check
    against the real schemas is in the pull request that added this class.
    """

    V1_REQUIRES = ("scheme", "network", "asset", "maxAmountRequired", "payTo",
                   "resource", "description", "maxTimeoutSeconds")
    V2_REQUIRES = ("scheme", "network", "asset", "amount", "payTo",
                   "maxTimeoutSeconds")

    def entry(self, amount=AMOUNT):
        patched = _challenge.append_nano(
            {"accepts": [{"scheme": "exact", "network": "base", "asset": "USDC",
                          "maxAmountRequired": "10000", "payTo": "0xabc",
                          "resource": RESOURCE}]},
            PAY_TO, amount)
        return patched["accepts"][-1]

    def test_every_field_x402_v1_requires_is_present(self):
        entry = self.entry()
        missing = [f for f in self.V1_REQUIRES if entry.get(f) is None]
        self.assertEqual(missing, [],
                         "x402 v1 rejects the entry without %s" % missing)

    def test_every_field_x402_v2_requires_is_present(self):
        entry = self.entry()
        missing = [f for f in self.V2_REQUIRES if entry.get(f) is None]
        self.assertEqual(missing, [],
                         "x402 v2 rejects the entry without %s" % missing)

    def test_both_amount_names_carry_the_same_integer(self):
        """A payer that reads either name must be told to send the same amount.
        If these two ever disagree, the entry overcharges one half of the
        ecosystem and undersells the other."""
        entry = self.entry()
        self.assertEqual(entry["amount"], entry["maxAmountRequired"])
        self.assertTrue(entry["amount"].isdigit(), entry["amount"])
        self.assertEqual(int(entry["amount"]), AMOUNT_RAW)

    def test_a_v2_client_reading_amount_gets_the_configured_price(self):
        """feeless402's `offer_amount_raw` tries `amount` FIRST, then
        `maxAmountRequired`, so `amount` is the field that is actually read."""
        self.assertEqual(int(self.entry()["amount"]), money.xno_to_raw(AMOUNT))

    def test_what_the_adapter_accepts_is_what_both_names_advertise(self):
        a = build(node=fakenode.FakeNode())
        entry = self.entry()
        self.assertEqual(int(entry["amount"]), a.amount_raw)
        self.assertEqual(int(entry["maxAmountRequired"]), a.amount_raw)

    def test_the_payment_window_is_a_positive_whole_number_of_seconds(self):
        window = self.entry()["maxTimeoutSeconds"]
        self.assertIsInstance(window, int)
        self.assertNotIsInstance(window, bool)
        self.assertGreater(window, 0)
        self.assertEqual(window, _challenge.MAX_TIMEOUT_SECONDS)

    def test_both_names_hold_for_every_hard_price(self):
        for amount in ("0.000001", "0.1", "0.3", "100", "99.999999",
                       "0.000000000000000000000000000003"):
            entry = self.entry(amount)
            self.assertEqual(entry["amount"], entry["maxAmountRequired"], amount)
            self.assertEqual(int(entry["amount"]), money.xno_to_raw(amount), amount)

    def test_the_entry_is_still_only_appended(self):
        """The extra fields must not have cost the one hard guarantee: the
        entries that were already there are untouched."""
        before = [{"scheme": "exact", "network": "base", "asset": "USDC",
                   "maxAmountRequired": "10000", "payTo": "0xabc",
                   "resource": RESOURCE}]
        patched = _challenge.append_nano({"accepts": copy.deepcopy(before)},
                                         PAY_TO, AMOUNT)
        self.assertEqual(patched["accepts"][:-1], before)
        self.assertEqual(len(patched["accepts"]), len(before) + 1)


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


try:
    import yaml as _yaml
except ImportError:                                      # pragma: no cover
    _yaml = None

#: Whether the YAML fixture shape can be exercised at all. PyYAML is NOT a
#: dependency of this package and must not become one - the README's quickstart
#: is `python3 -m unittest discover -s tests` with no install step, and that has
#: to stay true. The fifth shape needs PyYAML on BOTH sides, to write the
#: fixture and for `cli._challenge_of` to read it back, so on a bare
#: interpreter there is nothing to run rather than something silently skipped:
#: `test_patch_is_additive_only` asserts how many shapes it got, so the count
#: cannot quietly drop to four where PyYAML IS installed. CI installs it, so the
#: shape is covered there.
HAVE_YAML = _yaml is not None


def fixtures():
    two = testhost.challenge()
    one = testhost.challenge(testhost.USDC_ENTRIES[:1])
    three = testhost.challenge(testhost.USDC_ENTRIES + [dict(
        testhost.USDC_ENTRIES[0], network="polygon", maxAmountRequired="0.02")])
    shapes = {
        "two-entry.json": json.dumps(two, indent=2),
        "one-entry.json": json.dumps(one, indent=2),
        "three-entry.json": json.dumps(three, indent=2),
        "minified.json": json.dumps(two, separators=(",", ":")),
    }
    if HAVE_YAML:
        shapes["manifest.yaml"] = _to_yaml(two)
    return shapes


def _to_yaml(document):
    return _yaml.safe_dump(document, sort_keys=False)


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



class ReadsEveryVersionsAmountField(unittest.TestCase):
    """x402 renamed the price field between v1 (`maxAmountRequired`) and v2
    (`amount`), and `cli.py` read only the v1 name (dhyabi2/dual-rail#1).

    Every CLI test in this file passed over that defect, because
    `testhost.USDC_ENTRIES` declares `x402Version: 2` and still prices under
    the v1 name - the one field the code read. A fixture that cannot tell the
    two apart cannot fail on the difference, so these tests build a genuinely
    v2-shaped host and pin that it carries no v1 price field at all.
    """

    #: The host's own entries, priced the way a real x402 v2 seller prices
    #: them. Derived from the host rather than written out, so a change there
    #: cannot leave this fixture asserting against a shape nobody serves.
    @staticmethod
    def v2_entries():
        entries = []
        for entry in testhost.USDC_ENTRIES:
            moved = {k: v for k, v in entry.items() if k != "maxAmountRequired"}
            moved["amount"] = entry["maxAmountRequired"]
            entries.append(moved)
        return entries

    def test_the_v2_fixture_can_tell_the_difference(self):
        """The control. Without this the tests below could pass on a fixture
        that still carries the v1 field, which is how the defect survived."""
        for entry in self.v2_entries():
            self.assertNotIn("maxAmountRequired", entry)
            self.assertIn("amount", entry)
        self.assertEqual(testhost.challenge()["x402Version"], 2)
        self.assertTrue(all("maxAmountRequired" in e for e in testhost.USDC_ENTRIES),
                        "the stock host is the v1-named fixture these tests "
                        "exist to contrast with; if it moved, revisit them")

    def test_amount_of_reads_the_fields_in_the_payers_order(self):
        """feeless402 0.2.12's `offer_amount_raw` tries `amount`, then
        `maxAmountRequired`, then `max_amount_required`. What a payer reads is
        what `inspect` must report, so the order is theirs, not a schema's."""
        self.assertEqual(cli._amount_of({"amount": "7"}), ("7", "amount"))
        self.assertEqual(cli._amount_of({"maxAmountRequired": "7"}),
                         ("7", "maxAmountRequired"))
        self.assertEqual(cli._amount_of({"max_amount_required": "7"}),
                         ("7", "max_amount_required"))
        self.assertEqual(cli._amount_of({"amount": "7", "maxAmountRequired": "9"}),
                         ("7", "amount"), "a payer reads `amount` first")
        self.assertEqual(cli._amount_of({}), (None, None))
        self.assertEqual(cli._amount_of(None), (None, None))
        self.assertEqual(cli._amount_of({"amount": None, "maxAmountRequired": "9"}),
                         ("9", "maxAmountRequired"),
                         "an explicit null is not a price")

    def test_inspect_reports_a_v2_entrys_price(self):
        """Before the fix every rail of a v2 manifest read
        `maxAmountRequired: null` - an agent asking what the seller charges
        was told nothing, for every entry."""
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "v2.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(testhost.challenge(self.v2_entries()), handle, indent=2)
            code, out, _ = run_cli(["inspect", path])
            self.assertEqual(code, 0)
            report = json.loads(out)
            self.assertEqual(report["entries"], 2)
            for rail in report["rails"]:
                self.assertEqual(rail["amount"], "0.01")
                self.assertEqual(rail["amount_field"], "amount")
        finally:
            import shutil
            shutil.rmtree(directory)

    def test_verify_does_not_call_a_v2_sellers_own_rail_malformed(self):
        """The sharp end. `verify` is the deploy gate, and on a v2 manifest it
        failed `existing rail still settles` - telling a seller that mounting
        the Nano entry had broken the USDC rail they already had, on the
        strength of reading the wrong field name."""
        node = fakenode.FakeNode()
        gate, protected = testhost.make_host(self.v2_entries())
        app = build(node=node).wsgi(protected, gate=gate)
        with live_server(app) as base:
            code, out, _ = run_cli(["verify", base + "/report", "--json"])
        report = json.loads(out)
        failed = [c["name"] for c in report["checks"] if not c["pass"]]
        self.assertEqual(failed, ["nano rail settles"],
                         "only the missing payment proof may fail here")
        self.assertEqual(code, 1)   # still 1: no --payment was supplied
        settles = [c for c in report["checks"]
                   if c["name"] == "existing rail still settles"][0]
        self.assertTrue(settles["pass"])

    def test_verify_prints_the_nano_price_not_none(self):
        """The same read, in the line an operator actually looks at.

        The Nano entry WE append carries both field names, so it cannot show
        this defect - the entry has to be the seller's own. `append_nano`
        leaves an array that already names Nano alone, so a seller who
        already priced XNO the v2 way (run 135's `nano-mainnet` case) is
        exactly whose price printed as `None`.
        """
        theirs = {"scheme": "exact", "network": "nano:mainnet", "asset": "XNO",
                  "amount": str(10 ** 26), "payTo": PAY_TO,
                  "resource": "https://example.dev/report",
                  "maxTimeoutSeconds": 60, "extra": {"decimals": 30}}
        self.assertNotIn("maxAmountRequired", theirs,
                         "the fixture must not carry the field under test")
        node = fakenode.FakeNode()
        gate, protected = testhost.make_host(
            [dict(testhost.USDC_ENTRIES[0]), theirs])
        app = build(node=node).wsgi(protected, gate=gate)
        with live_server(app) as base:
            code, out, _ = run_cli(["verify", base + "/report", "--json"])
        report = json.loads(out)
        entry = [c for c in report["checks"]
                 if c["name"] == "nano entry present and last"][0]
        self.assertTrue(entry["pass"], "the seller's own Nano entry is last")
        self.assertNotIn("None", entry["detail"])
        self.assertIn(str(10 ** 26), entry["detail"],
                      "the Nano price belongs in the detail, in raw")

    def test_a_rail_that_really_is_malformed_names_the_field(self):
        """A red check that cannot say whether the subject or the check is
        broken gets read as the subject. This one names the entry and field."""
        broken = [{k: v for k, v in e.items()
                   if k not in ("maxAmountRequired", "amount")}
                  for e in testhost.USDC_ENTRIES]
        node = fakenode.FakeNode()
        gate, protected = testhost.make_host(broken)
        app = build(node=node).wsgi(protected, gate=gate)
        with live_server(app) as base:
            code, out, _ = run_cli(["verify", base + "/report", "--json"])
        report = json.loads(out)
        settles = [c for c in report["checks"]
                   if c["name"] == "existing rail still settles"][0]
        self.assertFalse(settles["pass"])
        self.assertIn("accepts[0] lacks", settles["detail"])
        self.assertIn("amount", settles["detail"])


class ReadsAMultiResourceManifest(unittest.TestCase):
    """`accepts[]` sits under each `resources[]` item in a manifest such as
    `extract.paypercall.dev/.well-known/x402`. `inspect` answered
    `no accepts[] array in this document` for all of them (dual-rail#1).
    """

    @staticmethod
    def manifest():
        return {"x402Version": 2, "resources": [
            {"resource": "https://example.dev/report",
             "accepts": [dict(testhost.USDC_ENTRIES[0])]},
            {"resource": "https://example.dev/extract",
             "accepts": [dict(testhost.USDC_ENTRIES[1]),
                         dict(_challenge.nano_entry(BURN, AMOUNT,
                                                    "https://example.dev/extract"))]},
        ]}

    def _write(self, directory, document):
        path = os.path.join(directory, "manifest.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
        return path

    def test_inspect_reports_each_resource_separately(self):
        directory = tempfile.mkdtemp()
        try:
            path = self._write(directory, self.manifest())
            code, out, _ = run_cli(["inspect", path])
            self.assertEqual(code, 0)
            report = json.loads(out)
            self.assertTrue(report["multi_resource"])
            self.assertEqual(len(report["resources"]), 2)
            self.assertEqual(report["entries"], 3)
            self.assertTrue(report["nano_entry_present"])
            first, second = report["resources"]
            self.assertEqual(first["resource_label"], "https://example.dev/report")
            self.assertFalse(first["nano_entry_present"])
            self.assertEqual(second["resource_label"], "https://example.dev/extract")
            self.assertTrue(second["nano_entry_present"])
            # Per resource, not flattened: a Nano entry's index is only
            # meaningful inside the array a client actually reads.
            self.assertEqual(second["nano_entry_index"], 1)
        finally:
            import shutil
            shutil.rmtree(directory)

    def test_add_refuses_a_multi_resource_manifest_and_says_why(self):
        """Deliberate. Choosing which resource gets the payout address is the
        operator's call; guessing it would put an address on an endpoint
        nobody asked to be paid for."""
        directory = tempfile.mkdtemp()
        try:
            path = self._write(directory, self.manifest())
            code, out, _ = run_cli(["add", "--manifest", path,
                                    "--pay-to", BURN, "--amount", AMOUNT, "--diff"])
            self.assertEqual(code, 2)
            report = json.loads(out)
            self.assertEqual(report["error"], "unreadable")
            self.assertIn("multi-resource", report["message"])
            self.assertIn("resources[]", report["message"])
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), self.manifest(),
                                 "add wrote to a manifest it refused")
        finally:
            import shutil
            shutil.rmtree(directory)

    def test_a_document_with_neither_shape_is_still_unreadable(self):
        directory = tempfile.mkdtemp()
        try:
            path = self._write(directory, {"x402Version": 2, "error": "nope"})
            code, out, _ = run_cli(["inspect", path])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out)["error"], "unreadable")
        finally:
            import shutil
            shutil.rmtree(directory)

# ------------------------------------------------------------------------------
# Replies recorded from a live mainnet node (`rpc.nano.to`, Nano V28.2) on
# 2026-10-04. Re-fetch either with:
#   curl -s -X POST https://rpc.nano.to -H 'Content-Type: application/json' \
#     -d '{"action":"block_info","json_block":"true","hash":"<HASH>"}'
# The point of recording them is that this defect was an assumption about these
# shapes, so a mock written from the same assumption would have agreed with it.

# A RECEIVE on a real account: money arriving. `block_account` is the account
# the block belongs to - the receiver - and there is no payee anywhere in it.
RECEIVE_REPLY = {
    "block_account": "nano_1natrium1o3z5519ifou7xii8crpxpk8y65qmkih8e8bpsjri651oza8imdd",
    "amount": "50000000000000000000000000000000",
    "confirmed": "true",
    "subtype": "receive",
    "contents": {"type": "state",
                 "link_as_account":
                     "nano_1s7fdbg491z6eo64sz3ghjhxzpkgbn6baxznfy6eaeu8epwkmzpz9yc76c7f"},
}
RECEIVER = RECEIVE_REPLY["block_account"]

# A LEGACY (pre-state) send, block 2 of the genesis account. It carries NO
# `subtype` at all, names its kind in `contents.type`, and spells its payee
# `contents.destination` - there is no `link_as_account`.
LEGACY_SEND_REPLY = {
    "block_account": "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3",
    "amount": "3271945835778254456378601994536232802",
    "confirmed": "true",
    "contents": {"type": "send",
                 "destination":
                     "nano_13ezf4od79h1tgj9aiu4djzcmmguendtjfuhwfukhuucboua8cpoihmh8byo"},
}
LEGACY_SENDER = LEGACY_SEND_REPLY["block_account"]
LEGACY_PAYEE = LEGACY_SEND_REPLY["contents"]["destination"]


class RecordedNode(nanonode.NanoNode):
    """Answers one recorded reply, through the real mapping."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def block_info(self, block_hash):
        self.calls.append(block_hash.upper())
        return nanonode.read_block_info(self.reply)


class OnlyASendIsAPayment(unittest.TestCase):
    """A block that paid nobody is not a proof that somebody paid us.

    Before this, `block_info` filled `destination` in from `block_account`
    whenever the subtype was not exactly "send". On a receive that is OUR OWN
    payout account, so a confirmed receive of at least the price verified - and
    every receive hash is public in our account history. Any stranger could read
    our ledger and be served for free, once per hash per resource.
    """

    def _verdict(self, node, pay_to, required=AMOUNT_RAW):
        try:
            return ("paid", _verify.verify(proof(), pay_to, required, RESOURCE,
                                           node, _verify.ReplayGuard()))
        except _verify.Unpaid as exc:
            return ("unpaid", exc.reason)

    def test_a_receive_on_our_own_account_is_not_a_payment(self):
        node = fakenode.FakeNode()
        node.received(BLOCK, AMOUNT_RAW * 100)
        self.assertEqual(self._verdict(node, PAY_TO)[1], "not_a_send")

    def test_a_recorded_receive_names_no_payee_at_all(self):
        """The mapping, against the reply a real node actually sends."""
        info = nanonode.read_block_info(RECEIVE_REPLY)
        self.assertEqual(info["subtype"], "receive")
        self.assertIsNone(info["destination"],
                          "a receive has no payee, so none may be invented for it")
        self.assertEqual(info["amount_raw"], 50 * 10 ** 30)

    def test_a_recorded_receive_does_not_pay_the_account_it_credited(self):
        """The whole hole, end to end: the receiver is the seller."""
        self.assertEqual(self._verdict(RecordedNode(RECEIVE_REPLY), RECEIVER)[1],
                          "not_a_send")

    def test_a_change_block_is_refused_as_what_it_is(self):
        """It was refused before too - as `underpaid`, because a change block
        carries no amount. A reason that names the wrong problem sends an
        operator reading their logs to the wrong place."""
        node = fakenode.FakeNode()
        node.settle(BLOCK, None, 0, True, subtype="change")
        self.assertEqual(self._verdict(node, PAY_TO)[1], "not_a_send")

    def test_a_block_whose_kind_cannot_be_read_is_not_a_send(self):
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW, True, subtype=None)
        self.assertEqual(self._verdict(node, PAY_TO)[1], "not_a_send")


class ALegacySendNamesItsOwnPayee(unittest.TestCase):
    """A legacy block has no `subtype`, so it took the same wrong branch - and
    there the invented payee was the SENDER. Both halves of that were wrong: a
    legacy send out of our own payout account read as a payment to us, and a
    legacy send that really did pay us was refused."""

    def _verdict(self, pay_to):
        try:
            _verify.verify(proof(), pay_to, 10 ** 24, RESOURCE,
                           RecordedNode(LEGACY_SEND_REPLY), _verify.ReplayGuard())
            return "paid"
        except _verify.Unpaid as exc:
            return exc.reason

    def test_the_payee_is_read_from_the_block_not_from_its_account(self):
        info = nanonode.read_block_info(LEGACY_SEND_REPLY)
        self.assertEqual(info["subtype"], "send")
        self.assertEqual(info["destination"], LEGACY_PAYEE)
        self.assertNotEqual(info["destination"], LEGACY_SENDER)

    def test_a_legacy_send_out_of_our_account_is_not_a_payment_to_us(self):
        self.assertEqual(self._verdict(LEGACY_SENDER), "wrong_destination")

    def test_a_legacy_send_to_us_is_a_payment(self):
        self.assertEqual(self._verdict(LEGACY_PAYEE), "paid")


class OneAccountTwoSpellings(unittest.TestCase):
    """`nano_` and the legacy `xrb_` are one account. A node is free to serve
    either, and refusing a payment over the spelling costs the payer their
    money and gets them nothing."""

    def test_the_payee_may_be_written_the_legacy_way(self):
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW)
        payment = _verify.verify(proof(), "xrb_" + PAY_TO[5:], AMOUNT_RAW, RESOURCE,
                                 node, _verify.ReplayGuard())
        self.assertEqual(payment["block_hash"], BLOCK)

    def test_the_node_may_spell_the_destination_the_legacy_way(self):
        node = fakenode.FakeNode()
        node.settle(BLOCK, "xrb_" + PAY_TO[5:], AMOUNT_RAW)
        payment = _verify.verify(proof(), PAY_TO, AMOUNT_RAW, RESOURCE,
                                 node, _verify.ReplayGuard())
        self.assertEqual(payment["block_hash"], BLOCK)

    def test_a_different_account_is_still_a_different_account(self):
        node = fakenode.FakeNode()
        node.settle(BLOCK, BURN, AMOUNT_RAW)
        with self.assertRaises(_verify.Unpaid) as caught:
            _verify.verify(proof(), PAY_TO, AMOUNT_RAW, RESOURCE,
                           node, _verify.ReplayGuard())
        self.assertEqual(caught.exception.reason, "wrong_destination")

    def test_a_destination_that_is_not_an_address_is_equal_to_nothing(self):
        node = fakenode.FakeNode()
        node.settle(BLOCK, "not an address", AMOUNT_RAW)
        with self.assertRaises(_verify.Unpaid) as caught:
            _verify.verify(proof(), PAY_TO, AMOUNT_RAW, RESOURCE,
                           node, _verify.ReplayGuard())
        self.assertEqual(caught.exception.reason, "wrong_destination")

    def test_a_misconfigured_payto_blames_the_seller_not_the_payer(self):
        """`dual_rail()` refuses a bad payTo at construction, so this is only
        reachable by calling `verify` directly - but when it is reached, the
        reason must not read as though the payer paid the wrong account."""
        node = fakenode.FakeNode()
        node.settle(BLOCK, PAY_TO, AMOUNT_RAW)
        with self.assertRaises(_verify.Unpaid) as caught:
            _verify.verify(proof(), PAY_TO[:-1] + "1", AMOUNT_RAW, RESOURCE,
                           node, _verify.ReplayGuard())
        self.assertEqual(caught.exception.reason, "invalid_payto")


if __name__ == "__main__":
    unittest.main()
