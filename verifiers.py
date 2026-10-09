"""Who can validate an XNO (`nano:mainnet`) x402 payment today, checked live.

Answers a seller's question - "which facilitators can validate XNO proofs
without trusting a new custodian?" - with only what this run could confirm:

    self      a `block_info` read against a public Nano node. No facilitator,
              no custodian: the payer broadcast its own signed send block and
              any node will show it to anyone. This is the read `verify.py`
              (the adapter's settlement check) makes.
    gosuda    gosuda/x402-facilitator, whose main-branch README has a Nano
              section (`nano:mainnet`, scheme `exact`). `custodial` is false
              only if that README text says the payer broadcasts its own block
              and the facilitator is read-only on the chain; otherwise unknown.

A check that cannot be reached is reported as such - never as a pass. Both
the node and the README fetcher are injected, so tests need no network.
"""

import urllib.request

import nanonode

DEFAULT_NODE = "https://rpc.nano.to"
GOSUDA_README = ("https://raw.githubusercontent.com/gosuda/x402-facilitator/"
                 "main/README.md")
GOSUDA_REPO = "https://github.com/gosuda/x402-facilitator"
#: Well-formed and known to no node: a node that answers "not found" to it is up.
PROBE_BLOCK = "0" * 63 + "1"
TIMEOUT = 10.0

#: Sentences the README must contain before we say the facilitator holds nothing.
NON_CUSTODIAL_EVIDENCE = ("payer broadcasts its own signed send block",
                          "read-only on the chain")


def fetch_text(url, timeout=TIMEOUT):
    request = urllib.request.Request(url, headers={"User-Agent": "dual-rail/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def nano_section(readme):
    """The text of the README's `### Nano` section, or None."""
    lines = readme.splitlines()
    for start, line in enumerate(lines):
        if line.strip() == "### Nano":
            body = []
            for following in lines[start + 1:]:
                if following.startswith("#"):
                    break
                body.append(following)
            return "\n".join(body)
    return None


def table_marks_exact_nano(readme):
    """True if the schemes x networks table ticks `exact` under `nano:*`."""
    column = None
    for line in readme.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if column is None:
            if "`nano:*`" in cells:
                column = cells.index("`nano:*`")
            continue
        if cells and cells[0] == "`exact`":
            return len(cells) > column and "\u2705" in cells[column]
    return False


def check_self(node, node_url):
    reachable, detail = False, ""
    try:
        node.block_info(PROBE_BLOCK)
        reachable = True
        detail = "block_info answered"
    except nanonode.NodeError as exc:
        detail = exc.message
    except Exception as exc:                            # never a fake pass
        detail = "could not reach %s: %s" % (nanonode.host_of(node_url), exc)
    return {"name": "self-verification (no facilitator, no custodian)",
            "kind": "self", "node": node_url, "reachable": reachable,
            "confirmed_live": reachable, "custodial": False,
            "how": "block_info on the payment's block hash, the read verify.py "
                   "makes; the payer broadcast the block, so nothing is held",
            "detail": detail}


def check_gosuda(fetch=fetch_text):
    out = {"name": "gosuda/x402-facilitator", "kind": "facilitator",
           "url": GOSUDA_REPO, "source": GOSUDA_README, "reachable": False,
           "what": "open-source facilitator you run yourself; this checks its "
                   "published README, not a hosted instance",
           "nano_section_found": False, "network": None, "scheme": None,
           "custodial": "unknown", "confirmed_live": False, "detail": ""}
    try:
        readme = fetch(GOSUDA_README)
    except Exception as exc:
        out["detail"] = "README not reachable: %s" % exc
        return out
    out["reachable"] = True
    section = nano_section(readme)
    if section is None:
        out["detail"] = "README has no '### Nano' section"
        return out
    out["nano_section_found"] = True
    flat = " ".join(section.split())
    out["network"] = "nano:mainnet" if "nano:mainnet" in flat else None
    out["scheme"] = "exact" if table_marks_exact_nano(readme) else None
    if all(phrase in flat for phrase in NON_CUSTODIAL_EVIDENCE):
        out["custodial"] = False
    out["confirmed_live"] = (out["network"] == "nano:mainnet"
                             and out["scheme"] == "exact")
    out["detail"] = ("README: the payer broadcasts its own signed send block; "
                     "the facilitator only reads it" if out["custodial"] is False
                     else "custody not stated in the README's Nano section")
    return out


def report(node=None, node_url=DEFAULT_NODE, fetch=fetch_text):
    if node is None:
        node = nanonode.HttpNanoNode(node_url, timeout=TIMEOUT)
    verifiers = [check_self(node, node_url), check_gosuda(fetch)]
    return {"network": "nano:mainnet", "verifiers": verifiers,
            "confirmed_live": sum(1 for v in verifiers if v["confirmed_live"])}
