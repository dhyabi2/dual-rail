"""An in-memory Nano node, so no test here needs a node or a socket.

`settle` puts a confirmed send block in it; `unconfirmed` puts one the
node has seen but not confirmed. `DeadNode` raises on every call, which
is how the fail-open constraint is tested: every check that matters is
run with the node dead and the assertion is that nothing 5xx'd.
"""

import nanonode


class FakeNode(nanonode.NanoNode):
    def __init__(self):
        self.blocks = {}
        self.calls = []

    def settle(self, block_hash, destination, amount_raw, confirmed=True):
        self.blocks[block_hash.upper()] = {
            "confirmed": bool(confirmed), "destination": destination,
            "amount_raw": int(amount_raw), "subtype": "send",
        }
        return block_hash.upper()

    def unconfirmed(self, block_hash, destination, amount_raw):
        return self.settle(block_hash, destination, amount_raw, confirmed=False)

    def block_info(self, block_hash: str) -> dict:
        self.calls.append(block_hash.upper())
        return dict(self.blocks.get(block_hash.upper(), {}))


class DeadNode(nanonode.NanoNode):
    """Every call raises, the way an unreachable node does."""

    def __init__(self, reason="node_unreachable"):
        self.reason = reason
        self.calls = []

    def block_info(self, block_hash: str) -> dict:
        self.calls.append(block_hash)
        raise nanonode.NodeError(
            self.reason, "could not reach the Nano node at node.example: timed out")
