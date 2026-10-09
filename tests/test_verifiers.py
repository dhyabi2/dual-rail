"""`dual-rail verifiers`: who can validate an XNO payment today.

No network: the node is `fakenode.FakeNode` (up) or `fakenode.DeadNode`
(unreachable), and the facilitator README is handed in as text. The README
fixture is the gosuda/x402-facilitator main-branch text, trimmed to the parts
the check reads.
"""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli
import fakenode
import verifiers

GOSUDA_README = """# x402-facilitator

## Supported schemes × networks

| Scheme  | `eip155:*` (EVM) | `solana:*` | `sui:*` | `tron:*` | `casper:*` | `nano:*` |
|---------|:----------------:|:----------:|:-------:|:--------:|:----------:|:--------:|
| `exact` |        ✅        |     ✅     |   \U0001f6a7    |    \U0001f6a7    |     ✅     |    ✅     |

### Casper

Casper payments are authorized by the payer and broadcast by a Casper
facilitator service.

### Nano

Nano is addressed as `nano:mainnet`. It is a feeless DAG ledger with no
smart contracts and no memo field, so the payer broadcasts its own signed
send block directly and there is no facilitator settlement to broadcast or
gas token to carry. The facilitator is read-only on the chain: it verifies
the referenced send block (in `payload.blockHash`) on at least two
independent public RPC nodes.

### Solana

The configured private key is the fee payer.
"""


def serve(text):
    def fetch(url):
        assert url == verifiers.GOSUDA_README, url
        return text
    return fetch


def offline(url):
    raise OSError("network is unreachable")


def run(node, fetch, json_out=True):
    args = cli.build_parser().parse_args(
        ["verifiers"] + (["--json"] if json_out else []))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.cmd_verifiers(args, node=node, fetch=fetch)
    return code, buffer.getvalue()


class WhoCanValidateXno(unittest.TestCase):
    def test_both_confirmed_and_neither_holds_funds(self):
        code, out = run(fakenode.FakeNode(), serve(GOSUDA_README))
        report = json.loads(out)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(report["confirmed_live"], 2)
        own, gosuda = report["verifiers"]
        self.assertEqual((own["kind"], own["reachable"], own["custodial"]),
                         ("self", True, False))
        self.assertEqual(own["node"], verifiers.DEFAULT_NODE)
        self.assertTrue(gosuda["nano_section_found"])
        self.assertEqual((gosuda["network"], gosuda["scheme"], gosuda["custodial"]),
                         ("nano:mainnet", "exact", False))

    def test_the_node_probe_is_one_block_info(self):
        node = fakenode.FakeNode()
        run(node, serve(GOSUDA_README))
        self.assertEqual(node.calls, [verifiers.PROBE_BLOCK.upper()])

    def test_a_dead_node_is_reported_unreachable_not_passed(self):
        code, out = run(fakenode.DeadNode(), serve(GOSUDA_README))
        own = json.loads(out)["verifiers"][0]
        self.assertFalse(own["reachable"])
        self.assertFalse(own["confirmed_live"])
        self.assertIn("could not reach", own["detail"])
        self.assertEqual(code, cli.EXIT_OK)     # the facilitator is still confirmed

    def test_nothing_reachable_exits_nonzero(self):
        code, out = run(fakenode.DeadNode(), offline)
        report = json.loads(out)
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertEqual(report["confirmed_live"], 0)
        gosuda = report["verifiers"][1]
        self.assertFalse(gosuda["reachable"])
        self.assertEqual(gosuda["custodial"], "unknown")

    def test_no_nano_section_is_not_a_verifier(self):
        readme = GOSUDA_README.split("### Nano")[0]
        code, out = run(fakenode.DeadNode(), serve(readme))
        gosuda = json.loads(out)["verifiers"][1]
        self.assertTrue(gosuda["reachable"])
        self.assertFalse(gosuda["nano_section_found"])
        self.assertFalse(gosuda["confirmed_live"])
        self.assertEqual(code, cli.EXIT_FAIL)

    def test_custody_is_unknown_unless_the_readme_says_so(self):
        readme = GOSUDA_README.replace("The facilitator is read-only on the chain:",
                                       "The facilitator:")
        _, out = run(fakenode.FakeNode(), serve(readme))
        self.assertEqual(json.loads(out)["verifiers"][1]["custodial"], "unknown")

    def test_human_output_names_both(self):
        code, out = run(fakenode.FakeNode(), serve(GOSUDA_README), json_out=False)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("gosuda/x402-facilitator", out)
        self.assertIn("reachable: true", out)
        self.assertIn("verifiers: 2 confirmed live", out)


if __name__ == "__main__":
    unittest.main()
