"""Why no x402 client will pay your Nano entry, in one read-only call.

A seller appends a Nano entry to its 402 challenge, a payer skips it, and
neither side says a word. The entry is not rejected with a reason - it fails a
schema check inside the client library and is dropped before any payment is
attempted. From the seller's side that is indistinguishable from nobody
wanting to pay in XNO.

This module is the missing error message. Give it a 402 challenge - ours,
anybody's, from a file or a live endpoint - and it says whether an x402 v1
client and an x402 v2 client would accept it, and names every field that
would sink it.

It is in two halves, deliberately:

`shape_problems` is **only** what the x402 schema itself rejects, and is meant
to be exactly equivalent to it. The rules are read off the schemas that do the
rejecting, in the version installed and inspected while this was written:

* ``@x402/core`` 2.28.0 ``PaymentRequirementsV1Schema``: ``scheme``,
  ``network``, ``maxAmountRequired``, ``resource``, ``description``, ``payTo``,
  ``maxTimeoutSeconds``, ``asset`` all required; ``maxTimeoutSeconds`` is
  ``z.number().positive()``, so the JSON string ``"60"`` is refused.
* ``@x402/core`` 2.28.0 ``PaymentRequirementsV2Schema``: ``scheme``,
  ``network``, ``amount``, ``asset``, ``payTo``, ``maxTimeoutSeconds``
  required - and **no** ``resource`` or ``description`` in the entry, because
  v2 moved the resource to a top-level object with a required ``url``. The
  amount field is renamed from ``maxAmountRequired`` to ``amount``, and
  ``NetworkSchemaV2`` requires a colon where ``NetworkSchemaV1`` took any
  non-empty string.

`nano_problems` is what a Nano-aware reader adds on top - the things no
generic x402 conformance tool can check, because they need the chain:

* an address that is well-formed but **fails its checksum**. x402's own schema
  asks only for a non-empty string, so the client accepts it and the money
  sent there is unspendable.
* an amount that is not a whole number of raw. ``@x402nano/typescript-common``
  0.1.0 declares an integer amount ``z.string().regex(/^\\d+$/)``, so the
  decimal ``"0.0001"`` is not a payable v2 amount however right it looks.
* a network identifier that means Nano but is not the canonical spelling, or
  does not say which Nano network. See ``network.py``.

Because the whole document is rejected or accepted as one, a sibling entry
sinks our own: ``accepts[0]`` carrying ``"network": "base"`` in a document
that says ``x402Version: 2`` fails ``NetworkSchemaV2``, and the v2 client
throws out the array our entry is in. `inspect` reports that against the
document rather than staying silent about an entry it was not asked about.

Read-only throughout: no key, no node, no network, no money. It parses a
document and returns a verdict.
"""

import money
import nanoaddr
import network as _network

from network import kind, show

#: A Nano state block's balance field is 128 bits, so no amount above this can
#: exist in a block at all. An entry asking for more is a typo, not a price.
MAX_RAW = 2 ** 128 - 1

ASSET = "XNO"
SCHEME = "exact"

#: `maxTimeoutSeconds` is `z.number().positive()` in both versions.
TIMEOUT_FIELD = "maxTimeoutSeconds"

#: Required entry fields, per version, read off @x402/core 2.28.0.
V1_REQUIRED = ("scheme", "network", "maxAmountRequired", "resource",
               "description", "payTo", TIMEOUT_FIELD, "asset")
V2_REQUIRED = ("scheme", "network", "amount", "asset", "payTo", TIMEOUT_FIELD)

#: The amount field's name in each version. x402 renamed it between the two.
AMOUNT_FIELD = {1: "maxAmountRequired", 2: "amount"}

#: Entry fields the schema declares NonEmptyString, per version.
V1_NON_EMPTY = ("scheme", "network", "maxAmountRequired", "resource", "payTo", "asset")
V2_NON_EMPTY = ("scheme", "amount", "asset", "payTo")

#: @x402/core's NetworkSchemaV2: z.string().min(3).refine(v => v.includes(':')).
V2_NETWORK_MIN_LENGTH = 3

#: Fields the x402 PaymentRequirements schema requires of a payment
#: requirement, but which a RESOURCE CATALOGUE entry is not expected to carry,
#: because the payer is handed them by the endpoint's own 402 response rather
#: than by the catalogue. Measured against the live pair on 2026-10-08:
#: `extract.paypercall.dev/.well-known/x402` omits all three on all 24 of its
#: entries, while a real 402 from the same resource carries
#: ``maxTimeoutSeconds: 60`` and a top-level ``resource`` object. Reporting
#: their absence against a catalogue would be a false alarm of exactly the kind
#: #4 removed from `verify` - the tool telling a seller its working rail is
#: broken. A field that is PRESENT and malformed is still a problem: this
#: relaxes only `field_missing`.
CHALLENGE_ONLY_FIELDS = ("maxTimeoutSeconds", "resource", "description")


def _problem(code, field, message):
    return {"code": code, "field": field, "message": message}


def _is_non_empty_string(value):
    """@x402/core's NonEmptyString: z.string().min(1)."""
    return isinstance(value, str) and len(value) >= 1


def _positive_number(value):
    """z.number().positive(). JSON true/false is a bool, which zod refuses."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _raw_of_integer_string(text):
    """An integer string of raw -> int, or None. `^\\d+$`, as the schema has it.

    Written out as `[0-9]` rather than `\\d`, which in Python's `re` - and in
    `str.isdigit()` - also matches Unicode digits no client reads back.
    """
    if not isinstance(text, str) or not text:
        return None
    for char in text:
        if char < "0" or char > "9":
            return None
    return int(text)


# --------------------------------------------------------------- shape

def shape_problems(entry, version) -> list:
    """Exactly what @x402/core's PaymentRequirements schema would reject.

    Nothing Nano-specific: this half is meant to be equivalent to the library,
    so that a document it passes is one the library parses, and a document it
    fails is one the library throws out. `tests/test_declaration.py` holds that
    equivalence against the installed package.
    """
    problems = []

    if not isinstance(entry, dict):
        problems.append(_problem("entry_not_an_object", "",
                                 "an accepts[] entry must be a JSON object, got %s"
                                 % kind(entry)))
        return problems

    required = V1_REQUIRED if version == 1 else V2_REQUIRED
    non_empty = V1_NON_EMPTY if version == 1 else V2_NON_EMPTY

    for field in required:
        if field not in entry:
            problems.append(_problem(
                "field_missing", field,
                "x402 v%d requires %s on every accepts[] entry; it is absent"
                % (version, field)))

    for field in non_empty:
        if field in entry and not _is_non_empty_string(entry[field]):
            problems.append(_problem(
                "field_not_a_non_empty_string", field,
                "x402 v%d declares %s a non-empty string; got %s"
                % (version, field, show(entry[field]))))

    if version == 2 and "network" in entry:
        value = entry["network"]
        if not isinstance(value, str) or len(value) < V2_NETWORK_MIN_LENGTH \
                or ":" not in value:
            problems.append(_problem(
                "network_not_caip2", "network",
                "x402 v2's NetworkSchemaV2 wants at least %d characters including a "
                "colon (CAIP-2); got %s. v1 took any non-empty string, so an entry "
                "like this is payable by a v1 client and invisible to a v2 one"
                % (V2_NETWORK_MIN_LENGTH, show(value))))

    if version == 1 and "description" in entry and not isinstance(entry["description"], str):
        problems.append(_problem(
            "description_not_a_string", "description",
            "x402 v1 declares description z.string() and requires it; null is "
            "refused, an empty string is allowed. Got %s" % show(entry["description"])))

    if TIMEOUT_FIELD in entry and not _positive_number(entry[TIMEOUT_FIELD]):
        problems.append(_problem(
            "timeout_not_a_positive_number", TIMEOUT_FIELD,
            "%s is %s. Both x402 versions declare it z.number().positive(), so a "
            "JSON string - even \"60\" - is refused"
            % (TIMEOUT_FIELD, show(entry[TIMEOUT_FIELD]))))

    # `extra` is z.record(z.unknown()).optional().nullable() in both versions:
    # an object or null, and nothing else. An entry whose extra is a string
    # fails here and takes the whole accepts[] array down with it.
    if "extra" in entry and entry["extra"] is not None \
            and not isinstance(entry["extra"], dict):
        problems.append(_problem(
            "extra_not_an_object", "extra",
            "extra is declared an object or null in both x402 versions; got %s"
            % kind(entry["extra"])))

    # v1 declares two more optional fields that v2's entry schema does not have.
    if version == 1:
        if "mimeType" in entry and not isinstance(entry["mimeType"], str):
            problems.append(_problem(
                "mime_type_not_a_string", "mimeType",
                "x402 v1 declares mimeType z.string().optional(), which admits an "
                "absent key but not a null one; got %s" % kind(entry["mimeType"])))
        if "outputSchema" in entry and entry["outputSchema"] is not None \
                and not isinstance(entry["outputSchema"], dict):
            problems.append(_problem(
                "output_schema_not_an_object", "outputSchema",
                "x402 v1 declares outputSchema an object or null; got %s"
                % kind(entry["outputSchema"])))

    return problems


# ---------------------------------------------------------------- nano

def amount_reading(value, field):
    """How an amount field reads. Returns raw, the decimal it means, problems.

    An integer string is raw; a decimal string is XNO and is reported with the
    raw it would mean, so a seller can see the figure they meant to type. A
    value that is not a string at all is the schema's business, not this one's.
    """
    problems = []
    if not _is_non_empty_string(value):
        return {"raw": None, "decimal_xno": None, "problems": problems}

    raw = _raw_of_integer_string(value)
    if raw is not None:
        if raw == 0:
            problems.append(_problem("amount_zero", field,
                                     "%s is zero; a price of nothing is not a price" % field))
        elif raw > MAX_RAW:
            problems.append(_problem(
                "amount_above_max_raw", field,
                "%s is %s raw, above the 128-bit maximum a Nano block balance can "
                "hold (%s) - no such payment can be built"
                % (field, value, MAX_RAW)))
        return {"raw": raw, "decimal_xno": money.raw_to_xno(raw) if raw <= MAX_RAW else None,
                "problems": problems}

    # Not an integer. A decimal is readable but is not what a v2 payer accepts.
    try:
        as_raw = money.xno_to_raw(value)
    except money.AmountError as exc:
        problems.append(_problem("amount_unparsable", field,
                                 "%s is neither an integer of raw nor a decimal "
                                 "number of XNO (%s)" % (field, exc.reason)))
        return {"raw": None, "decimal_xno": None, "problems": problems}

    problems.append(_problem(
        "amount_is_decimal_xno", field,
        "%s is %s, a decimal number of XNO. An x402 v2 Nano amount is an integer "
        "of raw (@x402nano/typescript-common declares it /^\\d+$/), so this is "
        "%s raw - a v2 client refuses the string as given"
        % (field, show(value), as_raw)))
    return {"raw": as_raw, "decimal_xno": value, "problems": problems}


def nano_problems(entry, version) -> dict:
    """What a Nano-aware reader adds on top of the schema.

    Returns {"problems": [...], "amount": {...}|None, "pay_to": {...}|None}.
    """
    out = {"problems": [], "amount": None, "pay_to": None}
    if not isinstance(entry, dict):
        return out
    problems = out["problems"]
    amount_field = AMOUNT_FIELD[version]

    net = _network.classify(entry.get("network"))
    if not net["nano"]:
        problems.append(_problem("network_not_nano", "network", net["message"]))
    elif not net["mainnet"]:
        problems.append(_problem("network_" + net["reason"], "network", net["message"]))
    elif net["canonical"] != entry.get("network"):
        problems.append(_problem(
            "network_not_canonical", "network",
            "%s is read as Nano mainnet here, but every shipped Nano x402 package "
            "matches on the literal %s - x402-nano-exact 0.1.0 and @x402nano/exact "
            "0.3.0 both carry that string, so a client comparing against it skips "
            "this entry" % (show(entry.get("network")), show(_network.CANONICAL))))

    if _is_non_empty_string(entry.get("scheme")) and entry["scheme"] != SCHEME:
        problems.append(_problem(
            "scheme_unknown", "scheme",
            "the only Nano scheme any shipped x402 package registers is %s; got %s"
            % (show(SCHEME), show(entry["scheme"]))))

    if _is_non_empty_string(entry.get("asset")) and entry["asset"].upper() != ASSET:
        problems.append(_problem(
            "asset_not_xno", "asset",
            "a nano:mainnet entry settles in %s; got %s"
            % (ASSET, show(entry["asset"]))))

    if amount_field in entry:
        reading = amount_reading(entry[amount_field], amount_field)
        if reading["raw"] is not None or reading["decimal_xno"] is not None:
            out["amount"] = {"raw": None if reading["raw"] is None else str(reading["raw"]),
                             "decimal_xno": reading["decimal_xno"]}
        problems.extend(reading["problems"])

    other = AMOUNT_FIELD[2 if version == 1 else 1]
    if other in entry and amount_field not in entry:
        problems.append(_problem(
            "amount_field_is_other_version_name", amount_field,
            "this entry carries %s, x402 v%d's name for the price. A v%d client "
            "reads %s, and v%d's schema has no %s, so the price is invisible to it"
            % (other, 2 if version == 1 else 1, version, amount_field, version, other)))

    if _is_non_empty_string(entry.get("payTo")):
        address = nanoaddr.validate(entry["payTo"])
        if address["valid"]:
            out["pay_to"] = {"valid": True, "normalised": address["normalised"]}
            if address["normalised"] != entry["payTo"].strip():
                out["pay_to"]["as_given"] = entry["payTo"]
        else:
            out["pay_to"] = {"valid": False, "reason": address["reason"]}
            # The reason code, not nanoaddr's own prose: a message is for humans
            # and the two bindings word theirs differently, so quoting one here
            # would make this verdict disagree across bindings over a difference
            # that is not about the declaration at all.
            problems.append(_problem(
                "pay_to_invalid", "payTo",
                "payTo is not a payable Nano account (%s). x402's own schema only "
                "checks that it is a non-empty string, so an address that fails its "
                "checksum is accepted by the client and the money it is sent is "
                "unspendable" % address["reason"]))

    extra = entry.get("extra")
    if isinstance(extra, dict) and "decimals" in extra and extra["decimals"] != money.DECIMALS:
        problems.append(_problem(
            "decimals_wrong", "extra.decimals",
            "XNO has %d decimal places; this entry declares %s, which would rescale "
            "every price a client computes from it"
            % (money.DECIMALS, show(extra["decimals"]))))

    return out


def split_challenge_only(problems) -> tuple:
    """(fatal, advertisement_only) over one entry's shape problems.

    `shape_problems` stays exactly equivalent to @x402/core and is not touched
    by this - the split happens one layer up, so the cross-check in
    `tests/cross_check_x402_core.js` keeps measuring the same function. Only a
    `field_missing` on a `CHALLENGE_ONLY_FIELDS` field moves: a field that is
    present and malformed stays fatal, because no later 402 repairs it.
    """
    fatal, advertised = [], []
    for issue in problems:
        if issue["code"] == "field_missing" and issue["field"] in CHALLENGE_ONLY_FIELDS:
            advertised.append(_problem(
                "challenge_only_field_absent", issue["field"],
                "%s is absent. In a 402 challenge the x402 schema requires it, but "
                "this is a resource catalogue: the payer reads %s off the "
                "endpoint's own 402 response, not off the catalogue entry. Not "
                "counted against this entry"
                % (issue["field"], issue["field"])))
        else:
            fatal.append(issue)
    return fatal, advertised


def check_entry(entry, version, catalogue=False) -> dict:
    """One accepts[] entry against one x402 version. Never raises.

    `payable` is true only when both halves pass: an entry a client of that
    version would accept AND a payment a Nano node could actually settle.

    `catalogue` says the entry came out of a `resources[]` item rather than a
    402 challenge, which relaxes `CHALLENGE_ONLY_FIELDS` and nothing else.
    """
    shape = shape_problems(entry, version)
    advertised = []
    if catalogue:
        shape, advertised = split_challenge_only(shape)
    nano = nano_problems(entry, version) if isinstance(entry, dict) else \
        {"problems": [], "amount": None, "pay_to": None}
    verdict = {
        "version": version,
        "problems": shape + nano["problems"],
        "network": _network.classify(entry.get("network") if isinstance(entry, dict) else None),
    }
    if advertised:
        verdict["advertisement_only"] = advertised
    if nano["amount"] is not None:
        verdict["amount"] = nano["amount"]
    if nano["pay_to"] is not None:
        verdict["pay_to"] = nano["pay_to"]
    verdict["payable"] = not verdict["problems"]
    return verdict


# ------------------------------------------------------------ document

def _payee_of(entry):
    """The normalised account an entry would be paid to, or None if unpayable.

    Normalised rather than as-given, so `xrb_` and `nano_` spellings of one
    account are not mistaken for two different payees.
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("payTo"), str):
        return None
    address = nanoaddr.validate(entry["payTo"])
    return address["normalised"] if address["valid"] else None


def declared_version(challenge) -> int:
    """The x402 version a document declares, or 0 if it declares none.

    @x402/core discriminates on `x402Version`, a literal 1 or 2; anything else
    matches neither half of the union and the whole document is rejected.
    """
    if not isinstance(challenge, dict):
        return 0
    value = challenge.get("x402Version")
    # `isinstance(True, int)` is true in Python and `True == 1`, so a bare
    # `value in (1, 2)` would read `"x402Version": true` as version 1 where the
    # Node binding reads it as no version at all.
    if isinstance(value, bool):
        return 0
    # JSON has ONE number type, so `1.0` and `1` are the same value and zod's
    # z.literal(1) matches both. Python's json gives the first back as a float,
    # which an int-only check would read as no version at all - a disagreement
    # with both the library and the Node binding over a document that parses.
    if isinstance(value, float):
        if not value.is_integer():
            return 0
        value = int(value)
    elif not isinstance(value, int):
        return 0
    return value if value in (1, 2) else 0


def _accepts_report(accepts, versions, resource_url=None, catalogue=False) -> dict:
    """A report over ONE accepts[] array - the shape every client actually reads.

    Shared by the single-challenge and the catalogue paths so the two cannot
    drift: a seller running `check` on a manifest and on that resource's own
    402 must get the same verdict about the same entry.

    `resource_url` is the v2 resource url this array belongs to, already read
    off whichever place the document keeps it. `catalogue` relaxes
    `CHALLENGE_ONLY_FIELDS`; see `split_challenge_only`.
    """
    report = {"problems": [], "entries": len(accepts), "nano_entries": [],
              "payable": False}
    if resource_url is not None:
        report["resource_url"] = resource_url
    problems = report["problems"]

    if not accepts:
        problems.append(_problem("accepts_empty", "accepts",
                                 "accepts is empty; both versions require at least one entry"))

    for index, entry in enumerate(accepts):
        net = _network.classify(entry.get("network") if isinstance(entry, dict) else None)
        if net["nano"]:
            checks = {str(version): check_entry(entry, version, catalogue)
                      for version in versions}
            report["nano_entries"].append({
                "index": index,
                "network_as_given": entry.get("network") if isinstance(entry, dict) else None,
                "canonical_network": net["canonical"],
                "pay_to": _payee_of(entry),
                "checks": checks,
                "payable": any(check["payable"] for check in checks.values()),
            })
            continue
        # Not ours, and still ours to report: the array is accepted or thrown
        # out as one, so a sibling the schema rejects takes our entry with it.
        for version in versions:
            sibling = shape_problems(entry, version)
            if catalogue:
                sibling, _ = split_challenge_only(sibling)
            if sibling:
                problems.append(_problem(
                    "sibling_entry_rejected", "accepts[%d]" % index,
                    "accepts[%d] is not a valid x402 v%d entry (%s), and a client "
                    "parses the whole 402 or none of it - so this entry alone makes "
                    "every other rail in the array unpayable, Nano included"
                    % (index, version,
                       ", ".join(sorted({issue["code"] for issue in sibling})))))

    if len(report["nano_entries"]) > 1:
        payees = sorted({entry["pay_to"] for entry in report["nano_entries"]
                         if entry["pay_to"] is not None})
        problems.append(_problem(
            "duplicate_nano_entries", "accepts",
            "%d entries in this array name Nano (%s). A client takes the first one "
            "it can pay, so %s"
            % (len(report["nano_entries"]),
               ", ".join(show(e["network_as_given"]) for e in report["nano_entries"]),
               "the later payout addresses are never used"
               if len(payees) > 1 else "the duplicate is dead weight")))

    report["payable"] = (not problems
                         and any(entry["payable"] for entry in report["nano_entries"]))
    return report


def inspect(challenge) -> dict:
    """A whole 402 challenge, or a whole resource catalogue. Never raises.

    Reports every Nano-family entry - including one spelled a way this
    package's own `find_nano` would miss, which is how a seller ends up with
    two Nano entries and two payout addresses in one array - and every sibling
    entry that would sink the document our entry is in.

    Two document shapes, told apart by which array is present:

    * a **402 challenge**, with a top-level `accepts[]`. The report keeps the
      shape it has always had.
    * a **resource catalogue**, with `resources[]`, each item carrying its own
      `url` and its own `accepts[]` - the shape
      `extract.paypercall.dev/.well-known/x402` serves for 24 endpoints. Until
      now this read as `accepts must be an array of entries; got null`
      (issue #1), so `check` said nothing at all about entries that were
      there: 0 entries, 0 naming Nano, not payable. A catalogue is reported
      one resource at a time, because its arrays are separate documents to
      every client that reads them and flattening them would invent an
      accepts[] no client ever sees.

    A catalogue report carries `multi_resource` and `resources`, and
    deliberately carries NO top-level `nano_entries`: an empty list there is
    the false answer this fix exists to remove, so a consumer written for the
    challenge shape raises instead of reading "none".

    One thing a catalogue is NOT: a document @x402/core models.
    `PaymentRequiredSchema.safeParse` on the live manifest fails for want of
    `resource` and `accepts` (measured 2026-10-08), so the equivalence claim
    in `shape_problems` is about the ENTRIES, which are PaymentRequirements
    either way, and not about the catalogue around them.
    """
    report = {"x402_version": declared_version(challenge),
              "document_kind": "payment_required", "problems": [],
              "entries": 0, "nano_entries": [], "payable": False}
    problems = report["problems"]

    if not isinstance(challenge, dict):
        problems.append(_problem("not_an_object", "",
                                 "a 402 challenge must be a JSON object, got %s"
                                 % kind(challenge)))
        return report

    if report["x402_version"] == 0:
        problems.append(_problem(
            "x402_version_missing_or_unknown", "x402Version",
            "x402Version must be the number 1 or 2; got %s. @x402/core "
            "discriminates its union on this field, so a document without it is "
            "rejected whole, before any entry is read"
            % show(challenge.get("x402Version"))))

    # Checked against the version the document declares - and against both when
    # it declares neither, since either kind of client may arrive.
    versions = (report["x402_version"],) if report["x402_version"] else (1, 2)

    accepts = challenge.get("accepts")
    if not isinstance(accepts, list):
        # A catalogue only when there is no top-level accepts[] at all: a 402
        # challenge that also lists resources is still a challenge, and the
        # array the payer was served is the one to judge.
        if isinstance(challenge.get("resources"), list):
            return _catalogue(challenge, report, versions)
        problems.append(_problem("accepts_not_an_array", "accepts",
                                 "accepts must be an array of entries; got %s"
                                 % kind(accepts)))
        return report

    report["entries"] = len(accepts)

    if report["x402_version"] == 2:
        resource = challenge.get("resource")
        if not isinstance(resource, dict) or not _is_non_empty_string(resource.get("url")):
            problems.append(_problem(
                "v2_resource_missing", "resource",
                "x402 v2 requires a top-level resource object with a non-empty url "
                "(@x402/core's ResourceInfoSchema). Without it the whole document "
                "fails, however good the entries are"))

    inner = _accepts_report(accepts, versions)
    problems.extend(inner["problems"])
    report["nano_entries"] = inner["nano_entries"]
    report["payable"] = not problems and any(entry["payable"]
                                             for entry in report["nano_entries"])
    return report


def _resource_url(item):
    """A `resources[]` item's url, under either spelling.

    The live manifest uses `url`; `resource` is what a v1 entry calls the same
    thing, and `cli._resource_sections` already reads both for the label.
    """
    for field in ("url", "resource"):
        if _is_non_empty_string(item.get(field)):
            return item[field]
    return None


def _catalogue(challenge, report, versions) -> dict:
    """The `resources[]` form. `report` already carries the version verdict."""
    problems = report["problems"]
    report["document_kind"] = "resource_catalogue"
    report["multi_resource"] = True
    # An empty list here is a false answer for a catalogue; see `inspect`.
    report.pop("nano_entries")
    report["resources"] = []

    items = challenge["resources"]
    if not items:
        problems.append(_problem(
            "resources_empty", "resources",
            "resources is empty; a catalogue with no resources advertises nothing"))

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(_problem(
                "resource_not_an_object", "resources[%d]" % index,
                "a resources[] item must be a JSON object, got %s" % kind(item)))
            continue
        url = _resource_url(item)
        section = {"index": index, "resource_label": url or "resources[%d]" % index}
        if url is None:
            problems.append(_problem(
                "resource_url_missing", "resources[%d]" % index,
                "resources[%d] has no non-empty url, so nothing in this catalogue "
                "says which endpoint its accepts[] prices. An agent reading the "
                "catalogue cannot reach it" % index))
        accepts = item.get("accepts")
        if not isinstance(accepts, list):
            section.update({"entries": 0, "nano_entries": [], "payable": False,
                            "problems": [_problem(
                                "accepts_not_an_array", "resources[%d].accepts" % index,
                                "accepts must be an array of entries; got %s"
                                % kind(accepts))]})
            report["resources"].append(section)
            continue
        section.update(_accepts_report(accepts, versions, resource_url=url,
                                       catalogue=True))
        report["resources"].append(section)

    report["entries"] = sum(section["entries"] for section in report["resources"])
    naming_nano = [section for section in report["resources"]
                   if section["nano_entries"]]
    report["resources_naming_nano"] = len(naming_nano)
    report["resources_not_payable"] = sum(1 for section in naming_nano
                                          if not section["payable"])
    # `payable` is "a payer would pay every Nano resource in this catalogue",
    # not "at least one": a catalogue of 24 endpoints with one broken entry is
    # a seller problem, and an `any` would hide it behind the 23 that work.
    report["payable"] = (not problems and bool(naming_nano)
                         and all(section["payable"] for section in naming_nano))
    return report
