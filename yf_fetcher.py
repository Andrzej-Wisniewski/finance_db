"""Komunikacja z yfinance: pobieranie danych i ich czyszczenie przed zapisem do MySQL."""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, TypeVar

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import config

log = logging.getLogger(__name__)

try:  # klasa istnieje w nowszych wersjach yfinance
    from yfinance.exceptions import YFRateLimitError
except ImportError:  # pragma: no cover
    class YFRateLimitError(Exception):  # type: ignore[no-redef]
        """Zaślepka dla starszych wersji yfinance."""

T = TypeVar("T")

# Co wolno pobrać dla danego asset_type.
# FOREX / COMMODITY / INDEX: tylko daily_quotes.
# CRYPTO: notowania + market_cap i podaż, bez sprawozdań.
# ETF: notowania + metadane (market_cap), bez sprawozdań.
# STOCK: notowania, metadane, raporty i wskaźniki.
STATEMENT_TYPES = {"STOCK"}
RATIO_TYPES = {"STOCK"}
META_TYPES = {"STOCK", "ETF", "CRYPTO"}
QUOTE_ONLY_TYPES = {"FOREX", "COMMODITY", "INDEX"}

# Limity wartości = szerokość kolumn DECIMAL w schema.sql. Wartość spoza zakresu => None
# (lepsza pusta komórka niż błąd "Out of range" przerywający cały zapis aktywa).
MAX_PRICE = 1e15        # DECIMAL(24,8)
MAX_RATIO = 1e14        # DECIMAL(20,4)
MAX_STATEMENT = 1e22    # DECIMAL(25,2)
MAX_BIGINT = 9e18

# Mapowanie: klucz w info z Yahoo -> kolumna w valuation_ratios (pierwszy niepusty wygrywa).
RATIO_SOURCES: dict[str, tuple[str, ...]] = {
    "current_price": ("currentPrice", "regularMarketPrice"),
    "fifty_two_week_low": ("fiftyTwoWeekLow",),
    "fifty_two_week_high": ("fiftyTwoWeekHigh",),
    "target_mean_price": ("targetMeanPrice",),
    "analyst_count": ("numberOfAnalystOpinions",),
    "trailing_pe": ("trailingPE",),
    "forward_pe": ("forwardPE",),
    "price_to_sales": ("priceToSalesTrailing12Months",),
    "price_to_book": ("priceToBook",),
    "ev_to_ebitda": ("enterpriseToEbitda",),
    "enterprise_to_revenue": ("enterpriseToRevenue",),
    "peg_ratio": ("pegRatio", "trailingPegRatio"),
    "return_on_equity": ("returnOnEquity",),
    "return_on_assets": ("returnOnAssets",),
    "profit_margin": ("profitMargins",),
    "gross_margin": ("grossMargins",),
    "operating_margin": ("operatingMargins",),
    "quick_ratio": ("quickRatio",),
    "current_ratio": ("currentRatio",),
    "dividend_yield": ("dividendYield",),  # uwaga: Yahoo zmieniał jednostkę (ułamek vs procent)
    "payout_ratio": ("payoutRatio",),
    "revenue_growth": ("revenueGrowth",),
    "earnings_growth": ("earningsGrowth",),
    "total_debt": ("totalDebt",),
    "total_cash": ("totalCash",),
    "free_cashflow": ("freeCashflow",),
    "operating_cashflow": ("operatingCashflow",),
    "debt_to_equity": ("debtToEquity",),
}
INTEGER_RATIOS = {"analyst_count", "total_debt", "total_cash", "free_cashflow", "operating_cashflow"}
PRICE_RATIOS = {"current_price", "fifty_two_week_low", "fifty_two_week_high", "target_mean_price"}

_USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
)


# --------------------------------------------------------------------------- wyjątki i typy

class RateLimitedError(Exception):
    """Yahoo zwróciło limit zapytań (HTTP 429). To NIE jest wina aktywa, więc nie liczymy błędu."""


class NoDataError(Exception):
    """Yahoo nie zwróciło żadnych notowań: ticker nieistniejący, wycofany albo źle zapisany."""


@dataclass(slots=True)
class FetchedData:
    market_cap: int | None = None
    shares_outstanding: int | None = None
    currency: str | None = None
    sector: str | None = None
    industry: str | None = None
    ratios: dict[str, Any] | None = None
    # statement_type -> lista krotek
    # (period_end, report_year, period_type, statement_type, line_item, value)
    statements: dict[str, list[tuple]] = field(default_factory=dict)
    # lista krotek (quote_date, open, high, low, close, adj_close, volume)
    prices: list[tuple] = field(default_factory=list)
    first_load: bool = False


# --------------------------------------------------------------------------- sesja HTTP

def make_session() -> Any:
    """Tworzy sesję HTTP z losową tożsamością przeglądarki.

    Nowe wersje yfinance wymagają sesji curl_cffi, która podszywa się pod prawdziwą przeglądarkę
    (w tym jej UserAgent i odcisk TLS), więc losujemy profil "impersonate". Dla starszych wersji
    yfinance bez curl_cffi wracamy do zwykłej sesji requests z losowym nagłówkiem User-Agent.
    """
    try:
        from curl_cffi import requests as curl_requests
        return curl_requests.Session(impersonate=random.choice(("chrome", "safari")))
    except ImportError:
        session = requests.Session()
        session.headers["User-Agent"] = random.choice(_USER_AGENTS)
        return session


# --------------------------------------------------------------------------- czyszczenie danych

def clean_number(value: Any, max_abs: float = MAX_RATIO, ndigits: int | None = None) -> float | None:
    """Zamienia dowolną wartość liczbową (też numpy/pandas) na zwykły float albo None.

    None dostają: None, NaN, +/-Inf, pd.NA, NaT, napisy typu "N/A" i liczby spoza zakresu kolumny.
    Zwykły float jest ważny, bo PyMySQL nie potrafi zapisać typów numpy (np.float64, np.int64).
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (np.floating, np.integer)):
            if not np.isfinite(value):
                return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number) or abs(number) >= max_abs:
        return None
    return round(number, ndigits) if ndigits is not None else number


def clean_int(value: Any, max_abs: float = MAX_BIGINT) -> int | None:
    number = clean_number(value, max_abs=max_abs)
    return None if number is None else int(number)


def clean_text(value: Any, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:max_len] if text else None


def to_date(value: Any) -> date | None:
    """Zamienia Timestamp/datetime/napis na datetime.date (bez strefy czasowej) albo None."""
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)  # zachowuje lokalną datę giełdy, o którą nam chodzi
    return ts.date()


def _is_rate_limit(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, YFRateLimitError) or "429" in text or "too many requests" in text


def _guard(label: str, func: Callable[[], T], default: T) -> T:
    """Wykonuje nieobowiązkowe pobranie. Zwykły błąd = wartość domyślna, limit 429 = wyjątek."""
    try:
        return func()
    except Exception as exc:  # noqa: BLE001 - yfinance rzuca bardzo różne wyjątki
        if _is_rate_limit(exc):
            raise RateLimitedError(str(exc)) from exc
        log.debug("%s: pominięto (%s: %s)", label, type(exc).__name__, exc)
        return default


# --------------------------------------------------------------------------- pobieranie

def _price_period(last_updated_at: datetime | None) -> tuple[str, bool]:
    """Inicjalizacja: 6 miesięcy. Codzienny przebieg: krótkie okno 5 dni.

    Zwraca (okres yfinance, history_load). history_load=True tylko gdy aktywo
    nie ma jeszcze last_updated_at. Krótkie okno zapisujemy w całości: UPSERT
    nie dubluje sesji, a pominięty dzień (albo świeca bez Close) nie ginie.
    """
    if last_updated_at is None:
        return config.PRICE_HISTORY_PERIOD, True
    return config.PRICE_DAILY_PERIOD, False


def classify_period(period_end: date, annual: bool) -> tuple[int, str]:
    """Rok i period_type raportu. Kwartał wynika z miesiąca daty końca okresu."""
    if annual:
        return period_end.year, "YEAR"
    quarter = (period_end.month - 1) // 3 + 1
    return period_end.year, f"Q{quarter}"


def _fill_missing_last_close(history: pd.DataFrame, ticker: Any) -> pd.DataFrame:
    """Uzupełnia Close ostatniej świecy ceną z fast_info, gdy mieści się w jej high/low.

    Yahoo po sesji GPW oddaje wolumen i zakres dnia, a Close zostawia puste.
    """
    if history is None or history.empty or "Close" not in history.columns:
        return history
    if pd.notna(history["Close"].iloc[-1]):
        return history
    last_price = _guard("last_price", lambda: ticker.fast_info.last_price, None)
    try:
        price = float(last_price)
    except (TypeError, ValueError):
        return history
    if not math.isfinite(price):
        return history
    low = history["Low"].iloc[-1] if "Low" in history.columns else None
    high = history["High"].iloc[-1] if "High" in history.columns else None
    try:
        if low is None or high is None or not (float(low) <= price <= float(high)):
            return history
    except (TypeError, ValueError):
        return history
    history = history.copy()
    history.iloc[-1, history.columns.get_loc("Close")] = price
    if "Adj Close" in history.columns and pd.isna(history["Adj Close"].iloc[-1]):
        history.iloc[-1, history.columns.get_loc("Adj Close")] = price
    return history


def prices_to_rows(
    history: pd.DataFrame,
    min_date: date | None = None,
    latest_only: bool = False,
) -> list[tuple]:
    """DataFrame z yfinance -> lista krotek gotowych do zapisu (bez NaN/Inf, daty bez strefy).

    latest_only=True zostawia wyłącznie ostatni wiersz — najnowszą sesję w oknie.
    """
    if history is None or history.empty:
        return []

    df = history.copy()
    index = pd.to_datetime(df.index)
    if getattr(index, "tz", None) is not None:
        # Usuwamy TZ zachowując lokalną datę sesji (nie konwertujemy na UTC).
        try:
            index = index.tz_localize(None)
        except (TypeError, AttributeError):
            index = index.tz_convert(None)
    df.index = index.normalize()

    df = df[df.index.notna()]
    df = df[~df.index.duplicated(keep="last")].sort_index()  # yfinance bywa dubluje ostatni dzień

    if min_date is not None:
        df = df[df.index >= pd.Timestamp(min_date)]

    # Ostatnia świeca bywa bez Close (GPW tuż po zamknięciu). Nie może przesłonić
    # wcześniejszej, już kompletnej sesji.
    if "Close" in df.columns:
        df = df[df["Close"].notna()]

    if latest_only and not df.empty:
        df = df.tail(1)

    if df.empty:
        return []

    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"] if "Close" in df.columns else np.nan
    columns = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    df = df.reindex(columns=columns)
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.where(pd.notnull(df), None)  # NaN / pd.NA -> None (MySQL NULL)
    df = df.dropna(subset=["Close"])  # wiersz bez ceny zamknięcia jest bezużyteczny

    rows: list[tuple] = []
    for ts, (o, h, l, c, adj, vol) in zip(df.index, df.itertuples(index=False, name=None)):
        trade_date = ts.date() if hasattr(ts, "date") else to_date(ts)
        if trade_date is None:
            continue
        if min_date is not None and trade_date < min_date:
            continue
        close_val = clean_number(c, MAX_PRICE, 8)
        if close_val is None:
            continue
        rows.append((
            trade_date,
            clean_number(o, MAX_PRICE, 8), clean_number(h, MAX_PRICE, 8),
            clean_number(l, MAX_PRICE, 8), close_val,
            clean_number(adj, MAX_PRICE, 8), clean_int(vol),
        ))
    return rows


def statement_to_rows(
    df: pd.DataFrame | None,
    *,
    annual: bool,
    statement_type: str,
    min_date: date | None = None,
) -> list[tuple]:
    """Sprawozdanie -> krotki (period_end, report_year, period_type, statement_type, pozycja, wartość)."""
    if df is None or df.empty:
        return []
    if statement_type not in {"INCOME", "BALANCE", "CASH_FLOW"}:
        raise ValueError(f"Niedozwolony statement_type: {statement_type}")
    rows: list[tuple] = []
    for column in df.columns:
        period_end = to_date(column)
        if period_end is None:
            continue
        if min_date is not None and period_end < min_date:
            continue
        report_year, period_type = classify_period(period_end, annual)
        for line_item, raw in df[column].items():
            value = clean_number(raw, MAX_STATEMENT, 2)
            item = clean_text(str(line_item), 255)
            if value is not None and item is not None:
                rows.append((period_end, report_year, period_type, statement_type, item, value))
    return rows


def get_safe(info: Any, key: str) -> Any:
    """Wartość z info albo None. Brak klucza i wyjątek nie przerywają spółki."""
    try:
        if not isinstance(info, dict):
            return None
        value = info.get(key)
    except Exception:  # noqa: BLE001
        return None
    return value


def extract_ratios(info: dict[str, Any]) -> dict[str, Any] | None:
    ratios: dict[str, Any] = {}
    for column, keys in RATIO_SOURCES.items():
        raw = None
        for key in keys:
            raw = get_safe(info, key)
            if raw is not None:
                break
        if column in INTEGER_RATIOS:
            ratios[column] = clean_int(raw)
        elif column in PRICE_RATIOS:  # kolumny DECIMAL(24,8)
            ratios[column] = clean_number(raw, MAX_PRICE, 8)
        else:                         # kolumny DECIMAL(20,4)
            ratios[column] = clean_number(raw, MAX_RATIO, 4)
    return ratios if any(v is not None for v in ratios.values()) else None


def fetch_asset(asset: dict[str, Any], session: Any) -> FetchedData:
    """Pobiera dane jednego aktywa według asset_type.

    STOCK: ceny, metadane, sprawozdania, wskaźniki.
    ETF: ceny i metadane (market_cap), bez sprawozdań.
    CRYPTO: ceny, market_cap i circulatingSupply, bez sprawozdań.
    FOREX / COMMODITY / INDEX: tylko ceny.

    Brak pola w info nie jest błędem. Rzuca NoDataError, RateLimitedError albo inny wyjątek sieci.
    """
    symbol: str = asset["symbol"]
    asset_type: str = asset["asset_type"]
    period, first_load = _price_period(asset.get("last_updated_at"))
    cutoff = config.retention_cutoff()
    price_min = config.quotes_history_cutoff() if first_load else None
    ticker = yf.Ticker(symbol, session=session)

    # 1. Ceny najpierw: brak notowań kończy pracę zanim wykonamy kosztowne dodatkowe zapytania.
    #    Inicjalizacja: 6 miesięcy. Cron: z krótkiego okna zostaje tylko ostatnia sesja.
    try:
        history = ticker.history(period=period, interval="1d", auto_adjust=False, actions=False)
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit(exc):
            raise RateLimitedError(str(exc)) from exc
        raise
    history = _fill_missing_last_close(history, ticker)
    # Całe krótkie okno, nie sama ostatnia świeca: 29 września ma Close,
    # a 30 września czasem wraca jeszcze z pustym Close.
    prices = prices_to_rows(history, min_date=price_min, latest_only=False)
    if not prices:
        raise NoDataError(f"Brak notowań dla {symbol} (period={period}, cutoff={cutoff})")

    data = FetchedData(prices=prices, first_load=first_load)

    # 2. Metadane tylko tam, gdzie mają sens. Brak klucza w info -> None, bez wyjątku.
    if asset_type in QUOTE_ONLY_TYPES:
        return data

    info: dict[str, Any] = _guard(f"{symbol} info", lambda: ticker.info or {}, {})
    if not isinstance(info, dict):
        info = {}
    data.market_cap = clean_int(info.get("marketCap"))
    data.currency = clean_text(info.get("currency"), 10)
    if data.currency:
        data.currency = data.currency.upper()

    if asset_type == "CRYPTO":
        data.shares_outstanding = clean_int(info.get("circulatingSupply"))
        if data.shares_outstanding is None:
            data.shares_outstanding = clean_int(info.get("sharesOutstanding"))
        return data

    if asset_type == "ETF":
        data.shares_outstanding = clean_int(info.get("sharesOutstanding"))
        return data

    # STOCK (i nieznany typ: metadane tak, sprawozdania tylko dla STOCK)
    data.shares_outstanding = clean_int(info.get("sharesOutstanding"))
    data.sector = clean_text(info.get("sector"), 100)
    data.industry = clean_text(info.get("industry"), 150)
    if asset_type in RATIO_TYPES:
        data.ratios = extract_ratios(info)
    if asset_type in STATEMENT_TYPES:
        sources: dict[str, tuple[Callable[[], Any], Callable[[], Any]]] = {
            "INCOME": (lambda: ticker.financials, lambda: ticker.quarterly_financials),
            "BALANCE": (lambda: ticker.balance_sheet, lambda: ticker.quarterly_balance_sheet),
            "CASH_FLOW": (lambda: ticker.cashflow, lambda: ticker.quarterly_cashflow),
        }
        for statement_type, (annual, quarterly) in sources.items():
            rows = statement_to_rows(
                _guard(f"{symbol} {statement_type} YEAR", annual, None),
                annual=True, statement_type=statement_type, min_date=cutoff,
            )
            rows += statement_to_rows(
                _guard(f"{symbol} {statement_type} Q", quarterly, None),
                annual=False, statement_type=statement_type, min_date=cutoff,
            )
            data.statements[statement_type] = rows

    return data
