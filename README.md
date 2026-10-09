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
python3 -m unittest discover -s tests          # 143 tests, includes the Python/Node conformance run
(cd node && npm test)                           # 75 tests
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
+       "maxAmountRequired": "100000000000000000000000000",
+       "maxAmountRequiredFormatted": "0.0001 XNO", "payTo": "nano_3t6k…",
+       "description": "Feeless native-coin settlement. Optional — the entries above are unchanged.",
+       "extra": { "decimals": 30, "adapter": "dual-rail/1" } }
    ]
  }
```

Same status code, same headers, same ordering, same `x-402-version`. One
additional element at the end of `accepts[]`, and nothing else.

One real `nano:mainnet` entry followed from the 402 to a confirmed block on the ledger, verified with no facilitator, is in [`docs/x402-nano-end-to-end.md`](docs/x402-nano-end-to-end.md).

`maxAmountRequired` is in **raw**, the asset's atomic unit, which is what
`extra.decimals: 30` declares it to be — 10\*\*30 raw to the XNO, so the
figure above is 0.0001 XNO. That is how every x402 client reads the field
(feeless402's `offer_amount_raw` is `int(offer["maxAmountRequired"])`), and it
is the same convention the USDC entries above use: their `"10000"` is 0.01 USDC
at 6 decimals, not ten thousand dollars. `maxAmountRequiredFormatted` carries
the decimal figure for a human. You still configure the price in XNO
(`amountXno: "0.0001"`); only the wire format is raw.

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
dual-rail check   <manifest-url|file> [--json]   # read-only; would a client PAY the Nano entry?
                                                 # reads a 402 challenge or a resources[] catalogue
dual-rail add --manifest <url|file> --pay-to nano_… --amount 0.0001 [--out p.json|--diff]
dual-rail verify <base-url> [--payment <block hash>] [--json]
```

`add` exits **3** rather than emit a patch that removes or modifies a line it
did not author — including on a YAML manifest, which cannot be re-rendered
without rewriting lines, and where it tells you to use `--out` instead. Run on
an already-patched manifest it exits 0 with `already_present` and emits nothing.

## Would a client actually pay your Nano entry?

`inspect` reads the price under whichever name your document uses — x402
renamed it from `maxAmountRequired` (v1) to `amount` (v2), and it reports which
one it found in `amount_field`. It also reads a **multi-resource** manifest,
where `accepts[]` sits under each `resources[]` item rather than at the top
level, and reports each resource separately. `add` deliberately refuses that
shape: which of your resources gets the payout address is your call, not this
tool's, so point it at the one resource's own 402.

`inspect` says what is in your `accepts[]` array. `check` says whether anything
would act on it — and it is a different question, because an x402 client that
cannot parse your Nano entry does not reject it with a reason. The entry fails a
schema check inside the client library and is **dropped before any payment is
attempted**. From your side that is indistinguishable from nobody wanting to pay
in XNO.

```
$ python3 cli.py check https://your-service.example/.well-known/x402

accepts[1]  network "nano-mainnet"
  x402 v2: 3 problem(s)
    [network_not_caip2] "nano-mainnet" is accepted by x402 v1 but refused by v2,
      whose NetworkSchemaV2 requires a colon (@x402/core 2.28.0). Emit "nano:mainnet"
    [amount_field_is_other_version_name] this entry carries maxAmountRequired,
      x402 v1's name for the price…
    [pay_to_invalid] payTo is not a payable Nano account (bad_checksum)…

check: NOT payable as it stands
```

It exits **1** when nothing in the document is payable, so it can gate a deploy.

The rules come in two halves, and the split is the point. `shape_problems` is
**only** what the x402 schema itself rejects, read off `@x402/core` 2.28.0's own
`PaymentRequirementsV1Schema` and `PaymentRequirementsV2Schema` — the renamed
amount field, the CAIP-2 network rule that v2 added and v1 did not have,
`maxTimeoutSeconds` being a `number` so that `"60"` is refused, `extra` having
to be an object or null. That half is checked for **equivalence** against the
real installed package by `tests/cross_check_x402_core.js`, over 120,000
nearly-valid documents of which the library accepts about 42%; the captured run
is in `tests/cross-check-against-x402-core.txt`. It is not part of the suite
because this package has no dependencies and is not acquiring one.

`nano_problems` is what no generic x402 conformance tool can check, because it
needs the chain:

- a `payTo` that is well-formed and **fails its checksum**. x402 asks only for a
  non-empty string, so the client accepts it and the money sent there is
  unspendable.
- an amount that is not a whole number of raw. `@x402nano/typescript-common`
  declares an integer amount `/^\d+$/`, so `"0.0001"` is not a payable v2
  amount however right it looks.
- an amount above 2\*\*128-1 raw, which no Nano block's balance field can hold.
- a network identifier that means Nano but is not the canonical spelling.

And because a client parses the whole 402 or none of it, `check` reports a
**sibling** entry that sinks the document your entry is in — a USDC entry
carrying `"network": "base"` in a document that says `x402Version: 2` fails
`NetworkSchemaV2`, and the array your Nano entry sits in is thrown out with it.

### Two document shapes, and the one that used to read as empty

A **402 challenge** has a top-level `accepts[]`. A **resource catalogue** —
what a seller serves at `/.well-known/x402` — has `resources[]`, each item
carrying its own `url` and its own `accepts[]`. `check` read only the first
shape and reported a catalogue as `accepts must be an array of entries; got
null`: **0 entries, 0 naming Nano, not payable**, exit 1, about a document
carrying payable Nano entries. From the seller's side that is the same thing as
nobody wanting to pay in XNO, which is the confusion this whole command exists
to end. It now reads both, one resource at a time — flattening them would
invent an `accepts[]` no client ever sees and make an entry's index meaningless:

```
$ python3 cli.py check https://extract.paypercall.dev/.well-known/x402

x402 version declared: 2
document:              resource catalogue, 24 resource(s)
accepts[] entries:     24 across all resources
resources naming Nano: 24, of which not payable: 0
```

`payable` for a catalogue is **every** Nano resource, not any: a catalogue of 24
endpoints with one unpayable entry is a seller problem, and an `any` would
report it healthy on the strength of the other 23. `resources_not_payable` says
how many, and each is printed under its own url.

A catalogue entry is an **advertisement**, not a challenge, and the three
fields the x402 schema requires that only a live 402 can supply —
`maxTimeoutSeconds`, and v1's `resource` and `description` — are reported as a
note rather than counted against it. Measured: the manifest above omits all
three on all 24 entries, while a real 402 from the same resource carries
`maxTimeoutSeconds: 60` and a top-level `resource` object. Reporting them as
problems would be the same false alarm `verify` used to raise about a working
USDC rail. The relaxation covers a **missing** field only — a
`maxTimeoutSeconds` of `"60"` is still refused — and nothing a later 402
cannot repair is relaxed at all: a bad checksum, a decimal amount, an amount
over the 128-bit ceiling, a wrong asset, scheme or network still sink the
entry.

`shape_problems` itself is untouched by this, which is why the equivalence run
above still reads 0 disagreements over 120,000 documents: the catalogue
relaxation sits one layer up, in `split_challenge_only`. A catalogue is not a
document `@x402/core` models — `PaymentRequiredSchema` rejects it for want of
`resource` and `accepts` — so the equivalence claim is about the **entries**,
which are `PaymentRequirements` in either shape, and not about the catalogue
around them.

## Network identifiers: there is more than one

`nano:mainnet` is the canonical spelling and the only one x402 v2 accepts.
`nano-mainnet` is also in the wild: legal under v1, whose `NetworkSchemaV1` is
any non-empty string, and refused by v2. `network.py` / `node/network.js` read
both as mainnet, emit the canonical one, and tell you which you were given.

A bare `nano` or `xno` is **refused**, not assumed: it names the family without
naming the network, and Nano's test networks use the same `nano_` address
prefix, so reading it as mainnet would be a guess about where money goes. It
comes back as `network_unspecified`, and `nano:testnet` comes back as
`not_mainnet` — a different answer from "this is not Nano", because the two want
different fixes.

This matters to the adapter and not only to the validator. `find_nano` used to
compare against the literal `nano:mainnet`, so a seller who **already** accepted
XNO and spelled it `nano-mainnet` looked like a seller with no Nano entry, and
`add` appended a second one. The array then carried two Nano prices and two
payout addresses, and which one got paid depended on the payer's protocol
version: a v1 client takes the first entry it can pay, a v2 client refuses
`nano-mainnet` outright and takes the other. `add` is now a no-op there, and
`check` reports such an array as `duplicate_nano_entries` if one reaches it.

## Money

Integer raw end to end (1 XNO = 10\*\*30 raw): `BigInt` in Node, `int` in Python.
A float amount is **refused**, not rounded — a double holds 53 bits of mantissa
against the ~100 a raw balance needs, so one float round trip loses real money
in the low-order digits. Underpayment by a single raw is underpayment.

## Verifying it before you mount it

```
$ python3 -m unittest discover -s tests
Ran 143 tests — OK

$ cd node && npm test
# pass 75

$ python3 -m unittest tests.test_conformance
```

The last one is the interesting one. The two bindings have to behave
**identically**, and a promise that they do is worth nothing without something
that runs them side by side: `tests/test_conformance.py` drives one shared
matrix — addresses, amounts, price bands, challenge shapes, network
identifiers, whole 402 declarations, payment proofs, settlement verdicts —
through both and asserts deep equality, case by case, **messages included**. It
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
block you were paid is a confirmed **send to your account**. It does not choose
your price. It does not
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
- **A proof must be a send to your account, and only that.** Until
  2026-10-04 a confirmed block of any other kind whose `block_account` was your
  payout address verified — which a *receive* on your own account is. Every
  receive hash is public in your account history, so a stranger could read your
  ledger and be served without paying. The payee is now read from the block's own
  send destination, anything that is not a send is refused as `not_a_send`, and
  the two accounts are compared by public key rather than by the `nano_`/`xrb_`
  spelling a node happens to serve.
- Version 1.0.0; not yet on PyPI or npm.

MIT licensed - see `LICENSE`.
