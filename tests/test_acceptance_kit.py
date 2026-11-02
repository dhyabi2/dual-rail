"""acceptance_kit: the runner's own laws. A skipped case is never a pass; a failing case is never skipped."""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acceptance_kit as kit  # noqa: E402
import challenge  # noqa: E402

PAY_TO = kit.PAY_TO


def good():
    return challenge.nano_entry(PAY_TO, "0.0001", "https://example.dev/r")


class Shape(unittest.TestCase):

    def test_the_entry_this_package_emits_passes_v1_and_v2(self):
        self.assertEqual(kit.shape_problems(good()), [])

    def test_each_defect_is_named(self):
        cases = {
            "float amount": ({"amount": "0.0001", "maxAmountRequired": "0.0001"}, "integer string"),
            "no timeout": ({"maxTimeoutSeconds": None}, "maxTimeoutSeconds"),
            "bad payTo": ({"payTo": PAY_TO[:-1] + "x"}, "checksum"),
            "colon-free network": ({"network": "nano-mainnet"}, "colon"),
            "amounts disagree": ({"amount": "1"}, "disagree"),
            "no v1 resource": ({"resource": None}, "resource"),
        }
        for label, (change, needle) in cases.items():
            entry = dict(good(), **change)
            problems = kit.shape_problems(entry)
            self.assertTrue(any(needle in p for p in problems), (label, problems))

    def test_a_manifest_without_a_nano_entry_fails_the_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            json.dump({"accepts": [{"scheme": "exact", "network": "base"}]}, open(path, "w"))
            ok, detail = kit.case_accepts_shape(path)
            self.assertFalse(ok)
            self.assertIn("no Nano entry", detail)

    def test_a_manifest_with_the_emitted_entry_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "m.json")
            json.dump({"accepts": [good()]}, open(path, "w"))
            self.assertTrue(kit.case_accepts_shape(path)[0])


class Runner(unittest.TestCase):

    def fake_invoice(self, tmp, passing):
        os.makedirs(os.path.join(tmp, "nano_invoice"))
        os.makedirs(os.path.join(tmp, "tests"))
        open(os.path.join(tmp, "nano_invoice", "core.py"), "w").close()
        open(os.path.join(tmp, "tests", "__init__.py"), "w").close()
        body = ["import unittest", "class TestCheck(unittest.TestCase):", "    pass", "class TestCreate(unittest.TestCase):", "    pass"]
        names = {t.split(".")[-1]: t.split(".")[-2] for ts in kit.INVOICE_CASES.values() for t in ts}
        lines = ["import unittest", "class TestCheck(unittest.TestCase):", "    pass", "class TestCreate(unittest.TestCase):", "    pass"]
        for name, cls in names.items():
            lines.append("def _t(self): self.assertTrue(%s)" % ("True" if passing else "False"))
            lines.append("setattr(%s, %r, _t)" % (cls, name))
        open(os.path.join(tmp, "tests", "test_invoice.py"), "w").write("\n".join(lines) + "\n")

    def test_without_nano_invoice_three_cases_are_skipped_and_exit_is_5(self):
        results = kit.run()
        self.assertEqual([r["status"] for r in results], ["pass", "pass", "skipped", "skipped", "skipped"])
        self.assertEqual(kit.exit_code(results), 5)

    def test_all_pass_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_invoice(tmp, True)
            results = kit.run(tmp)
            self.assertEqual([r["status"] for r in results], ["pass"] * 5, results)
            self.assertEqual(kit.exit_code(results), 0)

    def test_a_failing_invoice_test_fails_the_case_and_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_invoice(tmp, False)
            results = kit.run(tmp)
            failed = {r["case"] for r in results if r["status"] == "fail"}
            self.assertEqual(failed, {"duplicate-retry", "attribution-tests", "buyer-recovery"})
            self.assertEqual(kit.exit_code(results), 1)

    def test_a_failure_outranks_a_skip(self):
        self.assertEqual(kit.exit_code([{"status": "fail"}, {"status": "skipped"}]), 1)

    def test_the_case_names_are_the_five_in_the_spec(self):
        self.assertEqual(sorted(["refusal-probe", "accepts-shape"] + list(kit.INVOICE_CASES)),
                         sorted(["refusal-probe", "accepts-shape", "duplicate-retry", "attribution-tests",
                                 "buyer-recovery"]))

    def test_in_process_replay_case_passes(self):
        self.assertTrue(kit.case_replay_in_process()[0])

    def test_the_readme_table_names_every_case(self):
        text = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")).read()
        for name in ("refusal-probe", "accepts-shape", "duplicate-retry", "attribution-tests", "buyer-recovery"):
            self.assertIn("`%s`" % name, text)


if __name__ == "__main__":
    unittest.main()
