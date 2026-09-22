import unittest

from riyansh_bs_integration.core.errors import IntegrationError
from riyansh_bs_integration.core.logging import redact, safe_payload
from riyansh_bs_integration.core.responses import failure, success
from riyansh_bs_integration.core.validation import (
    request_fingerprint,
    validate_aadhaar,
    validate_ifsc,
    validate_pan,
)


class TestCoreContracts(unittest.TestCase):
    def test_recursive_redaction_masks_nested_secrets(self):
        value = {
            "aadhaar_number": "123412341234",
            "bank": {"account_number": "998877", "ifsc_code": "ABCD0001234"},
            "items": [{"api_secret": "do-not-log"}],
        }

        self.assertEqual(
            redact(value),
            {
                "aadhaar_number": "********1234",
                "bank": {"account_number": "**8877", "ifsc_code": "ABCD0001234"},
                "items": [{"api_secret": "[REDACTED]"}],
            },
        )

    def test_fingerprint_is_stable_across_key_order(self):
        self.assertEqual(
            request_fingerprint({"b": 2, "a": 1}),
            request_fingerprint({"a": 1, "b": 2}),
        )

    def test_json_string_is_parsed_before_redaction(self):
        self.assertEqual(
            safe_payload('{"aadhaar_number":"123412341234"}'),
            {"aadhaar_number": "********1234"},
        )

    def test_identity_validators_accept_expected_indian_formats(self):
        self.assertEqual(validate_pan("abcde1234f"), "ABCDE1234F")
        self.assertEqual(validate_aadhaar("1234 1234 1234"), "123412341234")
        self.assertEqual(validate_ifsc("abcd0001234"), "ABCD0001234")

    def test_identity_validators_reject_invalid_values(self):
        with self.assertRaises(IntegrationError):
            validate_pan("A123")
        with self.assertRaises(IntegrationError):
            validate_aadhaar("1234")
        with self.assertRaises(IntegrationError):
            validate_ifsc("INVALID")

    def test_response_envelopes_preserve_correlation_id(self):
        created = success({"name": "BS-ONB-00001"}, "Created", "COR-1")
        rejected = failure(
            IntegrationError("INVALID_PAN", "PAN is invalid", 422, field="pan_number"),
            "COR-2",
        )

        self.assertEqual(created["correlation_id"], "COR-1")
        self.assertTrue(created["success"])
        self.assertEqual(rejected["error"]["field"], "pan_number")
        self.assertFalse(rejected["success"])


if __name__ == "__main__":
    unittest.main()
