import pytest
from test_structured_jev import PACKAGE, XML, policy_for

from android_use.fields import read_fields
from android_use.money import MONEY, parse_money


@pytest.mark.parametrize(
    "text,value,currency",
    [
        ("－¥6", "-6", "CNY"),
        ("﹣￥6", "-6", "CNY"),
        ("−¥6", "-6", "CNY"),
        ("¥1,299.00", "1299.00", "CNY"),
        ("￥１，２９９.００", "1299.00", "CNY"),
        ("￥１，２９９．００", "1299.00", "CNY"),
        ("US$1,299.00", "1299.00", "USD"),
        ("HK$29.50", "29.50", "HKD"),
        ("12.50元", "12.50", "CNY"),
        ("¥-6", "-6", "CNY"),
        ("USD 12.50", "12.50", "USD"),
        ("€1,299.00", "1299.00", "EUR"),
    ],
)
def test_printed_money_sign_currency_and_grouping(text, value, currency):
    spans = [m.group() for m in MONEY.finditer(text)]
    assert spans == [text]
    assert parse_money(spans[0], currency)["decimal"] == value
    assert parse_money(spans[0], currency)["currency"] == currency


@pytest.mark.parametrize("text", ["¥1,29.00", "¥12.345", "¥1.299,00"])
def test_malformed_or_unsupported_grouping_never_becomes_partial_amount(text):
    assert not list(MONEY.finditer(text))


def test_currency_conflict_and_unknown_symbol():
    with pytest.raises(ValueError):
        parse_money("USD 10", "CNY")
    with pytest.raises(ValueError):
        parse_money("$10", "CNY")
    assert parse_money("¥10")["currency"] is None


@pytest.mark.parametrize(
    "evidence,currency,subtract,passed,reason",
    [
        ("商品 ¥1,299.00\n优惠 －¥6\n合计 ¥1,293.00", "CNY", False, True, "matched"),
        ("商品 ¥1,299.00\n优惠 －¥6\n合计 ¥1,293.00", "CNY", True, False, "negative_subtrahend"),
        ("商品 ¥12\n优惠 ¥3\n合计 ¥15", None, False, False, "currency_unresolved_or_mixed"),
        ("商品 USD12\n优惠 EUR3\n合计 USD15", None, False, False, "currency_unresolved_or_mixed"),
    ],
)
def test_sum_requires_resolved_currency_and_explicit_discount_sign(
    rig, evidence, currency, subtract, passed, reason
):
    runtime, backend = rig
    runtime._connect()
    backend.xml = XML
    names = ["items", "discount", "total"]
    amounts = [next(MONEY.finditer(line)).group() for line in evidence.splitlines()]
    selected = dict(zip(names, amounts, strict=True))
    result = read_fields(
        runtime,
        {
            "package": PACKAGE,
            "evidence_text": evidence,
            "currency": currency,
            "fields": [{"name": n, "instructions": n} for n in names],
            "sum_check": {
                "total": "total",
                "add": ["items"] if subtract else ["items", "discount"],
                "subtract": ["discount"] if subtract else [],
            },
        },
        policy_for(lambda name, opts: next(k for k, v in opts.items() if v == selected[name])),
    )
    assert result["checks"][0]["passed"] is passed
    assert result["checks"][0]["reason"] == reason
    assert result["fields"]["discount"]["value"] == amounts[1]
    assert result["all_fields_accepted"] is passed and not backend.actions
