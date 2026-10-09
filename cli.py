"""dual-rail: add XNO beside USDC/x402, never instead of it.

    dual-rail inspect <manifest-url|file>
        Print the current accepts[] entries and whether a Nano entry exists.
        Read-only. Writes nothing.

    dual-rail add --manifest <url|file> --pay-to nano_... --amount 0.0001
                  [--out patch.json | --diff]
        Emit the entry to append, or a unified diff. Exits 3 rather than emit a
        patch that removes or modifies anything.

    dual-rail check <manifest-url|file>
        Say whether an x402 client would pay the Nano entry in this 402, and
        name every field that would sink it. Read-only. Exits 1 if nothing in
        it is payable, so it can gate a deploy.

    dual-rail verify <base-url> [--payment <block hash>] [--json]
        The deliverable. Exit 0 only if every check passes.

        --payment is a settled block that paid this resource. It is SPENT by
        the run: one payment buys one call, so verifying twice needs two
        payments. That is the replay protection working, not a broken rail.

    dual-rail verifiers [--node URL] [--json]
        Who can validate an XNO (nano:mainnet) payment today, checked live:
        a public node read (no facilitator, no custodian) and any facilitator
        whose published docs name a Nano scheme. Read-only. Exits 1 if none
        could be confirmed from here.

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
import declaration
import money
import nanoaddr
import verifiers as _verifiers

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


def _document_of(text, path=""):
    """Parse a manifest. JSON always; YAML only if PyYAML happens to be there.

    This package has no dependencies and is not about to acquire one for a
    file format. A YAML manifest with no PyYAML installed is refused with a
    message that says what to do, which is better than a half-parse.

    Returns whatever the document says, shape unchecked - `check` has to be
    able to report a missing or malformed accepts[] rather than refuse to
    read the file at all.
    """
    stripped = text.lstrip()
    looks_json = stripped.startswith("{") or stripped.startswith("[")
    if looks_json or not path.endswith((".yaml", ".yml")):
        return json.loads(text)
    try:
        import yaml
    except ImportError:
        raise Unreadable(
            "%s looks like YAML and PyYAML is not installed. Convert it to JSON, "
            "or pip install pyyaml - this package will not take the dependency "
            "on your behalf." % path
        ) from None
    return yaml.safe_load(text)


def _challenge_of(text, path=""):
    """`_document_of`, and it must be a 402 challenge with an accepts[] array.

    Deliberately strict for `add`: a multi-resource manifest carries one
    accepts[] per `resources[]` item, and choosing which of them gets the
    payout address is not a guess this tool may make on an operator's behalf.
    `inspect` reads such a document through `_resource_sections` instead,
    because reporting on all of them decides nothing.
    """
    body = _document_of(text, path)
    if not isinstance(body, dict) or not isinstance(body.get("accepts"), list):
        if isinstance(body, dict) and isinstance(body.get("resources"), list):
            raise Unreadable(
                "this is a multi-resource manifest: accepts[] sits under each "
                "resources[] item, not at the top level. `dual-rail inspect` "
                "reads it; `add` will not, because which resource gets the "
                "payout address is the operator's choice, not this tool's - "
                "fetch the one resource's own 402 and patch that")
        raise Unreadable("no accepts[] array in this document")
    return body


#: How to read an entry's price, in the order the clients read it.
#: x402 renamed the field between protocol versions - `maxAmountRequired` in
#: v1, `amount` in v2 - and feeless402 0.2.12's `nano_pay.x402.offer_amount_raw`
#: is `int(offer[field])` over exactly this tuple, so this is what a payer
#: actually looks at rather than what either schema requires. Reading only the
#: v1 name reported every v2 entry's price as `null` (issue #1), and `verify`
#: called a v2 seller's own working rail malformed on the strength of it.
AMOUNT_FIELDS = ("amount", "maxAmountRequired", "max_amount_required")


def _amount_of(entry):
    """(value, field name) for an entry's price, or (None, None).

    Version-blind on purpose: a document's declared version does not bind its
    entries - a seller spelling a v1 Nano entry inside a v2 document is the
    case `challenge.find_nano` exists for - so the field that is *present*
    is the one a payer will read.
    """
    if not isinstance(entry, dict):
        return None, None
    for field in AMOUNT_FIELDS:
        if entry.get(field) is not None:
            return entry[field], field
    return None, None


def _resource_sections(body):
    """[(label, accepts)] for a document, top-level or multi-resource.

    One section for an ordinary 402, one per `resources[]` item for a manifest
    such as `extract.paypercall.dev/.well-known/x402`. Each is reported on its
    own: flattening them would invent an accepts[] array no client ever sees
    and would make the Nano entry's index meaningless.
    """
    if not isinstance(body, dict):
        return []
    if isinstance(body.get("accepts"), list):
        return [(None, body["accepts"])]
    sections = []
    for index, item in enumerate(body.get("resources") or []):
        if not isinstance(item, dict):
            continue
        accepts = item.get("accepts")
        label = item.get("resource") or item.get("url") or "resources[%d]" % index
        sections.append((label, accepts if isinstance(accepts, list) else []))
    return sections


def _is_yaml(path) -> bool:
    return str(path).endswith((".yaml", ".yml"))


def _rails(accepts):
    rails = []
    for entry in accepts:
        if not isinstance(entry, dict):
            rails.append({"scheme": None, "network": None, "asset": None,
                          "amount": None, "amount_field": None})
            continue
        amount, field = _amount_of(entry)
        rails.append({"scheme": entry.get("scheme"), "network": entry.get("network"),
                      "asset": entry.get("asset"),
                      "amount": amount, "amount_field": field})
    return rails


# ------------------------------------------------------------------ inspect

def cmd_inspect(args) -> int:
    try:
        _, _, text = _fetch(args.target)
        body = _document_of(text, args.target)
    except Exception as exc:
        print(json.dumps({"error": "unreadable", "message": str(exc)}, indent=2))
        return EXIT_USAGE

    sections = _resource_sections(body)
    if not sections:
        print(json.dumps({"error": "unreadable",
                          "message": "no accepts[] array in this document"}, indent=2))
        return EXIT_USAGE

    def report_for(accepts):
        index = _challenge.find_nano(accepts)
        return {"entries": len(accepts), "rails": _rails(accepts),
                "nano_entry_present": index != -1, "nano_entry_index": index,
                "resource": _challenge.resource_of(accepts)}

    # A single top-level accepts[] keeps the shape it has always had; the
    # multi-resource form reports each resource separately, because its
    # accepts[] arrays are separate documents to every client that reads them.
    if len(sections) == 1 and sections[0][0] is None:
        out = {"target": args.target}
        out.update(report_for(sections[0][1]))
    else:
        out = {"target": args.target, "multi_resource": True,
               "resources": [dict(report_for(accepts), resource_label=label)
                             for label, accepts in sections]}
        out["entries"] = sum(r["entries"] for r in out["resources"])
        out["nano_entry_present"] = any(r["nano_entry_present"]
                                        for r in out["resources"])
    print(json.dumps(out, indent=2))
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


# ------------------------------------------------------------------- check

def _print_nano_entry(entry, indent="") -> None:
    """One Nano entry's verdict, in the same words wherever it is reported.

    `advertisement_only` is printed and does NOT count against the entry: in a
    catalogue the payer reads those fields off the endpoint's own 402, so
    reporting them as problems would be the false alarm issue #1's `verify`
    half was fixed for.
    """
    print("\n%saccepts[%d]  network %s%s" % (
        indent, entry["index"], json.dumps(entry["network_as_given"]),
        "" if entry["canonical_network"] else "  (not read as mainnet)"))
    for version, check in sorted(entry["checks"].items()):
        print("%s  x402 v%s: %s" % (indent, version, "payable" if check["payable"]
                                    else "%d problem(s)" % len(check["problems"])))
        for issue in check["problems"]:
            print("%s    [%s] %s" % (indent, issue["code"], issue["message"]))
        for issue in check.get("advertisement_only", []):
            print("%s    (note) [%s] %s" % (indent, issue["code"], issue["message"]))


def cmd_check(args) -> int:
    """The declaration validator, pointed at a manifest.

    `inspect` reports what is in an accepts[] array; this reports whether a
    client would act on it. A seller whose entry is silently skipped has no
    other way to find out: the x402 libraries drop an entry that fails their
    schema without saying so, and from the seller's side that is
    indistinguishable from nobody wanting to pay in XNO.
    """
    try:
        _, _, text = _fetch(args.target)
        body = _document_of(text, args.target)
    except Exception as exc:
        print(json.dumps({"error": "unreadable", "message": str(exc)}, indent=2))
        return EXIT_USAGE

    report = declaration.inspect(body)
    report["target"] = args.target

    if args.json:
        print(json.dumps(report, indent=2))
        return EXIT_OK if report["payable"] else EXIT_FAIL

    print("\ndual-rail check %s\n" % args.target)
    print("x402 version declared: %s" % (report["x402_version"] or "none"))

    if report.get("multi_resource"):
        # A resource catalogue. Its accepts[] arrays are separate documents to
        # every client that reads them, so each resource is reported on its own
        # rather than flattened into an index no client would recognise.
        print("document:              resource catalogue, %d resource(s)"
              % len(report["resources"]))
        print("accepts[] entries:     %d across all resources" % report["entries"])
        print("resources naming Nano: %d, of which not payable: %d\n"
              % (report["resources_naming_nano"], report["resources_not_payable"]))
        for issue in report["problems"]:
            print("[document] %-34s %s" % (issue["field"] or "-", issue["message"]))
        for section in report["resources"]:
            if not section["nano_entries"] and not section["problems"]:
                continue
            print("\n%s" % section["resource_label"])
            for issue in section["problems"]:
                print("  [%s] %s" % (issue["code"], issue["message"]))
            for entry in section["nano_entries"]:
                _print_nano_entry(entry, indent="  ")
        print("\ncheck: %s\n" % (
            "payable - an x402 client would pay the Nano entry of every resource "
            "in this catalogue" if report["payable"]
            else "NOT payable as it stands"))
        return EXIT_OK if report["payable"] else EXIT_FAIL

    print("accepts[] entries:     %d" % report["entries"])
    print("entries naming Nano:   %d\n" % len(report["nano_entries"]))
    for issue in report["problems"]:
        print("[document] %-34s %s" % (issue["field"] or "-", issue["message"]))
    for entry in report["nano_entries"]:
        _print_nano_entry(entry)
    print("\ncheck: %s\n" % ("payable - an x402 client of the declared version would "
                             "pay this Nano entry" if report["payable"]
                             else "NOT payable as it stands"))
    return EXIT_OK if report["payable"] else EXIT_FAIL


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
              _amount_of(accepts[index])[0], (accepts[index].get("payTo") or "")[:12] + "..."))

    pay_to = accepts[index].get("payTo") if index != -1 else ""
    record("nano payTo passes checksum", nanoaddr.is_valid(pay_to or ""),
           nanoaddr.validate(pay_to or "").get("reason", "valid"))

    others = [e for i, e in enumerate(accepts) if i != index]

    def missing_fields(entry):
        """The fields this entry needs to be payable at all, and lacks.

        The price is looked up under every name x402 has used for it, not just
        the v1 one: reading `maxAmountRequired` alone called every v2 seller's
        own working rail malformed and failed this check on a correct manifest.
        """
        absent = [f for f in ("scheme", "network", "payTo")
                  if not (isinstance(entry, dict) and entry.get(f))]
        if _amount_of(entry)[0] is None:
            absent.append("/".join(AMOUNT_FIELDS))
        return absent

    faults = [(i, missing_fields(e)) for i, e in enumerate(others)]
    faults = [(i, absent) for i, absent in faults if absent]
    well_formed = bool(others) and not faults
    if not others:
        detail = ("no pre-existing entry to preserve - this 402 offered nothing "
                  "before the Nano entry, so there is no second rail to keep")
    elif faults:
        # Name the entry and the field, so a failure here cannot be read as
        # "dual-rail broke my rail" when it is this check misreading one.
        detail = "; ".join("accepts[%d] lacks %s" % (i, ", ".join(absent))
                           for i, absent in faults)
    else:
        detail = ("%d pre-existing challenge%s well-formed"
                  % (len(others), "" if len(others) == 1 else "s"))
    record("existing rail still settles", well_formed, detail)

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


# ---------------------------------------------------------------- verifiers

def cmd_verifiers(args, node=None, fetch=None) -> int:
    report = _verifiers.report(node=node, node_url=args.node,
                               fetch=fetch or _verifiers.fetch_text)
    ok = report["confirmed_live"] > 0
    if args.json:
        print(json.dumps(report, indent=2))
        return EXIT_OK if ok else EXIT_FAIL
    print("\ndual-rail verifiers  (who can validate an XNO nano:mainnet payment, checked now)\n")
    for v in report["verifiers"]:
        custody = {False: "no", True: "YES"}.get(v["custodial"], "unknown")
        print("[%s] %s" % ("live" if v["confirmed_live"] else "----", v["name"]))
        if v["kind"] == "self":
            print("       node %s  reachable: %s" % (v["node"], str(v["reachable"]).lower()))
            print("       holds funds: %s  (%s)" % (custody, v["how"]))
        else:
            print("       %s - %s" % (v["url"], v["what"]))
            print("       Nano section: %s  network: %s  scheme: %s  holds funds: %s"
                  % (str(v["nano_section_found"]).lower(), v["network"] or "-",
                     v["scheme"] or "-", custody))
        print("       %s" % v["detail"])
    print("\nverifiers: %d confirmed live\n" % report["confirmed_live"])
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

    check = sub.add_parser("check", help="would an x402 client pay this Nano entry?")
    check.add_argument("target")
    check.add_argument("--json", action="store_true")
    check.set_defaults(fn=cmd_check)

    verify = sub.add_parser("verify", help="prove both rails work against a live endpoint")
    verify.add_argument("base_url")
    verify.add_argument("--payment", help="a settled block hash to present as proof")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(fn=cmd_verify)

    who = sub.add_parser("verifiers", help="who can validate an XNO payment today (live)")
    who.add_argument("--node", default=_verifiers.DEFAULT_NODE,
                     help="public Nano node to read (default %(default)s)")
    who.add_argument("--json", action="store_true")
    who.set_defaults(fn=cmd_verifiers)
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
