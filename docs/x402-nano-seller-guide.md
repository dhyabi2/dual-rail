# Nano (XNO) for x402 sellers: one page

You already sell over x402 (USDC, Base, Solana). This page is the shortest path to
*also* being payable in XNO, with the traps that have actually bitten sellers and
buyers. Everything below was run or fetched on 2026-11-02 unless it says otherwise.

## 1. The entry

One `nano:mainnet` entry in `accepts[]`. Amounts are **raw, as a string of digits**
(1 XNO = 10^30 raw; `"100000000000000000000000000"` is 0.0001 XNO). `payTo` is a
`nano_` address you generated yourself ([key line](../README.md#first-five-minutes)).

```json
{ "scheme": "exact", "network": "nano:mainnet", "asset": "XNO",
  "amount": "100000000000000000000000000", "payTo": "nano_YOUR_ADDRESS",
  "maxTimeoutSeconds": 60 }
```

x402 v1 calls the price field `maxAmountRequired`, v2 calls it `amount`; send the one
your declared `x402Version` uses (`python3 cli.py inspect <url>` reports which it found).
Generate the patch instead of typing it: `python3 cli.py add --manifest <url> --pay-to nano_... --amount 0.0001 --diff`
(read-only; allowed range 0.000001-100 XNO).

## 2. How the buyer pays and how you check

The buyer sends exactly `amount` raw to `payTo` from its own wallet (no fee) and retries
with the **block hash** in the payment header. This repository reads:

```
X-PAYMENT: {"scheme":"exact","network":"nano:mainnet","payload":{"blockHash":"<64 hex>"}}
```

You check with one call to any public node. No facilitator, no account, no code of ours:

```
curl -s -H 'Content-Type: application/json' -d '{"action":"block_info","json_block":"true","hash":"<HASH>"}' https://rpc.nano.to
```

Require: `confirmed` is `"true"`, `subtype` is `"send"`, `contents.link_as_account` is **your**
`payTo` (not `block_account`, which is the payer), and `amount` equals your quote. Checked
today against block `B749B757...A58F`: `confirmed true`, `send`, `500000000000000000000000000`,
paid to `nano_1yo6c1t64a...`. Self-contained alternatives that do the same read:
`verify.py` here, [`nano-settlement-verify`](https://github.com/dhyabi2/nano-settlement-verify)
(stdlib, one file), or a facilitator that supports it
([gosuda/x402-facilitator](https://github.com/gosuda/x402-facilitator), two nodes must agree;
`python3 cli.py verifiers` checks live who can validate XNO today).

## 3. Traps (each one cost somebody a payment or a sale)

1. **A client library can drop or fail on an unknown network.** `x402-fetch@1.2.0` runs
   `accepts.map(x => PaymentRequirementsSchema.parse(x))` (its `dist/cjs/index.js` line 40) and
   `x402@1.2.0` types `network` as a closed zod enum (abstract, base, base-sepolia, avalanche,
   iotex, solana, sei, polygon, peaq, ...; the string `nano` appears nowhere in that file). I read
   that source in the published tarballs; I did not execute it. Consequence: an extra Nano entry
   beside USDC can make that client throw before it pays. If your buyers use it, offer Nano on a
   **separate Nano-only path** and keep the USDC path untouched. Newer `@x402/fetch` 2.x clients are
   a different codebase; test your own buyers rather than assuming.
2. **`nano:mainnet` is not in the x402 spec.** A client that does not know the network skips the
   entry silently, which looks exactly like "nobody wants to pay in XNO". `python3 cli.py check <url>`
   tells you whether a client of your declared version would actually pay it.
3. **A confirmed block proves someone paid you, not that they paid for this call.** Nano blocks have
   no memo field. Without a binding, one payment can be shown for two requests, or someone can show
   a block they did not send. Use a unique tagged amount per order
   ([`nano-invoice`](https://github.com/dhyabi2/nano-invoice): the lowest six raw digits are the tag,
   unique among open invoices, quarantined 7 days after close) and read the sender from the chain.
4. **The replay guard here is in memory, per process, and per resource.** The same block is refused
   again on the same path and accepted on another path (cases 13 and 14 in the checklist below). Run
   several workers and you need a shared store (`ReplayGuard.seen/remember`), and decide whether one
   payment may buy one call anywhere or on one path.
5. **Wallets that round.** A wallet that rounds the amount to a few decimals drops the tag and the payment
   matches no invoice (nano-invoice, Limits). Hand the buyer the amount in raw, or a full-precision `nano:` URI.
6. **Overpay and spelling.** gosuda compares the amount as an exact string, so an overpayment fails
   there; `verify.py` accepts at least the amount and reports `overpaid_raw`. gosuda lower-cases `payTo`
   text; `verify.py` and nano-settlement-verify compare the public key, so `xrb_` and `nano_` spellings
   of one account match. Pick one rule and test it.
7. **Raw is an integer.** Never parse with `float`: a double keeps 53 bits and a raw amount needs up
   to 128. nano-settlement-verify refuses a float `expect_raw` for this reason.
8. **Cloudflare fronts some public nodes** and answers Python's default `urllib` User-Agent with
   `403 error code: 1010`, which reads like a bad node. Send your own User-Agent.
9. **One node's word.** Failover is not agreement. For anything large ask two independent nodes
   (`nano_quorum.py` in nano-settlement-verify; gosuda requires two).
10. **Node outage must not cost you a USDC sale.** dual-rail falls through to your other rails on any
    Nano-side error (`node_unavailable`, case 12 below).

## 4. The refusal list, runnable (offline, 15 cases)

```
git clone https://github.com/dhyabi2/dual-rail && cd dual-rail && python3 docs/refusal_checklist.py
```

Output of that run, 2026-11-02, exit 0:

```
 0 ok   exact amount, right payTo, confirmed send        served
 1 ok   no X-PAYMENT header                              no_payment
 2 ok   header is not JSON                               malformed_payment
 3 ok   header is JSON but not an object                 malformed_payment
 4 ok   object with no block hash                        malformed_payment
 5 ok   proof for another network (not our business)     not_our_rail
 6 ok   block the node has never seen                    unknown_block
 7 ok   block not confirmed yet                          unconfirmed
 8 ok   a receive block on your own account              not_a_send
 9 ok   send to someone else                             wrong_destination
10 ok   one raw short                                    underpaid
11 ok   your own payTo fails its checksum                invalid_payto
12 ok   node unreachable                                 node_unavailable (falls through to your other rails)
13 ok   the same block again, same resource              replayed
14 ok   same block, DIFFERENT resource (guard is per resource) served
```

Port these to whatever verifier you run; a verifier that serves any of 1-13 is wrong.
Case 8 is the one people miss: a *receive* block on your own account is confirmed, carries a real
amount and its hash is public in your account history.

## 5. What this does not tell you

- It does not say anyone will pay. It says the door works and fails closed.
- The worked live example in [`x402-nano-end-to-end.md`](x402-nano-end-to-end.md) was run on 2026-10-09
  against a seller run by the authors; on 2026-11-02 that seller's host did not answer from this machine,
  so re-run its curl lines against your own endpoint rather than relying on it.
- Escrow, conditional release and refunds are not part of a Nano send. Decide your refund policy in
  your own terms (nano-invoice has `refund-hint`, instructions only; nothing is sent).
