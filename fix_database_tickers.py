"""Jednorazowo poprawia albo usuwa brudne tickery w assets.

Poprawny symbol (ANTO-L -> ANTO.L) jest nadpisywany, a licznik błędów zerowany,
żeby kolejne pobranie spróbowało jeszcze raz. None (same cyfry, podwójny sufiks,
futures, gotówka) usuwa wiersz. Notowania i skład ETF schodzą kaskadą.
"""
from __future__ import annotations

import logging
import sys

import db
from seed_tickers import listing_ticker, normalize_ticker

log = logging.getLogger("fix_database_tickers")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    renamed: list[str] = []
    deleted: list[str] = []
    with db.transaction() as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT asset_id, symbol, name, asset_type FROM assets ORDER BY asset_id")
            rows = list(cur.fetchall())
            for asset_id, symbol, name, asset_type in rows:
                cleaned = normalize_ticker(symbol)
                if cleaned is not None and asset_type == "STOCK":
                    cleaned = listing_ticker(cleaned, name)
                if cleaned is None:
                    cur.execute("DELETE FROM assets WHERE asset_id = %s", (asset_id,))
                    deleted.append(symbol)
                    continue
                if cleaned == symbol:
                    continue
                cur.execute("SELECT asset_id FROM assets WHERE symbol = %s", (cleaned,))
                existing = cur.fetchone()
                if existing is not None and int(existing[0]) != int(asset_id):
                    cur.execute("DELETE FROM assets WHERE asset_id = %s", (asset_id,))
                    deleted.append(f"{symbol} (zostaje {cleaned})")
                    continue
                cur.execute(
                    """
                    UPDATE assets
                    SET symbol = %s, is_active = TRUE, error_count = 0, last_updated_at = NULL
                    WHERE asset_id = %s
                    """,
                    (cleaned, asset_id),
                )
                renamed.append(f"{symbol} -> {cleaned}")
        finally:
            cur.close()
    log.info("Poprawione: %s", len(renamed))
    for line in renamed:
        log.info("  %s", line)
    log.info("Usunięte: %s", len(deleted))
    for line in deleted:
        log.info("  %s", line)
    db.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
