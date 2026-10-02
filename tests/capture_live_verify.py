#!/usr/bin/env python3
"""Regenerate tests/verify-against-a-live-endpoint.txt.

    $ python3 tests/capture_live_verify.py > tests/verify-against-a-live-endpoint.txt

Stands a real HTTP server up on loopback with the adapter mounted in front of
a host that has never heard of Nano, then runs the real CLI against it. The
loopback URL is rewritten to https://example.dev/report in the output so the
transcript reads as it will against a customer's endpoint; see the header of
the captured file for exactly what that does and does not prove.
"""

import os
import subprocess
import sys
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import adapter                                                    # noqa: E402
import fakenode                                                   # noqa: E402
import testhost                                                   # noqa: E402

PAY_TO = "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"
BLOCK_ONE = "9F2C" + "0" * 60
BLOCK_TWO = "A17D" + "0" * 60
PUBLIC = "https://example.dev/report"


class Quiet(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        return


def main() -> int:
    node = fakenode.FakeNode()
    node.settle(BLOCK_ONE, PAY_TO, 10 ** 26)
    node.settle(BLOCK_TWO, PAY_TO, 10 ** 26)
    gate, protected = testhost.make_host()
    app = adapter.dual_rail(payTo=PAY_TO, amountXno="0.0001",
                            node=node).wsgi(protected, gate=gate)

    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    url = "http://127.0.0.1:%d/report" % server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for args in (["verify", url, "--payment", BLOCK_ONE],
                     ["verify", url, "--payment", BLOCK_TWO, "--json"]):
            result = subprocess.run([sys.executable, os.path.join(HERE, "cli.py")] + args,
                                    capture_output=True, text=True, timeout=120)
            shown = " ".join(a.replace(url, PUBLIC) for a in args)
            print("$ dual-rail %s" % shown)
            print(result.stdout.replace(url, PUBLIC))
            print("exit=%d" % result.returncode)
            print("=" * 70)
            if result.returncode != 0:
                return result.returncode
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
