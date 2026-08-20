"""Best-effort PII redaction for the audit log. Reduces PII, does not eliminate it;
the test corpus documents which formats slip through (RR-W2-2)."""

import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SSN = re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b")
_PHONE = re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_CARD_CANDIDATE = re.compile(r"\b\d(?:[ -]?\d){12,15}\b")


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


def redact(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    text = _redact_cards(text)
    text = _SSN.sub("[SSN]", text)
    text = _PHONE.sub("[PHONE]", text)
    return text
