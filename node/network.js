'use strict';
// Nano network identifiers: one canonical spelling, several in the wild.
// Character for character the same contract as network.py; the conformance
// suite asserts the two produce identical verdicts, messages included.
//
// `nano:mainnet` is the canonical one - x402-nano-exact 0.1.0 declares
// NETWORK_NANO_MAINNET = "nano:mainnet" and @x402nano/exact 0.3.0 carries the
// same literal. `nano-mainnet` is live too: legal in x402 v1, whose
// NetworkSchemaV1 is z.string().min(1), and refused by v2, whose
// NetworkSchemaV2 is z.string().min(3).refine(v => v.includes(':')).
//
// A bare `nano` or `xno` is deliberately NOT read as mainnet: it names the
// family without the network, and Nano's test networks share the `nano_`
// address prefix, so reading it as mainnet would be a guess about where money
// goes. It reports `network_unspecified` and the caller decides.

const CANONICAL = 'nano:mainnet';
const MAINNET_SPELLINGS = ['nano:mainnet', 'nano-mainnet'];
const FAMILY_ONLY = ['nano', 'xno'];
const CAIP2_PATTERN = /^[-a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}$/;
const V2_MIN_LENGTH = 3;

class UnknownNetwork extends Error {
  constructor(reason, message) {
    super(message);
    this.reason = reason;
    this.name = 'UnknownNetwork';
  }
}

// A value as it would appear in JSON, so both bindings render the same text.
function show(value) {
  try {
    const text = JSON.stringify(value);
    return text === undefined ? 'null' : text;
  } catch (err) {
    return 'null';
  }
}

// A value's JSON type name. `typeof` and Python's type(x).__name__ disagree on
// every one of these; JSON's vocabulary is the one both bindings share.
function kind(value) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'boolean') return 'boolean';
  if (typeof value === 'number' || typeof value === 'bigint') return 'number';
  if (typeof value === 'string') return 'string';
  if (Array.isArray(value)) return 'array';
  if (typeof value === 'object') return 'object';
  return 'unknown';
}

function fold(value) {
  return value.trim().toLowerCase();
}

function classify(value) {
  if (typeof value !== 'string') {
    return {
      nano: false, mainnet: false, canonical: null,
      reason: 'not_a_string',
      message: `a network identifier must be a string, got ${kind(value)}`,
    };
  }

  const folded = fold(value);
  if (!folded) {
    return { nano: false, mainnet: false, canonical: null,
             reason: 'empty', message: 'network identifier is empty' };
  }

  if (MAINNET_SPELLINGS.includes(folded)) {
    return {
      nano: true, mainnet: true, canonical: CANONICAL,
      as_given: value, folded: folded !== value,
      x402_v1_legal: true,
      x402_v2_legal: folded.length >= V2_MIN_LENGTH && folded.includes(':'),
      caip2: CAIP2_PATTERN.test(value),
    };
  }

  if (FAMILY_ONLY.includes(folded)) {
    return {
      nano: true, mainnet: false, canonical: null,
      reason: 'network_unspecified',
      message: `${show(value)} names Nano but not which Nano network, and the test ` +
        `networks share the nano_ address prefix - say ${show(CANONICAL)} if you ` +
        'mean mainnet',
    };
  }

  for (const prefix of ['nano:', 'nano-', 'xno:', 'xno-']) {
    if (folded.startsWith(prefix)) {
      return {
        nano: true, mainnet: false, canonical: null,
        reason: 'not_mainnet',
        message: `${show(value)} is a Nano network identifier but not mainnet; this ` +
          `package settles on ${show(CANONICAL)} only`,
      };
    }
  }

  return {
    nano: false, mainnet: false, canonical: null,
    reason: 'not_nano',
    message: `${show(value)} is not a Nano network identifier`,
  };
}

function canonical(value) {
  const verdict = classify(value);
  if (verdict.mainnet) return verdict.canonical;
  throw new UnknownNetwork(verdict.reason, verdict.message);
}

function isNanoMainnet(value) {
  return classify(value).mainnet;
}

module.exports = { CANONICAL, MAINNET_SPELLINGS, FAMILY_ONLY, CAIP2_PATTERN,
                   V2_MIN_LENGTH, UnknownNetwork, show, kind, classify,
                   canonical, isNanoMainnet };
