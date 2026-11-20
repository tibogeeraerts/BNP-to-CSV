"""Turn a RawTransaction into an anonymised output Row.

Public API:
    to_row(raw)         -> Row
    clean_message(text) -> str

Only the seller name and the (cleaned) free-text payment message survive.
Card numbers, IBANs, BICs, addresses and bank references are dropped.
"""
from __future__ import annotations

import re

from bnp.models import RawTransaction, Row

# --- Patterns ---------------------------------------------------------------

# Masked or full card number: "1111 22XX XXXX 3333" (also without spaces).
CARD_RE = re.compile(r"\b\d{4} ?[\dX]{2}[\dX]{2} ?[\dX]{4} ?\d{4}\b")

# IBAN anywhere in a string: "BE12 3456 7890 1234", "DE89 3704 0044 0532 0130 00".
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")

# A counterparty line that is just an IBAN, optionally followed by a currency.
IBAN_LINE_RE = re.compile(r"^[A-Z]{2}\d{2}(?: ?[A-Z0-9]{1,4}){2,8}(?: [A-Z]{3})?$")

# BIC: 4 bank letters, 2 country letters, 2 location chars, optional 3 branch chars.
BIC_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?$")

# ISO 3166-1 alpha-2 country codes, used to tell a BIC from an 8/11-letter
# all-caps company name (e.g. "DELHAIZE" has "IZ", which is no country).
_COUNTRIES = set(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL "
    "BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV "
    "CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD "
    "GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM "
    "IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK "
    "LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW "
    "MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR "
    "PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS "
    "ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY "
    "UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW".split()
)

# "Nummer 1111 22XX XXXX 3333" line in card payments / cash withdrawals.
CARD_NUMBER_LINE_RE = re.compile(r"^Nummer\b.*" + CARD_RE.pattern)

P2P_RE = re.compile(r"^(.*?)\s*\bP2P MOBIL\b")

MESSAGE_RE = re.compile(r"^Mededeling\s*:\s*(.*)$", re.IGNORECASE)

# Lines that end a (multi-line) message: known labels or any "Word(s) :" label.
_KNOWN_LABELS = ("Bankreferentie", "Referte", "Mandaat", "Uitvoeringsdatum", "Behandelingskosten")
_GENERIC_LABEL_RE = re.compile(r"^[A-Za-zÀ-ÿ][\wÀ-ÿ.'-]*(?: [\wÀ-ÿ.'-]+){0,3}\s?:(?:\s|$)")

_NO_MESSAGE = {"geen mededeling", "andere betaling"}

# Belgian structured reference: +++123/4567/89012+++ or ***123/4567/89012***
STRUCTURED_REF_RE = re.compile(r"(?:\+{3}|\*{3})\s*\d{3}\s*/\s*\d{4}\s*/\s*\d{5}\s*(?:\+{3}|\*{3})")
# "/A/ 9ZZ1234-05-0009999" style references.
A_REF_RE = re.compile(r"/A/\s*\S*")
_DIGITS_SEPS_RE = re.compile(r"^[\d\-/.]+$")
_PUNCT_ONLY_RE = re.compile(r"^[\W_]+$")


# --- Helpers ----------------------------------------------------------------

def _is_iban_line(line: str) -> bool:
    return bool(IBAN_LINE_RE.match(line.strip()))


def _is_bic(line: str) -> bool:
    s = line.strip()
    return bool(BIC_RE.match(s)) and s[4:6] in _COUNTRIES


def _strip_ibans(text: str) -> str:
    # Only treat a match as an IBAN when it is digit-heavy, so names like
    # "AB12 SHOP NAME" are not mangled.
    return IBAN_RE.sub(lambda m: " " if sum(c.isdigit() for c in m.group()) >= 10 else m.group(), text)


def _scrub(text: str) -> str:
    """Remove any card number / IBAN fragments and tidy whitespace."""
    text = CARD_RE.sub(" ", text)
    text = _strip_ibans(text)
    return " ".join(text.split())


def _is_label(line: str) -> bool:
    s = line.strip()
    return s.startswith(_KNOWN_LABELS) or bool(_GENERIC_LABEL_RE.match(s))


# --- Seller -----------------------------------------------------------------

def _seller(raw: RawTransaction) -> str:
    desc = [l.strip() for l in raw.description_lines]

    # Deposit onto the card account ("Storting van" / "Some Payer" / city / ...):
    # the payer is the line right after the type (a "Nummer <card>" line follows later).
    if desc and desc[0].startswith("Storting van") and len(desc) > 1 and not _is_label(desc[1]):
        name = _scrub(desc[1])
        if name:
            return name

    # Card payment / cash withdrawal: merchant on the line after "Nummer <card>".
    for i, line in enumerate(desc):
        if CARD_NUMBER_LINE_RE.match(line) and i + 1 < len(desc):
            name = _scrub(desc[i + 1])
            if name:
                return name

    # P2P mobile payment: "Jan P2P MOBIL 30-10-2026".
    for line in desc:
        m = P2P_RE.match(line)
        if m:
            name = _scrub(m.group(1))
            if name:
                return name

    # Card number inline on the type line, e.g. a refund
    # "Terugbetaling met debetkaart 1111 22XX XXXX 3333" / "SOME SHOP CITY": merchant follows.
    if desc and CARD_RE.search(desc[0]) and len(desc) > 1 and not _is_label(desc[1]):
        name = _scrub(desc[1])
        if name:
            return name

    # Transfers / direct debits / Wero: first counterparty line that is no IBAN/BIC.
    cp = [l.strip() for l in raw.counterparty_lines if l.strip()]
    for line in cp:
        if _is_iban_line(line) or _is_bic(line):
            continue
        name = _scrub(line)
        if name:
            return name
    # A BIC-shaped all-caps name (e.g. "DELHAIZE") as the only line of a block
    # without any IBAN is a creditor name, not a BIC: a real BIC always comes
    # together with an IBAN on these statements.
    if len(cp) == 1 and not cp[0].endswith("XXX"):
        name = _scrub(cp[0])
        if name:
            return name

    # Fallback: payment type with any card number removed.
    return _scrub(desc[0]) if desc else ""


# --- Description ------------------------------------------------------------

def _message(raw: RawTransaction) -> str:
    lines = [l.strip() for l in raw.description_lines]
    for i, line in enumerate(lines):
        m = MESSAGE_RE.match(line)
        if not m:
            continue
        parts = [m.group(1).strip()]
        for cont in lines[i + 1:]:
            if not cont or _is_label(cont) or MESSAGE_RE.match(cont):
                break
            parts.append(cont)
        text = " ".join(p for p in parts if p)
        if text.strip().lower() in _NO_MESSAGE:
            return ""
        return text
    return ""


def clean_message(text: str) -> str:
    """Strip reference codes and other noise from a payment message."""
    text = STRUCTURED_REF_RE.sub(" ", text)
    text = A_REF_RE.sub(" ", text)
    text = CARD_RE.sub(" ", text)
    text = _strip_ibans(text)

    kept: list[str] = []
    for tok in text.split():
        if len(tok) >= 6:
            has_alpha = any(c.isalpha() for c in tok)
            has_digit = any(c.isdigit() for c in tok)
            if has_alpha and has_digit:
                continue
            if has_digit and _DIGITS_SEPS_RE.match(tok):
                continue
        kept.append(tok)

    # Drop dangling punctuation-only tokens at the edges ("PIZZA -" -> "PIZZA").
    while kept and _PUNCT_ONLY_RE.match(kept[0]):
        kept.pop(0)
    while kept and _PUNCT_ONLY_RE.match(kept[-1]):
        kept.pop()

    result = " ".join(kept)
    if result.lower() in _NO_MESSAGE:
        return ""
    return result


# --- Public -----------------------------------------------------------------

def to_row(raw: RawTransaction) -> Row:
    return Row(
        date=raw.booking_date,
        seller=_seller(raw),
        description=clean_message(_message(raw)),
        amount=raw.amount,
    )
