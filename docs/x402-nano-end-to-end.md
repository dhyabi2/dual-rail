# One x402 payment on Nano, end to end

The objection this page answers: *"there is no per-call payment requirement for
Nano that anyone can check."* There is. Below is one real one, followed from the
402 to the block on the ledger, checked three ways with no facilitator
involved, and then what a facilitator adds.

Every command here was run on **2026-10-09 between 02:31 and 02:32 UTC**, from a
checkout of this repository unless the block says otherwise. The output
underneath each one is what it actually printed. Where output was cut, the cut
is marked `[... trimmed ...]`. Nothing on this page sends money.

**Who is who.** The seller in this example, `extract.paypercall.dev`, is run by
the same people as this repository. Read the example as "the mechanism works on
mainnet", not as "an independent seller vouches for it". Section (e) covers what
it does not prove.

---

## (a) The 402: what the seller asks for

Call a paid endpoint without paying, and the seller answers HTTP 402 with an
`accepts[]` list. Here it has one entry, on `nano:mainnet`:

```console
$ curl -s https://extract.paypercall.dev/api/v1/nano-info | python3 -m json.tool
{
    "error": "payment_required",
    "message": "Pay 0.0005 XNO to nano_1yo6c1t64a... and retry with X-PAYMENT header, or use X-BALANCE with a nano_ account that holds a prepaid balance",
    "price_xno": 0.0005,
    "pay_to": "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7",
    "endpoint": "/api/v1/nano-info",
    "x402Version": 2,
    "resource": {
        "url": "https://extract.paypercall.dev/api/v1/nano-info",
        "mimeType": "application/json"
    },
    "accepts": [
        {
            "scheme": "exact",
            "network": "nano:mainnet",
            "asset": "XNO",
            "amount": "500000000000000000000000000",
            "payTo": "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7",
            "maxTimeoutSeconds": 60
        }
    ],
    "extensions": {
[... trimmed: a "rail-hint" object and a "bazaar" input schema ...]
    }
}
```

The response also carried the headers `x-402-version: 2` and a base64
`payment-required` header. The x402 part of the body, with the seller's own
extra fields and `extensions` taken out, is:

```json
{
  "x402Version": 2,
  "resource": {
    "url": "https://extract.paypercall.dev/api/v1/nano-info",
    "mimeType": "application/json"
  },
  "accepts": [
    {
      "scheme": "exact",
      "network": "nano:mainnet",
      "asset": "XNO",
      "amount": "500000000000000000000000000",
      "payTo": "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7",
      "maxTimeoutSeconds": 60
    }
  ]
}
```

What the fields mean:

- `amount` is in **raw**, Nano's smallest unit. 1 XNO = 10^30 raw, so
  `500000000000000000000000000` is 0.0005 XNO. It is a string of digits, never
  a decimal.
- `payTo` is the Nano account the money must go to.
- `scheme: exact` means "pay exactly this amount".

The seller's catalogue at `/.well-known/x402` lists the same entry for the same
resource:

```console
$ curl -s https://extract.paypercall.dev/.well-known/x402 | python3 -m json.tool | grep -B2 -A10 '"url": "https://extract.paypercall.dev/api/v1/nano-info"'
        },
        {
            "url": "https://extract.paypercall.dev/api/v1/nano-info",
            "method": "GET",
            "description": "Nano account intelligence: balance, representative, block count, frontier, weight, pending. Accepts ?account=nano_.... 0.0005 XNO per call.",
            "accepts": [
                {
                    "scheme": "exact",
                    "network": "nano:mainnet",
                    "asset": "XNO",
                    "amount": "500000000000000000000000000",
                    "payTo": "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7"
                }
```

And this repository's own checker agrees that an x402 v2 client would pay it:

```console
$ python3 cli.py check https://extract.paypercall.dev/api/v1/nano-info

dual-rail check https://extract.paypercall.dev/api/v1/nano-info

x402 version declared: 2
accepts[] entries:     1
entries naming Nano:   1


accepts[0]  network "nano:mainnet"
  x402 v2: payable

check: payable - an x402 client of the declared version would pay this Nano entry
```

Exit code 0.

This seller takes **only** XNO. A seller that already takes USDC would have its
USDC entries first and this entry last; see the README for what `cli.py add`
appends.

## (b) How the buyer pays

The buyer makes an ordinary Nano send from its own wallet: exactly `amount` raw
to `payTo`. Nano has no fee, so the seller receives exactly what was quoted.
No special contract or token is involved, and nobody else has to sign.

The send produces a **block hash**: 64 hex characters that name the payment on
the ledger. The buyer retries the request with that hash in the `X-PAYMENT`
header, for example:

```
{"scheme": "exact", "network": "nano:mainnet", "payload": {"blockHash": "B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F"}}
```

That is the shape this repository's `verify.py` reads. It is also where the
gosuda facilitator in (d) looks: it reads `payload.blockHash`. This page did not
make a send, so it shows a payment that already exists. Block
`B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F` is a 0.0005
XNO send to this `payTo`. Here it is on the ledger, read from a public Nano node:

```console
$ curl -s https://rpc.nano.to -H 'Content-Type: application/json' -d '{"action":"block_info","json_block":"true","hash":"B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F"}' | python3 -m json.tool
{
    "block_account": "nano_3m8cz87zwxb1y16ob4bzp1eyek78qaig8ktohk7d45b18sh6u9exbowbnekr",
    "amount": "500000000000000000000000000",
    "balance": "6275108217158176000000000000000",
    "height": "4",
    "local_timestamp": "1790150110",
    [... trimmed: successor ...]
    "confirmed": "true",
    "contents": {
        "type": "state",
        "account": "nano_3m8cz87zwxb1y16ob4bzp1eyek78qaig8ktohk7d45b18sh6u9exbowbnekr",
        [... trimmed: previous, representative, balance ...]
        "link_as_account": "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7",
        [... trimmed: link, signature, work, balance_nano ...]
    },
    "subtype": "send",
    "balance_nano": "6.275108217158176",
    "amount_nano": "0.0005"
}
```

These are the four facts that matter. The block is a `send`. It is
`confirmed`. It paid `link_as_account`, which is the `payTo` from (a). Its
`amount` is the raw amount from (a), to the digit. The `local_timestamp`
1790150110 is 2026-09-23 07:55:10 UTC.

## (c) Verifying it with no facilitator at all

Anyone can check this payment: the seller, the buyer, an auditor or a
stranger. All it takes is the block hash, the quoted amount, `payTo` and one
call to any Nano node. No account, API key or facilitator is needed.

**With this repository's verify path** (`verify.py`, which is what the adapter
runs before it admits a Nano-paid request):

```console
$ python3 -c '
import json, nanonode, verify
proof = json.dumps({"scheme": "exact", "network": "nano:mainnet",
                    "payload": {"blockHash": "B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F"}})
guard = verify.ReplayGuard()
node = nanonode.HttpNanoNode("https://rpc.nano.to")
payTo = "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7"
print(verify.verify(proof, payTo, 500000000000000000000000000, "/api/v1/nano-info", node, guard))
try:
    verify.verify(proof, payTo, 500000000000000000000000000, "/api/v1/nano-info", node, guard)
except verify.Unpaid as second:
    print("second use:", second.reason, "-", second.message)
'
{'block_hash': 'B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F', 'amount_raw': 500000000000000000000000000, 'payTo': 'nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7', 'overpaid_raw': 0}
second use: replayed - block B749B757EE75 has already been spent on this resource
```

Exit code 0. The second call shows that one payment buys one call. That guard
is in memory and per process (see the README).

**With the standalone verifier** `dhyabi2/nano-settlement-verify`, which is
stdlib Python with no dependencies. Its documented one-liner downloads the
GitHub tarball. In this sandbox `github.com` answered 403, so the same two
files were fetched from `raw.githubusercontent.com` into the same paths the
tarball unpacks to. Run this in an empty directory:

```console
$ curl -sf --create-dirs -o nano-settlement-verify-main/skills/nano-settlement-verify/verify_cli.py https://raw.githubusercontent.com/dhyabi2/nano-settlement-verify/main/skills/nano-settlement-verify/verify_cli.py
$ curl -sf --create-dirs -o nano-settlement-verify-main/skills/nano-settlement-verify/nano_settlement_verify.py https://raw.githubusercontent.com/dhyabi2/nano-settlement-verify/main/skills/nano-settlement-verify/nano_settlement_verify.py
$ python3 nano-settlement-verify-main/skills/nano-settlement-verify/verify_cli.py B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F 500000000000000000000000000 nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7
{"settled": true, "amount_raw": 500000000000000000000000000, "height": 4, "account": "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7"}
```

Exit code 0, which means settled. It also refuses what it should. Here is the
same block checked against a different price, and then against a different
`payTo`:

```console
$ python3 nano-settlement-verify-main/skills/nano-settlement-verify/verify_cli.py B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F 100000000000000000000000000 nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7
{"verdict": "mismatch", "expected": "100000000000000000000000000", "got": "500000000000000000000000000"}
$ python3 nano-settlement-verify-main/skills/nano-settlement-verify/verify_cli.py B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F 500000000000000000000000000 nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3
{"verdict": "mismatch", "expected": "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3", "got": "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7"}
```

Both exited with code 3.

Both checks above asked one node, `rpc.nano.to`. A second public node,
`rainstorm.city/api`, was not reachable from this sandbox (the proxy refused
it, exit 4 `node_unreachable`). So this page shows agreement from **one** node
only. You can pass any node URL as the fourth argument to `verify_cli.py`.

The seller also publishes its own record of what the payment bought. This is
the seller's claim, not something the ledger shows:

```console
$ curl -s "https://extract.paypercall.dev/api/v1/delivery-proof?block_hash=B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F" | python3 -m json.tool
{
    "block_hash": "B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F",
    "amount_xno": "0.000500",
    "source": "nano_3m8cz87zwx...",
    "endpoint": "/api/v1/nano-info",
    "status": "delivered",
    "created_at": "2026-09-23T07:55:13Z",
    "seller": "vend",
    "settlement_rail": "nano:mainnet/XNO",
    "x402_version": 2
}
```

## (d) What a facilitator adds

A facilitator is an outside service that checks payments for the seller. The
claim we were asked to back is that `gosuda/x402-facilitator` supports Nano,
via its PR #64. Here is what we could and could not check.

**Not verified from here:** PR #64 itself. Its title, whether it is merged and
its diff could not be read, because both `api.github.com/repos/gosuda/x402-facilitator/pulls/64`
and `github.com/gosuda/x402-facilitator/pull/64` answered 403 in this sandbox.

**Read on 2026-10-09 at about 02:27 UTC**, on the repository's `main` branch
through `raw.githubusercontent.com`. This is the source only; we did not build
or run it.

- `README.md` marks the `exact` scheme on `nano:*` as supported (✅), and
  explains: "Nano is addressed as `nano:mainnet` … the payer broadcasts its
  own signed send block directly and there is no facilitator settlement to
  broadcast or gas token to carry. The facilitator is read-only on the chain."
- `cmd/facilitator/registry.go` sends any `nano:` network to
  `nanofacilitator.NewNanoFacilitator`.
- `scheme/nano/facilitator/nano.go` has three entry points:
  - `Verify` reads the block hash from `payload.blockHash`, or from
    `paymentProof` or `transaction`. It requires x402 version 2, and requires
    the payment's scheme, network, asset, amount and `payTo` to match the
    requirement. Then it calls `VerifyBlock`.
  - `Settle` runs that check again and then claims the block hash exactly once
    (`ClaimStore`, a mutex-guarded in-memory map in `claim.go`). Nothing is
    broadcast, and the hash comes back as the `Transaction`.
  - `Supported` advertises `assetTransferMethod: "nano-native-send"`, the asset
    and the decimals. The constructor's comment says the private key "is
    unused: the payer broadcasts its own send block and the facilitator never
    signs, moves or holds funds".
- `scheme/nano/verify.go` `VerifyBlock` asks at least `MinIndependentEndpoints`
  nodes. The README says two, by default `https://rpc.nano.to` and
  `https://rainstorm.city/api`. It fails closed: if any node errors, or the
  block is not a send, not confirmed, paid a different destination or paid a
  different amount, the payment is refused. It also refuses if fewer than the
  minimum number of nodes are configured.

So a facilitator adds **two-node agreement**, **one-time claiming of a block
behind a standard x402 `/verify` and `/settle` API**, and **the payer's account
read back from the ledger**. It adds no custody and no signing. The check
itself is the same one shown in (c).

Two differences, both read from the source. First, gosuda compares the amount
as an exact string, so an overpayment fails there. This repository's
`verify.py` accepts at least the amount and reports `overpaid_raw`. Second,
gosuda compares `payTo` as lower-cased text, while `verify.py` and
`nano-settlement-verify` compare the public key, so the `xrb_` and `nano_`
spellings of one account match.

## (e) What this does NOT prove

- **That the payment bought real work.** The ledger shows that 0.0005 XNO moved
  to `payTo`. It does not show what the seller did for it. The delivery record
  above is the seller's own word.
- **That the payer is independent.** The payer account,
  `nano_3m8cz87zwxb1y16ob4bzp1eyek78qaig8ktohk7d45b18sh6u9exbowbnekr`, has
  received XNO from our own operator (our read of the public ledger, 2026-10-08). We do **not**
  count it as an independent buyer. The seller is ours too. This example
  proves the mechanism works on mainnet. It does not prove that anyone outside
  wants it.
- **That the facilitator works.** The Go code in (d) was read, not built or
  run, and nothing here sent a payment through it.
- **That a second node agrees.** Only one node answered from here; see (c).
- **That x402 defines `nano:mainnet`.** The x402 specification does not list
  it. A client that does not know the network skips the entry, which is why
  `cli.py add` appends it last, beside USDC and never in place of it.
