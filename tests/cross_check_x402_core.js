'use strict';
// Is `declaration.shapeProblems` actually equivalent to the x402 schema?
//
// The module claims its shape half rejects exactly what @x402/core rejects.
// That claim is worth nothing unless something checks it against the real
// package, and this package has NO DEPENDENCIES, so the check cannot live in
// the suite. It lives here, like tests/capture_live_verify.py, and is run by
// hand against an installed copy:
//
//     npm install --no-save @x402/core@2.28.0
//     node tests/cross_check_x402_core.js            # 4000 documents
//     node tests/cross_check_x402_core.js 20000 7    # count, seed
//
// Its captured output is in tests/cross-check-against-x402-core.txt.
//
// The documents are NEARLY VALID on purpose. A generator that randomises every
// field independently produces documents the library rejects every single
// time - the first version of this script scored 20000/20000 "agreement" while
// the library had accepted exactly none of them, which proves only that both
// sides can say no. These start from a document that parses and then break
// between zero and three things, so roughly 40% come back accepted and the
// agreement means something.

const fs = require('node:fs');
const path = require('node:path');

// `require('@x402/core/package.json')` is not in that package's `exports`, so
// the version has to be read off disk rather than imported.
function coreVersion() {
  try {
    const resolved = require.resolve('@x402/core/schemas');
    let dir = path.dirname(resolved);
    for (let i = 0; i < 6; i += 1) {
      const candidate = path.join(dir, 'package.json');
      if (fs.existsSync(candidate)) {
        const pkg = JSON.parse(fs.readFileSync(candidate, 'utf8'));
        if (pkg.name === '@x402/core') return pkg.version;
      }
      dir = path.dirname(dir);
    }
  } catch (err) { /* reported as unknown below */ }
  return 'unknown';
}

let PaymentRequiredSchema;
try {
  ({ PaymentRequiredSchema } = require('@x402/core/schemas'));
} catch (err) {
  console.error('@x402/core is not installed, so this cross-check cannot run.');
  console.error('It is deliberately NOT a dependency of this package:');
  console.error('  npm install --no-save @x402/core@2.28.0');
  console.error('Refusing to exit 0 - an unrun equivalence check is exactly the');
  console.error('state in which the claim in declaration.js quietly goes stale.');
  process.exit(2);
}

const declaration = require(path.join(__dirname, '..', 'node', 'declaration.js'));

const OURS = 'nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3';

// The document-level problem codes that are the schema's own, as opposed to the
// Nano-specific advice this module adds on top.
const SCHEMA_DOC_CODES = new Set(['not_an_object', 'x402_version_missing_or_unknown',
  'accepts_not_an_array', 'accepts_empty', 'v2_resource_missing',
  'sibling_entry_rejected']);

function weReject(doc) {
  const report = declaration.inspect(doc);
  if (report.problems.some((p) => SCHEMA_DOC_CODES.has(p.code))) return true;
  const version = report.x402_version;
  if (!version) return true;
  const accepts = doc && typeof doc === 'object' ? doc.accepts : null;
  if (!Array.isArray(accepts)) return true;
  return accepts.some((entry) => declaration.shapeProblems(entry, version).length > 0);
}

// ------------------------------------------------------------ the generator

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function validV2Entry(nano) {
  return nano
    ? { scheme: 'exact', network: 'nano:mainnet', amount: '100000000000000000000000000',
        asset: 'XNO', payTo: OURS, maxTimeoutSeconds: 60 }
    : { scheme: 'exact', network: 'eip155:8453', amount: '10000', asset: 'USDC',
        payTo: `0x${'1'.repeat(40)}`, maxTimeoutSeconds: 60 };
}

function validV1Entry(nano) {
  return { scheme: 'exact', network: nano ? 'nano:mainnet' : 'base',
           maxAmountRequired: nano ? '0.0001' : '0.01', resource: 'https://e.dev/r',
           description: 'a rail', payTo: nano ? OURS : `0x${'1'.repeat(40)}`,
           maxTimeoutSeconds: 60, asset: nano ? 'XNO' : 'USDC' };
}

const BAD = [null, '', ' ', 0, -1, true, false, [], {}, '60', 60, 60.5,
             'nano-mainnet', 'nano', 'base', 'a:b', 'ab', '0.0001', '9'.repeat(40),
             'xno', 'upto', `${OURS.slice(0, -1)}1`, 'notanaddress'];
const ENTRY_FIELDS = ['scheme', 'network', 'amount', 'maxAmountRequired', 'asset',
                      'payTo', 'maxTimeoutSeconds', 'resource', 'description',
                      'extra', 'mimeType', 'outputSchema'];

function pick(rng, list) { return list[Math.floor(rng() * list.length)]; }

function validDoc(rng) {
  const count = 1 + Math.floor(rng() * 3);
  if (rng() < 0.5) {
    const accepts = [];
    for (let i = 0; i < count; i += 1) accepts.push(validV2Entry(rng() < 0.7));
    return { x402Version: 2, resource: { url: 'https://e.dev/r' }, accepts };
  }
  const accepts = [];
  for (let i = 0; i < count; i += 1) accepts.push(validV1Entry(rng() < 0.7));
  return { x402Version: 1, accepts };
}

function mutate(doc, rng) {
  if (!doc || typeof doc !== 'object') return doc;
  const accepts = doc.accepts;
  const some = () => accepts[Math.floor(rng() * accepts.length)];
  switch (Math.floor(rng() * 10)) {
    case 0: doc.x402Version = pick(rng, [1, 2, 3, 0, true, '2', null, 1.0]); break;
    case 1: if (Array.isArray(accepts) && accepts.length) {
      const e = some(); if (e && typeof e === 'object') delete e[pick(rng, ENTRY_FIELDS)];
    } break;
    case 2: if (Array.isArray(accepts) && accepts.length) {
      const e = some(); if (e && typeof e === 'object') e[pick(rng, ENTRY_FIELDS)] = pick(rng, BAD);
    } break;
    case 3: doc.resource = pick(rng, [{ url: 'https://e.dev/r' }, { url: '' }, {}, null, 'u', 5]); break;
    case 4: delete doc.resource; break;
    case 5: if (Array.isArray(accepts)) {
      accepts.push(pick(rng, [null, 5, 'x', [], {}, validV2Entry(true), validV1Entry(true)]));
    } break;
    case 6: doc.accepts = pick(rng, [[], 'nope', 5, null, {}]); break;
    case 7: if (Array.isArray(accepts) && accepts.length) {
      const e = some();
      if (e && typeof e === 'object') {
        e.network = pick(rng, ['nano:mainnet', 'nano-mainnet', 'nano', 'NANO:MAINNET',
                               'nano:testnet', 'base', '', 'ab', 'a:b']);
      }
    } break;
    case 8: if (Array.isArray(accepts) && accepts.length) {
      const e = some();
      if (e && typeof e === 'object') e.extra = pick(rng, [{ decimals: 30 }, { decimals: 18 }, {}, null]);
    } break;
    default: delete doc.x402Version; break;
  }
  return doc;
}

// ------------------------------------------------------------------- run it

const count = Number(process.argv[2] || 4000);
const seed = Number(process.argv[3] || 1);
const rng = mulberry32(seed);

let agree = 0;
let accepted = 0;
const mismatches = [];
for (let i = 0; i < count; i += 1) {
  let doc = validDoc(rng);
  const rounds = Math.floor(rng() * 4);
  for (let r = 0; r < rounds; r += 1) doc = mutate(JSON.parse(JSON.stringify(doc)), rng);
  // through JSON, because that is how a 402 actually arrives
  doc = JSON.parse(JSON.stringify(doc));
  const parsed = PaymentRequiredSchema.safeParse(doc);
  if (parsed.success) accepted += 1;
  const libRejects = !parsed.success;
  if (libRejects === weReject(doc)) agree += 1;
  else if (mismatches.length < 10) {
    mismatches.push({
      libRejects,
      weReject: weReject(doc),
      issues: parsed.success ? [] : parsed.error.issues.slice(0, 3)
        .map((s) => `${s.path.join('.')}: ${s.code}`),
      doc: JSON.stringify(doc).slice(0, 300),
    });
  }
}

console.log(`cross-check against @x402/core ${coreVersion()}`);
console.log(`  seed ${seed}, ${count} nearly-valid documents`);
console.log(`  the library ACCEPTED ${accepted} of them (${(100 * accepted / count).toFixed(1)}%)`);
console.log(`  shape_problems agreed with it on ${agree}/${count}`);
mismatches.forEach((m) => {
  console.log(`  MISMATCH library_rejects=${m.libRejects} we_reject=${m.weReject}`);
  if (m.issues.length) console.log(`    library: ${m.issues.join(' | ')}`);
  console.log(`    ${m.doc}`);
});
if (accepted === 0) {
  console.error('\nREFUSING THIS RUN: the library accepted none of the documents, so');
  console.error('the agreement above only proves both sides reject everything. The');
  console.error('generator is broken - fix it rather than reading the number.');
  process.exit(1);
}
process.exit(agree === count ? 0 : 1);
