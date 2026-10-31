"""Types Pydantic partagés."""

from __future__ import annotations

import re
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator

# Format minimal : local@domaine.tld (pas d'espace, un seul @)
_BASIC_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email_lenient(value: str) -> str:
    """Valide une adresse email en autorisant les domaines à usage réservé.

    `email-validator` (et donc `EmailStr`) rejette les TLD réservés comme
    `.local`, `.test`, `.internal`… utilisés en dev/démo (ex. demo@libfad.local).
    On garde la validation stricte pour les domaines publics, mais on retombe
    sur un contrôle syntaxique basique pour ces cas, afin que l'API JSON accepte
    les mêmes adresses que le formulaire HTML. Lève ValueError si invalide.
    """
    value = value.strip()
    try:
        return validate_email(value, check_deliverability=False).normalized
    except EmailNotValidError:
        if _BASIC_EMAIL_RE.match(value):
            return value.lower()
        raise ValueError("Adresse email invalide")


def normalize_email(value: str) -> str:
    """Normalise un email pour comparaison/recherche (trim + minuscule).

    Ne lève pas d'erreur : utile au login où l'on compare à la valeur stockée.
    """
    return value.strip().lower()


# Type email tolérant aux domaines réservés, réutilisable dans les schémas.
Email = Annotated[str, AfterValidator(validate_email_lenient)]
