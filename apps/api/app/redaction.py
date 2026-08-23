"""Best-effort PII and secret redaction for the audit log. Reduces exposure, does
not eliminate it; the test corpus documents which formats slip through (RR-W2-2).
Secret detection is limited to known signatures on purpose: free-form passwords and
unknown token formats have no reliable pattern and are NOT caught."""

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SSN = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
_PHONE = re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_CARD_CANDIDATE = re.compile(r"\b\d(?:[ -]?\d){12,15}\b")

# Structured secrets with recognizable prefixes/shapes. High precision by design.
_SECRETS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bA(?:KIA|SIA)[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),  # OpenAI / Anthropic style
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # Google API key
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),  # JWT
]
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{12,}")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _redact_cards(text: str) -> str:
    # Luhn-validate so ordinary long numbers are not masked as cards.
    def repl(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 16 and _luhn_ok(digits):
            return "[CARD]"
        return match.group()

    return _CARD_CANDIDATE.sub(repl, text)


def _redact_secrets(text: str) -> str:
    for pattern in _SECRETS:
        text = pattern.sub("[SECRET]", text)
    return _BEARER.sub(r"\1[SECRET]", text)


def redact(text: str) -> str:
    text = _redact_secrets(text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _redact_cards(text)
    text = _SSN.sub("[SSN]", text)
    text = _PHONE.sub("[PHONE]", text)
    return text
