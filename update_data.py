"""Codzienna aktualizacja: kolejka od pustego last_updated_at, potem najstarsze.

Jedna paczka na uruchomienie. python update_data.py --missing powtarza paczki,
aż nie zostanie aktyw bez historii.
"""
from __future__ import annotations

import argparse
import logging
import random
import signal
import sys
import time

import requests
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from typing import Any, IO

import config
import db
import yf_fetcher
from yf_fetcher import NoDataError, RateLimitedError

log = logging.getLogger("update_data")

_NETWORK_ATTEMPTS = 3
_NETWORK_MARKERS = (
    "dnserror", "could not resolve host", "curl: (6)", "curl: (7)",
    "failed to perform", "connection reset", "timed out",
    "temporary failure in name resolution", "network is unreachable",
)


def is_network_error(exc: BaseException) -> bool:
    """Awaria DNS, połączenia albo curl. To nie jest wina tickera."""
    try:
        from curl_cffi.requests import exceptions as curl_exc
        curl_types: tuple[type, ...] = (curl_exc.RequestException,)
    except ImportError:
        curl_types = ()
    net_types = (requests.exceptions.RequestException, TimeoutError, ConnectionError, *curl_types)
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, net_types):
            return True
        text = f"{type(current).__name__} {current}".lower()
        if any(marker in text for marker in _NETWORK_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


@dataclass
class RunStats:
    """Liczniki do podsumowania przebiegu."""
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    rate_limited: int = 0
    deactivated: list[str] = field(default_factory=list)
    failed_symbols: list[str] = field(default_factory=list)
    price_rows: int = 0
    statement_rows: int = 0
    ratio_rows: int = 0
    first_loads: int = 0
    pruned: dict[str, int] = field(default_factory=dict)


def setup_logging() -> None:
    """Logi do konsoli i do pliku app.log z rotacją (config.LOG_MAX_BYTES x LOG_BACKUP_COUNT)."""
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file_handler)

    # Biblioteki bywają "gadatliwe" (yfinance loguje własne błędy jako ERROR).
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def acquire_lock() -> IO[str] | None:
    """Blokada pliku: drugi cron nie ruszy, jeśli poprzedni przebieg jeszcze trwa (tylko Unix/macOS)."""
    try:
        import fcntl
    except ImportError:  # Windows
        return None
    handle = open(config.LOCK_FILE, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        log.warning("Poprzedni przebieg jeszcze trwa (blokada %s). Kończę.", config.LOCK_FILE.name)
        sys.exit(0)
    return handle


def _handle_sigterm(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt  # dzięki temu SIGTERM przechodzi tą samą ścieżką co Ctrl+C


def register_failure(asset: dict[str, Any], exc: Exception, stats: RunStats) -> None:
    """Zapisuje błąd w bazie (error_count, ewentualnie is_active = FALSE) i w statystykach."""
    stats.failed += 1
    stats.failed_symbols.append(asset["symbol"])
    log.warning("%s: błąd (%s: %s)", asset["symbol"], type(exc).__name__, exc)
    try:
        errors, is_active = db.record_failure(asset["asset_id"], config.MAX_ERRORS)
    except Exception:  # noqa: BLE001 - błąd zapisu błędu nie może przerwać pętli
        log.exception("%s: nie udało się zapisać error_count", asset["symbol"])
        return
    if not is_active:
        stats.deactivated.append(asset["symbol"])
        log.error("%s: wyłączone (is_active = FALSE) po %s błędach.", asset["symbol"], errors)


def process_asset(asset: dict[str, Any], session: Any, stats: RunStats) -> None:
    """Pobiera i zapisuje dane jednego aktywa. Wyjątki obsługuje wywołujący."""
    data = yf_fetcher.fetch_asset(asset, session)
    counts = db.save_asset_data(
        asset["asset_id"],
        market_cap=data.market_cap,
        shares_outstanding=data.shares_outstanding,
        currency=data.currency,
        sector=data.sector,
        industry=data.industry,
        ratios=data.ratios,
        statements=data.statements,
        prices=data.prices,
    )
    stats.succeeded += 1
    stats.first_loads += int(data.first_load)
    stats.price_rows += counts["prices"]
    stats.statement_rows += counts["statements"]
    stats.ratio_rows += counts["ratios"]
    log.info(
        "%s [%s]: OK (notowania: %s%s, raporty: %s, wskaźniki: %s)",
        asset["symbol"], asset["asset_type"], counts["prices"],
        ", historia 6 mies." if data.first_load else ", ostatnia sesja",
        counts["statements"], counts["ratios"],
    )


def run(stats: RunStats, *, only_missing: bool = False) -> None:
    if only_missing:
        queue = db.fetch_uninitialized(config.BATCH_SIZE)
    else:
        queue = db.fetch_queue(config.BATCH_SIZE)
    log.info(
        "Start: %s aktywów w kolejce (limit %s, retencja %s lat, cutoff %s).",
        len(queue), config.BATCH_SIZE, config.DATA_RETENTION_YEARS, config.retention_cutoff(),
    )

    session = yf_fetcher.make_session()
    consecutive_rate_limits = 0

    for position, asset in enumerate(queue, start=1):
        # Co jakiś czas zmieniamy tożsamość sesji HTTP.
        if position > 1 and (position - 1) % config.SESSION_ROTATE_EVERY == 0:
            session = yf_fetcher.make_session()

        log.info("[%s/%s] %s", position, len(queue), asset["symbol"])
        stats.processed += 1
        attempt = 0
        while True:
            try:
                process_asset(asset, session, stats)
                consecutive_rate_limits = 0
                break
            except RateLimitedError as exc:
                # 429 to nie wina aktywa: nie liczymy błędu, robimy dłuższą pauzę i nową sesję.
                stats.processed -= 1
                stats.rate_limited += 1
                consecutive_rate_limits += 1
                log.warning("%s: limit zapytań Yahoo (%s/%s): %s", asset["symbol"],
                            consecutive_rate_limits, config.MAX_CONSECUTIVE_RATE_LIMITS, exc)
                if consecutive_rate_limits >= config.MAX_CONSECUTIVE_RATE_LIMITS:
                    log.error("Zbyt wiele limitów z rzędu. Przerywam, reszta kolejki zostanie na następny raz.")
                    break
                time.sleep(config.RATE_LIMIT_PAUSE_SEC * consecutive_rate_limits)
                session = yf_fetcher.make_session()
                break
            except NoDataError as exc:
                consecutive_rate_limits = 0
                register_failure(asset, exc, stats)
                break
            except Exception as exc:  # noqa: BLE001
                if is_network_error(exc):
                    attempt += 1
                    if attempt < _NETWORK_ATTEMPTS:
                        pause = random.uniform(3, 5)
                        log.warning("%s: błąd sieci, próba %s/%s za %.0f s (%s)",
                                    asset["symbol"], attempt, _NETWORK_ATTEMPTS, pause, exc)
                        time.sleep(pause)
                        session = yf_fetcher.make_session()
                        continue
                    stats.processed -= 1
                    consecutive_rate_limits = 0
                    log.warning("%s: błąd sieci po %s próbach, pomijam bez error_count (%s)",
                                asset["symbol"], _NETWORK_ATTEMPTS, exc)
                    session = yf_fetcher.make_session()
                    break
                consecutive_rate_limits = 0
                register_failure(asset, exc, stats)
                break

        if consecutive_rate_limits >= config.MAX_CONSECUTIVE_RATE_LIMITS:
            break
        if position < len(queue):
            time.sleep(random.uniform(config.DELAY_MIN, config.DELAY_MAX))


def log_summary(stats: RunStats, seconds: float) -> None:
    pruned_total = sum(stats.pruned.values())
    log.info("=" * 60)
    log.info("PODSUMOWANIE (czas: %.0f s)", seconds)
    log.info("Przetworzone aktywa : %s (sukces: %s, błędy: %s, w tym historia 6 mies.: %s)",
             stats.processed, stats.succeeded, stats.failed, stats.first_loads)
    log.info("Zapisane rekordy    : notowania %s, raporty %s, wskaźniki %s",
             stats.price_rows, stats.statement_rows, stats.ratio_rows)
    log.info("Retencja %s lat     : usunięto %s rekordów %s",
             config.DATA_RETENTION_YEARS, pruned_total, stats.pruned or "{}")
    if stats.rate_limited:
        log.warning("Limity Yahoo (429) : %s", stats.rate_limited)
    if stats.failed_symbols:
        log.warning("Aktywa z błędem   : %s", ", ".join(stats.failed_symbols[:50]))
    if stats.deactivated:
        log.error("Wyłączone (is_active=FALSE): %s", ", ".join(stats.deactivated))
    log.info("=" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pobierz notowania i raporty dla kolejki aktywów.")
    parser.add_argument(
        "--missing",
        action="store_true",
        help="Tylko aktywa z pustym last_updated_at, w pętli aż kolejka się skończy.",
    )
    args = parser.parse_args(argv)

    setup_logging()
    lock = acquire_lock()  # trzymamy referencję, żeby blokada żyła do końca procesu
    signal.signal(signal.SIGTERM, _handle_sigterm)

    stats = RunStats()
    started = time.monotonic()
    exit_code = 0
    try:
        if not args.missing:
            run(stats)
        else:
            while True:
                seen = stats.processed
                saved = stats.succeeded
                run(stats, only_missing=True)
                if stats.processed == seen:
                    log.info("Brak aktywów z pustym last_updated_at.")
                    break
                if stats.succeeded == saved:
                    log.error("Paczka nic nie zapisała. Reszta zostaje na później.")
                    exit_code = 1
                    break
    except KeyboardInterrupt:
        log.warning("Przerwano ręcznie. Kończę i wypisuję podsumowanie.")
        exit_code = 130
    except Exception:  # noqa: BLE001 - np. brak połączenia z bazą
        log.exception("Krytyczny błąd przebiegu.")
        exit_code = 1
    finally:
        try:
            stats.pruned = db.prune_old_data()
        except Exception:  # noqa: BLE001
            log.exception("Nie udało się wykonać prune_old_data().")
        log_summary(stats, time.monotonic() - started)
        db.close_pool()
        if lock:
            lock.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
