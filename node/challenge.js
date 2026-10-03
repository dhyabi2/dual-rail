'use strict';
// The accepts[] array, and the one entry this adapter is allowed to add.
// Character for character the same contract as challenge.py; the conformance
// suite asserts the two produce identical JSON.

const money = require('./money');

const NETWORK = 'nano:mainnet';
const ASSET = 'XNO';
const SCHEME = 'exact';
const ADAPTER_ID = 'dual-rail/1';
const DECIMALS = 30;
// x402 requires a payment window on every accepts[] entry, in BOTH protocol
// versions (see `nanoEntry`). 60s is the ecosystem's value; it is not a price,
// so it is a constant rather than one more thing an operator can get wrong.
const MAX_TIMEOUT_SECONDS = 60;
const DESCRIPTION =
  'Feeless native-coin settlement. Optional - the entries above are unchanged.';

class NotAdditive extends Error {
  constructor(message) {
    super(message);
    this.name = 'NotAdditive';
  }
}

// The amount is in the asset's ATOMIC unit - raw, 10n**30n to the XNO, which is
// what `extra.decimals` says. It used to carry the configured decimal XNO string
// verbatim ('0.0001'), and every x402 Nano client reads the field as an integer
// count of raw, so a patched 402 was unpayable. The decimal figure stays beside
// it in `maxAmountRequiredFormatted`, which is where feeless402's `compare_rails`
// looks for something human-readable.
//
// It goes out under BOTH names because x402 renamed the field between protocol
// versions and we append to somebody else's challenge: `maxAmountRequired` is
// required by v1, `amount` is required by v2, and each version's schema strips
// the other's field rather than rejecting it. `maxTimeoutSeconds` is required by
// both. See challenge.py for the measurement against `@x402/core` 2.28.0 and
// `@x402nano/exact` 0.3.0; the Python and Node entries must stay identical (the
// conformance suite asserts it).
function nanoEntry(payTo, amountXno, resource) {
  const raw = String(money.xnoToRaw(amountXno));
  return {
    scheme: SCHEME,
    network: NETWORK,
    asset: ASSET,
    amount: raw,
    maxAmountRequired: raw,
    maxAmountRequiredFormatted: `${amountXno} XNO`,
    payTo,
    resource: resource === undefined ? null : resource,
    description: DESCRIPTION,
    maxTimeoutSeconds: MAX_TIMEOUT_SECONDS,
    extra: { decimals: DECIMALS, adapter: ADAPTER_ID },
  };
}

function findNano(accepts) {
  if (!Array.isArray(accepts)) return -1;
  return accepts.findIndex((e) => e && typeof e === 'object' && e.network === NETWORK);
}

function resourceOf(accepts) {
  for (const entry of accepts || []) {
    if (entry && typeof entry === 'object' && entry.resource) return entry.resource;
  }
  return null;
}

function assertAdditive(before, after) {
  if (after.length !== before.length + 1) {
    throw new NotAdditive(
      `expected exactly one added entry, went from ${before.length} to ${after.length}`);
  }
  for (let i = 0; i < before.length; i += 1) {
    if (JSON.stringify(after[i]) !== JSON.stringify(before[i])) {
      throw new NotAdditive(
        `entry ${i} changed. The adapter may only append; this patch would alter ` +
        'a rail that already works.');
    }
  }
  if (findNano([after[after.length - 1]]) !== 0) {
    throw new NotAdditive('the appended entry is not the Nano entry');
  }
}

function appendNano(challenge, payTo, amountXno) {
  const patched = JSON.parse(JSON.stringify(challenge));
  if (!Array.isArray(patched.accepts)) {
    throw new NotAdditive('this 402 challenge has no accepts[] array to append to');
  }
  if (findNano(patched.accepts) !== -1) return patched;
  const before = JSON.parse(JSON.stringify(patched.accepts));
  patched.accepts.push(nanoEntry(payTo, amountXno, resourceOf(patched.accepts)));
  assertAdditive(before, patched.accepts);
  return patched;
}

module.exports = { NETWORK, ASSET, SCHEME, ADAPTER_ID, DECIMALS, DESCRIPTION,
                   MAX_TIMEOUT_SECONDS, NotAdditive, nanoEntry, findNano, resourceOf,
                   assertAdditive, appendNano };
