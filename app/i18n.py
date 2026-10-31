"""Helpers d'internationalisation (devises, dates, formats)."""

from decimal import Decimal

from babel.dates import format_date, format_datetime
from babel.numbers import format_currency, format_decimal
from num2words import num2words

SUPPORTED_LOCALES = ["fr_FR", "fr_MA", "en_US", "ar_MA"]
SUPPORTED_CURRENCIES = ["EUR", "MAD", "USD"]

# Libellés en lettres par devise : (unité, subdivision)
_CURRENCY_WORDS = {
    "MAD": ("DIRHAMS", "CENTIMES"),
    "EUR": ("EUROS", "CENTIMES"),
    "USD": ("DOLLARS", "CENTS"),
}


def amount_in_words(amount: Decimal | float | int, currency: str = "MAD") -> str:
    """Montant en toutes lettres (français), ex. « UN MILLION VINGT MILLE DIRHAMS »."""
    amount = Decimal(str(amount))
    integer = int(amount)
    cents = int((amount - integer) * 100)
    unit, sub = _CURRENCY_WORDS.get(currency, (currency, "CENTIMES"))
    words = num2words(integer, lang="fr").upper()
    result = f"{words} {unit}"
    if cents:
        result += f" ET {num2words(cents, lang='fr').upper()} {sub}"
    return result


def money(amount: Decimal | float | int, currency: str = "EUR", locale: str = "fr_FR") -> str:
    return format_currency(Decimal(str(amount)), currency, locale=locale)


def number(value: Decimal | float | int, locale: str = "fr_FR") -> str:
    return format_decimal(Decimal(str(value)), locale=locale)


def amount(value: Decimal | float | int, locale: str = "fr_FR") -> str:
    """Montant numérique formaté à 2 décimales, style local (ex. « 475 000,00 »)."""
    return format_decimal(Decimal(str(value)), format="#,##0.00", locale=locale)


def date_fr(value, locale: str = "fr_FR", fmt: str = "long") -> str:
    if value is None:
        return ""
    return format_date(value, format=fmt, locale=locale)


def datetime_fr(value, locale: str = "fr_FR", fmt: str = "medium") -> str:
    if value is None:
        return ""
    return format_datetime(value, format=fmt, locale=locale)
