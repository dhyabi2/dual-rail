import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSellerGuideFaq(unittest.TestCase):
    def test_faq_covers_send_receive_pending_and_points_at_the_refusal(self):
        text = open(os.path.join(HERE, "docs", "x402-nano-seller-guide.md"), encoding="utf-8").read()
        sec = text.split("## 4b. FAQ", 1)[1].split("\n## ", 1)[0]
        for word in ("send", "receive", "receivable", "confirmed", "not_a_send", "link_as_account", "replay"):
            self.assertIn(word, sec)
        # the refusal it cites is real
        self.assertIn('"not_a_send"', open(os.path.join(HERE, "verify.py"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
