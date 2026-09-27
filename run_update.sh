#!/bin/bash
# Uruchamiane z crona. Dane dostępowe do bazy są w pliku .env (czyta je config.py),
# więc w tym skrypcie NIE ma żadnych haseł.
set -euo pipefail

PROJECT_DIR="/Users/andrzejwisniewski/Desktop/finance_db"
PYTHON="${PYTHON:-python3}"

cd "$PROJECT_DIR"
# Logi zapisuje sam program (app.log z rotacją). Do update.log trafia tylko to, co wypisze Python przy awarii startu.
"$PYTHON" update_data.py >> update.log 2>&1
