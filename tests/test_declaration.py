"""The network identifier and the declaration validator.

Three things are under test here, and they are the same thing from three
sides: Nano has more than one network identifier in the wild, this package
assumed it had one, and that assumption cost a seller money.

1. `network.py` - which spellings are read as Nano mainnet, which are refused,
   and which are refused for a *different reason* because they name Nano
   without naming the network.
2. `declaration.py` - whether an x402 client would pay a Nano entry, split
   into what the x402 schema itself rejects (`shape_problems`, which is meant
   to be equivalent to `@x402/core`'s own) and what only a Nano-aware reader
   can catch (`nano_problems`).
3. The regression that started it: `challenge.find_nano` compared against the
   literal `nano:mainnet`, so a seller who already accepted XNO spelled
   `nano-mainnet` got a SECOND Nano entry appended beside theirs.

The equivalence claim in (2) is checked against the real installed package by
`tests/cross_check_x402_core.js`, which is not part of this suite because this
package has no dependencies. Its captured output is in
`tests/cross-check-against-x402-core.txt`.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import challenge as _challenge
import declaration
import money
import network
import testhost

OURS = "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"
THEIRS = "nano_1111111111111111111111111111111111111111111111111111hifc8npp"
RAW = "100000000000000000000000000"          # 0.0001 XNO


def v2_entry(**over):
    entry = {"scheme": "exact", "network": "nano:mainnet", "asset": "XNO",
             "amount": RAW, "payTo": OURS, "maxTimeoutSeconds": 60}
    entry.update(over)
    return entry


def v2_doc(*entries, **over):
    doc = {"x402Version": 2, "resource": {"url": "https://example.dev/report"},
           "accepts": list(entries) or [v2_entry()]}
    doc.update(over)
    return doc


class NetworkIdentifier(unittest.TestCase):

    def test_the_canonical_spelling_is_read_as_mainnet(self):
        verdict = network.classify("nano:mainnet")
        self.assertTrue(verdict["mainnet"])
        self.assertEqual(verdict["canonical"], "nano:mainnet")
        self.assertTrue(verdict["x402_v2_legal"])
        self.assertTrue(verdict["caip2"])

    def test_the_colon_free_spelling_is_mainnet_but_not_v2_legal(self):
        """This is the whole point: `nano-mainnet` is a real identifier that
        x402 v1 accepts and v2 refuses, so an entry carrying it is payable by
        an old client and invisible to a new one."""
        verdict = network.classify("nano-mainnet")
        self.assertTrue(verdict["mainnet"])
        self.assertEqual(verdict["canonical"], "nano:mainnet")
        self.assertTrue(verdict["x402_v1_legal"])
        self.assertFalse(verdict["x402_v2_legal"])
        self.assertFalse(verdict["caip2"])

    def test_case_and_whitespace_are_folded_and_the_fold_is_reported(self):
        verdict = network.classify("  NANO:MAINNET ")
        self.assertTrue(verdict["mainnet"])
        self.assertTrue(verdict["folded"])
        # Folded for matching, but not pretended to be CAIP-2: a namespace has
        # to be lowercase, so the seller is told their emitted string is not.
        self.assertFalse(verdict["caip2"])

    def test_a_bare_family_name_is_refused_rather_than_assumed_to_be_mainnet(self):
        """Refusing is the only safe answer. Nano's test networks use the same
        `nano_` address prefix, so reading a bare `nano` as mainnet would be a
        guess about where money goes."""
        for value in ("nano", "xno", "XNO"):
            verdict = network.classify(value)
            self.assertTrue(verdict["nano"], value)
            self.assertFalse(verdict["mainnet"], value)
            self.assertEqual(verdict["reason"], "network_unspecified", value)
            self.assertIsNone(verdict["canonical"], value)

    def test_another_nano_network_is_refused_with_its_own_reason(self):
        verdict = network.classify("nano:testnet")
        self.assertTrue(verdict["nano"])
        self.assertFalse(verdict["mainnet"])
        self.assertEqual(verdict["reason"], "not_mainnet")

    def test_a_foreign_network_is_not_nano(self):
        for value in ("base", "solana:mainnet", "eip155:8453", "nanomainnet"):
            self.assertEqual(network.classify(value)["reason"], "not_nano", value)

    def test_nothing_raises_and_canonical_fails_closed(self):
        for value in (None, 42, True, [], {}, "", "   "):
            self.assertFalse(network.classify(value)["mainnet"], repr(value))
            with self.assertRaises(network.UnknownNetwork):
                network.canonical(value)


class FindNanoRegression(unittest.TestCase):
    """The defect this all started from, reproduced before it is asserted away.

    `find_nano` compared `entry["network"] == "nano:mainnet"`. A seller who
    already accepted XNO and spelled it `nano-mainnet` therefore looked like a
    seller with no Nano entry, so `append_nano` appended a second one. The
    array then carried two Nano prices and two payout addresses, and which one
    got paid depended on the payer's protocol version.
    """

    def their_challenge(self):
        existing = {"scheme": "exact", "network": "nano-mainnet", "asset": "XNO",
                    "maxAmountRequired": "0.0001", "payTo": THEIRS,
                    "resource": "https://example.dev/report", "description": "XNO"}
        return testhost.challenge(testhost.USDC_ENTRIES + [existing])

    def test_a_variant_spelling_counts_as_an_existing_nano_entry(self):
        accepts = self.their_challenge()["accepts"]
        self.assertEqual(_challenge.find_nano(accepts), 2)

    def test_appending_is_a_no_op_when_they_already_accept_xno(self):
        before = self.their_challenge()
        after = _challenge.append_nano(before, OURS, "0.0001")
        self.assertEqual(after, before)
        self.assertEqual(len(after["accepts"]), 3)
        self.assertEqual([e["payTo"] for e in after["accepts"] if "nano" in str(e["network"])],
                         [THEIRS])

    def test_the_duplicate_it_used_to_make_is_reported_as_one(self):
        """Belt to the braces: if an array like that reaches us anyway - built
        before this fix, or by hand - `inspect` names it rather than passing
        it."""
        theirs = self.their_challenge()
        both = dict(theirs, accepts=theirs["accepts"] + [
            _challenge.nano_entry(OURS, "0.0001", "https://example.dev/report")])
        codes = [p["code"] for p in declaration.inspect(both)["problems"]]
        self.assertIn("duplicate_nano_entries", codes)
        self.assertFalse(declaration.inspect(both)["payable"])

    def test_a_canonical_entry_is_still_appended_normally(self):
        plain = testhost.challenge()
        patched = _challenge.append_nano(plain, OURS, "0.0001")
        self.assertEqual(len(patched["accepts"]), len(plain["accepts"]) + 1)
        self.assertEqual(patched["accepts"][-1]["network"], "nano:mainnet")


class Shape(unittest.TestCase):
    """`shape_problems` is only what the x402 schema itself rejects."""

    def test_a_well_formed_v2_entry_has_no_shape_problem(self):
        self.assertEqual(declaration.shape_problems(v2_entry(), 2), [])

    def test_the_amount_field_was_renamed_between_the_versions(self):
        v1_named = v2_entry()
        del v1_named["amount"]
        v1_named["maxAmountRequired"] = RAW
        codes = {p["code"]: p["field"] for p in declaration.shape_problems(v1_named, 2)}
        self.assertEqual(codes.get("field_missing"), "amount")

    def test_a_string_timeout_is_refused_by_both_versions(self):
        for version in (1, 2):
            codes = [p["code"] for p in
                     declaration.shape_problems(v2_entry(maxTimeoutSeconds="60"), version)]
            self.assertIn("timeout_not_a_positive_number", codes)

    def test_a_boolean_timeout_is_not_a_number(self):
        codes = [p["code"] for p in
                 declaration.shape_problems(v2_entry(maxTimeoutSeconds=True), 2)]
        self.assertIn("timeout_not_a_positive_number", codes)

    def test_v2_requires_a_colon_in_the_network_and_v1_does_not(self):
        entry = v2_entry(network="nano-mainnet")
        self.assertIn("network_not_caip2",
                      [p["code"] for p in declaration.shape_problems(entry, 2)])
        v1 = dict(entry, maxAmountRequired=RAW, resource="https://e.dev/r",
                  description="x")
        del v1["amount"]
        self.assertEqual(declaration.shape_problems(v1, 1), [])

    def test_extra_must_be_an_object_or_null(self):
        """Found by fuzzing against the real package: `extra` is
        z.record(z.unknown()).optional().nullable(), and an entry whose extra
        is a string takes the whole accepts[] array down with it."""
        self.assertIn("extra_not_an_object",
                      [p["code"] for p in declaration.shape_problems(v2_entry(extra="x"), 2)])
        self.assertEqual(declaration.shape_problems(v2_entry(extra=None), 2), [])
        self.assertEqual(declaration.shape_problems(v2_entry(extra={}), 2), [])

    def test_an_entry_that_is_not_an_object_says_so_in_json_words(self):
        for value, word in ((None, "null"), (5, "number"), ("x", "string"), ([], "array")):
            problems = declaration.shape_problems(value, 2)
            self.assertEqual(problems[0]["code"], "entry_not_an_object")
            self.assertIn(word, problems[0]["message"])


class NanoSpecific(unittest.TestCase):
    """`nano_problems` is what the generic schema cannot check."""

    def test_an_address_that_fails_its_checksum_is_caught(self):
        """x402's own schema asks only for a non-empty string, so a client
        accepts this and the money sent there is unspendable."""
        entry = v2_entry(payTo=OURS[:-1] + "1")
        self.assertEqual(declaration.shape_problems(entry, 2), [])
        problems = declaration.nano_problems(entry, 2)["problems"]
        self.assertEqual([p["code"] for p in problems], ["pay_to_invalid"])
        self.assertIn("bad_checksum", problems[0]["message"])

    def test_a_decimal_amount_is_not_a_payable_v2_amount(self):
        """dhyabi2/dual-rail#1, derived rather than asserted: the v2 amount is
        an integer of raw, so `0.0001` is refused as given."""
        problems = declaration.nano_problems(v2_entry(amount="0.0001"), 2)["problems"]
        codes = [p["code"] for p in problems]
        self.assertEqual(codes, ["amount_is_decimal_xno"])
        self.assertIn(RAW, problems[0]["message"])

    def test_an_integer_amount_of_raw_passes_and_is_reported_in_xno(self):
        out = declaration.nano_problems(v2_entry(), 2)
        self.assertEqual(out["problems"], [])
        self.assertEqual(out["amount"], {"raw": RAW, "decimal_xno": "0.0001"})

    def test_an_amount_no_block_could_carry_is_refused(self):
        """A state block's balance field is 128 bits, so this price can never
        be paid; it is a typo, not a price."""
        too_big = str(declaration.MAX_RAW + 1)
        codes = [p["code"] for p in
                 declaration.nano_problems(v2_entry(amount=too_big), 2)["problems"]]
        self.assertEqual(codes, ["amount_above_max_raw"])

    def test_a_zero_amount_is_refused(self):
        codes = [p["code"] for p in
                 declaration.nano_problems(v2_entry(amount="0"), 2)["problems"]]
        self.assertEqual(codes, ["amount_zero"])

    def test_a_non_canonical_mainnet_spelling_is_still_flagged_for_nano(self):
        codes = [p["code"] for p in
                 declaration.nano_problems(v2_entry(network="nano-mainnet"), 2)["problems"]]
        self.assertIn("network_not_canonical", codes)

    def test_wrong_decimals_would_rescale_every_price(self):
        codes = [p["code"] for p in
                 declaration.nano_problems(v2_entry(extra={"decimals": 18}), 2)["problems"]]
        self.assertEqual(codes, ["decimals_wrong"])
        self.assertEqual(
            declaration.nano_problems(v2_entry(extra={"decimals": 30}), 2)["problems"], [])

    def test_a_foreign_asset_on_a_nano_entry_is_refused(self):
        codes = [p["code"] for p in
                 declaration.nano_problems(v2_entry(asset="USDC"), 2)["problems"]]
        self.assertEqual(codes, ["asset_not_xno"])
        # `xno` lower case is the same asset, not a different one.
        self.assertEqual(declaration.nano_problems(v2_entry(asset="xno"), 2)["problems"], [])

    def test_an_xrb_address_is_reported_under_its_nano_spelling(self):
        out = declaration.nano_problems(v2_entry(payTo="xrb_" + OURS[5:]), 2)
        self.assertEqual(out["problems"], [])
        self.assertEqual(out["pay_to"]["normalised"], OURS)


class Document(unittest.TestCase):

    def test_a_complete_v2_document_is_payable(self):
        report = declaration.inspect(v2_doc())
        self.assertTrue(report["payable"], report["problems"])
        self.assertEqual(len(report["nano_entries"]), 1)

    def test_what_this_package_emits_is_payable_by_both_versions(self):
        """The validator pointed back at the emitter, which is the only way
        this repository can know its own output is payable.

        This test was written the other way round and asserted the entry was
        NOT payable, because on `main` it was not: `nano_entry` carried v1's
        `maxAmountRequired` in decimal XNO and no `maxTimeoutSeconds`, so a v2
        client dropped it. That is dhyabi2/dual-rail#1, and the branch this
        test now sits on is what fixed it - the entry carries the amount in raw
        under both versions' names plus the payment window. The assertion is
        inverted rather than deleted: it is the same fact, and now it guards the
        fix instead of recording the defect.
        """
        patched = _challenge.append_nano(testhost.challenge(), OURS, "0.0001")
        entry = patched["accepts"][-1]
        for version in (1, 2):
            check = declaration.check_entry(entry, version)
            self.assertTrue(check["payable"],
                            "x402 v%d would refuse our own entry: %s"
                            % (version, [p["code"] for p in check["problems"]]))
        # and the amount both versions are told is the configured price
        self.assertEqual(entry["amount"], entry["maxAmountRequired"])
        self.assertEqual(int(entry["amount"]), money.xno_to_raw("0.0001"))

    def test_a_sibling_entry_sinks_the_whole_document(self):
        """Our entry can be perfect and still never get paid: a client parses
        the whole 402 or none of it, so a USDC entry spelled the v1 way in a
        document that says v2 takes the Nano entry down with it."""
        usdc_v1_style = {"scheme": "exact", "network": "base", "asset": "USDC",
                         "amount": "10000", "maxTimeoutSeconds": 60,
                         "payTo": "0x" + "1" * 40}
        report = declaration.inspect(v2_doc(usdc_v1_style, v2_entry()))
        self.assertFalse(report["payable"])
        sibling = [p for p in report["problems"] if p["code"] == "sibling_entry_rejected"]
        self.assertEqual(len(sibling), 1)
        self.assertEqual(sibling[0]["field"], "accepts[0]")
        # and the Nano entry itself is still reported as fine
        self.assertTrue(report["nano_entries"][0]["payable"])

    def test_a_missing_top_level_resource_fails_a_v2_document(self):
        doc = v2_doc()
        del doc["resource"]
        codes = [p["code"] for p in declaration.inspect(doc)["problems"]]
        self.assertIn("v2_resource_missing", codes)

    def test_an_undeclared_version_is_checked_against_both(self):
        doc = v2_doc()
        del doc["x402Version"]
        report = declaration.inspect(doc)
        self.assertEqual(sorted(report["nano_entries"][0]["checks"]), ["1", "2"])
        self.assertIn("x402_version_missing_or_unknown",
                      [p["code"] for p in report["problems"]])

    def test_a_float_version_is_the_same_number_in_json(self):
        """JSON has one number type, so 1.0 IS 1 and zod's z.literal(1) matches
        it. An int-only check disagreed with both the library and the Node
        binding over a document that parses."""
        self.assertEqual(declaration.declared_version({"x402Version": 2.0}), 2)
        self.assertEqual(declaration.declared_version({"x402Version": 2.5}), 0)

    def test_a_boolean_version_is_not_version_one(self):
        """`True == 1` in Python; it does not in JSON or in JavaScript."""
        self.assertEqual(declaration.declared_version({"x402Version": True}), 0)

    def test_nothing_raises_on_any_shape(self):
        for value in (None, [], "nope", 7, {}, {"accepts": "x"},
                      {"x402Version": 2, "accepts": [None, 5, "x"]}):
            report = declaration.inspect(value)
            self.assertFalse(report["payable"], repr(value))
            self.assertTrue(report["problems"], repr(value))

    def test_the_whole_report_is_json_serialisable(self):
        json.dumps(declaration.inspect(v2_doc()))
        json.dumps(declaration.inspect(None))


class AmountGrammar(unittest.TestCase):
    """`money.xno_to_raw` used `str.isdigit()`, which is true for every Unicode
    digit. The Node binding's `/^\\d+$/` is ASCII, so the two bindings
    disagreed about whether a price was valid at all - and one input, `"²"`,
    was `isdigit()` true and `int()` unparsable, so it raised a bare
    `ValueError` straight through every `except money.AmountError` in the
    package, including the CLI's.
    """

    def test_a_unicode_digit_is_not_an_amount(self):
        for value in ("١", "٣", "\U0001d7db", "1.٣"):
            with self.assertRaises(money.AmountError, msg=value):
                money.xno_to_raw(value)

    def test_the_input_that_used_to_leak_a_value_error_now_refuses(self):
        with self.assertRaises(money.AmountError):
            money.xno_to_raw("²")
        # check_price is the gate every caller uses, and it caught only
        # AmountError - so this is the one that mattered.
        with self.assertRaises(money.AmountError):
            money.check_price("²")

    def test_a_lone_dot_is_not_zero(self):
        with self.assertRaises(money.AmountError):
            money.xno_to_raw(".")

    def test_ordinary_amounts_are_unaffected(self):
        self.assertEqual(money.xno_to_raw("0.0001"), 10 ** 26)
        self.assertEqual(money.xno_to_raw("1"), 10 ** 30)
        self.assertEqual(money.xno_to_raw("1."), 10 ** 30)
        self.assertEqual(money.xno_to_raw(".1"), 10 ** 29)
        self.assertEqual(money.check_price("0.0001"), 10 ** 26)


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()


class ResourceCatalogue(unittest.TestCase):
    """`check` was blind to a multi-resource manifest - issue #1's last item.

    `inspect` and `verify` learned the `resources[]` shape in #4; the path
    `check` uses, `declaration.inspect`, did not. It read
    `challenge.get("accepts")`, found nothing, and reported `0 entries, 0
    naming Nano, not payable` about a document carrying 24 payable Nano
    entries - the one answer the module exists to prevent, since from the
    seller's side "not payable" and "nobody wants XNO" look identical.

    The live shape these cases are cut from is
    `extract.paypercall.dev/.well-known/x402`, read 2026-10-08: 24 resources,
    each with its own `url` and `accepts[]`, every entry Nano-only, and not
    one carrying `maxTimeoutSeconds` - while a real 402 from the same resource
    carries `maxTimeoutSeconds: 60` and a top-level `resource` object. That
    pair is what `CHALLENGE_ONLY_FIELDS` is measured against.
    """

    ENTRY = {"scheme": "exact", "network": "nano:mainnet", "asset": "XNO",
             "amount": "100000000000000000000000000",
             "payTo": "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"}

    def catalogue(self, *items, **over):
        body = {"x402Version": 2, "resources": list(items)}
        body.update(over)
        return body

    def item(self, *entries, **over):
        out = {"url": "https://a.dev/x", "accepts": list(entries)}
        out.update(over)
        return out

    # ---------------------------------------------------- the defect itself

    def test_a_catalogue_is_no_longer_read_as_an_absent_accepts_array(self):
        report = declaration.inspect(self.catalogue(self.item(self.ENTRY)))
        self.assertEqual(report["document_kind"], "resource_catalogue")
        codes = {issue["code"] for issue in report["problems"]}
        self.assertNotIn("accepts_not_an_array", codes)
        self.assertEqual(report["entries"], 1)
        self.assertEqual(report["resources_naming_nano"], 1)
        self.assertTrue(report["payable"])

    def test_the_entries_a_catalogue_carries_are_actually_reported(self):
        report = declaration.inspect(self.catalogue(
            self.item(self.ENTRY),
            self.item(self.ENTRY, url="https://b.dev/x")))
        self.assertEqual(report["entries"], 2)
        self.assertEqual([section["resource_label"] for section in report["resources"]],
                         ["https://a.dev/x", "https://b.dev/x"])
        for section in report["resources"]:
            self.assertEqual(len(section["nano_entries"]), 1)
            self.assertTrue(section["payable"])

    def test_a_catalogue_report_carries_no_top_level_nano_entries(self):
        """An empty list there is the false answer this fix removes.

        A consumer written for the challenge shape must raise rather than read
        `[]` as "this catalogue names no Nano entry".
        """
        report = declaration.inspect(self.catalogue(self.item(self.ENTRY)))
        self.assertNotIn("nano_entries", report)
        with self.assertRaises(KeyError):
            report["nano_entries"]

    def test_a_402_challenge_keeps_the_shape_it_has_always_had(self):
        report = declaration.inspect(v2_doc())
        self.assertEqual(report["document_kind"], "payment_required")
        self.assertIn("nano_entries", report)
        self.assertNotIn("resources", report)
        self.assertNotIn("multi_resource", report)

    def test_a_challenge_that_also_lists_resources_is_still_a_challenge(self):
        """The array the payer was served is the one to judge."""
        body = v2_doc()
        body["resources"] = [self.item(dict(self.ENTRY, payTo=""))]
        report = declaration.inspect(body)
        self.assertEqual(report["document_kind"], "payment_required")
        self.assertTrue(report["payable"])

    # ------------------------------------- what the relaxation does and does not

    def test_a_missing_challenge_only_field_is_a_note_not_a_problem(self):
        report = declaration.inspect(self.catalogue(self.item(self.ENTRY)))
        check = report["resources"][0]["nano_entries"][0]["checks"]["2"]
        self.assertEqual(check["problems"], [])
        self.assertTrue(check["payable"])
        self.assertEqual([issue["field"] for issue in check["advertisement_only"]],
                         ["maxTimeoutSeconds"])
        self.assertEqual(check["advertisement_only"][0]["code"],
                         "challenge_only_field_absent")

    def test_the_same_entry_in_a_402_challenge_is_still_unpayable(self):
        """The relaxation is about the document, not about the entry.

        Hand the identical entry to `check_entry` as a challenge entry and
        `maxTimeoutSeconds` is fatal again - which is what @x402/core 2.28.0's
        PaymentRequirementsV2Schema says, and what a facilitator will say.
        """
        verdict = declaration.check_entry(self.ENTRY, 2)
        self.assertFalse(verdict["payable"])
        self.assertEqual([issue["field"] for issue in verdict["problems"]],
                         ["maxTimeoutSeconds"])
        self.assertNotIn("advertisement_only", verdict)

    def test_a_present_but_malformed_challenge_only_field_is_still_fatal(self):
        """Relaxed for `field_missing` only. `"60"` is a string, which zod refuses."""
        for bad in ("60", 0, True, -1):
            report = declaration.inspect(self.catalogue(
                self.item(dict(self.ENTRY, maxTimeoutSeconds=bad))))
            check = report["resources"][0]["nano_entries"][0]["checks"]["2"]
            self.assertFalse(check["payable"], repr(bad))
            self.assertEqual([issue["code"] for issue in check["problems"]],
                             ["timeout_not_a_positive_number"], repr(bad))
            self.assertFalse(report["payable"], repr(bad))

    def test_everything_a_later_402_cannot_repair_is_still_fatal(self):
        """A price, a destination or a network is the catalogue's own claim.

        None of these is a field the endpoint's 402 supplies later, so none is
        relaxed: a bad checksum means money sent there is unspendable however
        the challenge is spelled.
        """
        pay_to = self.ENTRY["payTo"]
        for over in ({"payTo": pay_to[:-1] + "1"}, {"payTo": ""},
                     {"amount": "0.0001"}, {"amount": "9" * 40},
                     {"network": "nano"}, {"network": "nano:testnet"},
                     {"asset": "USDC"}, {"scheme": "upto"}):
            report = declaration.inspect(self.catalogue(
                self.item(dict(self.ENTRY, **over))))
            self.assertFalse(report["payable"], repr(over))

    def test_a_usdc_sibling_without_a_timeout_does_not_sink_the_catalogue(self):
        """Otherwise every real catalogue reports a correct rail as broken.

        This is the same false alarm #4 took out of `verify`: telling a seller
        the USDC rail they already had has stopped working.
        """
        usdc = {"scheme": "exact", "network": "eip155:8453", "asset": "USDC",
                "amount": "10000", "payTo": "0x" + "1" * 40}
        report = declaration.inspect(self.catalogue(
            self.item(usdc, self.ENTRY)))
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["resources"][0]["problems"], [])
        self.assertTrue(report["payable"])

    def test_a_malformed_usdc_sibling_still_sinks_the_resource(self):
        usdc = {"scheme": "exact", "network": "eip155:8453", "asset": "USDC",
                "amount": "10000", "payTo": "0x" + "1" * 40, "maxTimeoutSeconds": "60"}
        report = declaration.inspect(self.catalogue(self.item(usdc, self.ENTRY)))
        codes = {issue["code"] for issue in report["resources"][0]["problems"]}
        self.assertIn("sibling_entry_rejected", codes)
        self.assertFalse(report["payable"])

    # ------------------------------------------------- the catalogue's own shape

    def test_one_broken_resource_is_not_hidden_by_the_ones_that_work(self):
        """`payable` is every Nano resource, not any.

        A catalogue of 24 endpoints with one bad entry is a seller problem, and
        an `any` would report it as healthy on the strength of the other 23.
        """
        report = declaration.inspect(self.catalogue(
            self.item(self.ENTRY),
            self.item(dict(self.ENTRY, payTo=""), url="https://b.dev/x"),
            self.item(self.ENTRY, url="https://c.dev/x")))
        self.assertEqual(report["resources_naming_nano"], 3)
        self.assertEqual(report["resources_not_payable"], 1)
        self.assertFalse(report["payable"])

    def test_a_resource_with_no_url_is_named(self):
        report = declaration.inspect(self.catalogue(self.item(self.ENTRY, url=None)))
        codes = {issue["code"] for issue in report["problems"]}
        self.assertIn("resource_url_missing", codes)
        self.assertFalse(report["payable"])
        self.assertEqual(report["resources"][0]["resource_label"], "resources[0]")

    def test_the_other_spelling_of_a_resources_item_url_is_read(self):
        report = declaration.inspect(self.catalogue(
            {"resource": "https://a.dev/x", "accepts": [self.ENTRY]}))
        self.assertEqual(report["resources"][0]["resource_label"], "https://a.dev/x")
        self.assertTrue(report["payable"])

    def test_an_empty_catalogue_advertises_nothing(self):
        report = declaration.inspect(self.catalogue())
        codes = {issue["code"] for issue in report["problems"]}
        self.assertIn("resources_empty", codes)
        self.assertFalse(report["payable"])

    def test_a_catalogue_naming_no_nano_entry_is_not_payable(self):
        usdc = {"scheme": "exact", "network": "eip155:8453", "asset": "USDC",
                "amount": "10000", "payTo": "0x" + "1" * 40}
        report = declaration.inspect(self.catalogue(self.item(usdc)))
        self.assertEqual(report["resources_naming_nano"], 0)
        self.assertFalse(report["payable"])

    def test_a_resource_item_that_is_not_an_object_is_named_and_skipped(self):
        report = declaration.inspect(self.catalogue(None, 5, "x",
                                                    self.item(self.ENTRY)))
        codes = [issue["code"] for issue in report["problems"]]
        self.assertEqual(codes.count("resource_not_an_object"), 3)
        self.assertFalse(report["payable"])

    def test_a_resource_whose_accepts_is_not_an_array_is_named(self):
        report = declaration.inspect(self.catalogue(
            {"url": "https://a.dev/x", "accepts": "nope"}))
        section = report["resources"][0]
        self.assertEqual([issue["code"] for issue in section["problems"]],
                         ["accepts_not_an_array"])
        self.assertEqual(section["entries"], 0)
        self.assertFalse(report["payable"])

    def test_resources_that_is_not_an_array_is_not_a_catalogue(self):
        report = declaration.inspect({"x402Version": 2, "resources": "nope"})
        self.assertEqual(report["document_kind"], "payment_required")
        self.assertEqual([issue["code"] for issue in report["problems"]],
                         ["accepts_not_an_array"])

    def test_a_catalogue_declaring_no_version_is_checked_against_both(self):
        report = declaration.inspect({"resources": [self.item(self.ENTRY)]})
        checks = report["resources"][0]["nano_entries"][0]["checks"]
        self.assertEqual(sorted(checks), ["1", "2"])
        codes = {issue["code"] for issue in report["problems"]}
        self.assertIn("x402_version_missing_or_unknown", codes)
        self.assertFalse(report["payable"])

    def test_a_v1_catalogue_relaxes_v1s_challenge_only_fields_too(self):
        """v1 requires `resource` and `description` on the entry; a catalogue
        keeps the url on the resources[] item and the description beside it."""
        entry = dict(self.ENTRY,
                     maxAmountRequired="100000000000000000000000000")
        del entry["amount"]
        report = declaration.inspect({"x402Version": 1,
                                      "resources": [self.item(entry)]})
        check = report["resources"][0]["nano_entries"][0]["checks"]["1"]
        self.assertEqual(check["problems"], [])
        self.assertEqual(sorted(issue["field"] for issue in check["advertisement_only"]),
                         ["description", "maxTimeoutSeconds", "resource"])
        self.assertTrue(report["payable"])

    def test_the_whole_catalogue_report_is_json_serialisable(self):
        json.dumps(declaration.inspect(self.catalogue(self.item(self.ENTRY))))

    def test_split_challenge_only_moves_nothing_else(self):
        problems = [
            {"code": "field_missing", "field": "maxTimeoutSeconds", "message": ""},
            {"code": "field_missing", "field": "payTo", "message": ""},
            {"code": "timeout_not_a_positive_number",
             "field": "maxTimeoutSeconds", "message": ""},
        ]
        fatal, advertised = declaration.split_challenge_only(problems)
        self.assertEqual([issue["field"] for issue in advertised], ["maxTimeoutSeconds"])
        self.assertEqual([issue["code"] for issue in fatal],
                         ["field_missing", "timeout_not_a_positive_number"])
