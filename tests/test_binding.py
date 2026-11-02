"""nano-block-sig/1 binding and the persistent replay store.

The threat these laws pin: a confirmed send block is public, so a payment proof that is
only the block hash can be presented by anyone who saw it. With a binding secret the
presenter must also sign, with the PAYING account's key, a message naming the block, the
seller's nonce, the resource and the payee.
"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import binding
import ed25519_blake2b as ed
import fakenode
import nanoaddr
import store
import testhost
import verify as _verify
from test_dual_rail import AMOUNT_RAW, BLOCK, PAY_TO, build, request

SECRET = b"0123456789abcdef-test-secret-only"
PAYER_PRIVATE = bytes.fromhex("11" * 32)
PAYER_PUBLIC = ed.public_key_from_private(PAYER_PRIVATE)
PAYER = nanoaddr.encode(PAYER_PUBLIC)
OTHER_PRIVATE = bytes.fromhex("22" * 32)
RESOURCE = "/report"

# Pinned across repos: nano-wallet-xno's pay402 must produce exactly this signature.
VECTOR_NONCE = "7fffffff.00112233445566778899aabbccddeeff"
VECTOR_SIGNATURE = ("4FD7E244EB8A7C4ABB8B1E8AF1A232B02BB696E28729048F89AB825883C479DA"
                    "0B166E755AE8DF2F94F85A3C962E28254629F50553C9D54B633999511FC52E05")


def sign(private, block, nonce, resource=RESOURCE, pay_to=PAY_TO):
    pub = ed.public_key_from_private(private)
    return ed.sign(binding.message_for(block, nonce, resource, pay_to), private, pub).hex().upper()


def header(block, nonce, signature, as_binding=True):
    inner = {"blockHash": block}
    if as_binding:
        inner["binding"] = {"nonce": nonce, "signature": signature}
    return json.dumps({"scheme": "exact", "network": "nano:mainnet", "payload": inner})


def paid_node(block=BLOCK, account=PAYER):
    node = fakenode.FakeNode()
    node.settle(block, PAY_TO, AMOUNT_RAW, account=account)
    return node


def mount(node, db=None, **kw):
    gate, protected = testhost.make_host()
    rail = build(node=node, bindingSecret=SECRET, replayDb=db, **kw)
    return rail, rail.wsgi(protected, gate=gate)


def nonce_from_402(app, path=RESOURCE):
    status, _, body = request(app, path=path)
    assert status.startswith("402"), status
    entry = json.loads(body)["accepts"][-1]
    return entry["extra"]["binding"]


class Binding(unittest.TestCase):
    def test_default_install_is_unchanged(self):
        gate, protected = testhost.make_host()
        app = build(node=paid_node()).wsgi(protected, gate=gate)
        _, _, body = request(app)
        self.assertNotIn("binding", json.loads(body)["accepts"][-1]["extra"])

    def test_the_402_carries_a_nonce_for_this_resource(self):
        _, app = mount(paid_node())
        info = nonce_from_402(app)
        self.assertEqual(info["scheme"], "nano-block-sig/1")
        self.assertEqual(info["resource"], RESOURCE)
        binding.check_nonce(SECRET, info["nonce"], RESOURCE, PAY_TO)

    def test_payer_with_binding_is_served(self):
        _, app = mount(paid_node())
        nonce = nonce_from_402(app)["nonce"]
        status, _, _ = request(app, {"X-PAYMENT": header(BLOCK, nonce, sign(PAYER_PRIVATE, BLOCK, nonce))})
        self.assertTrue(status.startswith("200"), status)

    def test_a_watcher_who_only_has_the_block_hash_is_refused(self):
        _, app = mount(paid_node())
        nonce = nonce_from_402(app)["nonce"]
        for hdr in (header(BLOCK, nonce, "", as_binding=False),
                    header(BLOCK, nonce, sign(OTHER_PRIVATE, BLOCK, nonce))):
            status, _, _ = request(app, {"X-PAYMENT": hdr})
            self.assertTrue(status.startswith("402"), status)
        # and the real payer is still served afterwards: the thief did not burn the block
        status, _, _ = request(app, {"X-PAYMENT": header(BLOCK, nonce, sign(PAYER_PRIVATE, BLOCK, nonce))})
        self.assertTrue(status.startswith("200"), status)

    def test_a_signature_for_one_resource_is_useless_on_another(self):
        _, app = mount(paid_node())
        nonce = nonce_from_402(app, "/report")["nonce"]
        sig = sign(PAYER_PRIVATE, BLOCK, nonce, "/report")
        status, _, _ = request(app, {"X-PAYMENT": header(BLOCK, nonce, sig)}, path="/other")
        self.assertTrue(status.startswith("402"), status)

    def test_nonce_from_another_seller_or_payee_is_refused(self):
        _, app = mount(paid_node())
        forged = binding.issue_nonce(b"a different secret, 16+ bytes", RESOURCE, PAY_TO)
        status, _, _ = request(app, {"X-PAYMENT": header(BLOCK, forged, sign(PAYER_PRIVATE, BLOCK, forged))})
        self.assertTrue(status.startswith("402"), status)
        with self.assertRaises(binding.BindingError):
            binding.check_nonce(SECRET, binding.issue_nonce(SECRET, RESOURCE, PAY_TO),
                                RESOURCE, nanoaddr.encode(bytes(31) + b"\x01"))

    def test_expired_nonce_is_refused(self):
        _, app = mount(paid_node())
        old = binding.issue_nonce(SECRET, RESOURCE, PAY_TO, now=time.time() - 7200)
        status, _, _ = request(app, {"X-PAYMENT": header(BLOCK, old, sign(PAYER_PRIVATE, BLOCK, old))})
        self.assertTrue(status.startswith("402"), status)

    def test_tampered_signature_bytes_never_raise(self):
        _, app = mount(paid_node())
        nonce = nonce_from_402(app)["nonce"]
        for sig in ("", "zz", "00" * 64, "FF" * 64, sign(PAYER_PRIVATE, BLOCK, nonce)[:-2]):
            status, _, _ = request(app, {"X-PAYMENT": header(BLOCK, nonce, sig)})
            self.assertTrue(status.startswith("402"), (sig, status))

    def test_weak_secret_refused_at_construction(self):
        with self.assertRaises(Exception) as caught:
            build(bindingSecret="short")
        self.assertEqual(caught.exception.reason, "weak_binding_secret")

    def test_cross_repo_vector(self):
        sig = sign(PAYER_PRIVATE, "AB" * 32, VECTOR_NONCE, "/report", PAY_TO)
        self.assertEqual(sig, VECTOR_SIGNATURE)


class PersistentReplay(unittest.TestCase):
    def test_a_spent_block_stays_spent_after_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "spent.db")
            _, app = mount(paid_node(), db)
            nonce = nonce_from_402(app)["nonce"]
            hdr = header(BLOCK, nonce, sign(PAYER_PRIVATE, BLOCK, nonce))
            self.assertTrue(request(app, {"X-PAYMENT": hdr})[0].startswith("200"))
            self.assertTrue(request(app, {"X-PAYMENT": hdr})[0].startswith("402"))
            # a brand-new process: new adapter, new connection, same file
            _, app2 = mount(paid_node(), db)
            nonce2 = nonce_from_402(app2)["nonce"]
            hdr2 = header(BLOCK, nonce2, sign(PAYER_PRIVATE, BLOCK, nonce2))
            self.assertTrue(request(app2, {"X-PAYMENT": hdr2})[0].startswith("402"))
            self.assertEqual(oct(os.stat(db).st_mode & 0o777), oct(0o600))

    def test_the_same_block_cannot_buy_a_second_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, app = mount(paid_node(), os.path.join(tmp, "s.db"))
            n1 = nonce_from_402(app, "/a")["nonce"]
            n2 = nonce_from_402(app, "/b")["nonce"]
            self.assertTrue(request(app, {"X-PAYMENT": header(BLOCK, n1, sign(PAYER_PRIVATE, BLOCK, n1, "/a"))},
                                    path="/a")[0].startswith("200"))
            self.assertTrue(request(app, {"X-PAYMENT": header(BLOCK, n2, sign(PAYER_PRIVATE, BLOCK, n2, "/b"))},
                                    path="/b")[0].startswith("402"))

    def test_concurrent_claims_have_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "s.db")
            guards = [store.SqliteReplayGuard(db) for _ in range(4)]   # four "processes"
            wins = []

            def go(g):
                wins.append(g.claim("/r", BLOCK))

            threads = [threading.Thread(target=go, args=(guards[i % 4],)) for i in range(32)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(sum(wins), 1)

    def test_old_in_memory_guard_still_works(self):
        guard = _verify.ReplayGuard()
        self.assertFalse(guard.seen("/r", BLOCK))
        guard.remember("/r", BLOCK)
        self.assertTrue(guard.seen("/r", BLOCK))


if __name__ == "__main__":
    unittest.main()
