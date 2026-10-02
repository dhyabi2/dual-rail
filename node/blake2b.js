'use strict';
// BLAKE2b with a variable digest length, because Nano's address checksum needs
// a 5-byte one and Node's crypto offers only blake2b512.
//
// This is NOT the first five bytes of a 64-byte digest: the digest length is
// mixed into the parameter block, so a 5-byte BLAKE2b is a different function
// from a truncated 64-byte one. Getting that wrong produces a checksum that
// looks plausible and rejects every real address.
//
// RFC 7693. Verified against Python's hashlib.blake2b in tests/conformance -
// the same 32-byte inputs, the same digests, both lengths.

const MASK64 = (1n << 64n) - 1n;

const IV = [
  0x6a09e667f3bcc908n, 0xbb67ae8584caa73bn, 0x3c6ef372fe94f82bn, 0xa54ff53a5f1d36f1n,
  0x510e527fade682d1n, 0x9b05688c2b3e6c1fn, 0x1f83d9abfb41bd6bn, 0x5be0cd19137e2179n,
];

const SIGMA = [
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
  [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
  [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
  [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
  [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
  [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
  [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
  [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
  [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
  [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
  [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
  [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
];

const add = (a, b) => (a + b) & MASK64;
const rotr = (x, n) => ((x >> n) | (x << (64n - n))) & MASK64;

function compress(h, block, counter, last) {
  const v = h.slice(0, 8).concat(IV.slice(0, 8));
  v[12] ^= counter & MASK64;
  v[13] ^= (counter >> 64n) & MASK64;
  if (last) v[14] ^= MASK64;

  const m = [];
  for (let i = 0; i < 16; i += 1) m.push(block.readBigUInt64LE(i * 8));

  const mix = (a, b, c, d, x, y) => {
    v[a] = add(add(v[a], v[b]), x);
    v[d] = rotr(v[d] ^ v[a], 32n);
    v[c] = add(v[c], v[d]);
    v[b] = rotr(v[b] ^ v[c], 24n);
    v[a] = add(add(v[a], v[b]), y);
    v[d] = rotr(v[d] ^ v[a], 16n);
    v[c] = add(v[c], v[d]);
    v[b] = rotr(v[b] ^ v[c], 63n);
  };

  for (let round = 0; round < 12; round += 1) {
    const s = SIGMA[round];
    mix(0, 4, 8, 12, m[s[0]], m[s[1]]);
    mix(1, 5, 9, 13, m[s[2]], m[s[3]]);
    mix(2, 6, 10, 14, m[s[4]], m[s[5]]);
    mix(3, 7, 11, 15, m[s[6]], m[s[7]]);
    mix(0, 5, 10, 15, m[s[8]], m[s[9]]);
    mix(1, 6, 11, 12, m[s[10]], m[s[11]]);
    mix(2, 7, 8, 13, m[s[12]], m[s[13]]);
    mix(3, 4, 9, 14, m[s[14]], m[s[15]]);
  }
  for (let i = 0; i < 8; i += 1) h[i] ^= v[i] ^ v[i + 8];
}

function blake2b(input, digestLength = 64) {
  if (digestLength < 1 || digestLength > 64) {
    throw new RangeError(`digest length must be 1..64, got ${digestLength}`);
  }
  const data = Buffer.from(input);
  const h = IV.slice();
  // The parameter block: digest length, key length 0, fanout 1, depth 1.
  h[0] ^= 0x01010000n ^ BigInt(digestLength);

  let offset = 0;
  while (data.length - offset > 128) {
    compress(h, data.subarray(offset, offset + 128), BigInt(offset + 128), false);
    offset += 128;
  }
  const finalBlock = Buffer.alloc(128);
  data.subarray(offset).copy(finalBlock);
  compress(h, finalBlock, BigInt(data.length), true);

  const out = Buffer.alloc(64);
  for (let i = 0; i < 8; i += 1) out.writeBigUInt64LE(h[i], i * 8);
  return out.subarray(0, digestLength);
}

module.exports = { blake2b };
