'use strict';
// The network identifier and the declaration validator, against the Node
// binding.
//
// tests/test_conformance.py already proves the two bindings return identical
// verdicts over every case in the matrix, so this file does not re-check the
// rules one by one - it covers what is specific to this binding: BigInt
// amounts, `hasOwnProperty` where Python has `in`, `typeof` where Python has
// `isinstance`, and the findNano regression that started all of it.

const assert = require('node:assert/strict');
const { test } = require('node:test');

const challenge = require('../challenge.js');
const declaration = require('../declaration.js');
const money = require('../money.js');
const net = require('../network.js');
const testhost = require('../testhost.js');

const OURS = 'nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3';
const THEIRS = 'nano_1111111111111111111111111111111111111111111111111111hifc8npp';
const RAW = '100000000000000000000000000';            // 0.0001 XNO

function entry(over = {}) {
  return { scheme: 'exact', network: 'nano:mainnet', asset: 'XNO',
           amount: RAW, payTo: OURS, maxTimeoutSeconds: 60, ...over };
}

function doc(entries, over = {}) {
  return { x402Version: 2, resource: { url: 'https://example.dev/report' },
           accepts: entries, ...over };
}

const codes = (problems) => problems.map((p) => p.code);

// ---------------------------------------------------------- 1. identifiers

test('1. the canonical spelling is Nano mainnet and is CAIP-2', () => {
  const verdict = net.classify('nano:mainnet');
  assert.equal(verdict.mainnet, true);
  assert.equal(verdict.canonical, 'nano:mainnet');
  assert.equal(verdict.x402_v2_legal, true);
  assert.equal(verdict.caip2, true);
});

test('2. nano-mainnet is mainnet, legal in v1, refused by v2', () => {
  const verdict = net.classify('nano-mainnet');
  assert.equal(verdict.mainnet, true);
  assert.equal(verdict.x402_v1_legal, true);
  assert.equal(verdict.x402_v2_legal, false);
});

test('3. a bare family name is refused rather than assumed to be mainnet', () => {
  for (const value of ['nano', 'xno', 'XNO']) {
    const verdict = net.classify(value);
    assert.equal(verdict.nano, true, value);
    assert.equal(verdict.mainnet, false, value);
    assert.equal(verdict.reason, 'network_unspecified', value);
  }
});

test('4. canonical() fails closed and classify() never throws', () => {
  for (const value of [null, undefined, 42, true, [], {}, '', '   ', 'base']) {
    assert.equal(net.classify(value).mainnet, false);
    assert.throws(() => net.canonical(value), net.UnknownNetwork);
  }
});

// ------------------------------------------------- 5-7. findNano regression

test('5. a variant spelling counts as an existing Nano entry', () => {
  const theirs = testhost.challenge([...testhost.USDC_ENTRIES, {
    scheme: 'exact', network: 'nano-mainnet', asset: 'XNO',
    maxAmountRequired: '0.0001', payTo: THEIRS,
    resource: 'https://example.dev/report', description: 'XNO' }]);
  assert.equal(challenge.findNano(theirs.accepts), 2);
});

test('6. appending is a no-op when they already accept XNO another way', () => {
  const theirs = testhost.challenge([...testhost.USDC_ENTRIES, {
    scheme: 'exact', network: 'nano-mainnet', asset: 'XNO',
    maxAmountRequired: '0.0001', payTo: THEIRS,
    resource: 'https://example.dev/report', description: 'XNO' }]);
  const after = challenge.appendNano(theirs, OURS, '0.0001');
  assert.deepEqual(after, theirs);
  assert.equal(after.accepts.length, 3);
  assert.deepEqual(after.accepts.filter((e) => String(e.network).includes('nano'))
                                .map((e) => e.payTo), [THEIRS]);
});

test('7. a canonical entry is still appended normally', () => {
  const plain = testhost.challenge();
  const patched = challenge.appendNano(plain, OURS, '0.0001');
  assert.equal(patched.accepts.length, plain.accepts.length + 1);
  assert.equal(patched.accepts[patched.accepts.length - 1].network, 'nano:mainnet');
});

// --------------------------------------------------------- 8-12. the amount

test('8. an integer amount of raw is a BigInt, never a Number', () => {
  const reading = declaration.amountReading(RAW, 'amount');
  assert.equal(typeof reading.raw, 'bigint');
  assert.equal(reading.raw, 10n ** 26n);
  assert.equal(reading.decimal_xno, '0.0001');
  assert.deepEqual(reading.problems, []);
});

test('9. a decimal amount is reported with the raw it means', () => {
  const reading = declaration.amountReading('0.0001', 'amount');
  assert.deepEqual(codes(reading.problems), ['amount_is_decimal_xno']);
  assert.match(reading.problems[0].message, new RegExp(RAW));
});

test('10. an amount above the 128-bit block field is refused', () => {
  const tooBig = (declaration.MAX_RAW + 1n).toString();
  assert.deepEqual(codes(declaration.amountReading(tooBig, 'amount').problems),
                   ['amount_above_max_raw']);
  // and the boundary itself is not
  assert.deepEqual(declaration.amountReading(declaration.MAX_RAW.toString(),
                                             'amount').problems, []);
});

test('11. a Unicode digit is not an amount in either binding', () => {
  for (const value of ['١', '²', '.']) {
    assert.throws(() => money.xnoToRaw(value), { name: 'AmountError' }, value);
  }
});

test('12. an amount that is not a string is the schema\'s business, not nano\'s', () => {
  // shape reports it; nano stays quiet rather than reporting the same fact twice
  assert.ok(codes(declaration.shapeProblems(entry({ amount: 123 }), 2))
              .includes('field_not_a_non_empty_string'));
  assert.deepEqual(declaration.nanoProblems(entry({ amount: 123 }), 2).problems, []);
});

// --------------------------------------------------- 13-17. shape vs nano

test('13. a well-formed v2 entry has no problem of either kind', () => {
  assert.deepEqual(declaration.shapeProblems(entry(), 2), []);
  assert.deepEqual(declaration.nanoProblems(entry(), 2).problems, []);
  assert.equal(declaration.checkEntry(entry(), 2).payable, true);
});

test('14. a present-but-null key is seen, as Python\'s `in` sees it', () => {
  assert.ok(codes(declaration.shapeProblems(entry({ payTo: null }), 2))
              .includes('field_not_a_non_empty_string'));
  // an absent key is a different problem with a different code
  const without = entry();
  delete without.payTo;
  assert.ok(codes(declaration.shapeProblems(without, 2)).includes('field_missing'));
});

test('15. extra must be an object or null', () => {
  assert.ok(codes(declaration.shapeProblems(entry({ extra: 'x' }), 2))
              .includes('extra_not_an_object'));
  assert.deepEqual(declaration.shapeProblems(entry({ extra: null }), 2), []);
});

test('16. an address that fails its checksum is caught where x402 cannot', () => {
  const bad = entry({ payTo: `${OURS.slice(0, -1)}1` });
  assert.deepEqual(declaration.shapeProblems(bad, 2), []);
  assert.deepEqual(codes(declaration.nanoProblems(bad, 2).problems), ['pay_to_invalid']);
});

test('17. a boolean timeout is not a positive number', () => {
  assert.ok(codes(declaration.shapeProblems(entry({ maxTimeoutSeconds: true }), 2))
              .includes('timeout_not_a_positive_number'));
});

// ---------------------------------------------------- 18-22. the document

test('18. a complete v2 document is payable', () => {
  const report = declaration.inspect(doc([entry()]));
  assert.equal(report.payable, true);
  assert.equal(report.nano_entries.length, 1);
});

test('19. a sibling entry sinks the whole document', () => {
  const report = declaration.inspect(doc([
    { scheme: 'exact', network: 'base', asset: 'USDC', amount: '10000',
      payTo: `0x${'1'.repeat(40)}`, maxTimeoutSeconds: 60 },
    entry(),
  ]));
  assert.equal(report.payable, false);
  const sibling = report.problems.filter((p) => p.code === 'sibling_entry_rejected');
  assert.equal(sibling.length, 1);
  assert.equal(sibling[0].field, 'accepts[0]');
  assert.equal(report.nano_entries[0].payable, true);
});

test('20. two Nano entries with two payees are reported as one problem', () => {
  const report = declaration.inspect(doc([
    entry({ network: 'nano-mainnet', payTo: THEIRS }), entry(),
  ]));
  assert.ok(codes(report.problems).includes('duplicate_nano_entries'));
  assert.equal(report.payable, false);
});

test('21. a float version is the same number as an integer one in JSON', () => {
  assert.equal(declaration.declaredVersion({ x402Version: 2.0 }), 2);
  assert.equal(declaration.declaredVersion({ x402Version: 2.5 }), 0);
  assert.equal(declaration.declaredVersion({ x402Version: true }), 0);
});

test('22. nothing throws on any shape, and every report is JSON', () => {
  for (const value of [null, undefined, [], 'nope', 7, {}, { accepts: 'x' },
                       { x402Version: 2, accepts: [null, 5, 'x'] }]) {
    const report = declaration.inspect(value);
    assert.equal(report.payable, false);
    assert.ok(report.problems.length > 0);
    JSON.parse(JSON.stringify(report));
  }
});

// ---------------------------------------------------------------------------
// The resource catalogue (dual-rail#1's last item). The conformance matrix
// already holds the two bindings to identical verdicts over 23 catalogue
// cases, so these cover what is specific to THIS binding: `delete` where
// Python has `pop`, `Array.isArray` where Python has `isinstance`, and the
// gap between an absent key and one set to `undefined`, which Python's dicts
// do not have.

const CATALOGUE_ENTRY = {
  scheme: 'exact', network: 'nano:mainnet', asset: 'XNO',
  amount: '100000000000000000000000000', payTo: OURS,
};

function catalogue(...items) {
  return { x402Version: 2, resources: items };
}

function resourceItem(entries, over = {}) {
  return Object.assign({ url: 'https://a.dev/x', accepts: entries }, over);
}

test('23. a catalogue is read instead of being called an absent accepts[]', () => {
  const report = declaration.inspect(catalogue(resourceItem([CATALOGUE_ENTRY])));
  assert.equal(report.document_kind, 'resource_catalogue');
  assert.ok(!codes(report.problems).includes('accepts_not_an_array'));
  assert.equal(report.entries, 1);
  assert.equal(report.resources_naming_nano, 1);
  assert.equal(report.payable, true);
});

test('24. `delete` really removes nano_entries, it does not set it undefined', () => {
  // `'nano_entries' in report` is false for a deleted key and TRUE for one set
  // to undefined - and an undefined key JSON.stringifys away, so a consumer
  // reading the parsed report would see no difference while one reading the
  // object in process would read `undefined.length` instead of throwing on a
  // missing key. Python's `pop` cannot express the wrong one; JS can.
  const report = declaration.inspect(catalogue(resourceItem([CATALOGUE_ENTRY])));
  assert.equal('nano_entries' in report, false);
  assert.equal(Object.prototype.hasOwnProperty.call(report, 'nano_entries'), false);
  assert.ok(!Object.keys(report).includes('nano_entries'));
});

test('25. a 402 challenge keeps its own shape, and keeps nano_entries', () => {
  const report = declaration.inspect(doc([entry()]));
  assert.equal(report.document_kind, 'payment_required');
  assert.equal('nano_entries' in report, true);
  assert.equal('resources' in report, false);
  assert.equal('multi_resource' in report, false);
});

test('26. only an array of resources is a catalogue', () => {
  for (const value of ['nope', 7, {}, null, undefined]) {
    const report = declaration.inspect({ x402Version: 2, resources: value });
    assert.equal(report.document_kind, 'payment_required', JSON.stringify(value));
    assert.ok(codes(report.problems).includes('accepts_not_an_array'));
  }
});

test('27. the relaxation moves field_missing and nothing else', () => {
  const split = declaration.splitChallengeOnly([
    { code: 'field_missing', field: 'maxTimeoutSeconds', message: '' },
    { code: 'field_missing', field: 'payTo', message: '' },
    { code: 'timeout_not_a_positive_number', field: 'maxTimeoutSeconds', message: '' },
  ]);
  assert.deepEqual(split.advertised.map((i) => i.field), ['maxTimeoutSeconds']);
  assert.deepEqual(split.fatal.map((i) => i.code),
                   ['field_missing', 'timeout_not_a_positive_number']);
  // CHALLENGE_ONLY_FIELDS.includes is the membership test; a near-name is not
  // a member, where a looser check (a substring, a startsWith) would pass it.
  const near = declaration.splitChallengeOnly([
    { code: 'field_missing', field: 'maxTimeoutSecond', message: '' },
    { code: 'field_missing', field: 'descriptions', message: '' },
  ]);
  assert.equal(near.advertised.length, 0);
  assert.equal(near.fatal.length, 2);
});

test('28. a present-but-malformed challenge-only field is still fatal', () => {
  for (const bad of ['60', 0, true, -1]) {
    const report = declaration.inspect(catalogue(resourceItem(
      [Object.assign({}, CATALOGUE_ENTRY, { maxTimeoutSeconds: bad })])));
    assert.equal(report.payable, false, JSON.stringify(bad));
  }
});

test('29. one broken resource is not hidden by the ones that work', () => {
  const report = declaration.inspect(catalogue(
    resourceItem([CATALOGUE_ENTRY]),
    resourceItem([Object.assign({}, CATALOGUE_ENTRY, { payTo: '' })],
                 { url: 'https://b.dev/x' }),
    resourceItem([CATALOGUE_ENTRY], { url: 'https://c.dev/x' })));
  assert.equal(report.resources_naming_nano, 3);
  assert.equal(report.resources_not_payable, 1);
  assert.equal(report.payable, false);
});

test('30. nothing throws on any catalogue shape, and every report is JSON', () => {
  for (const value of [catalogue(), catalogue(null, 5, 'x'),
                       catalogue({ url: 'https://a.dev/x' }),
                       catalogue({ url: 'https://a.dev/x', accepts: 'nope' }),
                       catalogue(resourceItem([CATALOGUE_ENTRY], { url: undefined })),
                       { resources: [resourceItem([CATALOGUE_ENTRY])] }]) {
    const report = declaration.inspect(value);
    assert.equal(report.payable, false, JSON.stringify(value));
    JSON.parse(JSON.stringify(report));
  }
});
