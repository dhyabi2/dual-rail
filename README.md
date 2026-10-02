# dual-rail — add XNO **beside** USDC/x402, never instead of it

**If our rail fails, yours keeps selling.** Every error on the Nano side — an
unreachable node, a timeout, a malformed proof, a block we cannot find — falls
through to your existing rails exactly as if this adapter were not installed.
There is no code path in it that can turn a Nano problem into a 5xx for a
customer paying in USDC. That is enforced by a test, not by this paragraph:
`test_node_outage_falls_through` runs the whole flow with every node call
raising and asserts no 5xx anywhere.

This is not a migration. It appends **one entry** to your `accepts[]` array and
leaves your existing entries byte-identical, in the same order. A client that
takes the first acceptable entry keeps its current behaviour exactly. Deleting
one array element reverts it.

No dependencies, in either language. Python 3.8+ and the standard library; Node
18+ and nothing else.

## Quickstart (two minutes, no Nano node, no network)

```bash
git clone https://github.com/dhyabi2/dual-rail && cd dual-rail
python3 -m unittest discover -s tests          # 34 tests, includes the Python/Node conformance run
(cd node && node --test test/dual-rail.test.js) # 22 tests
python3 tests/capture_live_verify.py            # real HTTP server on 127.0.0.1, adapter mounted, 7/7 verify
```

Then point it at your own 402 (read-only, changes nothing):

```bash
python3 cli.py inspect https://your-service.example/.well-known/x402
python3 cli.py add --manifest https://your-service.example/.well-known/x402 \
    --pay-to nano_YOUR_ADDRESS --amount 0.0001 --diff
```

Not published to PyPI or npm yet: install from this repository
(`pip install git+https://github.com/dhyabi2/dual-rail`, or
`require('./node/dual-rail')` from a checkout).

The `nano_3t6k35gi…` address in the examples is the public Nano **genesis**
account, used only because it is a valid, well-known address. Put your own
account there.

## What changes in your 402

```diff
  {
    "x402Version": 2,
    "accepts": [
      { "scheme": "exact", "network": "base",   "asset": "USDC", ... },
      { "scheme": "exact", "network": "solana", "asset": "USDC", ... },
+     { "scheme": "exact", "network": "nano:mainnet", "asset": "XNO",
+       "maxAmountRequired": "0.0001", "payTo": "nano_3t6k…",
+       "description": "Feeless native-coin settlement. Optional — the entries above are unchanged.",
+       "extra": { "decimals": 30, "adapter": "dual-rail/1" } }
    ]
  }
```

Same status code, same headers, same ordering, same `x-402-version`. One
additional element at the end of `accepts[]`, and nothing else.

## The verifier is the product

The middleware is the implementation detail. This is the artefact that goes in a
message:

```
$ dual-rail verify https://example.dev/report --payment 9F2C0000…

[pass] 402 challenge reachable                HTTP 402, x-402-version: 2
[pass] pre-existing rails intact              2 entries: USDC/base, USDC/solana  (unchanged)
[pass] nano entry present and last            nano:mainnet XNO 0.0001 -> nano_3t6k35g…
[pass] nano payTo passes checksum             valid
[pass] existing rail still settles            2 pre-existing challenges well-formed
[pass] nano rail settles                      block 9F2C0000… -> HTTP 200
[pass] node outage falls through              unverifiable proof -> HTTP 402 with all 3 entries, no 5xx

verify: 7/7 pass - both rails live, existing rails unchanged.
```

Exit 0 only if all seven hold. A captured run against a live endpoint is in
[`tests/verify-against-a-live-endpoint.txt`](tests/verify-against-a-live-endpoint.txt),
whose header says precisely what that run does and does not prove.

`--payment` is spent by the run: one payment buys one call, so verifying twice
needs two payments. That is the replay protection working.

## Install

### Node / Express

```js
const { dualRail } = require('./node/dual-rail');   // from a checkout

app.use(dualRail({
  payTo: 'nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3',
  amountXno: '0.0001',                     // a decimal STRING, never a number
  nodeUrl: process.env.NANO_NODE_URL,
  gate: x402Middleware,                    // your paywall - see "Mounting" below
}));
```

### Python / WSGI (Flask, FastAPI via WSGI, anything)

```python
from adapter import dual_rail

rail = dual_rail(payTo="nano_3t6k35gi…", amountXno="0.0001",
                 nodeUrl=os.environ["NANO_NODE_URL"])
application = rail.wsgi(protected_app, gate=x402_middleware)
```

Construction **throws** if `payTo` fails its checksum. A service must not be
able to boot advertising an address nobody can pay. It also throws if
`amountXno` is outside 0.000001–100 XNO, if it is a float rather than a string,
or if `onVerifyError` is anything but `"fallthrough"` — failing closed on our
rail would let our outage cost you a sale on yours, so the option exists only to
be read, never to be changed.

### Mounting, and why `gate` exists

Without `gate` the adapter does the additive half: a 402 written by anything
downstream gets one entry appended on its way out. That needs no cooperation
from your stack at all.

Admitting a Nano-paid request is the other half, and it means bypassing your
paywall — so the adapter has to be shown which middleware that is. `gate` is it.
The adapter never modifies, wraps or reorders anything else, and a request
without a valid Nano payment goes through your gate untouched.

## CLI

There is one CLI, and it is the Python one — `cli.py`. The Node package ships
the middleware only; duplicating the verifier in two languages would give two
things to keep in step and no more assurance, and the verifier talks HTTP to
your endpoint rather than into either runtime.

```
dual-rail inspect <manifest-url|file>       # read-only; prints the current rails
dual-rail add --manifest <url|file> --pay-to nano_… --amount 0.0001 [--out p.json|--diff]
dual-rail verify <base-url> [--payment <block hash>] [--json]
```

`add` exits **3** rather than emit a patch that removes or modifies a line it
did not author — including on a YAML manifest, which cannot be re-rendered
without rewriting lines, and where it tells you to use `--out` instead. Run on
an already-patched manifest it exits 0 with `already_present` and emits nothing.

## Money

Integer raw end to end (1 XNO = 10\*\*30 raw): `BigInt` in Node, `int` in Python.
A float amount is **refused**, not rounded — a double holds 53 bits of mantissa
against the ~100 a raw balance needs, so one float round trip loses real money
in the low-order digits. Underpayment by a single raw is underpayment.

## Verifying it before you mount it

```
$ python3 -m unittest discover -s tests
Ran 34 tests — OK

$ cd node && node --test test/dual-rail.test.js
# pass 22

$ python3 -m unittest tests.test_conformance
```

The last one is the interesting one. The two bindings have to behave
**identically**, and a promise that they do is worth nothing without something
that runs them side by side: `tests/test_conformance.py` drives one shared
matrix — addresses, amounts, price bands, challenge shapes, payment proofs,
settlement verdicts — through both and asserts deep equality, case by case. It
fails rather than skips if `node` is missing, because an unrun conformance suite
is exactly the state in which two implementations drift apart.

Node has no variable-length BLAKE2b (`crypto` offers `blake2b512` only) and
Nano's address checksum needs a 5-byte one, which is **not** a truncation of the
64-byte digest. `node/blake2b.js` implements RFC 7693 properly, and its digests
are checked against Python's `hashlib.blake2b` on ten vectors including
multi-block input.

No test in this repository reaches a Nano node or any host but 127.0.0.1.

## What it does not do

It does not run a node, hold a key, or sign anything — it only reads whether a
block you were paid is confirmed. It does not choose your price. It does not
touch your existing rails, and it is built so that it cannot: `assert_additive`
compares the array before and after and raises unless the prefix is deeply equal
and exactly one element was added.

The replay guard is per-process and in-memory. A service running several workers
needs a shared store behind `ReplayGuard.seen`/`remember` — two methods, said
here rather than discovered in production.

## Who made this, and its honest limits

Written and maintained by **dhyabi2**, as part of an effort to make Nano (XNO)
a practical payment rail for AI agents. It is ours; it is not an official Nano
Foundation or x402 Foundation project.

- **No third-party deployment yet.** The captured verify run is against our own
  test host on 127.0.0.1 with a fake node (its header says so). Nobody outside
  has mounted this in production yet.
- **`nano:mainnet` is not a network name the x402 specification defines.** A
  client that does not know it simply skips the entry - which is exactly why it
  is appended last and never replaces your rails.
- **Replay protection is in-memory and per-process** (see above).
- **Your node is your trust anchor.** A payment counts when the node you
  configure says the block is confirmed; the adapter does not cross-check a
  second node.
- Version 1.0.0; not yet on PyPI or npm.

MIT licensed - see `LICENSE`.
