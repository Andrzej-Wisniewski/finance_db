# Słownik danych finance_db

Pięć tabel. `daily_quotes`, `financial_reports`, `valuation_ratios` i `etf_constituents` wskazują na `assets.asset_id`. Usunięcie wiersza z `assets` usuwa wiersze zależne.

`financial_reports` dotyczy wyłącznie `asset_type = STOCK`. `valuation_ratios` dotyczy `STOCK`. `etf_constituents.etf_id` wskazuje aktywo `ETF`, `stock_id` wskazuje aktywo `STOCK`.

## assets

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
| is_active | BOOLEAN | Czy instrument jest w kolejce pobierania. |
| error_count | SMALLINT UNSIGNED | Liczba kolejnych nieudanych pobrań. |
| last_updated_at | DATETIME | Czas ostatniego udanego zapisu. Puste oznacza brak historii. |
| created_at | TIMESTAMP | Czas utworzenia wiersza. |
| updated_at | TIMESTAMP | Czas ostatniej zmiany wiersza. |

Indeks `idx_assets_queue (is_active, last_updated_at, asset_id)` obsługuje kolejkę pobierania.

## daily_quotes

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

Unikalność: asset_id, period_end, period_type, statement_type, line_item.

## valuation_ratios

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

Unikalność: asset_id, snapshot_date.

## etf_constituents

| Kolumna | Typ | Opis |
|---|---|---|
| etf_id | BIGINT UNSIGNED | Fundusz. Wskazuje assets.asset_id. |
| stock_id | BIGINT UNSIGNED | Spółka w składzie. Wskazuje assets.asset_id. |
| weight_percentage | DECIMAL(8,4) | Udział spółki w funduszu wyrażony w procentach. |
| source | ENUM | Pochodzenie składu: ISSUER albo YAHOO_TOP. |
| updated_at | TIMESTAMP | Czas ostatniej zmiany relacji. |

Klucz główny: etf_id, stock_id.
