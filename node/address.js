'use strict';
// Nano address codec and checksum validation, in Node's own crypto.
//
// The same algorithm as nanoaddr.py, and the conformance suite asserts the two
// agree character for character on the burn address, the genesis address, and
// every single-character mutation of both.

const { blake2b } = require('./blake2b.js');

const ALPHABET = '13456789abcdefghijkmnopqrstuwxyz';
const PREFIXES = ['nano_', 'xrb_'];
const BODY_LEN = 52;
const CHECKSUM_LEN = 8;

class InvalidAddress extends Error {
  constructor(reason, message) {
    super(message);
    this.reason = reason;
    this.name = 'InvalidAddress';
  }
}

function b32Encode(value, length) {
  let out = '';
  for (let shift = BigInt((length - 1) * 5); shift >= 0n; shift -= 5n) {
    out += ALPHABET[Number((value >> shift) & 0x1fn)];
  }
  return out;
}

function b32Decode(text) {
  let value = 0n;
  for (const ch of text) {
    const index = ALPHABET.indexOf(ch);
    if (index < 0) {
      throw new InvalidAddress('bad_character',
        `character ${JSON.stringify(ch)} is not in the Nano base32 alphabet ` +
        '(0, 2, l and v are never valid)');
    }
    value = (value << 5n) | BigInt(index);
  }
  return value;
}

function checksumFor(publicKey) {
  if (publicKey.length !== 32) {
    throw new InvalidAddress('bad_public_key_length',
      `public key must be 32 bytes, got ${publicKey.length}`);
  }
  // Reversed: Nano reads the 5-byte digest little-endian.
  const digest = Buffer.from(blake2b(publicKey, 5)).reverse();
  return b32Encode(BigInt('0x' + digest.toString('hex')), CHECKSUM_LEN);
}

function encode(publicKey, prefix = 'nano_') {
  if (!PREFIXES.includes(prefix)) {
    throw new InvalidAddress('bad_prefix', `prefix must be one of ${PREFIXES}`);
  }
  const body = b32Encode(BigInt('0x' + Buffer.from(publicKey).toString('hex')), BODY_LEN);
  return prefix + body + checksumFor(publicKey);
}

function decode(address) {
  if (typeof address !== 'string') {
    throw new InvalidAddress('not_a_string', `address must be a string, got ${typeof address}`);
  }
  const candidate = address.trim();
  if (!candidate) throw new InvalidAddress('empty', 'address is empty');
  const prefix = PREFIXES.find((p) => candidate.startsWith(p));
  if (!prefix) {
    throw new InvalidAddress('bad_prefix',
      "address must start with 'nano_' or the legacy 'xrb_'");
  }
  const rest = candidate.slice(prefix.length);
  if (rest.length !== BODY_LEN + CHECKSUM_LEN) {
    throw new InvalidAddress('bad_length',
      `expected ${BODY_LEN + CHECKSUM_LEN} characters after the prefix, got ${rest.length}`);
  }
  const body = rest.slice(0, BODY_LEN);
  const supplied = rest.slice(BODY_LEN);
  if (body[0] !== '1' && body[0] !== '3') {
    throw new InvalidAddress('bad_padding',
      `first character after the prefix must be '1' or '3', got ${JSON.stringify(body[0])}`);
  }
  const hex = b32Decode(body).toString(16).padStart(66, '0').slice(2);
  const publicKey = Buffer.from(hex, 'hex');
  const computed = checksumFor(publicKey);
  if (computed !== supplied) {
    throw new InvalidAddress('bad_checksum',
      `checksum mismatch: address carries ${JSON.stringify(supplied)}, ` +
      `the key implies ${JSON.stringify(computed)}`);
  }
  return publicKey;
}

function validate(address) {
  try {
    const publicKey = decode(address);
    const candidate = String(address).trim();
    return {
      valid: true,
      address: candidate,
      normalised: encode(publicKey, 'nano_'),
      public_key: publicKey.toString('hex').toUpperCase(),
      prefix: candidate.startsWith('nano_') ? 'nano_' : 'xrb_',
    };
  } catch (err) {
    if (err instanceof InvalidAddress) {
      return { valid: false, reason: err.reason, message: err.message };
    }
    throw err;
  }
}

const isValid = (address) => validate(address).valid;

module.exports = { ALPHABET, InvalidAddress, encode, decode, validate, isValid, checksumFor };
