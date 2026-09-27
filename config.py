"""Konfiguracja: dane dostępowe z pliku .env oraz parametry sterujące aktualizacją."""
from __future__ import annotations

import calendar
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# .env szukamy obok tego pliku, a nie w bieżącym katalogu roboczym.
# Dzięki temu skrypt działa tak samo uruchamiany ręcznie i z crona.
load_dotenv(BASE_DIR / ".env")


def _require(name: str) -> str:
    """Zwraca wartość zmiennej środowiskowej albo przerywa start z czytelnym komunikatem."""
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"Brak wymaganej zmiennej środowiskowej: {name} (uzupełnij plik .env)")
    return value


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


# --- Baza danych (bez domyślnych haseł i użytkowników "root": brak wartości = błąd) ---
DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT: int = _env_int("DB_PORT", 3306)
DB_USER: str = _require("DB_USER")
DB_PASSWORD: str = _require("DB_PASSWORD")
DB_NAME: str = _require("DB_NAME")
DB_POOL_SIZE: int = _env_int("DB_POOL_SIZE", 3)

# --- Parametry przebiegu ---
BATCH_SIZE: int = _env_int("BATCH_SIZE", 50)           # paczka crona; 1200 aktywów = ~24 przebiegi
DELAY_MIN: float = _env_float("DELAY_MIN", 0.5)        # losowa przerwa między aktywami [s]
DELAY_MAX: float = _env_float("DELAY_MAX", 1.5)
MAX_ERRORS: int = _env_int("MAX_ERRORS", 5)            # po tylu błędach aktywo dostaje is_active = FALSE

# --- Retencja historii (notowania + sprawozdania + wskaźniki) ---
DATA_RETENTION_YEARS: int = _env_int("DATA_RETENTION_YEARS", 3)

# --- Pobieranie cen ---
# Inicjalizacja (last_updated_at IS NULL): ostatnie N miesięcy zamknięć sesji.
# Codzienny cron: krótkie okno, z którego zostaje tylko ostatnia zamknięta sesja.
PRICE_HISTORY_MONTHS: int = _env_int("PRICE_HISTORY_MONTHS", 6)
PRICE_HISTORY_PERIOD: str = os.getenv("PRICE_HISTORY_PERIOD", "6mo")
PRICE_DAILY_PERIOD: str = os.getenv("PRICE_DAILY_PERIOD", "5d")

# --- Odporność na limity Yahoo (HTTP 429) ---
SESSION_ROTATE_EVERY: int = _env_int("SESSION_ROTATE_EVERY", 50)     # nowa sesja HTTP co N aktywów
RATE_LIMIT_PAUSE_SEC: float = _env_float("RATE_LIMIT_PAUSE_SEC", 60) # bazowa pauza po 429
MAX_CONSECUTIVE_RATE_LIMITS: int = _env_int("MAX_CONSECUTIVE_RATE_LIMITS", 3)

# --- Logi i blokada przed równoległym uruchomieniem ---
LOG_FILE: Path = BASE_DIR / os.getenv("LOG_FILE", "app.log")
LOG_MAX_BYTES: int = _env_int("LOG_MAX_BYTES", 5 * 1024 * 1024)
LOG_BACKUP_COUNT: int = _env_int("LOG_BACKUP_COUNT", 5)
LOCK_FILE: Path = BASE_DIR / "update.lock"

if DELAY_MIN < 0 or DELAY_MIN > DELAY_MAX:
    raise RuntimeError("Niepoprawna konfiguracja: wymagane 0 <= DELAY_MIN <= DELAY_MAX")
if BATCH_SIZE < 1 or MAX_ERRORS < 1:
    raise RuntimeError("Niepoprawna konfiguracja: BATCH_SIZE i MAX_ERRORS muszą być >= 1")
if DATA_RETENTION_YEARS < 1:
    raise RuntimeError("Niepoprawna konfiguracja: DATA_RETENTION_YEARS musi być >= 1")
if PRICE_HISTORY_MONTHS < 1:
    raise RuntimeError("Niepoprawna konfiguracja: PRICE_HISTORY_MONTHS musi być >= 1")


def retention_cutoff(as_of: date | None = None) -> date:
    """Najstarsza data, którą wolno przechowywać / pobierać (as_of minus DATA_RETENTION_YEARS)."""
    today = as_of or date.today()
    try:
        return today.replace(year=today.year - DATA_RETENTION_YEARS)
    except ValueError:
        # 29 lutego w roku nieprzestępnym
        return today.replace(year=today.year - DATA_RETENTION_YEARS, day=28)


def quotes_history_cutoff(as_of: date | None = None) -> date:
    """Najstarsza data notowania przy inicjalizacji (as_of minus PRICE_HISTORY_MONTHS)."""
    today = as_of or date.today()
    month_index = today.year * 12 + (today.month - 1) - PRICE_HISTORY_MONTHS
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
