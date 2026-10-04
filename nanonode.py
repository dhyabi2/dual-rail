"""The only file in this package that reaches the network.

One RPC call: `block_info`, to ask whether a payment block exists, is
confirmed, where it went and how much. Nothing else is needed to verify
an exact-amount payment, and nothing else is asked for.

Constraint 2 of the spec lives here in spirit: every failure is a
`NodeError`, and the adapter's answer to a `NodeError` is always to fall
through to the service's existing rails. Our outage must never cost them
a USDC sale.
"""

import json
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 3.0


class NodeError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def host_of(url: str) -> str:
    """A node URL's host, with any credentials stripped.

    Node URLs are routinely https://user:key@node.example, and this
    string ends up in logs.
    """
    try:
        rest = url.split("://", 1)[1] if "://" in url else url
        return rest.split("/", 1)[0].rsplit("@", 1)[-1] or "the configured node"
    except Exception:                                   # pragma: no cover - defensive
        return "the configured node"


def read_block_info(answer: dict) -> dict:
    """What one `block_info` reply means, with no socket in the way.

    Separate from the HTTP call so the shapes a real node answers can be
    asserted against recorded replies rather than against a mock of our own
    assumptions - the assumption is what was wrong here.
    """
    contents = answer.get("contents") or {}
    # A state block names its kind in `subtype`; a legacy block has no
    # `subtype` at all and names its kind in `contents.type` (measured against
    # mainnet: a 2019 send answers subtype=None, contents.type="send",
    # contents.destination set and contents.link_as_account absent). Either way
    # the kind is read off the block and never defaulted, so a block whose kind
    # we cannot read is not a send.
    subtype = answer.get("subtype") or contents.get("type")
    destination = None
    if subtype == "send":
        # The payee, from wherever this block spells it. Never `block_account`,
        # which is the account the block BELONGS to - on a send that is the
        # payer, and on a receive it is us.
        destination = contents.get("link_as_account") or contents.get("destination")
    return {
        "confirmed": str(answer.get("confirmed", "false")).lower() == "true",
        "destination": destination,
        "amount_raw": int(answer["amount"]) if answer.get("amount") else 0,
        "subtype": subtype,
    }


class NanoNode:
    def block_info(self, block_hash: str) -> dict:
        """`{"confirmed","destination","amount_raw","subtype"}`, or {} if unknown.

        `subtype` is the block's own kind, read off the block. `destination`
        is filled in only for a send, because only a send has one: for any
        other kind it is `None`, and a caller must not read a payee out of a
        block that never paid anybody.
        """
        raise NotImplementedError


class HttpNanoNode(NanoNode):
    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT):
        self.url = url
        self.timeout = timeout

    def block_info(self, block_hash: str) -> dict:
        payload = json.dumps({"action": "block_info", "json_block": "true",
                              "hash": block_hash}).encode("utf-8")
        request = urllib.request.Request(self.url, data=payload,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "dual-rail/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                answer = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise NodeError("node_unreachable",
                            "could not reach the Nano node at %s: %s"
                            % (host_of(self.url), exc.reason)) from None
        except (ValueError, OSError) as exc:
            raise NodeError("node_unreachable",
                            "the Nano node at %s answered something unusable: %s"
                            % (host_of(self.url), exc)) from None
        if "error" in answer:
            if answer["error"] in ("Block not found", "Invalid block hash"):
                return {}
            raise NodeError("node_error", "the Nano node at %s returned: %s"
                            % (host_of(self.url), answer["error"]))
        return read_block_info(answer)
