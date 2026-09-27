#!/bin/bash
# Pełny reset finance_db i pierwsze pobranie 6 miesięcy z Yahoo.
# Uruchomienie: bash init_project.sh
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "Brak pliku .env (DB_HOST, DB_PORT, DB_USER, DB_NAME, DB_PASSWORD)."
  exit 1
fi

env_get() {
  # Wartość klucza z .env, bez eksportu hasła do logów.
  local line
  line="$(grep -E "^${1}=" .env | tail -n 1 || true)"
  local val="${line#*=}"
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  printf '%s' "$val"
}

DB_HOST="$(env_get DB_HOST)"
DB_PORT="$(env_get DB_PORT)"
DB_USER="$(env_get DB_USER)"
DB_NAME="$(env_get DB_NAME)"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-finance_db}"

if [[ -z "$DB_USER" ]]; then
  echo "W .env brakuje DB_USER."
  exit 1
fi

echo "Baza: ${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo "To USUNIE całą bazę ${DB_NAME} (stare tabele i dane) i pobierze historię od zera."
read -r -p "Kontynuować? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Przerwano."
  exit 0
fi

echo
echo "Krok 1/5: Resetowanie bazy danych..."
echo "MySQL zapyta o hasło (osobno od .env)."
{
  echo "DROP DATABASE IF EXISTS \`${DB_NAME}\`;"
  cat schema.sql
} | mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p --default-character-set=utf8mb4
echo "Baza ${DB_NAME} utworzona ze schema.sql."

echo
echo "Krok 2/5: Aktywacja środowiska..."
if [[ ! -d venv ]]; then
  python3 -m venv venv
  echo "Utworzono venv."
fi
# shellcheck disable=SC1091
source venv/bin/activate
echo "Python: $(command -v python)"

echo
echo "Krok 3/5: Instalacja zależności..."
python -m pip install -r requirements.txt

echo
echo "Krok 4/5: Zasiewanie aktywów i indeksów..."
python seed_tickers.py

echo
echo "Krok 5/5: Pierwsze pobranie (6 miesięcy)..."
echo "Notowania i raporty (paczki, aż skończą się nowe aktywa)..."
left="$(python - <<'PY'
import db
with db.transaction() as conn:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM assets WHERE is_active = TRUE AND asset_type <> 'INDEX' AND last_updated_at IS NULL"
    )
    print(cur.fetchone()[0])
    cur.close()
db.close_pool()
PY
)"

pass_no=0
while [[ "$left" -gt 0 ]]; do
  pass_no=$((pass_no + 1))
  if [[ "$pass_no" -gt 40 ]]; then
    echo "Przerwano po 40 paczkach. Zostało nowych aktywów: ${left}. Uruchom ponownie: python update_data.py --missing"
    exit 1
  fi
  echo "Paczka ${pass_no}, zostało nowych aktywów: ${left}"
  python update_data.py
  new_left="$(python - <<'PY'
import db
with db.transaction() as conn:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM assets WHERE is_active = TRUE AND asset_type <> 'INDEX' AND last_updated_at IS NULL"
    )
    print(cur.fetchone()[0])
    cur.close()
db.close_pool()
PY
)"
  if [[ "$new_left" -ge "$left" ]]; then
    echo "Paczka nic nie ruszyła (zostało ${new_left}). Sprawdź app.log i uruchom ponownie później."
    exit 1
  fi
  left="$new_left"
done

echo
echo "Gotowe. Indeksy i spółki mają historię 6 miesięcy. Dalej cron: setup_cron.txt"
