"""dual-rail: add XNO beside USDC/x402, never instead of it.

    dual-rail inspect <manifest-url|file>
        Print the current accepts[] entries and whether a Nano entry exists.
        Read-only. Writes nothing.

    dual-rail add --manifest <url|file> --pay-to nano_... --amount 0.0001
                  [--out patch.json | --diff]
        Emit the entry to append, or a unified diff. Exits 3 rather than emit a
        patch that removes or modifies anything.

    dual-rail verify <base-url> [--payment <block hash>] [--json]
        The deliverable. Exit 0 only if every check passes.

        --payment is a settled block that paid this resource. It is SPENT by
        the run: one payment buys one call, so verifying twice needs two
        payments. That is the replay protection working, not a broken rail.

The verifier is the product; the middleware is the implementation detail.
Until `verify` prints 7/7 against a real URL, the message that says "this
is not a migration" is not sendable.
"""

import argparse
import difflib
import json
import sys
import urllib.error
import urllib.request

import adapter as _adapter
import challenge as _challenge
import money
import nanoaddr

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NOT_ADDITIVE = 3

TIMEOUT = 10.0
UNKNOWN_BLOCK = "0" * 63 + "1"      # well-formed, and no node will know it


def _fetch(location, headers=None):
    """A URL or a local file. Returns (status, headers, text)."""
    if "://" not in location:
        with open(location, encoding="utf-8") as handle:
            return 200, {}, handle.read()
    request = urllib.request.Request(location, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


class Unreadable(ValueError):
    pass


def _challenge_of(text, path=""):
    """Parse a manifest. JSON always; YAML only if PyYAML happens to be there.

    This package has no dependencies and is not about to acquire one for a
    file format. A YAML manifest with no PyYAML installed is refused with a
    message that says what to do, which is better than a half-parse.
    """
    stripped = text.lstrip()
    looks_json = stripped.startswith("{") or stripped.startswith("[")
    if looks_json or not path.endswith((".yaml", ".yml")):
        body = json.loads(text)
    else:
        try:
            import yaml
        except ImportError:
            raise Unreadable(
                "%s looks like YAML and PyYAML is not installed. Convert it to JSON, "
                "or pip install pyyaml - this package will not take the dependency "
                "on your behalf." % path
            ) from None
        body = yaml.safe_load(text)
    if not isinstance(body, dict) or not isinstance(body.get("accepts"), list):
        raise Unreadable("no accepts[] array in this document")
    return body


def _is_yaml(path) -> bool:
    return str(path).endswith((".yaml", ".yml"))


def _rails(accepts):
    return [{"scheme": e.get("scheme"), "network": e.get("network"),
             "asset": e.get("asset"), "maxAmountRequired": e.get("maxAmountRequired")}
            for e in accepts]


# ------------------------------------------------------------------ inspect

def cmd_inspect(args) -> int:
    try:
        _, _, text = _fetch(args.target)
        body = _challenge_of(text, args.target)
    except Exception as exc:
        print(json.dumps({"error": "unreadable", "message": str(exc)}, indent=2))
        return EXIT_USAGE
    accepts = body["accepts"]
    index = _challenge.find_nano(accepts)
    print(json.dumps({
        "target": args.target,
        "entries": len(accepts),
        "rails": _rails(accepts),
        "nano_entry_present": index != -1,
        "nano_entry_index": index,
        "resource": _challenge.resource_of(accepts),
    }, indent=2))
    return EXIT_OK


# ---------------------------------------------------------------------- add

def cmd_add(args) -> int:
    verdict = nanoaddr.validate(args.pay_to)
    if not verdict["valid"]:
        print(json.dumps({"error": "invalid_address", "reason": verdict["reason"],
                          "message": verdict["message"]}, indent=2))
        return EXIT_USAGE
    try:
        money.check_price(args.amount)
    except money.AmountError as exc:
        print(json.dumps({"error": exc.reason, "message": exc.message}, indent=2))
        return EXIT_USAGE
    try:
        _, _, text = _fetch(args.manifest)
        body = _challenge_of(text, args.manifest)
    except Exception as exc:
        print(json.dumps({"error": "unreadable", "message": str(exc)}, indent=2))
        return EXIT_USAGE

    if _challenge.find_nano(body["accepts"]) != -1:
        print(json.dumps({"status": "already_present",
                          "message": "a Nano entry is already in accepts[]; nothing to do"},
                         indent=2))
        return EXIT_OK

    try:
        patched = _challenge.append_nano(body, verdict["normalised"], args.amount)
    except _challenge.NotAdditive as exc:
        print("refusing to emit a non-additive patch: %s" % exc, file=sys.stderr)
        return EXIT_NOT_ADDITIVE

    if args.diff and _is_yaml(args.manifest):
        # A YAML round trip re-renders the whole document - quoting, key order,
        # flow vs block style - so a diff of it cannot be PROVEN to remove
        # nothing. Refuse rather than emit one that merely looks additive.
        print("refusing to emit a non-additive patch: a YAML manifest cannot be "
              "re-rendered without rewriting lines this tool did not author. Use "
              "--out to get the entry and append it yourself.", file=sys.stderr)
        return EXIT_NOT_ADDITIVE

    if args.diff:
        before = json.dumps(body, indent=2).splitlines(keepends=True)
        after = json.dumps(patched, indent=2).splitlines(keepends=True)
        diff = list(difflib.unified_diff(before, after, "a/manifest.json", "b/manifest.json"))
        removals = [line for line in diff
                    if line.startswith("-") and not line.startswith("---")]
        if not _removals_are_only_reflow(removals):
            print("refusing to emit a non-additive patch: it would delete %d line(s) that "
                  "are not the trailing-bracket reflow" % len(removals), file=sys.stderr)
            return EXIT_NOT_ADDITIVE
        sys.stdout.write("".join(diff))
        return EXIT_OK

    entry = patched["accepts"][-1]
    blob = json.dumps(entry, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(blob + "\n")
        print(json.dumps({"status": "written", "path": args.out,
                          "appended_at_index": len(patched["accepts"]) - 1}, indent=2))
    else:
        print(blob)
    return EXIT_OK


def _removals_are_only_reflow(removals) -> bool:
    """A JSON re-render moves the closing bracket of the element before ours from
    `}` to `},`. That one line is the only deletion an additive patch may contain."""
    for line in removals:
        if line[1:].strip() not in ("}", "},", "]", "],"):
            return False
    return True


# ------------------------------------------------------------------- verify

def cmd_verify(args) -> int:
    checks = []

    def record(name, passed, detail=""):
        checks.append({"name": name, "pass": bool(passed), "detail": detail})

    base = args.base_url.rstrip("/")
    url = base if "/" in base.split("://", 1)[-1] else base + "/"

    status, headers, text = None, {}, ""
    try:
        status, headers, text = _fetch(url)
    except Exception as exc:
        record("402 challenge reachable", False, str(exc))
        return _report(args, checks, [], [])

    version = headers.get("x-402-version") or headers.get("X-402-Version") or ""
    record("402 challenge reachable", status == 402,
           "HTTP %s%s" % (status, ", x-402-version: %s" % version if version else ""))

    try:
        body = _challenge_of(text)
        accepts = body["accepts"]
    except Exception as exc:
        record("pre-existing rails intact", False, str(exc))
        return _report(args, checks, [], [])

    index = _challenge.find_nano(accepts)
    rails_after = _rails(accepts)
    rails_before = _rails([e for i, e in enumerate(accepts) if i != index])

    record("pre-existing rails intact", rails_after[:len(rails_before)] == rails_before,
           "%d entr%s: %s  (unchanged)"
           % (len(rails_before), "y" if len(rails_before) == 1 else "ies",
              ", ".join("%s/%s" % (r["asset"] or "?", (r["network"] or "?").split(":")[0])
                        for r in rails_before) or "none"))

    record("nano entry present and last", index == len(accepts) - 1 and index != -1,
           "" if index == -1 else "%s %s %s -> %s"
           % (accepts[index].get("network"), accepts[index].get("asset"),
              accepts[index].get("maxAmountRequired"), (accepts[index].get("payTo") or "")[:12] + "..."))

    pay_to = accepts[index].get("payTo") if index != -1 else ""
    record("nano payTo passes checksum", nanoaddr.is_valid(pay_to or ""),
           nanoaddr.validate(pay_to or "").get("reason", "valid"))

    others = [e for i, e in enumerate(accepts) if i != index]
    well_formed = bool(others) and all(
        e.get("scheme") and e.get("network") and e.get("payTo") and e.get("maxAmountRequired")
        for e in others)
    record("existing rail still settles", well_formed,
           "%d pre-existing challenge%s well-formed"
           % (len(others), "" if len(others) == 1 else "s"))

    if args.payment:
        paid_status, _, paid_text = _fetch(url, {_adapter.PAYMENT_HEADER: _proof(args.payment)})
        settled = paid_status < 400 and paid_status != 402
        detail = "block %s... -> HTTP %s" % (args.payment[:8], paid_status)
        if not settled and paid_status == 402:
            # One payment buys one call, so a second `verify` with the SAME block
            # is refused by design. That looks like a broken rail if nobody says so.
            detail += ("  (a block already presented to this resource has been "
                       "consumed by replay protection - use a fresh payment)")
        record("nano rail settles", settled, detail)
    else:
        record("nano rail settles", False,
               "no payment proof supplied - pass --payment <block hash> to check this "
               "against a real settled block")

    outage_status, _, outage_text = _fetch(url, {_adapter.PAYMENT_HEADER: _proof(UNKNOWN_BLOCK)})
    entries_intact = False
    try:
        entries_intact = len(_challenge_of(outage_text)["accepts"]) == len(accepts)
    except Exception:
        entries_intact = False
    record("node outage falls through",
           outage_status == 402 and entries_intact and outage_status < 500,
           "unverifiable proof -> HTTP %s with all %d entries, no 5xx"
           % (outage_status, len(accepts)))

    return _report(args, checks, rails_before, rails_after)


def _proof(block_hash: str) -> str:
    return json.dumps({"scheme": _challenge.SCHEME, "network": _challenge.NETWORK,
                       "payload": {"blockHash": block_hash}})


def _report(args, checks, rails_before, rails_after) -> int:
    passed = sum(1 for check in checks if check["pass"])
    ok = passed == len(checks) and len(checks) == 7
    if args.json:
        print(json.dumps({"url": args.base_url, "checks": checks, "pass": ok,
                          "rails_before": rails_before, "rails_after": rails_after},
                         indent=2))
    else:
        print("\ndual-rail verify %s\n" % args.base_url)
        for check in checks:
            print("[%s] %-38s %s" % ("pass" if check["pass"] else "fail",
                                     check["name"], check["detail"]))
        print("\nverify: %d/%d pass%s\n"
              % (passed, len(checks),
                 " - both rails live, existing rails unchanged." if ok else ""))
    return EXIT_OK if ok else EXIT_FAIL


# ---------------------------------------------------------------------- main

def build_parser():
    parser = argparse.ArgumentParser(prog="dual-rail", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    inspect = sub.add_parser("inspect", help="print the current accepts[] entries")
    inspect.add_argument("target")
    inspect.set_defaults(fn=cmd_inspect)

    add = sub.add_parser("add", help="emit the entry to append")
    add.add_argument("--manifest", required=True)
    add.add_argument("--pay-to", required=True)
    add.add_argument("--amount", required=True)
    add.add_argument("--out")
    add.add_argument("--diff", action="store_true")
    add.set_defaults(fn=cmd_add)

    verify = sub.add_parser("verify", help="prove both rails work against a live endpoint")
    verify.add_argument("base_url")
    verify.add_argument("--payment", help="a settled block hash to present as proof")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(fn=cmd_verify)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return EXIT_USAGE
    if not hasattr(args, "json"):
        args.json = False
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
