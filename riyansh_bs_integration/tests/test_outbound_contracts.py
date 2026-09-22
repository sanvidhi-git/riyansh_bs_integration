import unittest

from riyansh_bs_integration.core.outbound import classify_response, retry_delay


class TestOutboundContracts(unittest.TestCase):
    def test_response_classification(self):
        self.assertEqual(classify_response(200), "delivered")
        self.assertEqual(classify_response(429), "retry")
        self.assertEqual(classify_response(503), "retry")
        self.assertEqual(classify_response(400), "failed")
        self.assertEqual(classify_response(401), "failed")

    def test_retry_delay_is_bounded(self):
        self.assertEqual([retry_delay(i) for i in range(1, 7)], [60, 300, 900, 3600, 3600, 3600])


if __name__ == "__main__":
    unittest.main()
