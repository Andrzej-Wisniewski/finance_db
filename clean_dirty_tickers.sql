-- Czyści śmieci ze składów ETF. Relacje i notowania schodzą kaskadą (ON DELETE CASCADE).
-- Nie rusza klas akcji USA (BRK-B) ani funduszy bez tych wad.
-- Uruchomienie: mysql -u root -p finance_db < clean_dirty_tickers.sql

USE finance_db;

-- Sufiks giełdy zapisany myślnikiem: 6861-T, ABBN-SW. Nie: BRK-B (jedna litera klasy).
DELETE FROM assets
WHERE asset_type = 'STOCK'
  AND (
        symbol REGEXP '^[0-9]+-[A-Za-z0-9.]+$'
     OR symbol REGEXP '^[A-Za-z0-9]+-[A-Za-z]{2,}$'
  );

-- Kontrakty futures, np. IXTZ6, IXAZ6, IXPZ6.
DELETE FROM assets
WHERE asset_type = 'STOCK'
  AND symbol REGEXP '^[A-Za-z][A-Za-z0-9]*[0-9]$';

-- Kody gotówki z arkuszy, np. 2602335D, oraz jawne symbole gotówki.
DELETE FROM assets
WHERE asset_type = 'STOCK'
  AND (
        symbol REGEXP '^[0-9]{5,}[A-Za-z]$'
     OR symbol IN ('USD', 'EUR', 'GBP', 'CHF', 'JPY', 'CAD', 'CASH', 'CASH_USD', 'CASH_EUR')
     OR symbol LIKE 'CASH%'
  );

-- Wyłączone po serii błędów pobierania. Aktywne ETF-y i poprawne spółki zostają.
DELETE FROM assets
WHERE is_active = 0;
