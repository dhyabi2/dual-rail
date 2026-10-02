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
