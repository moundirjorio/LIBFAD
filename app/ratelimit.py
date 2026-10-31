"""Limitation de tentatives en mémoire (anti-brute-force sur la connexion).

Simple fenêtre glissante par clé (IP + email). Suffisant pour un mono-process ;
pour du multi-instance, remplacer le stockage par Redis.
"""

import time
from collections import defaultdict, deque

from app.config import settings

_FAILURES: dict[str, deque[float]] = defaultdict(deque)


def _prune(key: str, now: float) -> deque[float]:
    dq = _FAILURES[key]
    window = settings.login_window_seconds
    while dq and now - dq[0] > window:
        dq.popleft()
    return dq


def is_blocked(key: str) -> bool:
    """True si le nombre max de tentatives échouées est atteint dans la fenêtre."""
    dq = _prune(key, time.time())
    return len(dq) >= settings.login_max_attempts


def record_failure(key: str) -> None:
    now = time.time()
    _prune(key, now).append(now)


def reset(key: str) -> None:
    _FAILURES.pop(key, None)
