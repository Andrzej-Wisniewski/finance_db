"""Warstwa dostępu do MySQL: pula połączeń, kolejka aktywów, UPSERT i retencja."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from typing import Any, Iterator, Mapping, Sequence

import pymysql
from dbutils.pooled_db import PooledDB

import config

log = logging.getLogger(__name__)

# Rodzaje sprawozdań w financial_reports. Wartość trafia do SQL jako parametr, nie jako nazwa tabeli.
STATEMENT_TYPES: tuple[str, ...] = (
    "INCOME",
    "BALANCE",
    "CASH_FLOW",
)

# Kolumny tabeli valuation_ratios (poza asset_id i snapshot_date).
RATIO_COLUMNS: tuple[str, ...] = (
    "current_price", "fifty_two_week_low", "fifty_two_week_high", "target_mean_price",
    "analyst_count", "trailing_pe", "forward_pe", "price_to_sales", "price_to_book",
    "ev_to_ebitda", "enterprise_to_revenue", "peg_ratio",
    "return_on_equity", "return_on_assets",
    "profit_margin", "gross_margin", "operating_margin",
    "quick_ratio", "current_ratio",
    "dividend_yield", "payout_ratio", "revenue_growth", "earnings_growth",
    "total_debt", "total_cash", "free_cashflow", "operating_cashflow", "debt_to_equity",
)

# Tabele objęte polityką retencji: (nazwa, kolumna daty biznesowej).
RETENTION_TARGETS: tuple[tuple[str, str], ...] = (
    ("daily_quotes", "quote_date"),
    ("financial_reports", "period_end"),
    ("valuation_ratios", "snapshot_date"),
)

CHUNK_SIZE = 1000          # ile wierszy wysyłamy w jednym executemany
PRUNE_CHUNK = 10_000       # DELETE ... LIMIT — unika długich locków na dużych tabelach

_pool: PooledDB | None = None


# --------------------------------------------------------------------------- połączenia

def get_pool() -> PooledDB:
    """Tworzy pulę przy pierwszym użyciu (leniwa inicjalizacja) i zwraca ją."""
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=config.DB_POOL_SIZE,
            mincached=1,
            maxcached=config.DB_POOL_SIZE,
            blocking=True,          # gdy pula pusta, czekaj zamiast rzucać wyjątek
            ping=1,                 # sprawdź, czy połączenie żyje, zanim je oddasz
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME,
            charset="utf8mb4",
            autocommit=False,       # transakcjami zarządzamy sami
            connect_timeout=10,
            read_timeout=120,
            write_timeout=120,
        )
        log.debug("Utworzono pulę połączeń (max %s).", config.DB_POOL_SIZE)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def transaction() -> Iterator[Any]:
    """Pożycza połączenie z puli. Sukces = COMMIT, wyjątek = ROLLBACK. Na końcu zwraca je do puli."""
    conn = get_pool().connection()
    try:
        yield conn
        conn.commit()
    except BaseException:  # także KeyboardInterrupt: nie zostawiamy otwartej transakcji
        conn.rollback()
        raise
    finally:
        conn.close()  # w PooledDB close() oznacza "oddaj do puli", a nie "rozłącz"


def _executemany(cur: Any, sql: str, rows: Sequence[Sequence[Any]]) -> int:
    """Wysyła wiersze paczkami po CHUNK_SIZE. Zwraca liczbę przetworzonych wierszy."""
    if not rows:
        return 0
    for start in range(0, len(rows), CHUNK_SIZE):
        cur.executemany(sql, rows[start:start + CHUNK_SIZE])
    return len(rows)


# --------------------------------------------------------------------------- kolejka

def fetch_uninitialized(limit: int) -> list[dict[str, Any]]:
    """Aktywa bez żadnego udanego zapisu (last_updated_at IS NULL), niezależnie od typu."""
    sql = """
        SELECT asset_id, symbol, asset_type, last_updated_at
        FROM assets
        WHERE is_active = TRUE AND last_updated_at IS NULL
        ORDER BY asset_id ASC
        LIMIT %s
    """
    with transaction() as conn:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cur.execute(sql, (limit,))
            return list(cur.fetchall())
        finally:
            cur.close()


def fetch_queue(
    limit: int,
    *,
    only_type: str | None = None,
    exclude_type: str | None = None,
) -> list[dict[str, Any]]:
    """Kolejka aktywów: najpierw nowe (last_updated_at IS NULL), potem od najstarszej aktualizacji.

    only_type / exclude_type zawężają asset_type (parametry, nie sklejany SQL).
    """
    sql = """
        SELECT asset_id, symbol, asset_type, last_updated_at
        FROM assets
        WHERE is_active = TRUE
          AND (%s IS NULL OR asset_type = %s)
          AND (%s IS NULL OR asset_type <> %s)
        ORDER BY (last_updated_at IS NULL) DESC, last_updated_at ASC, asset_id ASC
        LIMIT %s
    """
    with transaction() as conn:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cur.execute(sql, (only_type, only_type, exclude_type, exclude_type, limit))
            return list(cur.fetchall())
        finally:
            cur.close()


# --------------------------------------------------------------------------- zapis danych

_SQL_PRICES = """
    INSERT INTO daily_quotes
        (asset_id, quote_date, open_price, high_price, low_price, close_price, adj_close, volume)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        open_price = VALUES(open_price), high_price = VALUES(high_price),
        low_price = VALUES(low_price), close_price = VALUES(close_price),
        adj_close = VALUES(adj_close), volume = VALUES(volume)
"""

_SQL_REPORTS = """
    INSERT INTO financial_reports
        (asset_id, period_end, report_year, period_type, statement_type, line_item, value)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        value = VALUES(value)
"""

_SQL_RATIOS = (
    "INSERT INTO valuation_ratios (asset_id, snapshot_date, {cols}) "
    "VALUES (%s, %s, {marks}) ON DUPLICATE KEY UPDATE {updates}"
).format(
    cols=", ".join(RATIO_COLUMNS),
    marks=", ".join(["%s"] * len(RATIO_COLUMNS)),
    updates=", ".join(f"{c} = VALUES({c})" for c in RATIO_COLUMNS),
)

# COALESCE: jeśli Yahoo chwilowo nie zwróci np. sektora, zostawiamy poprzednią wartość.
_SQL_MARK_SUCCESS = """
    UPDATE assets
    SET last_updated_at = NOW(),
        error_count = 0,
        market_cap = COALESCE(%s, market_cap),
        sector = COALESCE(%s, sector),
        industry = COALESCE(%s, industry),
        shares_outstanding = COALESCE(%s, shares_outstanding),
        currency = COALESCE(%s, currency)
    WHERE asset_id = %s
"""


def save_asset_data(
    asset_id: int,
    *,
    market_cap: int | None,
    shares_outstanding: int | None,
    currency: str | None,
    sector: str | None,
    industry: str | None,
    ratios: Mapping[str, Any] | None,
    statements: Mapping[str, Sequence[tuple]],
    prices: Sequence[tuple],
) -> dict[str, int]:
    """Zapisuje komplet danych jednego aktywa w JEDNEJ transakcji (wszystko albo nic).

    Dzięki temu awaria w połowie nie zostawia aktywa z cenami bez sprawozdań, a
    last_updated_at zmienia się dopiero, gdy wszystkie zapisy się powiodły.
    """
    counts = {"prices": 0, "statements": 0, "ratios": 0}
    with transaction() as conn:
        cur = conn.cursor()
        try:
            counts["prices"] = _executemany(cur, _SQL_PRICES, [(asset_id, *row) for row in prices])

            report_rows: list[tuple] = []
            for statement_type in STATEMENT_TYPES:
                for row in statements.get(statement_type) or []:
                    if row[3] != statement_type:
                        raise ValueError(f"Niezgodny statement_type w wierszu: {row[3]}")
                    report_rows.append((asset_id, *row))
            counts["statements"] = _executemany(cur, _SQL_REPORTS, report_rows)

            if ratios:
                params = (asset_id, date.today(), *(ratios.get(c) for c in RATIO_COLUMNS))
                cur.execute(_SQL_RATIOS, params)
                counts["ratios"] = 1

            cur.execute(
                _SQL_MARK_SUCCESS,
                (market_cap, sector, industry, shares_outstanding, currency, asset_id),
            )
        finally:
            cur.close()
    return counts


def record_failure(asset_id: int, max_errors: int) -> tuple[int, bool]:
    """Zwiększa error_count; po osiągnięciu max_errors wyłącza aktywo. Zwraca (error_count, is_active)."""
    # Kolejność w SET ma znaczenie: MySQL wykonuje przypisania od lewej do prawej.
    # is_active liczymy więc PIERWSZY, na starej wartości error_count (stąd "+ 1").
    sql_update = """
        UPDATE assets
        SET is_active = IF(error_count + 1 >= %s, FALSE, is_active),
            error_count = error_count + 1
        WHERE asset_id = %s
    """
    with transaction() as conn:
        cur = conn.cursor()
        try:
            cur.execute(sql_update, (max_errors, asset_id))
            cur.execute("SELECT error_count, is_active FROM assets WHERE asset_id = %s", (asset_id,))
            row = cur.fetchone()
        finally:
            cur.close()
    if row is None:
        return 0, False
    return int(row[0]), bool(row[1])


# --------------------------------------------------------------------------- retencja

def prune_old_data(years: int | None = None) -> dict[str, int]:
    """Usuwa rekordy starsze niż DATA_RETENTION_YEARS. Zwraca {tabela: liczba_usuniętych}."""
    years = years if years is not None else config.DATA_RETENTION_YEARS
    cutoff = config.retention_cutoff()
    deleted: dict[str, int] = {}

    allowed = {t for t, _ in RETENTION_TARGETS}
    for table, date_col in RETENTION_TARGETS:
        if table not in allowed:
            raise ValueError(f"Niedozwolona tabela retencji: {table}")
        # Whitelist nazw — nie interpolujemy niczego spoza RETENTION_TARGETS.
        sql = f"DELETE FROM {table} WHERE {date_col} < %s LIMIT %s"
        total = 0
        while True:
            with transaction() as conn:
                cur = conn.cursor()
                try:
                    cur.execute(sql, (cutoff, PRUNE_CHUNK))
                    n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                finally:
                    cur.close()
            total += n
            if n < PRUNE_CHUNK:
                break
        deleted[table] = total
        log.info("Retencja: %s — usunięto %s wierszy starszych niż %s.", table, total, cutoff)

    return deleted


# --------------------------------------------------------------------------- seed

def bulk_upsert_assets(assets: Sequence[Mapping[str, str]], chunk_size: int = 500) -> int:
    """Wsadowy UPSERT listy aktywów (używa go seed_tickers.py). Każda paczka to osobna transakcja.

    Celowo NIE aktualizujemy is_active, error_count ani last_updated_at przy duplikacie:
    ponowny seed nie może "wskrzesić" aktywa, które system wyłączył z powodu błędów.
    """
    sql = """
        INSERT INTO assets (symbol, name, asset_type, exchange, currency, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name = VALUES(name), asset_type = VALUES(asset_type),
            exchange = VALUES(exchange), currency = VALUES(currency)
    """
    rows = [
        (a["symbol"], a["name"], a["asset_type"], a["exchange"], a["currency"], True)
        for a in assets
    ]
    for start in range(0, len(rows), chunk_size):
        with transaction() as conn:
            cur = conn.cursor()
            try:
                cur.executemany(sql, rows[start:start + chunk_size])
            finally:
                cur.close()
    return len(rows)


def insert_stocks_if_missing(stocks: Sequence[Mapping[str, str]]) -> int:
    """Nowe spółki jako STOCK. Istniejący symbol zostaje nietknięty (typ, last_updated_at, is_active)."""
    sql = """
        INSERT IGNORE INTO assets (symbol, name, asset_type, exchange, currency, is_active)
        VALUES (%s, %s, 'STOCK', %s, %s, TRUE)
    """
    rows = [
        (s["symbol"], s["name"], s.get("exchange") or "US", s.get("currency") or "USD")
        for s in stocks
    ]
    if not rows:
        return 0
    with transaction() as conn:
        cur = conn.cursor()
        try:
            cur.executemany(sql, rows)
        finally:
            cur.close()
    return len(rows)


def replace_etf_constituents(
    etf_symbol: str,
    holdings: Sequence[Mapping[str, Any]],
    source: str,
) -> int:
    """Podmienia skład jednego ETF. holdings: symbol, name, weight_percentage."""
    if source not in ("ISSUER", "YAHOO_TOP"):
        raise ValueError(f"Niedozwolone źródło składu: {source}")
    with transaction() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT asset_id FROM assets WHERE symbol = %s AND asset_type = 'ETF'",
                (etf_symbol,),
            )
            row = cur.fetchone()
            if row is None:
                log.warning("Pomijam skład %s: brak ETF w assets.", etf_symbol)
                return 0
            etf_id = int(row[0])
            cur.execute("DELETE FROM etf_constituents WHERE etf_id = %s", (etf_id,))
            if not holdings:
                return 0
            symbols = [h["symbol"] for h in holdings]
            placeholders = ", ".join(["%s"] * len(symbols))
            cur.execute(
                f"SELECT symbol, asset_id FROM assets WHERE symbol IN ({placeholders})",
                symbols,
            )
            ids = {symbol: int(asset_id) for symbol, asset_id in cur.fetchall()}
            link_rows = []
            for holding in holdings:
                stock_id = ids.get(holding["symbol"])
                if stock_id is None or stock_id == etf_id:
                    continue
                link_rows.append((etf_id, stock_id, holding.get("weight_percentage"), source))
            if link_rows:
                cur.executemany(
                    """
                    INSERT INTO etf_constituents (etf_id, stock_id, weight_percentage, source)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        weight_percentage = VALUES(weight_percentage),
                        source = VALUES(source)
                    """,
                    link_rows,
                )
            return len(link_rows)
        finally:
            cur.close()
