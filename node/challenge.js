'use strict';
// The accepts[] array, and the one entry this adapter is allowed to add.
// Character for character the same contract as challenge.py; the conformance
// suite asserts the two produce identical JSON.

const NETWORK = 'nano:mainnet';
const ASSET = 'XNO';
const SCHEME = 'exact';
const ADAPTER_ID = 'dual-rail/1';
const DECIMALS = 30;
const DESCRIPTION =
  'Feeless native-coin settlement. Optional - the entries above are unchanged.';

class NotAdditive extends Error {
  constructor(message) {
    super(message);
    this.name = 'NotAdditive';
  }
}

function nanoEntry(payTo, amountXno, resource) {
  return {
    scheme: SCHEME,
    network: NETWORK,
    asset: ASSET,
    maxAmountRequired: String(amountXno),
    payTo,
    resource: resource === undefined ? null : resource,
    description: DESCRIPTION,
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
                   NotAdditive, nanoEntry, findNano, resourceOf, assertAdditive, appendNano };
