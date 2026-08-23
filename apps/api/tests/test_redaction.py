from app.redaction import redact


def test_email_masked():
    out = redact("reach me at alice@example.com please")
    assert "[EMAIL]" in out
    assert "alice@example.com" not in out


def test_ssn_masked():
    out = redact("my ssn is 123-45-6789")
    assert "[SSN]" in out
    assert "123-45-6789" not in out


def test_phone_masked():
    out = redact("call (415) 555-0132 tomorrow")
    assert "[PHONE]" in out
    assert "555-0132" not in out


def test_valid_card_masked():
    out = redact("card 4111 1111 1111 1111 on file")
    assert "[CARD]" in out
    assert "4111" not in out


def test_non_luhn_number_not_treated_as_card():
    # 16 digits but not a valid card number: must NOT be masked as a card
    out = redact("order number 1234 5678 9012 3456")
    assert "[CARD]" not in out


def test_structured_secrets_masked():
    caught = {
        "aws key": "AKIAIOSFODNN7EXAMPLE",
        "openai/anthropic": "sk-ant-api03-abc123def456ghi789",
        "github": "ghp_" + "a" * 36,
        "slack": "xoxb-123456789012-abcdefghijkl",
        "google": "AIza" + "b" * 35,
        "bearer": "Authorization: Bearer abcdef0123456789ghijkl",
    }
    for label, secret in caught.items():
        out = redact(f"the value is {secret}")
        assert "[SECRET]" in out, label
        assert secret not in out, label


def test_private_key_block_masked():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIBmabc\n-----END RSA PRIVATE KEY-----"
    out = redact(f"here it is:\n{key}")
    assert "[SECRET]" in out
    assert "MIIBmabc" not in out


# Documented residuals (RR-W2-2): these are expected to slip through. Secret
# detection is signature-based, so anything without a known shape is not caught.


def test_residual_obfuscated_email_slips_through():
    out = redact("reach me at alice [at] example [dot] com")
    assert "[EMAIL]" not in out  # known gap, documented


def test_residual_international_phone_slips_through():
    out = redact("ring +44 20 7946 0958")
    assert "[PHONE]" not in out  # known gap, documented


def test_residual_freeform_password_slips_through():
    # A free-form password has no signature to match without carpet-bombing prose.
    out = redact("my password is Hunter2!Correct-Horse")
    assert "[SECRET]" not in out


def test_residual_unknown_token_format_slips_through():
    # The AWS *secret* key (no prefix) and custom tokens are not catchable by shape.
    out = redact("secret is wJalrXUtnFEMIK7MDENGbPxRfiCYzEXAMPLEKEY")
    assert "[SECRET]" not in out
