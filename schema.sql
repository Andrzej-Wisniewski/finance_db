-- =====================================================================
-- FINANCE DB — MySQL 8.x DDL (model operacyjny ETL / yfinance)
-- Tabele: assets, daily_quotes, financial_reports, valuation_ratios
-- Retencja: 3 lata (notowania + raporty + wskaźniki wyceny)
-- Inicjalizacja notowań: ostatnie 6 miesięcy, potem dopisek ostatniej sesji
-- Silnik: InnoDB, charset: utf8mb4, collation: utf8mb4_unicode_ci
--
-- Uruchomienie (świeża baza — DROP istniejących tabel):
--   mysql -u root -p < schema.sql
-- =====================================================================

CREATE DATABASE IF NOT EXISTS finance_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE finance_db;

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ---------------------------------------------------------------------
-- Ponowny deploy deweloperski: DROP w odwrotnej kolejności zależności.
-- W produkcji używaj migracji, nie DROP TABLE.
-- ---------------------------------------------------------------------
DROP EVENT IF EXISTS ev_prune_market_data_3y;
DROP PROCEDURE IF EXISTS sp_prune_old_market_data;

DROP TABLE IF EXISTS etf_constituents;
DROP TABLE IF EXISTS valuation_ratios;
DROP TABLE IF EXISTS financial_reports;
DROP TABLE IF EXISTS financial_cash_flow;
DROP TABLE IF EXISTS financial_balance_sheet;
DROP TABLE IF EXISTS financial_income_statement;
DROP TABLE IF EXISTS daily_quotes;
DROP TABLE IF EXISTS historical_prices;
DROP TABLE IF EXISTS assets;

SET FOREIGN_KEY_CHECKS = 1;

-- =====================================================================
-- WARSTWA 1: MASTER — aktywa
-- =====================================================================

CREATE TABLE assets (
    asset_id         BIGINT UNSIGNED    NOT NULL AUTO_INCREMENT,
    symbol           VARCHAR(32)        NOT NULL,          -- ticker Yahoo: PKO.WA, 7203.T, BTC-USD, EURUSD=X, GC=F
    name             VARCHAR(250)       NOT NULL,
    asset_type       ENUM('STOCK','ETF','COMMODITY','FOREX','CRYPTO','INDEX') NOT NULL,
    exchange         VARCHAR(50)        NOT NULL,
    currency         VARCHAR(10)        NOT NULL,          -- ISO lub GBp (LSE w pensach); aktualizowane z Yahoo
    shares_outstanding BIGINT UNSIGNED  NULL,              -- liczba akcji; Yahoo sharesOutstanding
    sector           VARCHAR(100)       NULL,
    industry         VARCHAR(150)       NULL,
    market_cap       BIGINT UNSIGNED    NULL,
    is_active        BOOLEAN            NOT NULL DEFAULT TRUE,
    error_count      SMALLINT UNSIGNED  NOT NULL DEFAULT 0,
    last_updated_at  DATETIME           NULL,              -- NULL = nigdy nie zaciągnięte (priorytet kolejki)
    created_at       TIMESTAMP          NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (asset_id),
    UNIQUE KEY uq_assets_symbol (symbol),
    -- Kolejka ETL: is_active + (NULL last_updated_at pierwsze) + najstarsze
    KEY idx_assets_queue (is_active, last_updated_at, asset_id),
    KEY idx_assets_type_active (asset_type, is_active),
    KEY idx_assets_created_at (created_at),
    KEY idx_assets_last_updated_at (last_updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================================
-- WARSTWA 2: NOTOWANIA DZIENNE (zamknięcie sesji)
-- Kolumna quote_date = data sesji (wymagane "date"; unikamy słowa zarezerwowanego DATE).
-- OHLCV: yfinance zwraca Open, High, Low, Close, Adj Close i Volume.
-- Unikalność: PRIMARY KEY (asset_id, quote_date) → UPSERT ON DUPLICATE KEY.
-- =====================================================================

CREATE TABLE daily_quotes (
    asset_id     BIGINT UNSIGNED NOT NULL,
    quote_date   DATE            NOT NULL,
    open_price   DECIMAL(24,8)   NULL,
    high_price   DECIMAL(24,8)   NULL,
    low_price    DECIMAL(24,8)   NULL,
    close_price  DECIMAL(24,8)   NULL,
    adj_close    DECIMAL(24,8)   NULL,
    volume       BIGINT UNSIGNED NULL,
    created_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (asset_id, quote_date),
    KEY idx_dq_date (quote_date),
    KEY idx_dq_created_at (created_at),
    CONSTRAINT fk_dq_asset
        FOREIGN KEY (asset_id) REFERENCES assets (asset_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================================
-- WARSTWA 3: RAPORTY FINANSOWE — tylko asset_type = STOCK
-- Kolumny danych bez zmian: period_end, line_item, value.
-- statement_type rozróżnia dawne tabele income / balance / cash flow
-- (ta sama pozycja, np. Net Income, występuje w więcej niż jednym sprawozdaniu).
-- period_type: YEAR albo kwartał kalendarzowy daty końca okresu (Q1–Q4).
-- report_year: rok okresu (kolumna "year"; YEAR jest typem w MySQL).
-- =====================================================================

CREATE TABLE financial_reports (
    report_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id        BIGINT UNSIGNED NOT NULL,
    period_end      DATE            NOT NULL,
    report_year     SMALLINT UNSIGNED NOT NULL,
    period_type     ENUM('YEAR','Q1','Q2','Q3','Q4') NOT NULL,
    statement_type  ENUM('INCOME','BALANCE','CASH_FLOW') NOT NULL,
    line_item       VARCHAR(255)    NOT NULL,
    value           DECIMAL(25,2)   NULL,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (report_id),
    UNIQUE KEY uq_financial_reports (asset_id, period_end, period_type, statement_type, line_item),
    KEY idx_reports_year_period (asset_id, report_year, period_type),
    KEY idx_reports_period_end (period_end),
    KEY idx_reports_created_at (created_at),
    CONSTRAINT fk_reports_asset
        FOREIGN KEY (asset_id) REFERENCES assets (asset_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================================
-- WARSTWA 4: WSKAŹNIKI WYCENY (snapshot na dzień)
-- Osobna tabela: inna ziarnistość niż raport roczny/kwartalny.
-- Kolumny danych bez zmian.
-- =====================================================================

CREATE TABLE valuation_ratios (
    ratio_id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_id             BIGINT UNSIGNED NOT NULL,
    snapshot_date        DATE            NOT NULL,
    current_price        DECIMAL(24,8)   NULL,
    fifty_two_week_low   DECIMAL(24,8)   NULL,
    fifty_two_week_high  DECIMAL(24,8)   NULL,
    target_mean_price    DECIMAL(24,8)   NULL,
    analyst_count        INT UNSIGNED    NULL,
    trailing_pe          DECIMAL(20,4)   NULL,   -- C/Z
    forward_pe           DECIMAL(20,4)   NULL,
    price_to_sales       DECIMAL(20,4)   NULL,   -- C/S
    price_to_book        DECIMAL(20,4)   NULL,
    ev_to_ebitda         DECIMAL(20,4)   NULL,   -- EV/EBITDA
    enterprise_to_revenue DECIMAL(20,4)  NULL,
    peg_ratio            DECIMAL(20,4)   NULL,
    return_on_equity     DECIMAL(20,4)   NULL,
    return_on_assets     DECIMAL(20,4)   NULL,
    profit_margin        DECIMAL(20,4)   NULL,
    gross_margin         DECIMAL(20,4)   NULL,
    operating_margin     DECIMAL(20,4)   NULL,
    quick_ratio          DECIMAL(20,4)   NULL,
    current_ratio        DECIMAL(20,4)   NULL,
    dividend_yield       DECIMAL(20,4)   NULL,
    payout_ratio         DECIMAL(20,4)   NULL,
    revenue_growth       DECIMAL(20,4)   NULL,
    earnings_growth      DECIMAL(20,4)   NULL,
    total_debt           BIGINT          NULL,
    total_cash           BIGINT          NULL,
    free_cashflow        BIGINT          NULL,
    operating_cashflow   BIGINT          NULL,
    debt_to_equity       DECIMAL(20,4)   NULL,
    created_at           TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (ratio_id),
    UNIQUE KEY uq_ratios_asset_date (asset_id, snapshot_date),
    KEY idx_ratios_snapshot_date (snapshot_date),
    KEY idx_ratios_created_at (created_at),
    CONSTRAINT fk_ratios_asset
        FOREIGN KEY (asset_id) REFERENCES assets (asset_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================================
-- Skład ETF: która spółka, w którym funduszu, z jaką wagą.
-- weight_percentage: 15.25 = 15,25%. source ISSUER = pełny plik, YAHOO_TOP = tylko top 10.
-- =====================================================================

CREATE TABLE etf_constituents (
    etf_id             BIGINT UNSIGNED NOT NULL,
    stock_id           BIGINT UNSIGNED NOT NULL,
    weight_percentage  DECIMAL(8,4)    NULL,
    source             ENUM('ISSUER','YAHOO_TOP') NOT NULL,
    updated_at         TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (etf_id, stock_id),
    KEY idx_constituents_stock (stock_id),
    CONSTRAINT fk_constituents_etf
        FOREIGN KEY (etf_id) REFERENCES assets (asset_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_constituents_stock
        FOREIGN KEY (stock_id) REFERENCES assets (asset_id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =====================================================================
-- RETENCJA 3 LATA — procedura + event (raz dziennie)
-- Wymaga: SET GLOBAL event_scheduler = ON; (uprawnienie SYSTEM_VARIABLES_ADMIN)
-- Aplikacja i tak woła prune_old_data() na końcu update_data.py.
-- =====================================================================

DELIMITER $$

CREATE PROCEDURE sp_prune_old_market_data()
BEGIN
    DECLARE v_cutoff DATE;
    SET v_cutoff = DATE_SUB(CURDATE(), INTERVAL 3 YEAR);

    DELETE FROM daily_quotes
     WHERE quote_date < v_cutoff;

    DELETE FROM financial_reports
     WHERE period_end < v_cutoff;

    DELETE FROM valuation_ratios
     WHERE snapshot_date < v_cutoff;
END$$

CREATE EVENT ev_prune_market_data_3y
    ON SCHEDULE EVERY 1 DAY
    STARTS (CURRENT_TIMESTAMP + INTERVAL 1 HOUR)
    ON COMPLETION PRESERVE
    ENABLE
    COMMENT 'Usuwa notowania i raporty starsze niż 3 lata'
    DO
        CALL sp_prune_old_market_data()$$

DELIMITER ;

-- Event scheduler: odkomentuj na serwerze, jeśli masz uprawnienia.
-- SET GLOBAL event_scheduler = ON;

-- =====================================================================
-- Koniec pliku
-- =====================================================================
