"""Phone masking/hashing and PII removal before text goes anywhere (logs, LLM)."""

import hashlib
import hmac
import re

from app.config import settings

CARD_REMOVED = "<CARD_REMOVED>"

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PASSPORT = re.compile(r"\b[A-Za-z]{1,2}\d{7,8}\b")  # e.g. EB1234567
# Digits that may be split by single spaces or dashes: 4111 1111-1111 1111, +880 1700 001234
DIGIT_RUN = re.compile(r"\+?\d(?:[ -]?\d)+")


def mask_phone(phone: str) -> str:
    return "****" + re.sub(r"\D", "", phone)[-4:]


def hash_phone(phone: str) -> str:
    # Digits only, so "+880..." and "880..." give the same hash
    digits = re.sub(r"\D", "", phone).encode()
    key = settings.phone_hash_key.get_secret_value().encode()
    return hmac.new(key, digits, hashlib.sha256).hexdigest()


def mask_pii(text: str) -> tuple[str, dict[str, str]]:
    """Returns (masked text, {"<PII_1>": original, ...}).
    Card-like numbers (13-19 digits) become <CARD_REMOVED> and are NEVER kept in the mapping.
    """
    mapping: dict[str, str] = {}

    def token(match: re.Match) -> str:
        value = match.group()
        for key, original in mapping.items():  # same value -> same token
            if original == value:
                return key
        key = f"<PII_{len(mapping) + 1}>"
        mapping[key] = value
        return key

    def digits(match: re.Match) -> str:
        count = len(re.sub(r"\D", "", match.group()))
        if count < 9:
            return match.group()  # amounts like 10000 stay readable
        if 13 <= count <= 19:
            return CARD_REMOVED  # even if it's really a phone: removing is the safe side
        return token(match)

    text = EMAIL.sub(token, text)
    text = PASSPORT.sub(token, text)
    text = DIGIT_RUN.sub(digits, text)
    return text, mapping
