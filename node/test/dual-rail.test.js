'use strict';
// The same fourteen numbered tests as tests/test_dual_rail.py, against the Node
// binding. The definition of done says both bindings must behave identically on
// the whole matrix; this file is half of that, and conformance.test.js is the
// half that compares the two implementations directly.

const assert = require('node:assert/strict');
const http = require('node:http');
const { test } = require('node:test');

const { dualRail, ConfigurationError, ReplayGuard } = require('../dual-rail.js');
const challenge = require('../challenge.js');
const money = require('../money.js');
const address = require('../address.js');
const testhost = require('../testhost.js');

const PAY_TO = 'nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3';
const BURN = 'nano_1111111111111111111111111111111111111111111111111111hifc8npp';
const AMOUNT = '0.0001';
const AMOUNT_RAW = 10n ** 26n;
const BLOCK = '9F2C' + '0'.repeat(60);
const RESOURCE = 'https://example.dev/report';

function proof(blockHash = BLOCK, network = challenge.NETWORK) {
  return JSON.stringify({ scheme: 'exact', network, payload: { blockHash } });
}

function serverFor(node, options = {}) {
  const { gate, protected: paid } = testhost.makeHost();
  const middleware = dualRail({ payTo: PAY_TO, amountXno: AMOUNT, node, gate, ...options });
  return http.createServer((req, res) => {
    Promise.resolve(middleware(req, res, () => paid(req, res)))
      .catch((err) => { res.writeHead(500); res.end(String(err)); });
  });
}

async function withServer(server, fn) {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address();
  try {
    return await fn(`http://127.0.0.1:${port}`);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

async function call(server, headers = {}, path = '/report') {
  return withServer(server, async (base) => {
    const response = await fetch(base + path, { headers: { host: 'example.dev', ...headers } });
    return { status: response.status, headers: response.headers, body: await response.text() };
  });
}

test('1. existing accepts[] entries are unchanged and not mutated', () => {
  const original = testhost.challenge();
  const before = JSON.parse(JSON.stringify(original.accepts));
  const rail = dualRail({ payTo: PAY_TO, amountXno: AMOUNT }).rail;
  const patched = rail.patchChallenge(original);
  assert.deepEqual(patched.accepts.slice(0, 2), before);
  assert.deepEqual(original.accepts, before);
});

test('2. the nano entry is appended last', () => {
  const rail = dualRail({ payTo: PAY_TO, amountXno: AMOUNT }).rail;
  const accepts = rail.patchChallenge(testhost.challenge()).accepts;
  assert.equal(challenge.findNano(accepts), accepts.length - 1);
  assert.equal(accepts.at(-1).resource, RESOURCE);
  assert.deepEqual(accepts.at(-1).extra, { decimals: 30, adapter: 'dual-rail/1' });
});

test('3. construction rejects a bad payTo, and nothing is ever listened on', () => {
  const broken = BURN.slice(0, -1) + (BURN.at(-1) === '3' ? '4' : '3');
  assert.equal(address.isValid(broken), false);
  const listened = [];
  assert.throws(
    () => {
      const middleware = dualRail({ payTo: broken, amountXno: AMOUNT });
      listened.push(http.createServer(middleware).listen(0));
    },
    (err) => err instanceof ConfigurationError && err.reason === 'invalid_address');
  assert.deepEqual(listened, [], 'a port was opened despite an unpayable payTo');
});

test('4. a node outage falls through, and no 5xx escapes', async () => {
  const node = new testhost.DeadNode();
  const seen = [];

  let result = await call(serverFor(node), { 'x-payment': proof() });
  seen.push(result.status);
  assert.equal(result.status, 402);
  assert.equal(JSON.parse(result.body).accepts.length, 3);

  result = await call(serverFor(node));
  seen.push(result.status);

  result = await call(serverFor(node), { 'x-payment': JSON.stringify({ network: 'base' }) });
  seen.push(result.status);
  assert.equal(result.status, 200);
  assert.equal(JSON.parse(result.body).report, 'the goods');

  for (const status of seen) assert.ok(status < 500, `a 5xx escaped: ${status}`);
  assert.ok(node.calls.length > 0, 'the node was never even tried');
});

test('5. a valid nano payment admits the request', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, PAY_TO, AMOUNT_RAW);
  const result = await call(serverFor(node), { 'x-payment': proof() });
  assert.equal(result.status, 200);
  assert.equal(JSON.parse(result.body).report, 'the goods');
});

test('6. an unconfirmed payment is rejected', async () => {
  const node = new testhost.FakeNode();
  node.unconfirmed(BLOCK, PAY_TO, AMOUNT_RAW);
  assert.equal((await call(serverFor(node), { 'x-payment': proof() })).status, 402);
});

test('7. a payment to the wrong destination is rejected', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, BURN, AMOUNT_RAW);
  assert.equal((await call(serverFor(node), { 'x-payment': proof() })).status, 402);
});

test('8. one raw of underpayment is rejected', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, PAY_TO, AMOUNT_RAW - 1n);
  assert.equal((await call(serverFor(node), { 'x-payment': proof() })).status, 402);
});

test('9. overpayment is accepted', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, PAY_TO, AMOUNT_RAW + 1n);
  assert.equal((await call(serverFor(node), { 'x-payment': proof() })).status, 200);
});

test('10. a replayed block is rejected the second time', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, PAY_TO, AMOUNT_RAW);
  await withServer(serverFor(node), async (base) => {
    const first = await fetch(base + '/report', { headers: { 'x-payment': proof() } });
    const second = await fetch(base + '/report', { headers: { 'x-payment': proof() } });
    assert.equal(first.status, 200);
    assert.equal(second.status, 402);
  });
});

test('11 & 12. appending is additive over every manifest shape, and idempotent', () => {
  const rail = dualRail({ payTo: PAY_TO, amountXno: AMOUNT }).rail;
  const shapes = [
    testhost.challenge(),
    testhost.challenge(testhost.USDC_ENTRIES.slice(0, 1)),
    testhost.challenge([...testhost.USDC_ENTRIES,
      { ...testhost.USDC_ENTRIES[0], network: 'polygon', maxAmountRequired: '0.02' }]),
  ];
  for (const shape of shapes) {
    const before = JSON.parse(JSON.stringify(shape.accepts));
    const patched = rail.patchChallenge(shape);
    assert.deepEqual(patched.accepts.slice(0, before.length), before);
    assert.equal(patched.accepts.length, before.length + 1);
    assert.deepEqual(rail.patchChallenge(patched), patched);
  }
});

test('13. both rails are reported live against a real server', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, PAY_TO, AMOUNT_RAW);
  const unpaid = await call(serverFor(node));
  const accepts = JSON.parse(unpaid.body).accepts;
  assert.equal(unpaid.status, 402);
  assert.equal(accepts.length, 3);
  assert.equal(accepts.at(-1).network, challenge.NETWORK);
  assert.ok(address.isValid(accepts.at(-1).payTo));
  assert.equal((await call(serverFor(node), { 'x-payment': proof() })).status, 200);
});

test('14. the 402 is byte-identical apart from accepts[]', async () => {
  const { gate } = testhost.makeHost();
  const bareResult = await call(http.createServer((req, res) => gate(req, res, () => {})));
  const withResult = await call(serverFor(new testhost.FakeNode()));

  assert.equal(bareResult.status, withResult.status);
  for (const name of ['content-type', 'x-402-version', 'cache-control']) {
    assert.equal(bareResult.headers.get(name), withResult.headers.get(name), name);
  }
  const before = JSON.parse(bareResult.body);
  const after = JSON.parse(withResult.body);
  assert.deepEqual(Object.keys(before).sort(), Object.keys(after).sort());
  for (const field of Object.keys(before)) {
    if (field !== 'accepts') assert.deepEqual(before[field], after[field], field);
  }
  assert.deepEqual(after.accepts.slice(0, before.accepts.length), before.accepts);
  assert.equal(after.accepts.length, before.accepts.length + 1);
});

test('the price band and the float refusal', () => {
  for (const bad of ['0.0000001', '101', '0']) {
    assert.throws(() => dualRail({ payTo: PAY_TO, amountXno: bad }),
                  (err) => err.reason === 'price_out_of_range', bad);
  }
  assert.throws(() => dualRail({ payTo: PAY_TO, amountXno: 0.0001 }),
                (err) => err.reason === 'price_out_of_range');
  assert.throws(() => money.xnoToRaw(0.1), (err) => err.reason === 'float_amount');
});

test('onVerifyError must be fallthrough, and the network is fixed', () => {
  for (const bad of ['reject', '503', '', null]) {
    assert.throws(() => dualRail({ payTo: PAY_TO, amountXno: AMOUNT, onVerifyError: bad }),
                  (err) => err.reason === 'invalid_on_verify_error');
  }
  assert.throws(() => dualRail({ payTo: PAY_TO, amountXno: AMOUNT, network: 'nano:beta' }),
                (err) => err.reason === 'invalid_network');
});

test('a node outage is logged once, not once per request', async () => {
  const lines = [];
  const node = new testhost.DeadNode();
  await withServer(serverFor(node, { logger: (line) => lines.push(line) }), async (base) => {
    for (let i = 0; i < 5; i += 1) {
      await fetch(base + '/report', { headers: { 'x-payment': proof() } });
    }
  });
  assert.equal(lines.length, 1, JSON.stringify(lines));
});

test('a USDC proof never reaches the Nano node', async () => {
  const node = new testhost.FakeNode();
  await call(serverFor(node), { 'x-payment': JSON.stringify({ network: 'base' }) });
  assert.deepEqual(node.calls, []);
});

test('a malformed payment falls through rather than erroring', async () => {
  const node = new testhost.FakeNode();
  for (const header of ['not json', 'e30=', JSON.stringify({ network: challenge.NETWORK }),
                        JSON.stringify({ network: challenge.NETWORK, payload: { blockHash: 'xy' } })]) {
    assert.equal((await call(serverFor(node), { 'x-payment': header })).status, 402, header);
  }
});

test('a base64 payment is accepted', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, PAY_TO, AMOUNT_RAW);
  const encoded = Buffer.from(proof()).toString('base64');
  assert.equal((await call(serverFor(node), { 'x-payment': encoded })).status, 200);
});

test('a 402 we do not understand is left alone', async () => {
  const gate = (req, res) => {
    res.writeHead(402, { 'Content-Type': 'text/html' });
    res.end('<html>pay up</html>');
  };
  const middleware = dualRail({ payTo: PAY_TO, amountXno: AMOUNT,
                                node: new testhost.FakeNode(), gate });
  const server = http.createServer((req, res) => middleware(req, res, () => {}));
  const result = await call(server);
  assert.equal(result.status, 402);
  assert.equal(result.body, '<html>pay up</html>');
});

test('replay is scoped per resource', () => {
  const guard = new ReplayGuard();
  guard.remember('https://a/one', BLOCK);
  assert.equal(guard.seen('https://a/one', BLOCK), true);
  assert.equal(guard.seen('https://a/two', BLOCK), false);
});

test('the hardest decimals round trip in BigInt', () => {
  for (const value of ['0.000001', '0.1', '0.2', '0.3', '100', '99.999999',
                       '0.' + '0'.repeat(29) + '1']) {
    assert.equal(money.rawToXno(money.xnoToRaw(value)), value);
  }
  assert.equal(money.xnoToRaw('0.1') + money.xnoToRaw('0.2'), money.xnoToRaw('0.3'));
});

// `maxAmountRequired` must be the ATOMIC amount - raw - or no x402 Nano client
// can pay the entry this adapter appends. It carried the configured decimal XNO
// string verbatim ('0.0001'); every x402 client reads the field as an integer
// count of the asset's atomic unit, which is what `extra.decimals: 30` declares
// it to be, so a patched 402 was unpayable. Mirrors AdvertisedAmountIsPayable in
// tests/test_dual_rail.py; the conformance suite asserts the two agree.
function nanoEntryFor(amountXno) {
  const patched = challenge.appendNano(
    { accepts: [{ scheme: 'exact', network: 'base', asset: 'USDC',
                  maxAmountRequired: '10000', payTo: '0xabc',
                  resource: 'https://example.dev/report' }] },
    PAY_TO, amountXno);
  return patched.accepts[patched.accepts.length - 1];
}

test('the advertised amount is an integer of raw', () => {
  const field = nanoEntryFor('0.0001').maxAmountRequired;
  assert.match(field, /^[0-9]+$/, `maxAmountRequired must be integer raw, got ${field}`);
  assert.equal(BigInt(field), money.xnoToRaw('0.0001'));
});

test('a client reading maxAmountRequired the x402 way gets the configured price', () => {
  // Exactly what feeless402's offer_amount_raw does: parse the field as an integer.
  assert.equal(BigInt(nanoEntryFor('0.0001').maxAmountRequired), 10n ** 26n);
});

test('the decimal figure is still there for a human', () => {
  assert.equal(nanoEntryFor('0.0001').maxAmountRequiredFormatted, '0.0001 XNO');
});

test('the hardest prices survive the wire', () => {
  for (const amount of ['0.000001', '0.1', '0.3', '100', '99.999999',
                        '0.' + '0'.repeat(29) + '1']) {
    const field = nanoEntryFor(amount).maxAmountRequired;
    assert.match(field, /^[0-9]+$/, amount);
    assert.equal(BigInt(field), money.xnoToRaw(amount), amount);
    assert.equal(money.rawToXno(BigInt(field)), amount, amount);
  }
});

// Getting the unit right was not enough: measured against the published packages
// (`@x402/core` 2.28.0, `@x402nano/exact` 0.3.0), the entry was REJECTED by both
// protocol versions' schemas - v1 for a missing `maxTimeoutSeconds`, v2 for a
// missing `amount` AND `maxTimeoutSeconds`. x402 renamed the amount field between
// versions and this adapter appends to somebody else's challenge, so it carries
// both names; each version's schema strips the other's field rather than refusing
// it. Mirrors EntryIsAValidPaymentRequirements in tests/test_dual_rail.py.
const V1_REQUIRES = ['scheme', 'network', 'asset', 'maxAmountRequired', 'payTo',
                     'resource', 'description', 'maxTimeoutSeconds'];
const V2_REQUIRES = ['scheme', 'network', 'asset', 'amount', 'payTo',
                     'maxTimeoutSeconds'];

test('every field x402 v1 requires is present', () => {
  const entry = nanoEntryFor('0.0001');
  const missing = V1_REQUIRES.filter((f) => entry[f] === undefined || entry[f] === null);
  assert.deepEqual(missing, [], `x402 v1 rejects the entry without ${missing}`);
});

test('every field x402 v2 requires is present', () => {
  const entry = nanoEntryFor('0.0001');
  const missing = V2_REQUIRES.filter((f) => entry[f] === undefined || entry[f] === null);
  assert.deepEqual(missing, [], `x402 v2 rejects the entry without ${missing}`);
});

test('both amount names carry the same integer', () => {
  // If these ever disagree the entry overcharges one half of the ecosystem and
  // undersells the other.
  const entry = nanoEntryFor('0.0001');
  assert.equal(entry.amount, entry.maxAmountRequired);
  assert.match(entry.amount, /^[0-9]+$/, entry.amount);
  assert.equal(BigInt(entry.amount), AMOUNT_RAW);
});

test('a v2 client reading amount gets the configured price', () => {
  // feeless402's offer_amount_raw tries `amount` FIRST, then `maxAmountRequired`.
  assert.equal(BigInt(nanoEntryFor('0.0001').amount), money.xnoToRaw('0.0001'));
});

test('the payment window is a positive whole number of seconds', () => {
  const window = nanoEntryFor('0.0001').maxTimeoutSeconds;
  assert.equal(typeof window, 'number');
  assert.ok(Number.isInteger(window), `${window} is not a whole number of seconds`);
  assert.ok(window > 0);
  assert.equal(window, challenge.MAX_TIMEOUT_SECONDS);
});

test('both names hold for every hard price', () => {
  for (const amount of ['0.000001', '0.1', '0.3', '100', '99.999999',
                        '0.' + '0'.repeat(29) + '1']) {
    const entry = nanoEntryFor(amount);
    assert.equal(entry.amount, entry.maxAmountRequired, amount);
    assert.equal(BigInt(entry.amount), money.xnoToRaw(amount), amount);
  }
});

test('the entry is still only appended', () => {
  const before = [{ scheme: 'exact', network: 'base', asset: 'USDC',
                    maxAmountRequired: '10000', payTo: '0xabc', resource: RESOURCE }];
  const patched = challenge.appendNano(
    { accepts: JSON.parse(JSON.stringify(before)) }, PAY_TO, AMOUNT);
  assert.deepEqual(patched.accepts.slice(0, -1), before);
  assert.equal(patched.accepts.length, before.length + 1);
});

// ---------------------------------------------------------------------------
// Only a send is a payment. The Node half of the same cases in
// tests/test_dual_rail.py, including the two replies recorded from a live
// mainnet node (rpc.nano.to, Nano V28.2) on 2026-10-04.

const { DualRail, Unpaid } = require('../dual-rail.js');
const { readBlockInfo } = require('../node-client.js');

// A RECEIVE on a real account: money arriving. `block_account` is the account
// the block belongs to - the receiver - and there is no payee anywhere in it.
const RECEIVE_REPLY = {
  block_account: 'nano_1natrium1o3z5519ifou7xii8crpxpk8y65qmkih8e8bpsjri651oza8imdd',
  amount: '50000000000000000000000000000000',
  confirmed: 'true',
  subtype: 'receive',
  contents: { type: 'state',
              link_as_account:
                'nano_1s7fdbg491z6eo64sz3ghjhxzpkgbn6baxznfy6eaeu8epwkmzpz9yc76c7f' },
};
const RECEIVER = RECEIVE_REPLY.block_account;

// A LEGACY (pre-state) send, block 2 of the genesis account. No `subtype` at
// all, kind in `contents.type`, payee in `contents.destination`.
const LEGACY_SEND_REPLY = {
  block_account: 'nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3',
  amount: '3271945835778254456378601994536232802',
  confirmed: 'true',
  contents: { type: 'send',
              destination:
                'nano_13ezf4od79h1tgj9aiu4djzcmmguendtjfuhwfukhuucboua8cpoihmh8byo' },
};
const LEGACY_SENDER = LEGACY_SEND_REPLY.block_account;
const LEGACY_PAYEE = LEGACY_SEND_REPLY.contents.destination;

class RecordedNode {
  constructor(reply) { this.reply = reply; this.calls = []; }

  async blockInfo(blockHash) {
    this.calls.push(blockHash.toUpperCase());
    return readBlockInfo(this.reply);
  }
}

async function verdict(node, payTo, amountXno = AMOUNT) {
  const rail = new DualRail({ payTo, amountXno, node });
  try {
    await rail.verify(proof(), RESOURCE);
    return 'paid';
  } catch (err) {
    if (err instanceof Unpaid) return err.reason;
    throw err;
  }
}

test('a receive on our own account is not a payment', async () => {
  const node = new testhost.FakeNode();
  node.received(BLOCK, AMOUNT_RAW * 100n);
  assert.equal(await verdict(node, PAY_TO), 'not_a_send');
});

test('a recorded receive names no payee at all', () => {
  const info = readBlockInfo(RECEIVE_REPLY);
  assert.equal(info.subtype, 'receive');
  assert.equal(info.destination, null,
    'a receive has no payee, so none may be invented for it');
  assert.equal(info.amount_raw, 50n * 10n ** 30n);
});

test('a recorded receive does not pay the account it credited', async () => {
  assert.equal(await verdict(new RecordedNode(RECEIVE_REPLY), RECEIVER, '0.000001'),
    'not_a_send');
});

test('a change block is refused as what it is, not as underpaid', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, null, 0n, true, 'change');
  assert.equal(await verdict(node, PAY_TO), 'not_a_send');
});

test('a block whose kind cannot be read is not a send', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, PAY_TO, AMOUNT_RAW, true, null);
  assert.equal(await verdict(node, PAY_TO), 'not_a_send');
});

test('a legacy send names its own payee, not its account', () => {
  const info = readBlockInfo(LEGACY_SEND_REPLY);
  assert.equal(info.subtype, 'send');
  assert.equal(info.destination, LEGACY_PAYEE);
  assert.notEqual(info.destination, LEGACY_SENDER);
});

test('a legacy send out of our account is not a payment to us', async () => {
  assert.equal(await verdict(new RecordedNode(LEGACY_SEND_REPLY), LEGACY_SENDER, '0.000001'),
    'wrong_destination');
});

test('a legacy send to us is a payment', async () => {
  assert.equal(await verdict(new RecordedNode(LEGACY_SEND_REPLY), LEGACY_PAYEE, '0.000001'),
    'paid');
});

// Named for what it reaches: `DualRail` normalises payTo at construction
// (`this.payTo = verdict.normalised`), so this case never gets as far as the
// comparison - reverting `sameAccount` to a text compare leaves it green. The
// comparison itself is reached by the node-side spelling, below.
test('a payTo given the legacy xrb_ way is normalised at construction', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, PAY_TO, AMOUNT_RAW);
  const rail = new DualRail({ payTo: 'xrb_' + PAY_TO.slice(5), amountXno: AMOUNT, node });
  assert.equal(rail.payTo, PAY_TO);
  assert.equal(await verdict(node, 'xrb_' + PAY_TO.slice(5)), 'paid');
});

test('the node may spell the destination the legacy way', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, 'xrb_' + PAY_TO.slice(5), AMOUNT_RAW);
  assert.equal(await verdict(node, PAY_TO), 'paid');
});

test('a different account is still a different account', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, BURN, AMOUNT_RAW);
  assert.equal(await verdict(node, PAY_TO), 'wrong_destination');
});

test('a destination that is not an address is equal to nothing', async () => {
  const node = new testhost.FakeNode();
  node.settle(BLOCK, 'not an address', AMOUNT_RAW);
  assert.equal(await verdict(node, PAY_TO), 'wrong_destination');
});
