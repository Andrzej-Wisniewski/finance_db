# finance_db
Lokalna baza notowań i fundamentów. Python zaciąga tickery z Yahoo Finance, MySQL trzyma historię, a codzienny przebieg dopisuje tylko to, czego jeszcze nie ma z dzisiaj.
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MySQL](https://img.shields.io/badge/MySQL-8.x-4479A1?logo=mysql&logoColor=white)](https://www.mysql.com/)
## ✨ Features
- **Inicjalizacja bazy** z `schema.sql`: aktywa, notowania, sprawozdania, wskaźniki i składy ETF.
- **Integracja z Yahoo Finance** przez `yfinance`: OHLCV, raporty wyłącznie dla `STOCK` oraz snapshot wskaźników.
- **Zasiew tickerów** (`seed_tickers.py`): akcje, ETF-y, indeksy, forex, krypto i surowce. Skrypt można odpalać ponownie.
- **Codzienna aktualizacja** (`update_data.py`): aktywne rekordy bez zapisu albo starsze niż dziś, paczkami, z UPSERT bez duplikatów.
- **Automatyzacja** cronem w dni robocze. Instrukcja jest w `setup_cron.txt`.
- **Retencja 3 lat** dla notowań, raportów i wskaźników.
## 📁 Project Structure
```text
finance_db/
├── schema.sql            # DDL: tabele, klucze, retencja
├── config.py             # .env oraz parametry przebiegu
├── db.py                 # pula MySQL, kolejka, UPSERT
├── yf_fetcher.py         # pobranie i czyszczenie danych z Yahoo Finance
├── seed_tickers.py       # pierwszy i ponowny zasiew aktywów oraz składów ETF
├── update_data.py        # codzienna aktualizacja notowań, raportów i wskaźników
├── init_project.sh       # reset bazy i pierwsze pobranie historii
├── run_update.sh         # opakowanie crona wokół update_data.py
├── setup_cron.txt        # wpis crontab
├── requirements.txt      # zależności Pythona
├── DB_SCHEMA_GUIDE.md    # słownik tabel i relacji
└── README.md
```
Poza tym w katalogu roboczym zostają `.env`, `venv`, `app.log` i `update.lock`. Nie są częścią kodu aplikacji.
## 📋 Prerequisites
- **Python 3.10+**
- **MySQL 8** na localhost albo innym hoście z `.env`
- Wirtualne środowisko (`venv`), tworzy je `init_project.sh`, jeśli go nie ma
- Plik `.env` obok skryptów:
```dotenv
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=twoj_uzytkownik
DB_PASSWORD=twoje_haslo
DB_NAME=finance_db
```
## 🚀 Installation & Setup
```bash
git clone <adres-repozytorium> finance_db
cd finance_db
```
Uzupełnij `.env`, potem:
```bash
bash init_project.sh
```
Skrypt pyta o potwierdzenie, bo **kasuje całą bazę** z `.env`. Dalej wczytuje `schema.sql`, stawia `venv`, instaluje `requirements.txt`, uruchamia `seed_tickers.py` i powtarza `update_data.py`, aż aktywa bez historii dostaną pierwsze notowania. MySQL zapyta o hasło osobno od `.env`.
Ręcznie, bez resetu całej bazy:
```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
./venv/bin/python seed_tickers.py
./venv/bin/python update_data.py --missing
```
`schema.sql` ładuj tylko na pustą bazę. Plik robi `DROP TABLE`.
## ▶️ Usage
Jedna pełna aktualizacja tego, co nie ma jeszcze zapisu z dzisiaj:
```bash
./venv/bin/python update_data.py
```
To samo z opakowania crona (log awarii startu trafia do `update.log`, przebieg do `app.log`):
```bash
bash run_update.sh
```
Tylko aktywa, które nigdy nie miały udanego zapisu (`last_updated_at` jest puste):
```bash
./venv/bin/python update_data.py --missing
```
Ponowny zasiew listy tickerów, bez kasowania notowań:
```bash
./venv/bin/python seed_tickers.py
```
## ⏱️ Automation
Codzienny przebieg jest przewidziany na **02:00, poniedziałek–piątek**, po sesji USA. Gotowy wpis crontab i komenda sprawdzająca log są w `setup_cron.txt`.
`run_update.sh` woła `update_data.py` bez flagi `--missing`, więc bierze kolejkę dzienną, a nie sam pierwszy zasiew.
## 🗄️ Database Schema
Pięć tabel: `assets`, `daily_quotes`, `financial_reports`, `valuation_ratios`, `etf_constituents`. Klucze obce schodzą kaskadą z `assets`.
Słownik kolumn, ograniczeń i relacji jest w **[DB_SCHEMA_GUIDE.md](DB_SCHEMA_GUIDE.md)**.
