"""acceptance_kit: one command, five cases a Nano-paid seller should be able to pass.

    python3 acceptance_kit.py [--nano-invoice PATH_TO_CHECKOUT_AT_v0.2.1] [--manifest URL|FILE] [--list] [--json]

Case                What it proves
refusal-probe       a check that is meant to say no does say no: 15 offline payments, 1 honest and 14 that must
                    be refused for the stated reason (wrong amount, wrong payee, unconfirmed, replay, node down)
accepts-shape       the Nano accepts[] entry carries every field x402 v1 AND v2 require, raw amount as an integer
                    string, a checksummed payTo, a colon network; with --manifest, the same check on YOUR manifest
duplicate-retry     the same payment presented twice is served once (ReplayGuard, and the on-disk store across a
                    restart); nano-invoice: a second exact transfer is `duplicate` and refunded to its sender
attribution-tests   exact amount pays and records the chain's sender; off-by-one raw pays nothing; short is
                    underpaid; after expiry is late and refunded (nano-invoice's own tests, by name)
buyer-recovery      the invoice id is re-derivable from merchant + order key, the same order key returns the same
                    invoice, a duplicate is refunded: the buyer who lost the reply can recover (nano-invoice)

The last three need nano-invoice; this kit installs nothing and fetches nothing. Give it a checkout:
    git clone --branch v0.2.1 https://github.com/dhyabi2/nano-invoice
Without it those cases are reported SKIPPED and the exit code is 5 (incomplete), never 0.
Exit: 0 every case passed; 1 a case failed; 5 nothing failed but a case was skipped. Reads only.
"""
import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import challenge  # noqa: E402
import nanoaddr  # noqa: E402
import verify  # noqa: E402

PAY_TO = "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"
SHAPE_REQUIRED = {"v1": ("scheme", "network", "maxAmountRequired", "resource", "description", "payTo",
                         "maxTimeoutSeconds", "asset"),
                  "v2": ("scheme", "network", "amount", "asset", "payTo", "maxTimeoutSeconds")}

INVOICE_CASES = {
    "duplicate-retry": ["tests.test_invoice.TestCheck.test_duplicate_payment_is_refunded_to_its_own_sender"],
    "attribution-tests": ["tests.test_invoice.TestCheck.test_exact_amount_pays_and_records_chain_sender",
                          "tests.test_invoice.TestCheck.test_off_by_one_raw_does_not_pay",
                          "tests.test_invoice.TestCheck.test_underpaid",
                          "tests.test_invoice.TestCheck.test_payment_after_expiry_is_late_and_refunded"],
    "buyer-recovery": ["tests.test_invoice.TestCreate.test_idempotent_same_order_key_same_invoice",
                       "tests.test_invoice.TestCreate.test_id_is_rederivable_and_order_key_not_stored",
                       "tests.test_invoice.TestCheck.test_duplicate_payment_is_refunded_to_its_own_sender"],
}


def case_refusal_probe():
    done = subprocess.run([sys.executable, os.path.join(HERE, "docs", "refusal_checklist.py")],
                          capture_output=True, text=True, timeout=120)
    return done.returncode == 0, (done.stdout.strip().splitlines() or [""])[-1] or done.stderr[-200:]


def shape_problems(entry):
    problems = []
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    for version, names in SHAPE_REQUIRED.items():
        for name in names:
            if entry.get(name) in (None, ""):
                problems.append("%s requires %r" % (version, name))
    for name in ("amount", "maxAmountRequired"):
        v = entry.get(name)
        if v is not None and not (isinstance(v, str) and v.isdigit()):
            problems.append("%r must be an integer string of raw, got %r" % (name, v))
    if entry.get("amount") is not None and entry.get("maxAmountRequired") is not None \
            and entry["amount"] != entry["maxAmountRequired"]:
        problems.append("amount and maxAmountRequired disagree")
    if not nanoaddr.validate(str(entry.get("payTo", "")))["valid"]:
        problems.append("payTo fails its checksum")
    if ":" not in str(entry.get("network", "")):
        problems.append("network %r has no colon (x402 v2 requires one)" % entry.get("network"))
    return problems


def case_accepts_shape(manifest=None):
    import cli
    emitted = challenge.nano_entry(PAY_TO, "0.0001", "https://example.dev/r")
    problems = ["emitted: " + p for p in shape_problems(emitted)]
    where = "the entry this package emits"
    if manifest:
        _, _, text = cli._fetch(manifest)
        body = cli._challenge_of(text, manifest)
        idx = challenge.find_nano(body["accepts"])
        if idx == -1:
            problems.append("manifest has no Nano entry")
        else:
            problems += ["manifest: " + p for p in shape_problems(body["accepts"][idx])]
        where += " and the Nano entry in " + manifest
    return not problems, ("ok: %s" % where) if not problems else "; ".join(problems)


def case_replay_in_process():
    """The dual-rail half of duplicate-retry: a spent block is spent, in memory and on disk across a restart."""
    import fakenode
    import store
    h = "%064X" % 1
    node = fakenode.FakeNode()
    node.settle(h, PAY_TO, 10 ** 26)
    header = json.dumps({"scheme": "exact", "network": "nano:mainnet", "payload": {"blockHash": h}})
    guard = verify.ReplayGuard()
    verify.verify(header, PAY_TO, 10 ** 26, "/r", node, guard)
    try:
        verify.verify(header, PAY_TO, 10 ** 26, "/r", node, guard)
        return False, "second presentation of one block was served"
    except verify.Unpaid:
        pass
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "spent.db")
        g1 = store.SqliteReplayGuard(path)
        verify.verify(header, PAY_TO, 10 ** 26, "/r", node, g1)
        g2 = store.SqliteReplayGuard(path)                 # a restarted process
        try:
            verify.verify(header, PAY_TO, 10 ** 26, "/r", node, g2)
            return False, "a restarted process served a spent block again"
        except verify.Unpaid:
            pass
    return True, "second presentation refused, in memory and after a restart"


def run_invoice_tests(checkout, tests):
    done = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest"] + tests, cwd=checkout,
                          capture_output=True, text=True, timeout=300)
    tail = (done.stderr.strip().splitlines() or [""])[-1]
    return done.returncode == 0, "%d nano-invoice tests: %s" % (len(tests), tail)


def run(nano_invoice=None, manifest=None):
    results = []

    def add(name, fn):
        try:
            ok, detail = fn()
            results.append({"case": name, "status": "pass" if ok else "fail", "detail": detail})
        except Exception as exc:  # a crashing case is a failed case, never a skipped one
            results.append({"case": name, "status": "fail", "detail": "%s: %s" % (type(exc).__name__, exc)})

    add("refusal-probe", case_refusal_probe)
    add("accepts-shape", lambda: case_accepts_shape(manifest))
    have = nano_invoice and os.path.isfile(os.path.join(nano_invoice, "nano_invoice", "core.py"))
    for name, tests in INVOICE_CASES.items():
        def fn(name=name, tests=tests):
            ok, detail = run_invoice_tests(nano_invoice, tests)
            if name == "duplicate-retry":
                ok2, detail2 = case_replay_in_process()
                return ok and ok2, detail + "; dual-rail: " + detail2
            return ok, detail
        if have:
            add(name, fn)
        else:
            results.append({"case": name, "status": "skipped",
                            "detail": "needs a nano-invoice checkout: git clone --branch v0.2.1 "
                                      "https://github.com/dhyabi2/nano-invoice  then --nano-invoice PATH"})
    return results


def exit_code(results):
    if any(r["status"] == "fail" for r in results):
        return 1
    return 5 if any(r["status"] == "skipped" for r in results) else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--nano-invoice")
    p.add_argument("--manifest")
    p.add_argument("--json", action="store_true")
    p.add_argument("--list", action="store_true")
    a = p.parse_args(argv)
    if a.list:
        print("\n".join(["refusal-probe", "accepts-shape"] + list(INVOICE_CASES)))
        return 0
    results = run(a.nano_invoice, a.manifest)
    if a.json:
        print(json.dumps({"results": results, "exit": exit_code(results)}, indent=2))
    else:
        for r in results:
            print("[%-7s] %-18s %s" % (r["status"], r["case"], r["detail"]))
        passed = sum(r["status"] == "pass" for r in results)
        print("acceptance-kit: %d/%d pass, %d skipped" % (passed, len(results),
                                                         sum(r["status"] == "skipped" for r in results)))
    return exit_code(results)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
