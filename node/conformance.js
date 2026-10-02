'use strict';
// Runs the shared conformance matrix through the Node binding and prints the
// result as JSON on stdout. tests/test_conformance.py runs the same matrix
// through the Python binding and asserts the two are deeply equal.
//
// "Identical behaviour" in the definition of done is not a promise anyone can
// keep by reading two files. This is the thing that checks it.

const address = require('./address.js');
const challenge = require('./challenge.js');
const money = require('./money.js');
const { DualRail, parsePayment, blockHashOf, isOurs } = require('./dual-rail.js');
const testhost = require('./testhost.js');

const PAY_TO = 'nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3';
const AMOUNT = '0.0001';

function addresses(cases) {
  return cases.map((value) => {
    const verdict = address.validate(value);
    return verdict.valid
      ? { valid: true, normalised: verdict.normalised, public_key: verdict.public_key }
      : { valid: false, reason: verdict.reason };
  });
}

function amounts(cases) {
  return cases.map((value) => {
    try {
      const raw = money.xnoToRaw(value);
      return { ok: true, raw: raw.toString(), back: money.rawToXno(raw) };
    } catch (err) {
      return { ok: false, reason: err.reason };
    }
  });
}

function prices(cases) {
  return cases.map((value) => {
    try {
      return { ok: true, raw: money.checkPrice(value).toString() };
    } catch (err) {
      return { ok: false, reason: err.reason };
    }
  });
}

function challenges(shapes) {
  const rail = new DualRail({ payTo: PAY_TO, amountXno: AMOUNT });
  return shapes.map((shape) => {
    try {
      return { ok: true, patched: rail.patchChallenge(shape) };
    } catch (err) {
      return { ok: false, reason: err.name };
    }
  });
}

async function payments(cases) {
  const out = [];
  for (const testCase of cases) {
    const node = new testhost.FakeNode();
    if (testCase.block) {
      node.settle(testCase.block.hash, testCase.block.destination,
                  BigInt(testCase.block.amount_raw), testCase.block.confirmed);
    }
    const rail = new DualRail({ payTo: PAY_TO, amountXno: testCase.required || AMOUNT, node });
    const resource = testCase.resource || 'https://example.dev/report';
    if (testCase.replay) {
      await rail.checkPayment(testCase.header, resource);
    }
    let verdict;
    try {
      const payment = await rail.verify(testCase.header, resource);
      verdict = { paid: true, block_hash: payment.blockHash,
                  overpaid_raw: payment.overpaidRaw.toString() };
    } catch (err) {
      verdict = { paid: false, reason: err.reason };
    }
    out.push({ ...verdict, node_calls: node.calls.length });
  }
  return out;
}

function proofs(cases) {
  return cases.map((header) => {
    try {
      const payload = parsePayment(header);
      return { ok: true, ours: isOurs(payload),
               block: isOurs(payload) ? blockHashOf(payload) : null };
    } catch (err) {
      return { ok: false, reason: err.reason };
    }
  });
}

async function main() {
  const matrix = JSON.parse(require('node:fs').readFileSync(process.argv[2], 'utf8'));
  const result = {
    addresses: addresses(matrix.addresses),
    amounts: amounts(matrix.amounts),
    prices: prices(matrix.prices),
    challenges: challenges(matrix.challenges),
    proofs: proofs(matrix.proofs),
    payments: await payments(matrix.payments),
  };
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
}

main().catch((err) => { process.stderr.write(String(err.stack) + '\n'); process.exit(1); });
