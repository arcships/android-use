"""Preserve printed spans; normalize signs/separators without inventing currency."""

import re
import unicodedata
from decimal import Decimal

SIGNS = r"+＋\-−－﹣"
CURRENCY = r"HK\$|US\$|RMB|CNY|USD|HKD|EUR|GBP|JPY|CAD|AUD|[¥￥$€£]"
MONEY = re.compile(
    rf"(?<![\d.,，．{SIGNS}])(?:[{SIGNS}]\s*)?(?:(?:{CURRENCY})\s*)?"
    rf"(?:[{SIGNS}]\s*)?(?:\d{{1,3}}(?:[,，]\d{{3}})+|\d+)(?:[.．]\d{{1,2}})?"
    rf"(?:\s*(?:元|CNY|USD|HKD|EUR|GBP|JPY|CAD|AUD))?(?![\d.,，．])",
    re.IGNORECASE,
)


def parse_money(span, declared=None):
    normalized = unicodedata.normalize("NFKC", span).replace("−", "-")
    signs = re.findall(r"[+\-]", normalized)
    if len(signs) > 1:
        raise ValueError("Ambiguous amount sign")
    currency_tokens = re.findall(CURRENCY + r"|元", normalized, re.IGNORECASE)
    currencies = set()
    ambiguous = []
    for token in currency_tokens:
        token = token.upper()
        if token in {"RMB", "CNY", "元"}:
            currencies.add("CNY")
        elif token in {"US$", "USD"}:
            currencies.add("USD")
        elif token == "HK$":
            currencies.add("HKD")
        elif token in {"€", "£"}:
            currencies.add({"€": "EUR", "£": "GBP"}[token])
        elif token in {"¥", "$"}:
            ambiguous.append(token)
        else:
            currencies.add(token)
    if len(currencies) > 1 or (declared and currencies and declared not in currencies):
        raise ValueError("Conflicting currency")
    currency = next(iter(currencies), declared)
    for symbol in ambiguous:
        allowed = {"CNY", "JPY"} if symbol == "¥" else {"USD", "HKD", "CAD", "AUD"}
        if currency is not None and currency not in allowed:
            raise ValueError("Currency symbol conflicts with declaration")
    # NFKC can normalize fullwidth digits and comma, preserving the original span.
    value = re.sub(r"[^\d.\-+]", "", normalized)
    return {
        "decimal": str(Decimal(value)),
        "currency": currency,
        "currency_source": "printed" if currencies else "declared" if declared else "unknown",
    }
