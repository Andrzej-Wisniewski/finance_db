"""Seed listy aktywów (akcje, ETF-y, krypto, forex, surowce) do tabeli assets.

Tickery są zapisywane w formacie Yahoo Finance. Skrypt można uruchamiać wielokrotnie:
istniejące aktywa są aktualizowane, a nowe dodawane (patrz db.bulk_upsert_assets).
"""
from __future__ import annotations

import csv
import io
import logging
import re
import sys
import zipfile
from typing import Callable, TypedDict
from xml.etree import ElementTree as ET

import pandas as pd
import requests
from lxml import html as lxml_html

import db

log = logging.getLogger("seed_tickers")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
HTTP_TIMEOUT = 25  # sekundy; bez timeoutu requests potrafi wisieć w nieskończoność


class Asset(TypedDict):
    symbol: str
    name: str
    asset_type: str
    exchange: str
    currency: str


# Musi pokrywać się z ENUM asset_type w schema.sql
ALLOWED_ASSET_TYPES = frozenset({"STOCK", "ETF", "COMMODITY", "FOREX", "CRYPTO", "INDEX"})

# Sufiksy / format Yahoo Finance:
#   US (NYSE/NASDAQ): BRK-B (klasa z myślnikiem, bez sufiksu)
#   GPW: .WA | XETRA: .DE | TSX: .TO | KOSPI: .KS | TSE: .T
#   Paryż: .PA | Amsterdam: .AS | LSE: .L | KOSDAQ: .KQ
#   Forex: EURUSD=X | Surowce (futures): GC=F | Krypto: BTC-USD
# Dozwolone znaki tickera Yahoo: litery, cyfry oraz . - = ^
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-=^]{0,29}$")


# --------------------------------------------------------------------------- czyszczenie

def clean_text(value: object, max_len: int) -> str | None:
    """Zwraca oczyszczony napis albo None (dla None, NaN, "nan", pustych). Usuwa przypisy [1] z Wikipedii."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = re.sub(r"\[.*?\]", "", str(value))
    text = " ".join(text.split())
    if not text or text.lower() in {"nan", "none", "-"}:
        return None
    return text[:max_len]


def make_asset(symbol: object, name: object, asset_type: str, exchange: str, currency: str) -> Asset | None:
    """Buduje zwalidowany rekord. Zwraca None, jeśli ticker jest pusty lub ma niepoprawny format."""
    if asset_type not in ALLOWED_ASSET_TYPES:
        log.debug("Odrzucono nieznany asset_type=%r dla %r", asset_type, symbol)
        return None
    sym = clean_text(symbol, 32)  # VARCHAR(32) w schema.sql
    if sym is None:
        return None
    sym = sym.upper().replace(" ", "")
    if not _SYMBOL_RE.match(sym):  # zbyt długie lub dziwne tickery odrzucamy, zamiast je ucinać
        log.debug("Odrzucono niepoprawny ticker: %r", symbol)
        return None
    return {
        "symbol": sym,
        "name": clean_text(name, 250) or sym,   # brak nazwy => użyj tickera; VARCHAR(250)
        "asset_type": asset_type,
        "exchange": clean_text(exchange, 50) or "UNKNOWN",
        "currency": (clean_text(currency, 10) or "USD").upper(),
    }


# --------------------------------------------------------------------------- Wikipedia

def _flatten_column(col: object) -> str:
    """Kolumny wielopoziomowe (MultiIndex) spłaszczamy do jednego napisu."""
    if isinstance(col, tuple):
        return " ".join(dict.fromkeys(str(part).strip() for part in col))
    return str(col).strip()


def read_wiki_tables(url: str) -> list[pd.DataFrame]:
    response = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
    response.raise_for_status()  # 403/404/500 => wyjątek, a nie parsowanie strony błędu
    try:
        tables = pd.read_html(io.StringIO(response.text))
    except ValueError as exc:
        raise RuntimeError(f"brak tabel HTML na stronie ({exc})") from exc
    for table in tables:
        table.columns = [_flatten_column(c) for c in table.columns]
    return tables


def find_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    """Szuka kolumny po nazwie (bez względu na wielkość liter): najpierw dokładnie, potem "zawiera"."""
    lowered = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    for cand in candidates:
        for key, original in lowered.items():
            if cand.lower() in key:
                return original
    return None


def _token(raw: object) -> str | None:
    text = clean_text(raw, 60)
    return text.upper().replace(" ", "") if text else None


def us_symbol(raw: object) -> str | None:
    """USA: Yahoo zapisuje klasy akcji z myślnikiem (BRK.B -> BRK-B)."""
    token = _token(raw)
    return token.replace(".", "-") if token else None


def suffix_symbol(suffix: str) -> Callable[[object], str | None]:
    """Tworzy konwerter dodający sufiks giełdy (.TO, .DE, .PA, .AS, .L), o ile jeszcze go nie ma."""
    def convert(raw: object) -> str | None:
        token = _token(raw)
        if not token:
            return None
        _head, dot, dot_suffix = token.rpartition(".")
        if dot and dot_suffix in _EXCHANGE_SUFFIXES:
            return token
        if token.endswith(suffix):
            result = token
        else:
            result = token.replace(".", "-") + suffix  # BAM.A -> BAM-A.TO, BT.A -> BT-A.L
        if _has_two_exchange_suffixes(result):
            return None
        return result
    return convert


def fetch_index(
    label: str, url: str, ticker_cols: tuple[str, ...], name_cols: tuple[str, ...],
    symbol_fn: Callable[[object], str | None], exchange: str, currency: str, min_rows: int,
) -> list[Asset]:
    """Pobiera skład indeksu z Wikipedii. Każdy błąd jest logowany, a funkcja zwraca pustą listę."""
    try:
        tables = read_wiki_tables(url)
        table = next((t for t in tables if len(t) >= min_rows and find_column(t, ticker_cols)), None)
        if table is None:
            raise RuntimeError(f"nie znaleziono tabeli z kolumną {ticker_cols} i min. {min_rows} wierszami")
        ticker_col = find_column(table, ticker_cols)
        name_col = find_column(table, name_cols)

        assets: list[Asset] = []
        for _, row in table.iterrows():
            asset = make_asset(symbol_fn(row[ticker_col]), row[name_col] if name_col else None,
                               "STOCK", exchange, currency)
            if asset:
                assets.append(asset)
        return assets
    except Exception as exc:  # noqa: BLE001
        log.error("Błąd pobierania %s: %s", label, exc)
        return []


# --------------------------------------------------------------------------- źródła danych

def fetch_nasdaq100() -> list[Asset]:
    """Nasdaq-100. Wikipedia usunęła tabelę składu; slickcharts ma zwykłą tabelę Symbol/Company."""
    url = "https://www.slickcharts.com/nasdaq100"
    try:
        response = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        tables = pd.read_html(io.StringIO(response.text))
        table = next((t for t in tables if find_column(t, ("Symbol", "Ticker")) and len(t) >= 50), None)
        if table is None:
            raise RuntimeError("brak tabeli Symbol na stronie składu Nasdaq-100")
        ticker_col = find_column(table, ("Symbol", "Ticker"))
        name_col = find_column(table, ("Company", "Name", "Security"))
        assets: list[Asset] = []
        for _, row in table.iterrows():
            asset = make_asset(us_symbol(row[ticker_col]), row[name_col] if name_col else None,
                               "STOCK", "NASDAQ", "USD")
            if asset:
                assets.append(asset)
        return assets
    except Exception as exc:  # noqa: BLE001
        log.error("Błąd pobierania Nasdaq-100: %s", exc)
        return []


def fetch_us_stocks() -> list[Asset]:
    sp500 = fetch_index("S&P 500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                        ("Symbol", "Ticker"), ("Security", "Company"), us_symbol, "US", "USD", 100)
    return sp500 + fetch_nasdaq100()


def fetch_canada_and_latam() -> list[Asset]:
    tsx = fetch_index("TSX 60", "https://en.wikipedia.org/wiki/S%26P/TSX_60",
                      ("Symbol", "Ticker"), ("Company", "Security"), suffix_symbol(".TO"), "TSX", "CAD", 30)
    latam_adrs = [
        ("MELI", "MercadoLibre"), ("VALE", "Vale S.A."), ("PBR", "Petrobras"),
        ("ITUB", "Itaú Unibanco"), ("BBD", "Banco Bradesco"), ("NU", "Nu Holdings"),
        ("ABEV", "Ambev"), ("SQM", "Sociedad Química y Minera de Chile"),
        ("GGB", "Gerdau"), ("EC", "Ecopetrol"), ("BSBR", "Banco Santander Brasil"),
        ("XP", "XP Inc."), ("STNE", "StoneCo"), ("CIG", "CEMIG"), ("EBR", "Eletrobras"),
    ]
    adrs = [make_asset(s, n, "STOCK", "US_ADR", "USD") for s, n in latam_adrs]
    return tsx + [a for a in adrs if a]


def fetch_poland_gpw() -> list[Asset]:
    """Ręczna lista GPW. Sufiks .WA dodajemy w pętli, więc w liście są same kody z GPW."""
    gpw = [
        # WIG20
        ("PKO", "PKO Bank Polski"), ("PKN", "ORLEN"), ("DNP", "Dino Polska"), ("ALE", "Allegro"),
        ("CDR", "CD Projekt"), ("PZU", "PZU"), ("PEO", "Bank Pekao"), ("KGH", "KGHM Polska Miedź"),
        ("LPP", "LPP"), ("SPL", "Santander Bank Polska"), ("PGE", "PGE"), ("KRU", "Kruk"),
        ("ALR", "Alior Bank"), ("MBK", "mBank"), ("JSW", "JSW"), ("CPS", "Cyfrowy Polsat"),
        ("OPL", "Orange Polska"), ("KTY", "Grupa Kęty"), ("PCO", "Pepco Group"), ("BDX", "Budimex"),
        # mWIG40 i wybrane sWIG80
        ("CCC", "CCC"), ("MIL", "Bank Millennium"), ("TPE", "Tauron"), ("ENA", "Enea"),
        ("ACP", "Asseco Poland"), ("ASE", "Asseco South Eastern Europe"), ("EAT", "AmRest"),
        ("TXT", "Text (LiveChat)"), ("XTB", "XTB"), ("CAR", "Inter Cars"),
        ("GPW", "Giełda Papierów Wartościowych"), ("BFT", "Benefit Systems"), ("1AT", "Atal"),
        ("DOM", "Dom Development"), ("NEU", "Neuca"), ("CLN", "Celon Pharma"),
        ("RVU", "Ryvu Therapeutics"), ("MAB", "Mabion"), ("STS", "STS Holding"),
        ("EUR", "Eurocash"), ("PKP", "PKP Cargo"),
    ]
    assets = [make_asset(f"{code}.WA", name, "STOCK", "GPW", "PLN") for code, name in gpw]
    return [a for a in assets if a]


def fetch_europe() -> list[Asset]:
    """DAX, CAC 40, AEX i FTSE 100: każdy indeks ma własny sufiks i walutę (STOXX 600 nie ma ich w tabeli)."""
    indices = [
        ("DAX", "https://en.wikipedia.org/wiki/DAX", ".DE", "XETRA", "EUR", 30),
        ("CAC 40", "https://en.wikipedia.org/wiki/CAC_40", ".PA", "EURONEXT_PARIS", "EUR", 30),
        ("AEX", "https://en.wikipedia.org/wiki/AEX_index", ".AS", "EURONEXT_AMSTERDAM", "EUR", 20),
        # Yahoo notuje spółki z Londynu w pensach, stąd waluta "GBp", a nie "GBP".
        ("FTSE 100", "https://en.wikipedia.org/wiki/FTSE_100_Index", ".L", "LSE", "GBp", 80),
    ]
    assets: list[Asset] = []
    for label, url, suffix, exchange, currency, min_rows in indices:
        assets += fetch_index(label, url, ("Ticker", "Symbol"), ("Company", "Name"),
                              suffix_symbol(suffix), exchange, currency, min_rows)
    return assets


_NIKKEI_CODE = re.compile(r"\(\s*(?:TYO|TSE)\s*:\s*([0-9]{3}[0-9A-Z])\s*\)", re.IGNORECASE)


def fetch_japan_nikkei() -> list[Asset]:
    """Nikkei 225. Skład jest listą, nie tabelą: 'Nazwa (TYO: 7203)' -> 7203.T."""
    url = "https://en.wikipedia.org/wiki/Nikkei_225"
    try:
        response = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        doc = lxml_html.fromstring(response.content)
        section = doc.get_element_by_id("Components").getparent().getparent()
        assets: list[Asset] = []
        for heading in section.xpath(".//div[contains(@class, 'mw-heading3')]"):
            ul = heading.getnext()
            while ul is not None and ul.tag != "ul":
                ul = ul.getnext()
            if ul is None:
                continue
            for li in ul.xpath("./li"):
                text = " ".join(li.text_content().split())
                match = _NIKKEI_CODE.search(text)
                if not match:
                    continue
                name = text[:match.start()].strip(" -")
                asset = make_asset(f"{match.group(1)}.T", name or None, "STOCK", "TSE", "JPY")
                if asset:
                    assets.append(asset)
        if len(assets) < 100:
            raise RuntimeError(f"za mało spółek Nikkei ({len(assets)})")
        return assets
    except Exception as exc:  # noqa: BLE001
        log.error("Błąd pobierania Nikkei 225: %s", exc)
        return []


# Największe spółki KOSPI. KRX wymaga logowania, więc bez pykrx. Sufiks Yahoo: .KS.
_KOSPI_TOP: tuple[tuple[str, str], ...] = (
    ("005930", "Samsung Electronics"), ("000660", "SK hynix"), ("373220", "LG Energy Solution"),
    ("207940", "Samsung Biologics"), ("005380", "Hyundai Motor"), ("000270", "Kia"),
    ("068270", "Celltrion"), ("005490", "POSCO Holdings"), ("035420", "NAVER"), ("035720", "Kakao"),
    ("105560", "KB Financial"), ("055550", "Shinhan Financial"), ("086790", "Hana Financial"),
    ("012330", "Hyundai Mobis"), ("066570", "LG Electronics"), ("051910", "LG Chem"),
    ("006400", "Samsung SDI"), ("028260", "Samsung C&T"), ("003550", "LG Corp"),
    ("017670", "SK Telecom"), ("030200", "KT"), ("032830", "Samsung Life"),
    ("000810", "Samsung Fire & Marine"), ("009150", "Samsung Electro-Mechanics"),
    ("018260", "Samsung SDS"), ("033780", "KT&G"), ("015760", "KEPCO"), ("034730", "SK Inc"),
    ("096770", "SK Innovation"), ("010130", "Korea Zinc"), ("267250", "HD Hyundai"),
    ("329180", "HD Hyundai Heavy Industries"), ("009540", "HD Korea Shipbuilding"),
    ("010950", "S-Oil"), ("024110", "Industrial Bank of Korea"), ("316140", "Woori Financial"),
    ("138040", "Meritz Financial"), ("323410", "KakaoBank"), ("259960", "Krafton"),
    ("352820", "HYBE"), ("011200", "HMM"), ("003670", "POSCO Future M"), ("011070", "LG Innotek"),
    ("090430", "Amorepacific"), ("021240", "Coway"), ("097950", "CJ CheilJedang"),
    ("000720", "Hyundai E&C"), ("004020", "Hyundai Steel"), ("003490", "Korean Air"),
    ("034220", "LG Display"), ("051900", "LG H&H"), ("006800", "Mirae Asset Securities"),
    ("032640", "LG Uplus"), ("028050", "Samsung Engineering"), ("009830", "Hanwha Solutions"),
    ("000880", "Hanwha"), ("012450", "Hanwha Aerospace"), ("042660", "Hanwha Ocean"),
    ("010140", "Samsung Heavy Industries"), ("011170", "Lotte Chemical"),
    ("004990", "Lotte Holdings"), ("005830", "DB Insurance"), ("006360", "GS Holdings"),
    ("001040", "CJ Corp"), ("011780", "Kumho Petrochemical"), ("000150", "Doosan"),
    ("034020", "Doosan Enerbility"), ("064350", "Hyundai Rotem"), ("047050", "POSCO International"),
    ("036570", "NCSoft"), ("251270", "Netmarble"), ("000990", "DB HiTek"),
    ("128940", "Hanmi Pharm"), ("196170", "Alteogen"), ("402340", "SK Square"),
    ("326030", "SK Biopharmaceuticals"), ("302440", "SK Bioscience"), ("000100", "Yuhan"),
    ("271560", "Orion"), ("139480", "E-Mart"), ("282330", "BGF Retail"),     ("004170", "Shinsegae"), ("298040", "Hyosung"),
)


def fetch_korea_top(limit: int = 80) -> list[Asset]:
    """Statyczna lista dużych spółek KOSPI. API KRX (pykrx) wymaga już konta."""
    assets = [
        make_asset(f"{code}.KS", name, "STOCK", "KOSPI", "KRW")
        for code, name in _KOSPI_TOP[:limit]
    ]
    return [a for a in assets if a]


def fetch_indices() -> list[Asset]:
    """Tickery indeksów zaczynają się od ^, więc nie przechodzą przez walidację akcji."""
    indices = [
        ("^GSPC", "S&P 500", "USD"), ("^IXIC", "Nasdaq Composite", "USD"),
        ("^DJI", "Dow Jones Industrial Average", "USD"), ("^GDAXI", "DAX", "EUR"),
        ("WIG20.WA", "WIG20", "PLN"), ("^FTSE", "FTSE 100", "GBP"),
        ("^FCHI", "CAC 40", "EUR"), ("^N225", "Nikkei 225", "JPY"),
    ]
    return [
        {"symbol": symbol, "name": name, "asset_type": "INDEX", "exchange": "INDEX", "currency": currency}
        for symbol, name, currency in indices
    ]


# Fundusze akcyjne: próbujemy składu. Fizyczne metale, ropa, bitcoin i alokacje wieloaktyowe nie mają spółek.
ETF_CATEGORIES: dict[str, list[tuple[str, str]]] = {
    "sektory_us": [
        ("XLY", "Consumer Discretionary SPDR"), ("VCR", "Consumer Discretionary Vanguard"),
        ("XLP", "Consumer Staples SPDR"), ("VDC", "Consumer Staples Vanguard"),
        ("XLE", "Energy SPDR"), ("VDE", "Energy Vanguard"),
        ("XLF", "Financials SPDR"), ("VFH", "Financials Vanguard"),
        ("XLK", "Technology SPDR"), ("VGT", "Information Technology Vanguard"),
        ("XLB", "Materials SPDR"), ("VAW", "Materials Vanguard"),
        ("XLRE", "Real Estate SPDR"), ("VNQ", "Real Estate Vanguard"),
        ("XLV", "Health Care SPDR"), ("VHT", "Health Care Vanguard"),
        ("XLI", "Industrials SPDR"), ("XLU", "Utilities SPDR"), ("XLC", "Communication Services SPDR"),
    ],
    "surowce": [
        ("UNG", "US Natural Gas Fund"), ("PDBC", "Invesco Optimum Yield Diversified Commodity"),
        ("GSG", "iShares S&P GSCI Commodity"), ("CPER", "US Copper Index"),
        ("COPX", "Global X Copper Miners"), ("PPLT", "abrdn Physical Platinum"),
        ("USO", "US Oil Fund"), ("BNO", "US Brent Oil"),
        ("SLV", "iShares Silver"), ("SIVR", "abrdn Physical Silver"),
        ("GLD", "SPDR Gold"), ("IAU", "iShares Gold"),
    ],
    "tematyczne": [
        ("IBB", "iShares Biotechnology"), ("XBI", "SPDR Biotech"),
        ("IBIT", "iShares Bitcoin Trust"), ("FBTC", "Fidelity Bitcoin"),
        ("BLOK", "Amplify Transformational Data Sharing"), ("FDIG", "Fidelity Crypto Industry"),
        ("CIBR", "First Trust Cybersecurity"), ("HACK", "Amplify Cybersecurity"),
        ("ICLN", "iShares Global Clean Energy"), ("TAN", "Invesco Solar"),
        ("VTV", "Vanguard Value"), ("SCHV", "Schwab US Large-Cap Value"),
        ("DBMF", "iMGP DBi Managed Futures"), ("KMLM", "KFA Mount Lucas Managed Futures"),
        ("MTUM", "iShares MSCI USA Momentum"), ("PDP", "Invesco DWA Momentum"),
        ("AOA", "iShares Core Aggressive Allocation"), ("IYLD", "iShares Morningstar Multi-Asset Income"),
        ("ITA", "iShares US Aerospace & Defense"), ("PPA", "Invesco Aerospace & Defense"),
        ("SMH", "VanEck Semiconductor"), ("SOXX", "iShares Semiconductor"),
        ("SCHD", "Schwab US Dividend Equity"), ("VYM", "Vanguard High Dividend Yield"),
        ("BOTZ", "Global X Robotics & AI"), ("AIQ", "Global X Artificial Intelligence"),
        ("LIT", "Global X Lithium & Battery Tech"), ("BATT", "Amplify Lithium & Battery Technology"),
        ("PHO", "Invesco Water Resources"), ("CGW", "Invesco S&P Global Water"),
        ("KRBN", "KraneShares Global Carbon"), ("CRBN", "iShares MSCI Global Impact"),
        ("ARKK", "ARK Innovation"), ("URA", "Global X Uranium"),
    ],
    "rynek": [
        ("SPY", "S&P 500 ETF"), ("QQQ", "Nasdaq 100 ETF"), ("VTI", "Total Stock Market"),
        ("IWM", "Russell 2000 ETF"), ("EFA", "MSCI EAFE ETF"), ("EEM", "Emerging Markets ETF"),
        ("EWZ", "MSCI Brazil ETF"), ("EWC", "MSCI Canada ETF"), ("EWG", "MSCI Germany ETF"),
    ],
}

# Brak spółek w składzie: metal fizyczny, kontrakty, bitcoin, alokacja, certyfikaty emisji.
_NO_STOCK_HOLDINGS = {
    "UNG", "PDBC", "GSG", "CPER", "PPLT", "USO", "BNO", "SLV", "SIVR", "GLD", "IAU",
    "IBIT", "FBTC", "DBMF", "KMLM", "AOA", "IYLD", "KRBN",
}


def fetch_etfs() -> list[Asset]:
    assets: list[Asset] = []
    for pairs in ETF_CATEGORIES.values():
        for symbol, name in pairs:
            asset = make_asset(symbol, name, "ETF", "US", "USD")
            if asset:
                assets.append(asset)
    return assets


def fetch_macro() -> list[Asset]:
    """Krypto, forex i surowce. Tickery, które Yahoo odrzuci, wyłączy potem mechanizm error_count."""
    # POL (dawniej MATIC) i RENDER (dawniej RNDR) zmieniły nazwy. Część nowszych monet
    # (np. SUI, APT, ARB, OP) Yahoo zapisuje z numerycznym identyfikatorem, więc zweryfikuj je na stronie Yahoo.
    cryptos = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK",
        "POL", "LTC", "UNI", "ATOM", "ICP", "SHIB", "TRX", "BCH", "NEAR", "APT",
        "STX", "FIL", "ETC", "RENDER", "HBAR", "INJ", "TIA", "OP", "ARB", "SUI20947",
    ]
    forex = [
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
        "EURPLN", "USDPLN", "CHFPLN", "GBPPLN", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY",
    ]
    # Zboża w Yahoo mają prefiks "Z" (ZC=F, nie C=F), a kontrakt na 10-letnie obligacje to ZN=F.
    commodities = [
        ("GC=F", "Gold"), ("SI=F", "Silver"), ("PL=F", "Platinum"), ("PA=F", "Palladium"),
        ("CL=F", "Crude Oil WTI"), ("BZ=F", "Brent Crude Oil"), ("NG=F", "Natural Gas"),
        ("RB=F", "RBOB Gasoline"), ("HO=F", "Heating Oil"), ("HG=F", "Copper"),
        ("ALI=F", "Aluminum"), ("ZC=F", "Corn"), ("ZW=F", "Wheat"), ("ZS=F", "Soybeans"),
        ("ZM=F", "Soybean Meal"), ("ZL=F", "Soybean Oil"), ("ZR=F", "Rough Rice"),
        ("KC=F", "Coffee"), ("CC=F", "Cocoa"), ("SB=F", "Sugar"), ("CT=F", "Cotton"),
        ("OJ=F", "Orange Juice"), ("LBR=F", "Lumber"), ("LE=F", "Live Cattle"),
        ("GF=F", "Feeder Cattle"), ("HE=F", "Lean Hogs"),
        ("ZN=F", "10-Year T-Note Futures"), ("ZB=F", "30-Year T-Bond Futures"), ("DX-Y.NYB", "US Dollar Index"),
    ]
    assets = [make_asset(f"{c}-USD", f"{c} Crypto", "CRYPTO", "CCC", "USD") for c in cryptos]
    assets += [make_asset(f"{p}=X", f"Forex {p}", "FOREX", "CCY", p[3:]) for p in forex]
    assets += [make_asset(s, n, "COMMODITY", "FUTURES", "USD") for s, n in commodities]
    return [a for a in assets if a]


# --------------------------------------------------------------------------- uruchomienie

def collect_assets() -> list[Asset]:
    """Zbiera aktywa ze wszystkich źródeł. Awaria jednego źródła nie przerywa pozostałych."""
    sources: list[tuple[str, Callable[[], list[Asset]]]] = [
        ("USA (S&P 500, Nasdaq)", fetch_us_stocks),
        ("Kanada + LatAm", fetch_canada_and_latam),
        ("Polska (GPW)", fetch_poland_gpw),
        ("Europa (DE/FR/NL/UK)", fetch_europe),
        ("Japonia (Nikkei 225)", fetch_japan_nikkei),
        ("Korea (KOSPI)", fetch_korea_top),
        ("ETF-y", fetch_etfs),
        ("Indeksy", fetch_indices),
        ("Krypto/Forex/Surowce", fetch_macro),
    ]
    unique: dict[str, Asset] = {}  # deduplikacja po tickerze: pierwsze źródło wygrywa
    for label, fetch in sources:
        try:
            found = fetch()
        except Exception:  # noqa: BLE001
            log.exception("Źródło %s zakończyło się błędem", label)
            found = []
        new = 0
        for asset in found:
            if asset["symbol"] not in unique:
                unique[asset["symbol"]] = asset
                new += 1
        log.info("%-24s pobrano %4d, nowych unikalnych %4d", label, len(found), new)
    return list(unique.values())


_SPDR = ("XLK", "XLF", "XLV", "XLE", "XLI", "XLP", "XLY", "XLC", "XLB", "XLU", "XLRE")
_ARK_CSV = "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv"
_SKIP_HOLDING = {"", "-", "CASH", "USD", "N/A", "NAN"}
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# Sufiksy giełd Yahoo. Jednoliterowa klasa akcji (BRK-B) tu nie wchodzi.
_EXCHANGE_SUFFIXES = {
    "T", "L", "SW", "TO", "DE", "PA", "AS", "WA", "KS", "KQ", "HK", "AX",
    "SI", "TW", "TWO", "OL", "ST", "HE", "CO", "IR", "VI", "MI", "MC", "BR",
}
_CASH_SYMBOLS = {"USD", "EUR", "GBP", "CHF", "JPY", "CAD", "CASH", "CASH_USD", "CASH_EUR"}
_FUTURES_TICKER = re.compile(r"^[A-Z][A-Z0-9]*\d$")
_CASH_CODE = re.compile(r"^\d{5,}[A-Z]$")


def _has_two_exchange_suffixes(token: str) -> bool:
    """AIR-PA.DE i MT-AS.PA: sufiks po myślniku i po kropce to dwie giełdy."""
    if "-" not in token or "." not in token:
        return False
    head, _, dot_suffix = token.rpartition(".")
    if dot_suffix not in _EXCHANGE_SUFFIXES:
        return False
    return any(part in _EXCHANGE_SUFFIXES for part in head.split("-")[1:])


def normalize_ticker(raw_symbol: object) -> str | None:
    """Ticker z arkusza wystawcy na format Yahoo. Śmieci zwracają None.

    Ostatni myślnik przed sufiksem giełdy zamienia na kropkę:
    ANTO-L -> ANTO.L, TECK-B-TO -> TECK-B.TO. Klasa akcji USA zostaje (BRK-B).
    Same cyfry, podwójny sufiks giełdy, futures, gotówka i symbol
    z samych liter dłuższy niż 5 znaków (RKLBUQ) są odrzucane.
    """
    token = _token(raw_symbol)
    if token is None:
        return None
    if token in _CASH_SYMBOLS or token.startswith("CASH") or token.isdigit():
        return None
    if token.startswith("^"):
        return token if re.fullmatch(r"\^[A-Z0-9][A-Z0-9.\-]{0,28}", token) else None
    if _has_two_exchange_suffixes(token):
        return None
    if "-" in token:
        base, _, suffix = token.rpartition("-")
        if suffix in _EXCHANGE_SUFFIXES and base:
            token = f"{base}.{suffix}"
    class_share = re.fullmatch(r"([A-Z]+)\.([A-Z])", token)
    if class_share and class_share.group(2) not in _EXCHANGE_SUFFIXES:
        token = f"{class_share.group(1)}-{class_share.group(2)}"
    if _has_two_exchange_suffixes(token):
        return None
    if token.isalpha() and len(token) >= 6:
        return None
    if _FUTURES_TICKER.fullmatch(token) or _CASH_CODE.fullmatch(token):
        return None
    if not _SYMBOL_RE.fullmatch(token):
        return None
    return token


def listing_ticker(symbol: str, name: object) -> str:
    """GDR bez sufiksu giełdy (KAP) to notowanie londyńskie KAP.L."""
    text = str(name or "").upper()
    if "GDR" in text and "." not in symbol and "-" not in symbol:
        return f"{symbol}.L"
    return symbol


def _holding(symbol: object, name: object, weight: object, *, as_fraction: bool = False) -> dict[str, str | float | None] | None:
    ticker = normalize_ticker(symbol)
    if ticker is None:
        return None
    ticker = listing_ticker(ticker, name)
    asset = make_asset(ticker, name, "STOCK", "US", "USD")
    if asset is None or asset["symbol"] in _SKIP_HOLDING:
        return None
    try:
        number = float(str(weight).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        number = None
    if number is not None and as_fraction:
        number *= 100
    return {"symbol": asset["symbol"], "name": asset["name"], "weight_percentage": number}


def _xlsx_rows(content: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(content)) as book:
        shared = ET.fromstring(book.read("xl/sharedStrings.xml"))
        strings = ["".join(node.itertext()) for node in shared.findall(f".//{_XLSX_NS}si")]
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in sheet.findall(f".//{_XLSX_NS}sheetData/{_XLSX_NS}row"):
        cells: dict[str, str] = {}
        for cell in row.findall(f"{_XLSX_NS}c"):
            ref = cell.get("r") or ""
            column = "".join(ch for ch in ref if ch.isalpha())
            value = cell.find(f"{_XLSX_NS}v")
            if value is None or not column:
                continue
            raw = value.text or ""
            cells[column] = strings[int(raw)] if cell.get("t") == "s" else raw
        if cells:
            last = max(cells)
            rows.append([cells.get(chr(ord("A") + i), "") for i in range(ord(last) - ord("A") + 1)])
    return rows


def _rows_from_header(rows: list[list[str]], ticker_names: tuple[str, ...], weight_names: tuple[str, ...]) -> list[dict]:
    header_at = None
    for index, row in enumerate(rows):
        lowered = [cell.strip().lower() for cell in row]
        if any(name in lowered for name in ticker_names) and any(name in lowered for name in weight_names):
            header_at = index
            break
    if header_at is None:
        return []
    header = [cell.strip().lower() for cell in rows[header_at]]
    ticker_col = next(i for i, cell in enumerate(header) if cell in ticker_names)
    weight_col = next(i for i, cell in enumerate(header) if cell in weight_names)
    name_col = next((i for i, cell in enumerate(header) if cell in {"name", "company"}), None)
    holdings = []
    for row in rows[header_at + 1:]:
        if ticker_col >= len(row):
            continue
        name = row[name_col] if name_col is not None and name_col < len(row) else None
        weight = row[weight_col] if weight_col < len(row) else None
        holding = _holding(row[ticker_col], name, weight)
        if holding:
            holdings.append(holding)
    return holdings


def _http_bytes(url: str) -> bytes:
    response = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    return response.content


def _issuer_holdings(symbol: str) -> list[dict]:
    if symbol in _SPDR:
        url = (
            "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/"
            f"holdings-daily-us-en-{symbol.lower()}.xlsx"
        )
        return _rows_from_header(_xlsx_rows(_http_bytes(url)), ("ticker",), ("weight",))
    if symbol == "ARKK":
        text = _http_bytes(_ARK_CSV).decode("utf-8", errors="replace")
        holdings = []
        for row in csv.DictReader(io.StringIO(text)):
            holding = _holding(row.get("ticker"), row.get("company"), row.get("weight (%)"))
            if holding:
                holdings.append(holding)
        return holdings
    return []


def _yahoo_top_holdings(symbol: str) -> list[dict]:
    import yfinance as yf
    frame = yf.Ticker(symbol).funds_data.top_holdings
    if frame is None or getattr(frame, "empty", True):
        return []
    holdings = []
    for ticker, row in frame.iterrows():
        holding = _holding(ticker, row.get("Name"), row.get("Holding Percent"), as_fraction=True)
        if holding:
            holdings.append(holding)
    return holdings


def seed_etf_constituents() -> None:
    """Skład sektorowych i tematycznych ETF. Najpierw plik wystawcy, inaczej top 10 z Yahoo."""
    tracked = [
        symbol
        for pairs in ETF_CATEGORIES.values()
        for symbol, _name in pairs
        if symbol not in _NO_STOCK_HOLDINGS
    ]
    for symbol in tracked:
        source = "ISSUER"
        try:
            holdings = _issuer_holdings(symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: plik wystawcy niedostępny (%s)", symbol, exc)
            holdings = []
        if len(holdings) < 15:
            source = "YAHOO_TOP"
            try:
                holdings = _yahoo_top_holdings(symbol)
            except Exception as exc:  # noqa: BLE001
                log.error("%s: brak składu (%s)", symbol, exc)
                continue
        if not holdings:
            log.error("%s: pusty skład", symbol)
            continue
        db.insert_stocks_if_missing(holdings)
        linked = db.replace_etf_constituents(symbol, holdings, source)
        log.info("%s: skład %s, zapisanych relacji %s", symbol, source, linked)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    assets = collect_assets()
    if not assets:
        log.error("Nie zebrano żadnych aktywów. Nic nie zapisuję.")
        return 1
    log.info("RAZEM DO ZAPISU: %s aktywów.", len(assets))
    try:
        saved = db.bulk_upsert_assets(assets)
        log.info("Zapisano %s aktywów w bazie.", saved)
        seed_etf_constituents()
    finally:
        db.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
