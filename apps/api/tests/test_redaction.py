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


# Documented residuals (RR-W2-2): these evasions are expected to slip through.


def test_residual_obfuscated_email_slips_through():
    out = redact("reach me at alice [at] example [dot] com")
    assert "[EMAIL]" not in out  # known gap, documented


def test_residual_international_phone_slips_through():
    out = redact("ring +44 20 7946 0958")
    assert "[PHONE]" not in out  # known gap, documented
