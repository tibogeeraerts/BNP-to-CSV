"""Tests for bnp.clean. All names, IBANs, card numbers and messages are invented."""
import re
from datetime import date
from decimal import Decimal

import pytest

from bnp.clean import clean_message, to_row
from bnp.models import RawTransaction

D = date(2026, 10, 31)

IBAN_CHECK = re.compile(r"\b[A-Z]{2}\d{2}(?: ?\d{4}){3}")
CARD_CHECK = re.compile(r"\d{4} \d{2}XX XXXX \d{4}")
BIC_CHECK = re.compile(r"\b(?:GEBABEBB|KREDBEBBXXX|BBRUBEBB|COBADEFFXXX)\b")


def raw(desc, cp=(), amount="-10.00"):
    return RawTransaction(
        number="0001",
        booking_date=D,
        description_lines=list(desc),
        counterparty_lines=list(cp),
        amount=Decimal(amount),
    )


CASES = {
    "card": (
        raw([
            "Betaling met debetkaart",
            "Nummer 1111 22XX XXXX 3333",
            "SOME SHOP NV",
            "SOMECITY",
            "31-10-2026 13:30",
            "Bancontact",
            "Bankreferentie : 2610311111111111",
        ], amount="-23.45"),
        "SOME SHOP NV",
        "",
    ),
    "card_code_like_merchant": (
        raw([
            "Betaling met debetkaart",
            "Nummer 1111 22XX XXXX 3333",
            "SOMESTREAM*P1A2B3C4D",
            "SOMECITY",
            "Bankreferentie : 2610311111111112",
        ]),
        "SOMESTREAM*P1A2B3C4D",
        "",
    ),
    "cash_withdrawal": (
        raw([
            "Geldopneming met debetkaart",
            "Nummer 1111 22XX XXXX 3333",
            "SOMEBANK AUTOMAAT",
            "SOMECITY",
            "20-10-2026 10:00",
            "Bankreferentie : 2610201111111111",
        ], amount="-50.00"),
        "SOMEBANK AUTOMAAT",
        "",
    ),
    "p2p": (
        raw([
            "Betaling met debetkaart 1111 22XX XXXX 3333",
            "Jan P2P MOBIL 30-10-2026",
            "Bancontact",
            "Bankreferentie : 2610301111111111",
        ]),
        "Jan",
        "",
    ),
    "transfer_full": (
        raw(
            [
                "Overschrijving in euro",
                "Mededeling : Maandelijkse huur",
                "Uitvoeringsdatum : 04-10-2026",
                "Bankreferentie : 2610041111111111",
            ],
            ["BE12 3456 7890 1234 EUR", "GEBABEBB", "Jan Voorbeeld", "Voorbeeldstraat 1", "9999 DORP"],
            amount="-750.00",
        ),
        "Jan Voorbeeld",
        "Maandelijkse huur",
    ),
    "transfer_incoming_no_message": (
        raw(
            ["Overschrijving in uw voordeel", "Andere betaling", "Bankreferentie : 2610041111111112"],
            ["BE98 7654 3210 9876 EUR", "KREDBEBBXXX", "Anna Testers"],
            amount="15.00",
        ),
        "Anna Testers",
        "",
    ),
    "standing_order": (
        raw(
            [
                "Doorlopende opdracht",
                "Mededeling : Spaarpot",
                "Bankreferentie : 2610011111111111",
            ],
            ["DE82 1013 0800 1058 6089 03", "COBADEFFXXX", "Some Savings GmbH", "Musterweg 5", "10115 BERLIN"],
            amount="-100.00",
        ),
        "Some Savings GmbH",
        "Spaarpot",
    ),
    "direct_debit": (
        raw(
            [
                "Europese domiciliëring",
                "Mededeling : 1026-1126 PREMIE INDIEN VAN TOEPASSING",
                "Mandaat nummer : 12345678",
                "Referte opdrachtgever : NOTPROVIDED",
                "Bankreferentie : 2610051111111111",
            ],
            ["SOME INSURER"],
            amount="-31.00",
        ),
        "SOME INSURER",
        "PREMIE INDIEN VAN TOEPASSING",
    ),
    "direct_debit_eight_letter_name": (
        raw(["Europese domiciliëring", "Bankreferentie : 2610051111111112"], ["DELHAIZE"]),
        "DELHAIZE",
        "",
    ),
    "wero": (
        raw(
            [
                "Wero-betaling",
                "Mededeling : ABC1XYZ2QWE3RTY4UIO5PAS6 0A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D",
                "PQ A1B2C3 123456789012 SOME CONCERT NIGHT",
                "Bankreferentie : 2610121111111111",
            ],
            ["NL12 ABNA 0123 4567 89", "ABNANL2A", "Ticketshop via Example Tickets B.V."],
            amount="-45.00",
        ),
        "Ticketshop via Example Tickets B.V.",
        "PQ SOME CONCERT NIGHT",
    ),
    "multi_line_message": (
        raw(
            [
                "Overschrijving in euro",
                "Mededeling: Gym Company NV Gym 12--3456-7890123",
                "12-34567890 4W PREMIUM",
                "Referte : ABCDEF1234",
                "Bankreferentie : 2610131111111111",
            ],
            ["BE11 2222 3333 4444 EUR", "BBRUBEBB", "Gym Company NV", "Sportlaan 3"],
        ),
        "Gym Company NV",
        "Gym Company NV Gym 4W PREMIUM",
    ),
    "geen_mededeling": (
        raw(
            ["Overschrijving in euro", "Mededeling : Geen mededeling", "Bankreferentie : 2610141111111111"],
            ["BE55 6666 7777 8888 EUR", "GEBABEBB", "Tom Fictief"],
        ),
        "Tom Fictief",
        "",
    ),
    "structured_ref_only": (
        raw(
            ["Overschrijving in euro", "Mededeling : +++123/4567/89012+++", "Bankreferentie : 2610151111111111"],
            ["BE55 6666 7777 8888 EUR", "GEBABEBB", "SOME UTILITY NV"],
        ),
        "SOME UTILITY NV",
        "",
    ),
    "fallback": (
        raw(["Betaling met debetkaart 1111 22XX XXXX 3333", "Bankreferentie : 2610161111111111"]),
        "Betaling met debetkaart",
        "",
    ),
    "fallback_no_card": (
        raw(["Kosten rekening", "Bankreferentie : 2610161111111112"], amount="-2.00"),
        "Kosten rekening",
        "",
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_to_row(name):
    tx, seller, description = CASES[name]
    row = to_row(tx)
    assert row.seller == seller
    assert row.description == description
    assert row.date == tx.booking_date
    assert row.amount == tx.amount


@pytest.mark.parametrize("name", list(CASES))
def test_no_sensitive_data_leaks(name):
    row = to_row(CASES[name][0])
    for value in (row.seller, row.description):
        assert not IBAN_CHECK.search(value), value
        assert not CARD_CHECK.search(value), value
        assert not BIC_CHECK.search(value), value
        assert "Voorbeeldstraat" not in value and "Musterweg" not in value
        assert "Bankreferentie" not in value


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Maandelijkse huur", "Maandelijkse huur"),
        ("/A/ 9ZZ1234-05-0009999", ""),
        (
            "ABC1XYZ2QWE3RTY4UIO5PAS6 0A1B2C3D4E5F6A7B8C9D0E1F2A3B4C5D PQ T6DWUV "
            "201766625061 SOME CONCERT NIGHT",
            "PQ SOME CONCERT NIGHT",
        ),
        ("Gym Company NV Gym 12--3456-7890123 12-34567890 4W PREMIUM", "Gym Company NV Gym 4W PREMIUM"),
        ("1026-1126 PREMIE INDIEN VAN TOEPASSING", "PREMIE INDIEN VAN TOEPASSING"),
        ("PIZZA - 10/10/26", "PIZZA"),
        ("+++123/4567/89012+++", ""),
        ("***123/4567/89012***", ""),
        ("ABCD1EFGH2IJKL3MNOP4QRST", ""),
        ("  Etentje   met  vrienden  ", "Etentje met vrienden"),
        ("Terugbetaling BE12 3456 7890 1234", "Terugbetaling"),
        ("", ""),
    ],
)
def test_clean_message(text, expected):
    assert clean_message(text) == expected


def _raw(desc, cp=(), amount="5.00"):
    return RawTransaction(number="0001", booking_date=date(2024, 6, 1),
                          description_lines=list(desc), counterparty_lines=list(cp),
                          amount=Decimal(amount))


def test_card_refund_with_inline_card_number_uses_merchant():
    row = to_row(_raw(["Terugbetaling met debetkaart 1111 22XX XXXX 3333", "FICTIVE SHOP CITY",
                       "01-06-2024 18:09", "Bancontact", "Bankreferentie : 2406010000000000"]))
    assert row.seller == "FICTIVE SHOP CITY"
    assert row.description == ""


def test_deposit_uses_payer_not_date():
    row = to_row(_raw(["Storting van", "Fictive Festival", "Somewhere OOSTENRIJK",
                       "Op de rekening gekoppeld aan de debetkaart", "Nummer 1111 22XX XXXX 3333",
                       "01-06-2024", "Bankreferentie : 2406010000000000"]))
    assert row.seller == "Fictive Festival"


def test_mobile_payment_never_exposes_payer_iban():
    row = to_row(_raw(["Mobiele betaling", "Opdrachtgever rekening: BE00 1111 2222 3333 EUR",
                       "BIC: ABCDBE22", "Bancontact", "Bankreferentie : 2406010000000000"]))
    assert row.seller == "Mobiele betaling"
    assert "1111" not in row.seller + row.description
