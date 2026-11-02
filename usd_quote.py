# VENDORED from dhyabi2/xno-call-binding usd_quote.py (docs/USD-PEGGED-QUOTE.md); do not fork the rules here.
"""USD-pegged, settle-at-quote pricing for XNO. Stdlib only; read-only (fetches two public prices).

Price is fixed in USD cents. At request time the seller quotes a raw XNO amount from the
median of two independent feeds and the quote is valid for 60 seconds. A refund returns the
SAME raw amount that was paid, so neither side carries FX exposure after settlement.

Run:  python3 usd_quote.py 250        # quote $2.50 live from CoinGecko + KuCoin
"""
import json
import sys
import time
import urllib.request

XNO = 10 ** 30
VALIDITY_S = 60
MAX_SPREAD = 0.02  # refuse to quote if the two feeds disagree by more than 2%


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "xno-usd-quote/1"})
    return json.load(urllib.request.urlopen(req, timeout=10))


def feed_coingecko():
    return float(_get("https://api.coingecko.com/api/v3/simple/price?ids=nano&vs_currencies=usd")["nano"]["usd"])


def feed_kucoin():
    return float(_get("https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=XNO-USDT")["data"]["price"])


def quote(usd_cents, feeds, now=None, max_spread=MAX_SPREAD):
    """Return a quote dict. usd_cents must be an int. Needs two feeds; refuses on one."""
    if not isinstance(usd_cents, int) or isinstance(usd_cents, bool) or usd_cents <= 0:
        raise ValueError("usd_cents must be a positive int")
    prices = []
    for f in feeds:
        try:
            p = float(f())
            if p > 0:
                prices.append(p)
        except Exception:
            pass
    if len(prices) < 2:
        raise RuntimeError("fewer than two feeds answered: no quote")
    lo, hi = min(prices), max(prices)
    if (hi - lo) / lo > max_spread:
        raise RuntimeError("feeds disagree by more than %.0f%%: no quote" % (max_spread * 100))
    mid_micro_usd = round(sum(prices) / len(prices) * 10 ** 6)  # integer micro-USD per XNO
    # raw = cents/100 USD / (micro/1e6 USD per XNO) * 1e30, rounded UP so the seller is never short
    num = usd_cents * 10 ** 6 * XNO
    den = 100 * mid_micro_usd
    raw = -(-num // den)
    t = int(now if now is not None else time.time())
    return {"usd_cents": usd_cents, "price_micro_usd": mid_micro_usd, "amount_raw": str(raw),
            "quoted_at": t, "expires_at": t + VALIDITY_S, "feeds": prices}


def accept(q, paid_raw, paid_at):
    """Settle-at-quote: the paid amount must equal the quote and arrive before expiry."""
    if int(paid_raw) != int(q["amount_raw"]):
        return "refusable_wrong_amount"
    if paid_at > q["expires_at"]:
        return "refusable_late"
    return "settled"


def refund_raw(q, paid_raw):
    """Refund is the same raw amount returned: zero FX exposure for both sides."""
    return int(paid_raw)


if __name__ == "__main__":
    cents = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    q = quote(cents, [feed_coingecko, feed_kucoin])
    print(json.dumps(q, indent=1))
