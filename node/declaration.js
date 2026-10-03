'use strict';
// Why no x402 client will pay your Nano entry, in one read-only call.
// Character for character the same contract as declaration.py; the conformance
// suite asserts the two produce identical verdicts, messages included.
//
// Two halves, deliberately. `shapeProblems` is ONLY what the x402 schema
// itself rejects, and is meant to be exactly equivalent to it - read off
// @x402/core 2.28.0's PaymentRequirementsV1Schema and
// PaymentRequirementsV2Schema. `nanoProblems` is what a Nano-aware reader adds
// on top: an address that is well-formed but fails its checksum (x402 asks
// only for a non-empty string, so the money sent there is unspendable), an
// amount that is not a whole number of raw, and a network identifier that
// means Nano but is not the canonical spelling.
//
// Because a client parses the whole 402 or none of it, a sibling entry sinks
// ours: accepts[0] carrying "network": "base" in a document that says
// x402Version 2 fails NetworkSchemaV2 and the array our entry is in is thrown
// out. `inspect` reports that rather than staying silent about an entry it was
// not asked about.
//
// Read-only throughout: no key, no node, no network, no money.

const money = require('./money.js');
const address = require('./address.js');
const net = require('./network.js');

const { show, kind } = net;

// A Nano state block's balance field is 128 bits, so no amount above this can
// exist in a block at all. An entry asking for more is a typo, not a price.
const MAX_RAW = 2n ** 128n - 1n;

const ASSET = 'XNO';
const SCHEME = 'exact';
const TIMEOUT_FIELD = 'maxTimeoutSeconds';

const V1_REQUIRED = ['scheme', 'network', 'maxAmountRequired', 'resource',
                     'description', 'payTo', TIMEOUT_FIELD, 'asset'];
const V2_REQUIRED = ['scheme', 'network', 'amount', 'asset', 'payTo', TIMEOUT_FIELD];

const AMOUNT_FIELD = { 1: 'maxAmountRequired', 2: 'amount' };

const V1_NON_EMPTY = ['scheme', 'network', 'maxAmountRequired', 'resource', 'payTo', 'asset'];
const V2_NON_EMPTY = ['scheme', 'amount', 'asset', 'payTo'];

const V2_NETWORK_MIN_LENGTH = 3;

function problem(code, field, message) {
  return { code, field, message };
}

// `'x' in obj` in Python is key presence, explicit null included.
function has(object, field) {
  return Object.prototype.hasOwnProperty.call(object, field);
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// @x402/core's NonEmptyString: z.string().min(1).
function isNonEmptyString(value) {
  return typeof value === 'string' && value.length >= 1;
}

// z.number().positive(). A boolean is not a number to zod, nor to typeof.
function isPositiveNumber(value) {
  return typeof value === 'number' && value > 0;
}

// An integer string of raw -> BigInt, or null. /^\d+$/, as the schema has it.
function rawOfIntegerString(text) {
  if (typeof text !== 'string' || !/^[0-9]+$/.test(text)) return null;
  return BigInt(text);
}

// ----------------------------------------------------------------- shape

// Exactly what @x402/core's PaymentRequirements schema would reject. Nothing
// Nano-specific, so a document this passes is one the library parses.
function shapeProblems(entry, version) {
  const problems = [];

  if (!isObject(entry)) {
    problems.push(problem('entry_not_an_object', '',
      `an accepts[] entry must be a JSON object, got ${kind(entry)}`));
    return problems;
  }

  const required = version === 1 ? V1_REQUIRED : V2_REQUIRED;
  const nonEmpty = version === 1 ? V1_NON_EMPTY : V2_NON_EMPTY;

  for (const field of required) {
    if (!has(entry, field)) {
      problems.push(problem('field_missing', field,
        `x402 v${version} requires ${field} on every accepts[] entry; it is absent`));
    }
  }

  for (const field of nonEmpty) {
    if (has(entry, field) && !isNonEmptyString(entry[field])) {
      problems.push(problem('field_not_a_non_empty_string', field,
        `x402 v${version} declares ${field} a non-empty string; got ${show(entry[field])}`));
    }
  }

  if (version === 2 && has(entry, 'network')) {
    const value = entry.network;
    if (typeof value !== 'string' || value.length < V2_NETWORK_MIN_LENGTH ||
        !value.includes(':')) {
      problems.push(problem('network_not_caip2', 'network',
        `x402 v2's NetworkSchemaV2 wants at least ${V2_NETWORK_MIN_LENGTH} characters ` +
        `including a colon (CAIP-2); got ${show(value)}. v1 took any non-empty ` +
        'string, so an entry like this is payable by a v1 client and invisible to a v2 one'));
    }
  }

  if (version === 1 && has(entry, 'description') && typeof entry.description !== 'string') {
    problems.push(problem('description_not_a_string', 'description',
      'x402 v1 declares description z.string() and requires it; null is ' +
      `refused, an empty string is allowed. Got ${show(entry.description)}`));
  }

  if (has(entry, TIMEOUT_FIELD) && !isPositiveNumber(entry[TIMEOUT_FIELD])) {
    problems.push(problem('timeout_not_a_positive_number', TIMEOUT_FIELD,
      `${TIMEOUT_FIELD} is ${show(entry[TIMEOUT_FIELD])}. Both x402 versions declare ` +
      'it z.number().positive(), so a JSON string - even "60" - is refused'));
  }

  // `extra` is z.record(z.unknown()).optional().nullable() in both versions:
  // an object or null, and nothing else. An entry whose extra is a string
  // fails here and takes the whole accepts[] array down with it.
  if (has(entry, 'extra') && entry.extra !== null && !isObject(entry.extra)) {
    problems.push(problem('extra_not_an_object', 'extra',
      `extra is declared an object or null in both x402 versions; got ${kind(entry.extra)}`));
  }

  // v1 declares two more optional fields that v2's entry schema does not have.
  if (version === 1) {
    if (has(entry, 'mimeType') && typeof entry.mimeType !== 'string') {
      problems.push(problem('mime_type_not_a_string', 'mimeType',
        'x402 v1 declares mimeType z.string().optional(), which admits an ' +
        `absent key but not a null one; got ${kind(entry.mimeType)}`));
    }
    if (has(entry, 'outputSchema') && entry.outputSchema !== null &&
        !isObject(entry.outputSchema)) {
      problems.push(problem('output_schema_not_an_object', 'outputSchema',
        `x402 v1 declares outputSchema an object or null; got ${kind(entry.outputSchema)}`));
    }
  }

  return problems;
}

// ------------------------------------------------------------------ nano

function amountReading(value, field) {
  const problems = [];
  if (!isNonEmptyString(value)) return { raw: null, decimal_xno: null, problems };

  const raw = rawOfIntegerString(value);
  if (raw !== null) {
    if (raw === 0n) {
      problems.push(problem('amount_zero', field,
        `${field} is zero; a price of nothing is not a price`));
    } else if (raw > MAX_RAW) {
      problems.push(problem('amount_above_max_raw', field,
        `${field} is ${value} raw, above the 128-bit maximum a Nano block balance can ` +
        `hold (${MAX_RAW}) - no such payment can be built`));
    }
    return { raw, decimal_xno: raw <= MAX_RAW ? money.rawToXno(raw) : null, problems };
  }

  // Not an integer. A decimal is readable but is not what a v2 payer accepts.
  let asRaw;
  try {
    asRaw = money.xnoToRaw(value);
  } catch (err) {
    problems.push(problem('amount_unparsable', field,
      `${field} is neither an integer of raw nor a decimal number of XNO (${err.reason})`));
    return { raw: null, decimal_xno: null, problems };
  }

  problems.push(problem('amount_is_decimal_xno', field,
    `${field} is ${show(value)}, a decimal number of XNO. An x402 v2 Nano amount is ` +
    'an integer of raw (@x402nano/typescript-common declares it /^\\d+$/), so this is ' +
    `${asRaw} raw - a v2 client refuses the string as given`));
  return { raw: asRaw, decimal_xno: value, problems };
}

function nanoProblems(entry, version) {
  const out = { problems: [], amount: null, pay_to: null };
  if (!isObject(entry)) return out;
  const problems = out.problems;
  const amountField = AMOUNT_FIELD[version];

  const network = net.classify(entry.network);
  if (!network.nano) {
    problems.push(problem('network_not_nano', 'network', network.message));
  } else if (!network.mainnet) {
    problems.push(problem(`network_${network.reason}`, 'network', network.message));
  } else if (network.canonical !== entry.network) {
    problems.push(problem('network_not_canonical', 'network',
      `${show(entry.network)} is read as Nano mainnet here, but every shipped Nano ` +
      `x402 package matches on the literal ${show(net.CANONICAL)} - x402-nano-exact ` +
      '0.1.0 and @x402nano/exact 0.3.0 both carry that string, so a client comparing ' +
      'against it skips this entry'));
  }

  if (isNonEmptyString(entry.scheme) && entry.scheme !== SCHEME) {
    problems.push(problem('scheme_unknown', 'scheme',
      `the only Nano scheme any shipped x402 package registers is ${show(SCHEME)}; ` +
      `got ${show(entry.scheme)}`));
  }

  if (isNonEmptyString(entry.asset) && entry.asset.toUpperCase() !== ASSET) {
    problems.push(problem('asset_not_xno', 'asset',
      `a nano:mainnet entry settles in ${ASSET}; got ${show(entry.asset)}`));
  }

  if (has(entry, amountField)) {
    const reading = amountReading(entry[amountField], amountField);
    if (reading.raw !== null || reading.decimal_xno !== null) {
      out.amount = { raw: reading.raw === null ? null : reading.raw.toString(),
                     decimal_xno: reading.decimal_xno };
    }
    problems.push(...reading.problems);
  }

  const other = AMOUNT_FIELD[version === 1 ? 2 : 1];
  if (has(entry, other) && !has(entry, amountField)) {
    const otherVersion = version === 1 ? 2 : 1;
    problems.push(problem('amount_field_is_other_version_name', amountField,
      `this entry carries ${other}, x402 v${otherVersion}'s name for the price. A ` +
      `v${version} client reads ${amountField}, and v${version}'s schema has no ` +
      `${other}, so the price is invisible to it`));
  }

  if (isNonEmptyString(entry.payTo)) {
    const verified = address.validate(entry.payTo);
    if (verified.valid) {
      out.pay_to = { valid: true, normalised: verified.normalised };
      if (verified.normalised !== entry.payTo.trim()) out.pay_to.as_given = entry.payTo;
    } else {
      out.pay_to = { valid: false, reason: verified.reason };
      // The reason code, not address.js's own prose: a message is for humans
      // and the two bindings word theirs differently, so quoting one here
      // would make this verdict disagree across bindings over a difference
      // that is not about the declaration at all.
      problems.push(problem('pay_to_invalid', 'payTo',
        `payTo is not a payable Nano account (${verified.reason}). x402's own schema ` +
        'only checks that it is a non-empty string, so an address that fails its ' +
        'checksum is accepted by the client and the money it is sent is unspendable'));
    }
  }

  const extra = entry.extra;
  if (isObject(extra) && has(extra, 'decimals') && extra.decimals !== money.DECIMALS) {
    problems.push(problem('decimals_wrong', 'extra.decimals',
      `XNO has ${money.DECIMALS} decimal places; this entry declares ` +
      `${show(extra.decimals)}, which would rescale every price a client computes from it`));
  }

  return out;
}

function checkEntry(entry, version) {
  const shape = shapeProblems(entry, version);
  const nano = isObject(entry) ? nanoProblems(entry, version)
                               : { problems: [], amount: null, pay_to: null };
  const verdict = {
    version,
    problems: shape.concat(nano.problems),
    network: net.classify(isObject(entry) ? entry.network : null),
  };
  if (nano.amount !== null) verdict.amount = nano.amount;
  if (nano.pay_to !== null) verdict.pay_to = nano.pay_to;
  verdict.payable = verdict.problems.length === 0;
  return verdict;
}

// -------------------------------------------------------------- document

// The normalised account an entry would be paid to, or null if unpayable.
// Normalised rather than as-given, so `xrb_` and `nano_` spellings of one
// account are not mistaken for two different payees.
function payeeOf(entry) {
  if (!isObject(entry) || typeof entry.payTo !== 'string') return null;
  const verified = address.validate(entry.payTo);
  return verified.valid ? verified.normalised : null;
}

// The x402 version a document declares, or 0 if it declares none. JSON has one
// number type, so 1.0 and 1 are the same value and zod's z.literal(1) matches
// both; a boolean is not a number to either binding.
function declaredVersion(challenge) {
  if (!isObject(challenge)) return 0;
  const value = challenge.x402Version;
  if (typeof value !== 'number' || !Number.isInteger(value)) return 0;
  return value === 1 || value === 2 ? value : 0;
}

function inspect(challenge) {
  const report = { x402_version: declaredVersion(challenge), problems: [],
                   entries: 0, nano_entries: [], payable: false };
  const problems = report.problems;

  if (!isObject(challenge)) {
    problems.push(problem('not_an_object', '',
      `a 402 challenge must be a JSON object, got ${kind(challenge)}`));
    return report;
  }

  if (report.x402_version === 0) {
    problems.push(problem('x402_version_missing_or_unknown', 'x402Version',
      `x402Version must be the number 1 or 2; got ${show(challenge.x402Version)}. ` +
      '@x402/core discriminates its union on this field, so a document without it is ' +
      'rejected whole, before any entry is read'));
  }

  const accepts = challenge.accepts;
  if (!Array.isArray(accepts)) {
    problems.push(problem('accepts_not_an_array', 'accepts',
      `accepts must be an array of entries; got ${kind(accepts)}`));
    return report;
  }
  if (accepts.length === 0) {
    problems.push(problem('accepts_empty', 'accepts',
      'accepts is empty; both versions require at least one entry'));
  }
  report.entries = accepts.length;

  if (report.x402_version === 2) {
    const resource = challenge.resource;
    if (!isObject(resource) || !isNonEmptyString(resource.url)) {
      problems.push(problem('v2_resource_missing', 'resource',
        'x402 v2 requires a top-level resource object with a non-empty url ' +
        "(@x402/core's ResourceInfoSchema). Without it the whole document " +
        'fails, however good the entries are'));
    }
  }

  // Checked against the version the document declares - and against both when
  // it declares neither, since either kind of client may arrive.
  const versions = report.x402_version ? [report.x402_version] : [1, 2];

  accepts.forEach((entry, index) => {
    const network = net.classify(isObject(entry) ? entry.network : null);
    if (network.nano) {
      const checks = {};
      for (const version of versions) checks[String(version)] = checkEntry(entry, version);
      report.nano_entries.push({
        index,
        network_as_given: isObject(entry) && entry.network !== undefined ? entry.network : null,
        canonical_network: network.canonical,
        pay_to: payeeOf(entry),
        checks,
        payable: Object.values(checks).some((check) => check.payable),
      });
      return;
    }
    // Not ours, and still ours to report: the array is accepted or thrown out
    // as one, so a sibling the schema rejects takes our entry with it.
    for (const version of versions) {
      const sibling = shapeProblems(entry, version);
      if (sibling.length) {
        const codes = [...new Set(sibling.map((issue) => issue.code))].sort();
        problems.push(problem('sibling_entry_rejected', `accepts[${index}]`,
          `accepts[${index}] is not a valid x402 v${version} entry ` +
          `(${codes.join(', ')}), and a client parses the whole 402 or none of it - ` +
          'so this entry alone makes every other rail in the array unpayable, ' +
          'Nano included'));
      }
    }
  });

  if (report.nano_entries.length > 1) {
    const payees = [...new Set(report.nano_entries.map((e) => e.pay_to)
                                                  .filter((p) => p !== null))].sort();
    problems.push(problem('duplicate_nano_entries', 'accepts',
      `${report.nano_entries.length} entries in this array name Nano ` +
      `(${report.nano_entries.map((e) => show(e.network_as_given)).join(', ')}). ` +
      'A client takes the first one it can pay, so ' +
      (payees.length > 1 ? 'the later payout addresses are never used'
                         : 'the duplicate is dead weight')));
  }

  report.payable = problems.length === 0 &&
                   report.nano_entries.some((entry) => entry.payable);
  return report;
}

module.exports = { MAX_RAW, ASSET, SCHEME, TIMEOUT_FIELD, V1_REQUIRED, V2_REQUIRED,
                   V1_NON_EMPTY, V2_NON_EMPTY, AMOUNT_FIELD, V2_NETWORK_MIN_LENGTH,
                   shapeProblems, amountReading, nanoProblems, checkEntry,
                   declaredVersion, inspect };
