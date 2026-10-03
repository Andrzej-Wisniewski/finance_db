# Słownik danych finance_db

MySQL 8, InnoDB, `utf8mb4` / `utf8mb4_unicode_ci`. Pięć tabel. `daily_quotes`, `financial_reports`, `valuation_ratios` i `etf_constituents` wskazują na `assets.asset_id`. Usunięcie wiersza z `assets` usuwa wiersze zależne (`ON DELETE CASCADE`).

`financial_reports` i `valuation_ratios` są zapisywane dla `asset_type = STOCK`. `etf_constituents.etf_id` wskazuje aktywo `ETF`, `stock_id` wskazuje aktywo `STOCK`.

Retencja danych rynkowych to 3 lata. Procedura `sp_prune_old_market_data` i zdarzenie `ev_prune_market_data_3y` (raz dziennie) usuwają stare wiersze z `daily_quotes`, `financial_reports` i `valuation_ratios`. To samo wywołuje `update_data.py` na końcu przebiegu.

## Jak uruchomić

Potrzebny jest lokalny plik `.env` z `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD` i `DB_NAME`. Hasło nie trafia do skryptów.

Od zera, razem z historią notowań:

```bash
bash init_project.sh
```

Skrypt pyta o potwierdzenie, kasuje bazę z `.env`, wczytuje `schema.sql`, tworzy `venv`, instaluje `requirements.txt`, uruchamia `seed_tickers.py`, a potem powtarza `update_data.py`, aż skończą się aktywa bez `last_updated_at`.

To samo ręcznie:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
mysql -h 127.0.0.1 -P 3306 -u "$DB_USER" -p --default-character-set=utf8mb4 < schema.sql
./venv/bin/python seed_tickers.py
./venv/bin/python update_data.py --missing
```

Codziennie, bez flagi, `update_data.py` bierze aktywne rekordy z pustym `last_updated_at` albo z datą starszą niż dziś i powtarza paczki, aż każdy ma zapis z bieżącego dnia. Harmonogram jest w `setup_cron.txt` (02:00, poniedziałek–piątek). `run_update.sh` odpala ten sam skrypt z crona.

`--missing` dociąga tylko aktywa, które nie mają jeszcze żadnego zapisu.

## Pliki

| Plik | Rola |
|---|---|
| `schema.sql` | Tworzy bazę, tabele, procedurę i zdarzenie retencji. |
| `config.py` | Czyta `.env` i parametry przebiegu: wielkość paczki, retencję, okno historii. |
| `db.py` | Pula połączeń, kolejka aktywów, zapis UPSERT i retencja wołana z Pythona. |
| `yf_fetcher.py` | Pobiera i czyści notowania, sprawozdania oraz wskaźniki z Yahoo Finance. |
| `seed_tickers.py` | Wpisuje aktywa, indeksy i składy ETF. Można uruchamiać wielokrotnie. |
| `update_data.py` | Codzienna aktualizacja notowań, sprawozdań i wskaźników. |
| `requirements.txt` | Zależności Pythona. |
| `init_project.sh` | Reset bazy i pierwsze pobranie historii. |
| `run_update.sh` | Opakowanie crona wokół `update_data.py`. |
| `setup_cron.txt` | Instrukcja wpisu crontab. |
| `DB_SCHEMA_GUIDE.md` | Ten słownik. |

## assets

Klucz główny: `asset_id`. Unikalność: `uq_assets_symbol (symbol)`.

Indeksy: `idx_assets_queue (is_active, last_updated_at, asset_id)`, `idx_assets_type_active (asset_type, is_active)`, `idx_assets_created_at (created_at)`, `idx_assets_last_updated_at (last_updated_at)`.

| Kolumna | Typ | Opis |
|---|---|---|
| asset_id | BIGINT UNSIGNED | Identyfikator aktywa używany jako klucz obcy. |
| symbol | VARCHAR(32) | Unikalny ticker w formacie źródła notowań. |
| name | VARCHAR(250) | Nazwa instrumentu. |
| asset_type | ENUM | Rodzaj instrumentu: STOCK, ETF, COMMODITY, FOREX, CRYPTO, INDEX. |
| exchange | VARCHAR(50) | Rynek notowań. |
| currency | VARCHAR(10) | Waluta notowania. |
| shares_outstanding | BIGINT UNSIGNED | Liczba akcji albo podaży w obiegu. |
| sector | VARCHAR(100) | Sektor gospodarczy. |
| industry | VARCHAR(150) | Branża. |
| market_cap | BIGINT UNSIGNED | Kapitalizacja rynkowa. |
| is_active | BOOLEAN | Czy instrument jest w kolejce pobierania. Domyślnie prawda. |
| error_count | SMALLINT UNSIGNED | Liczba kolejnych nieudanych pobrań. Domyślnie zero. |
| last_updated_at | DATETIME | Czas ostatniego udanego zapisu. Puste oznacza brak historii. |
| created_at | TIMESTAMP | Czas utworzenia wiersza. |
| updated_at | TIMESTAMP | Czas ostatniej zmiany wiersza. |

## daily_quotes

Klucz główny: `(asset_id, quote_date)`.

Klucz obcy: `fk_dq_asset` — `asset_id` → `assets.asset_id`, `ON DELETE CASCADE`, `ON UPDATE CASCADE`.

Indeksy: `idx_dq_date (quote_date)`, `idx_dq_created_at (created_at)`.

| Kolumna | Typ | Opis |
|---|---|---|
| asset_id | BIGINT UNSIGNED | Aktywo, którego dotyczy sesja. |
| quote_date | DATE | Data sesji. Razem z asset_id tworzy klucz główny. |
| open_price | DECIMAL(24,8) | Cena otwarcia. |
| high_price | DECIMAL(24,8) | Najwyższa cena sesji. |
| low_price | DECIMAL(24,8) | Najniższa cena sesji. |
| close_price | DECIMAL(24,8) | Cena zamknięcia. |
| adj_close | DECIMAL(24,8) | Cena zamknięcia skorygowana o dywidendy i splity. |
| volume | BIGINT UNSIGNED | Wolumen sesji. |
| created_at | TIMESTAMP | Czas utworzenia wiersza. |
| updated_at | TIMESTAMP | Czas ostatniej zmiany wiersza. |

## financial_reports

Klucz główny: `report_id`. Unikalność: `uq_financial_reports (asset_id, period_end, period_type, statement_type, line_item)`.

Klucz obcy: `fk_reports_asset` — `asset_id` → `assets.asset_id`, `ON DELETE CASCADE`, `ON UPDATE CASCADE`.

Indeksy: `idx_reports_year_period (asset_id, report_year, period_type)`, `idx_reports_period_end (period_end)`, `idx_reports_created_at (created_at)`.

| Kolumna | Typ | Opis |
|---|---|---|
| report_id | BIGINT UNSIGNED | Identyfikator pozycji sprawozdania. |
| asset_id | BIGINT UNSIGNED | Spółka, której dotyczy pozycja. |
| period_end | DATE | Data końca okresu sprawozdawczego. |
| report_year | SMALLINT UNSIGNED | Rok okresu. |
| period_type | ENUM | Rodzaj okresu: YEAR, Q1, Q2, Q3, Q4. |
| statement_type | ENUM | Sprawozdanie: INCOME, BALANCE, CASH_FLOW. |
| line_item | VARCHAR(255) | Nazwa pozycji ze źródła. |
| value | DECIMAL(25,2) | Kwota pozycji. |
| created_at | TIMESTAMP | Czas utworzenia wiersza. |
| updated_at | TIMESTAMP | Czas ostatniej zmiany wiersza. |

## valuation_ratios

Klucz główny: `ratio_id`. Unikalność: `uq_ratios_asset_date (asset_id, snapshot_date)`.

Klucz obcy: `fk_ratios_asset` — `asset_id` → `assets.asset_id`, `ON DELETE CASCADE`, `ON UPDATE CASCADE`.

Indeksy: `idx_ratios_snapshot_date (snapshot_date)`, `idx_ratios_created_at (created_at)`.

| Kolumna | Typ | Opis |
|---|---|---|
| ratio_id | BIGINT UNSIGNED | Identyfikator snapshotu. |
| asset_id | BIGINT UNSIGNED | Aktywo, którego dotyczy snapshot. |
| snapshot_date | DATE | Dzień zapisu wskaźników. |
| current_price | DECIMAL(24,8) | Cena w chwili pobrania. |
| fifty_two_week_low | DECIMAL(24,8) | Minimum ceny z ostatnich 52 tygodni. |
| fifty_two_week_high | DECIMAL(24,8) | Maksimum ceny z ostatnich 52 tygodni. |
| target_mean_price | DECIMAL(24,8) | Średnia cena docelowa analityków. |
| analyst_count | INT UNSIGNED | Liczba opinii analityków. |
| trailing_pe | DECIMAL(20,4) | Cena do zysku za ostatnie dwanaście miesięcy. |
| forward_pe | DECIMAL(20,4) | Cena do prognozowanego zysku. |
| price_to_sales | DECIMAL(20,4) | Cena do przychodów za ostatnie dwanaście miesięcy. |
| price_to_book | DECIMAL(20,4) | Cena do wartości księgowej. |
| ev_to_ebitda | DECIMAL(20,4) | Wartość przedsiębiorstwa do EBITDA. |
| enterprise_to_revenue | DECIMAL(20,4) | Wartość przedsiębiorstwa do przychodów. |
| peg_ratio | DECIMAL(20,4) | Cena do zysku względem tempa wzrostu. |
| return_on_equity | DECIMAL(20,4) | Rentowność kapitału własnego. |
| return_on_assets | DECIMAL(20,4) | Rentowność aktywów. |
| profit_margin | DECIMAL(20,4) | Marża zysku netto. |
| gross_margin | DECIMAL(20,4) | Marża brutto. |
| operating_margin | DECIMAL(20,4) | Marża operacyjna. |
| quick_ratio | DECIMAL(20,4) | Wskaźnik podwyższonej płynności. |
| current_ratio | DECIMAL(20,4) | Wskaźnik bieżącej płynności. |
| dividend_yield | DECIMAL(20,4) | Stopa dywidendy. |
| payout_ratio | DECIMAL(20,4) | Udział zysku wypłacony jako dywidenda. |
| revenue_growth | DECIMAL(20,4) | Tempo wzrostu przychodów. |
| earnings_growth | DECIMAL(20,4) | Tempo wzrostu zysku. |
| total_debt | BIGINT | Zadłużenie ogółem. |
| total_cash | BIGINT | Środki pieniężne ogółem. |
| free_cashflow | BIGINT | Wolne przepływy pieniężne. |
| operating_cashflow | BIGINT | Przepływy pieniężne z działalności operacyjnej. |
| debt_to_equity | DECIMAL(20,4) | Relacja długu do kapitału własnego. |
| created_at | TIMESTAMP | Czas utworzenia wiersza. |
| updated_at | TIMESTAMP | Czas ostatniej zmiany wiersza. |

## etf_constituents

Klucz główny: `(etf_id, stock_id)`.

Klucze obce: `fk_constituents_etf` — `etf_id` → `assets.asset_id`; `fk_constituents_stock` — `stock_id` → `assets.asset_id`. Oba z `ON DELETE CASCADE` i `ON UPDATE CASCADE`.

Indeks: `idx_constituents_stock (stock_id)`.

| Kolumna | Typ | Opis |
|---|---|---|
| etf_id | BIGINT UNSIGNED | Fundusz. Wskazuje assets.asset_id. |
| stock_id | BIGINT UNSIGNED | Spółka w składzie. Wskazuje assets.asset_id. |
| weight_percentage | DECIMAL(8,4) | Udział spółki w funduszu wyrażony w procentach. |
| source | ENUM | Pochodzenie składu: ISSUER albo YAHOO_TOP. |
| updated_at | TIMESTAMP | Czas ostatniej zmiany relacji. |
