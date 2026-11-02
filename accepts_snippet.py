"""accepts_snippet: paste-in generator for the Nano accept-leg, keyed to a seller's own manifest.

    python3 accepts_snippet.py --manifest URL|FILE --pay-to nano_... [--usd 0.01 | --amount 0.0001]
                               [--framework python|node] [--out-dir DIR]

Reads the seller's x402 challenge/manifest (read-only) and prints, as one JSON object (or files in --out-dir):
  * manifest        the exact modified document: their accepts[] with ONE Nano entry appended, nothing else changed
  * manifest_diff   the unified diff of that change (JSON manifests; refused for YAML, as `dual-rail add` does)
  * middleware_diff the server change, against the minimal app in the README (apply by hand: it is not your file)
  * test            a standalone unittest that fails if an existing entry changed or the Nano entry is not last
  * price           how the XNO amount was derived. With --usd the price stays in USD: converted to raw at read
                    time from two independent feeds (median, refuses past a 2 percent spread, rounds UP), valid
                    60 seconds, and a refund returns the same raw. With neither flag the USD figure is read from
                    the manifest's own USDC entry (6 decimals) when there is one.
Exit 0 ok; 2 usage or unreadable; 3 not additive (nothing is emitted); 4 no price could be derived.
Nothing is signed, no key is read or written, no payment is made.
"""
import argparse
import copy
import difflib
import json
import os
import sys
import time

import challenge as _challenge
import cli as _cli
import money
import nanoaddr
import usd_quote

EXIT_OK, EXIT_USAGE, EXIT_NOT_ADDITIVE, EXIT_NO_PRICE = 0, 2, 3, 4
RULE = ("Price is fixed in USD. At request time the seller converts it to raw XNO from the median of two "
        "independent public feeds (refused if they differ by more than 2 percent), rounding up so the seller "
        "is never short; the quote is valid for 60 seconds and a refund returns the same raw amount, so "
        "neither side carries exchange-rate exposure after settlement.")


class NoPrice(Exception):
    pass


def usd_from_manifest(accepts):
    """USD price of the first USDC-looking entry (6 decimals), as a decimal string, or None."""
    for entry in accepts:
        if not isinstance(entry, dict):
            continue
        asset = str(entry.get("asset", "")).lower()
        dec = (entry.get("extra") or {}).get("decimals") if isinstance(entry.get("extra"), dict) else None
        name = str((entry.get("extra") or {}).get("name", "")).lower() if isinstance(entry.get("extra"), dict) else ""
        if "usdc" not in name and "usdc" not in asset and dec != 6:
            continue
        value, _field = _cli._amount_of(entry)
        if isinstance(value, str) and value.isdigit():
            raw = int(value)
            whole, frac = divmod(raw, 10 ** 6)
            return "%d.%s" % (whole, str(frac).zfill(6).rstrip("0") or "0") if frac else str(whole)
    return None


def usd_to_cents(usd):
    text = str(usd).strip()
    whole, _, frac = text.partition(".")
    if not whole.isdigit() or (frac and not frac.isdigit()) or len(frac) > 2:
        raise ValueError("--usd must be a decimal with at most two places (whole cents), got %r" % usd)
    cents = int(whole) * 100 + int((frac + "00")[:2])
    if cents <= 0:
        raise ValueError("--usd must be positive")
    return cents


def price_for(usd, amount, accepts, feeds, now=None):
    if amount is not None:
        money.check_price(amount)
        return {"mode": "fixed_xno", "amount_xno": str(amount), "usd": None}
    source = "flag"
    if usd is None:
        usd = usd_from_manifest(accepts)
        source = "manifest_usdc_entry"
        if usd is None:
            raise NoPrice("no --usd/--amount given and no USDC entry with a readable price in the manifest")
    cents = usd_to_cents(usd)
    q = usd_quote.quote(cents, feeds, now=now)
    amount_xno = money.raw_to_xno(int(q["amount_raw"]))
    money.check_price(amount_xno)
    return {"mode": "usd_pegged", "usd": str(usd), "usd_source": source, "usd_cents": cents,
            "price_micro_usd": q["price_micro_usd"], "amount_raw": q["amount_raw"], "amount_xno": amount_xno,
            "quoted_at": q["quoted_at"], "expires_at": q["expires_at"], "feeds_usd_per_xno": q["feeds"],
            "rule": RULE}


PY_BEFORE = """from myapp import protected_app, x402_middleware

application = x402_middleware(protected_app)
"""
PY_AFTER_FIXED = """import os
from adapter import dual_rail
from myapp import protected_app, x402_middleware

PAY_TO = "%(pay_to)s"
rail = dual_rail(payTo=PAY_TO, amountXno="%(amount_xno)s", nodeUrl=os.environ["NANO_NODE_URL"])
application = rail.wsgi(protected_app, gate=x402_middleware)
"""
PY_AFTER_USD = """import os, time
from adapter import dual_rail
from verify import ReplayGuard
from usd_quote import quote, feed_coingecko, feed_kucoin
from money import raw_to_xno
from myapp import protected_app, x402_middleware

PAY_TO, CENTS, GUARD, _R = "%(pay_to)s", %(usd_cents)d, ReplayGuard(), [None, 0.0]
def rail():  # USD %(usd)s, re-quoted every 50 s from two feeds; one spent-block guard across re-quotes
    if time.time() - _R[1] > 50:
        _R[0] = dual_rail(payTo=PAY_TO, amountXno=raw_to_xno(int(quote(CENTS, [feed_coingecko, feed_kucoin])["amount_raw"])),
                          nodeUrl=os.environ["NANO_NODE_URL"], guard=GUARD); _R[1] = time.time()
    return _R[0]
application = lambda env, start: rail().wsgi(protected_app, gate=x402_middleware)(env, start)
"""
JS_BEFORE = """const app = require('./app');
app.use(x402Middleware);
"""
JS_AFTER_FIXED = """const { dualRail } = require('./node/dual-rail');
const app = require('./app');
app.use(dualRail({
  payTo: '%(pay_to)s',
  amountXno: '%(amount_xno)s',   // a decimal STRING
  nodeUrl: process.env.NANO_NODE_URL,
  gate: x402Middleware,
}));
"""


def middleware_diff(framework, price, pay_to):
    if framework == "node":
        before, after = JS_BEFORE, JS_AFTER_FIXED % {"pay_to": pay_to, "amount_xno": price["amount_xno"]}
        name = "app.js"
        note = ("// amountXno was priced from USD %s at read time (%s); re-run this tool to re-quote.\n"
                % (price["usd"], price.get("quoted_at"))) if price["mode"] == "usd_pegged" else ""
        after = note + after
    else:
        before = PY_BEFORE
        name = "app.py"
        if price["mode"] == "usd_pegged":
            after = PY_AFTER_USD % {"pay_to": pay_to, "usd_cents": price["usd_cents"], "usd": price["usd"]}
        else:
            after = PY_AFTER_FIXED % {"pay_to": pay_to, "amount_xno": price["amount_xno"]}
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        "a/" + name, "b/" + name))


TEST = '''"""Generated by accepts_snippet.py. Fails if the Nano rail changed any existing entry or is not appended last."""
import json, unittest, urllib.request

URL = %(url)r
EXISTING = json.loads(%(existing)r)       # the entries that were there when this was generated
PAY_TO = %(pay_to)r


class NanoAcceptLeg(unittest.TestCase):
    def accepts(self):
        req = urllib.request.Request(URL, headers={"User-Agent": "accepts-test/1"})
        try:
            body = urllib.request.urlopen(req, timeout=15).read()
        except urllib.error.HTTPError as e:      # a 402 carries the challenge
            body = e.read()
        doc = json.loads(body)
        return doc["accepts"] if "accepts" in doc else doc["resources"][0]["accepts"]

    def test_existing_entries_unchanged_in_order(self):
        self.assertEqual(self.accepts()[:len(EXISTING)], EXISTING)

    def test_one_nano_entry_appended_last_and_pays_us(self):
        a = self.accepts()
        self.assertEqual(len(a), len(EXISTING) + 1)
        self.assertTrue(a[-1]["network"].startswith("nano"))
        self.assertEqual(a[-1]["payTo"], PAY_TO)
        self.assertEqual(int(a[-1]["amount"]), int(a[-1]["maxAmountRequired"]))   # raw, an integer

if __name__ == "__main__":
    unittest.main()
'''


def generate(manifest, pay_to, usd=None, amount=None, framework="python", feeds=None, now=None, fetch=None):
    verdict = nanoaddr.validate(pay_to)
    if not verdict["valid"]:
        raise ValueError("invalid_address: %s" % verdict["reason"])
    fetch = fetch or _cli._fetch
    _, _, text = fetch(manifest)
    yaml = _cli._is_yaml(manifest)
    body = _cli._challenge_of(text, manifest)
    accepts = body["accepts"]
    if _challenge.find_nano(accepts) != -1:
        return {"status": "already_present", "message": "a Nano entry is already in accepts[]; nothing to do"}
    price = price_for(usd, amount, accepts, feeds if feeds is not None
                      else [usd_quote.feed_coingecko, usd_quote.feed_kucoin], now)
    patched = _challenge.append_nano(body, verdict["normalised"], price["amount_xno"])
    _challenge.assert_additive(accepts, patched["accepts"])
    out = {"status": "ok", "manifest": patched, "price": price,
           "nano_entry": patched["accepts"][-1],
           "middleware_diff": middleware_diff(framework, price, verdict["normalised"]),
           "test": TEST % {"url": manifest if "://" in manifest else "<URL OF YOUR 402/MANIFEST>",
                           "existing": json.dumps(accepts, sort_keys=True), "pay_to": verdict["normalised"]}}
    if yaml:
        out["manifest_diff"] = None
        out["manifest_diff_note"] = ("YAML cannot be re-rendered without rewriting lines this tool did not "
                                     "author; append `nano_entry` to accepts[] yourself.")
    else:
        before = json.dumps(body, indent=2).splitlines(True)
        after = json.dumps(patched, indent=2).splitlines(True)
        diff = list(difflib.unified_diff(before, after, "a/manifest.json", "b/manifest.json"))
        removals = [l for l in diff if l.startswith("-") and not l.startswith("---")]
        if not _cli._removals_are_only_reflow(removals):
            raise _challenge.NotAdditive("the diff would delete %d line(s) that are not the trailing-bracket reflow"
                                         % len(removals))
        out["manifest_diff"] = "".join(diff)
    return out


def main(argv=None, fetch=None, feeds=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--manifest", required=True)
    p.add_argument("--pay-to", required=True)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--usd")
    g.add_argument("--amount")
    p.add_argument("--framework", choices=("python", "node"), default="python")
    p.add_argument("--out-dir")
    a = p.parse_args(argv)
    try:
        out = generate(a.manifest, a.pay_to, a.usd, a.amount, a.framework, feeds, fetch=fetch)
    except _challenge.NotAdditive as e:
        print("refusing to emit a non-additive patch: %s" % e, file=sys.stderr)
        return EXIT_NOT_ADDITIVE
    except NoPrice as e:
        print(json.dumps({"error": "no_price", "message": str(e)}))
        return EXIT_NO_PRICE
    except (ValueError, OSError, RuntimeError, KeyError, _cli.Unreadable, money.AmountError) as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}))
        return EXIT_USAGE
    if a.out_dir and out["status"] == "ok":
        os.makedirs(a.out_dir, exist_ok=True)
        files = {"manifest.nano.json": json.dumps(out["manifest"], indent=2) + "\n",
                 "manifest.diff": out["manifest_diff"] or "", "middleware.diff": out["middleware_diff"],
                 "test_nano_accepts.py": out["test"], "price.json": json.dumps(out["price"], indent=2) + "\n"}
        for name, text in files.items():
            with open(os.path.join(a.out_dir, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        print(json.dumps({"status": "written", "dir": a.out_dir, "files": sorted(files)}))
    else:
        print(json.dumps(out, indent=2))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
