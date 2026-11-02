# Hosted Nano x402 facilitator (verify-only, no custody)

For a seller whose objection is "a Nano accept adds a second treasury and a verifier we would have to run"
(MiroShark #309, DeskCrew #2, Hive, RED-QUEEN): neither is needed.

* **No second treasury.** `payTo` is the seller's own `nano_` address. The payer's send block lands there directly.
  The facilitator never holds, receives or signs anything: `hosted_facilitator.py` has no key, no seed and no
  `process` call, and a test pins that (`test_no_secret_surface`).
* **No verifier to run.** `POST /verify` and `POST /settle` read a public node (`block_info`) and answer whether the
  block is a confirmed send, to your address, for at least the raw amount. Integer raw only (1 XNO = 10^30 raw).

## Three-line integration (seller side)

1. Add one `accepts[]` entry (exact scheme, `network: "nano:mainnet"`, `payTo: <your nano_ address>`, `amount: <raw>`).
   Generate it: `python3 accepts_snippet.py --help` (dual-rail v1.2.0).
2. Point your x402 middleware's facilitator URL at this service, or call `/verify` yourself with
   `{"paymentPayload": {...}, "paymentRequirements": {...}}`.
3. Serve the resource when `isValid` is true; call `/settle` once to claim the block.

Run it locally in one line (no install, stdlib only):

```
git clone https://github.com/dhyabi2/dual-rail && cd dual-rail && PORT=8080 python3 hosted_facilitator.py   # then: curl -s localhost:8080/supported
```

## Behaviour

* A node outage answers HTTP 200 with `isValid:false` and `fallthrough:true`, never a 5xx: your USDC rails keep selling.
* `/verify` never spends a block; `/settle` claims it for the resource. The default replay guard is in process memory,
  which on a serverless host is best effort only: a seller that needs hard one-payment-one-call keeps its own ledger
  (or uses the binding in `binding.py`). This is a limit, stated here.
* Optional paid route `GET /v1/block-check?hash=H` (402 with a `nano:mainnet` entry, then the node's view of block H) and
  `/.well-known/x402`, enabled only when `PAY_TO` is set to a valid address. This is the "cheap live thing to buy that
  does not run on our box" from the IF-WE-STOPPED note.

## Deploy (owner-only steps; nothing here was deployed by the author)

Vercel (Python function in `api/index.py`, `vercel.json` rewrites everything to it):

```
vercel deploy --prod        # from the dual-rail repo root
vercel env add PAY_TO       # the owner's own nano_ address, only if the paid route is wanted
vercel env add PRICE_XNO    # optional, default 0.0001
```

Then point a hostname at it and, for the paid route, publish `https://<host>/.well-known/x402` (already served).
Nothing else is configured and no secret is stored. The owner picks the address; no wallet exists in the cloud.
