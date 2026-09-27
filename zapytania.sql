use finance_db;

SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;

-- STRUKTURA CALEJ BAZY DANYCH
SELECT 
    TABLE_NAME AS nazwa_tabeli,
    COLUMN_NAME AS nazwa_kolumny,
    DATA_TYPE AS typ_danych,
    CHARACTER_MAXIMUM_LENGTH AS max_dlugosc,
    IS_NULLABLE AS czy_puste
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() 
ORDER BY TABLE_NAME, ORDINAL_POSITION;

-- NAJWYŻSZA ZMIENNOŚĆ DZIENNA WŚRÓD PAR WALUTOWYCH (FOREX) W CIĄGU 30 DNI
SELECT a.symbol, a.name, a.currency,  
	   ROUND(AVG(((dq.high_price - dq.low_price) / dq.close_price) * 100), 2) AS sr_zmiennosc_cenowa FROM assets a
JOIN daily_quotes dq ON a.asset_id = dq.asset_id 
WHERE 
	asset_type = 'FOREX' 
	AND is_active = 1 
	AND quote_date > CURRENT_DATE() - INTERVAL 30 DAY 
GROUP BY symbol, name, currency
ORDER BY sr_zmiennosc_cenowa DESC;

-- ŚREDNI KURSY DLA GLÓWNYCH PAR Z USD
SELECT a.name,
	   YEAR(q.quote_date) AS rok, MONTH(q.quote_date) AS miesiac, 
	   AVG(q.close_price) AS srednia_zamkniecia
FROM assets a
JOIN daily_quotes q ON a.asset_id = q.asset_id
WHERE asset_type = 'FOREX' AND a.name LIKE '%USD%'
GROUP BY a.name, YEAR(q.quote_date), MONTH(q.quote_date)
ORDER BY name, YEAR(q.quote_date), MONTH(q.quote_date);

-- WALUTY ZE STOPĄ ZWROTU POWYŻEJ ŚREDNIEJ DLA CAŁEGO RYNKU FOREX
WITH skrajne_ceny AS (
	SELECT a.asset_id, a.name,
	   FIRST_VALUE(q.adj_close) OVER (PARTITION BY q.asset_id ORDER BY q.quote_date ASC) AS najstarsza_cena,
	   LAST_VALUE(q.adj_close) OVER (PARTITION BY q.asset_id ORDER BY q.quote_date ASC 
			ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS najnowsza_cena
	FROM daily_quotes q
    JOIN assets a ON a.asset_id = q.asset_id 
	WHERE a.asset_type = 'FOREX' AND q.quote_date > CURRENT_DATE() - INTERVAL 180 DAY
), stopy_zwrotu AS (
	SELECT DISTINCT name,
		   ROUND((najnowsza_cena - najstarsza_cena) / najstarsza_cena * 100, 3) AS calkowita_stopa_zwrotu_procent
    FROM skrajne_ceny
), srednia_rynku AS (
    SELECT AVG(calkowita_stopa_zwrotu_procent) AS srednia_stopa_zwrotu
    FROM stopy_zwrotu
)

SELECT 
    sz.name, sz.calkowita_stopa_zwrotu_procent
FROM stopy_zwrotu sz
CROSS JOIN srednia_rynku sr
WHERE sz.calkowita_stopa_zwrotu_procent > sr.srednia_stopa_zwrotu
ORDER BY sz.calkowita_stopa_zwrotu_procent DESC;

-- KORELACJA ZMIAN DZIENNYCH MIĘDZY PARĄ WALUTOWĄ (EUR/USD) A INDEKSEM GIEŁDOWYM S&P 500 (^GSPC)
WITH skrajne_ceny AS (
		SELECT a.asset_id, a.symbol, q.adj_close, q.quote_date,
		   LAG(q.adj_close) OVER (PARTITION BY q.asset_id ORDER BY q.quote_date) AS poprzedni_adj_close
		FROM daily_quotes q
		JOIN assets a ON a.asset_id = q.asset_id 
		WHERE (a.symbol = '^GSPC' OR a.symbol = 'EURUSD=X')
			AND q.quote_date > CURRENT_DATE() - INTERVAL 180 DAY
), stopa_zwrotu_sp500 AS (
		SELECT  quote_date,
			(adj_close - poprzedni_adj_close) / poprzedni_adj_close AS dzienna_stopa_zwrotu_sp500
		FROM skrajne_ceny
), stopy_zwrotu_forex AS (
		SELECT  quote_date,
		(adj_close - poprzedni_adj_close) / poprzedni_adj_close AS dzienna_stopa_zwrotu_forex
		FROM skrajne_ceny
		WHERE symbol = 'EURUSD=X'
)

SELECT 
    (COUNT(*) * SUM(szf.dzienna_stopa_zwrotu_forex * szs.dzienna_stopa_zwrotu_sp500) 
	- SUM(szf.dzienna_stopa_zwrotu_forex) * SUM(szs.dzienna_stopa_zwrotu_sp500)) / 
    SQRT((COUNT(*) * SUM(POW(szf.dzienna_stopa_zwrotu_forex, 2)) 
	- POW(SUM(szf.dzienna_stopa_zwrotu_forex), 2)) * (COUNT(*) * 
    SUM(POW(szs.dzienna_stopa_zwrotu_sp500, 2)) - POW(SUM(szs.dzienna_stopa_zwrotu_sp500), 2))) 
		AS korelacja_eurusd_sp500
FROM stopy_zwrotu_forex szf
JOIN stopa_zwrotu_sp500 szs ON szf.quote_date = szs.quote_date;

-- OBLICZ 6-MIESIĘCZNĄ PROCENTOWĄ STOPĘ ZWROTU DLA FUNDUSZY ETF, 
-- PORÓWNUJĄC SKORYGOWANĄ CENĘ ZAMKNIĘCIA Z PIERWSZEGO I OSTATNIEGO DNIA OSTATNICH 180 DNI
WITH skrajne_ceny AS (
	SELECT a.asset_id, a.name,
	   FIRST_VALUE(adj_close) OVER (PARTITION BY asset_id ORDER BY quote_date ASC) AS najstarsza_cena,
	   LAST_VALUE(adj_close) OVER (PARTITION BY asset_id ORDER BY quote_date ASC 
			ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS najnowsza_cena
	FROM daily_quotes q
    JOIN assets a ON a.asset_id = q.asset_id 
	WHERE a.asset_type = 'ETF' AND q.quote_date > CURRENT_DATE() - INTERVAL 180 DAY
)

SELECT DISTINCT name, ROUND((najnowsza_cena - najstarsza_cena) / najstarsza_cena * 100, 3) AS calkowita_stopa_zwrotu_procent  
FROM skrajne_ceny
ORDER BY calkowita_stopa_zwrotu_procent DESC;

-- PODAJ SUME, MAKSIMUM I DZIENNĄ ŚREDNIĄ WOLUMENU ORAZ WARTOŚCI OBROTU DLA FUNDUSZY ETF Z OSTATNICH 30 DNI
SELECT a.name, SUM(q.volume) AS suma_wolumenu, 
	   MAX(q.volume) AS najwiekszy_wolumen_sesja,
	   AVG(q.volume) AS sredni_dzienny_wolumen,
       ROUND(AVG(q.volume * q.close_price), 2) AS srednia_wartosc_obrotu
FROM daily_quotes q
JOIN assets a ON a.asset_id = q.asset_id 
WHERE a.asset_type = 'ETF' AND q.quote_date > CURRENT_DATE() - INTERVAL 30 DAY
GROUP BY a.asset_id, a.name
ORDER BY srednia_wartosc_obrotu DESC;

-- TOP 5 ETF-ÓW O NAJWIĘKSZEJ DZIENNEJ ZMIENIE CENY (STOPIE ZWROTU) NA OSTATNIEJ SESJI
WITH wyliczenie_sesji AS (
	SELECT a.asset_id, a.name, q.quote_date, q.adj_close,
		LAG(q.adj_close, 1) OVER (PARTITION BY q.asset_id ORDER BY q.quote_date) AS roznica_dzien,
		LAG(q.adj_close, 5) OVER (PARTITION BY q.asset_id ORDER BY q.quote_date) AS	roznica_tydzien,
		LAG(q.adj_close, 21) OVER (PARTITION BY q.asset_id ORDER BY q.quote_date) AS roznica_miesiac,
		LAG(q.adj_close, 126) OVER (PARTITION BY q.asset_id ORDER BY q.quote_date) AS roznica_pol_roku,
        DENSE_RANK() OVER (ORDER BY q.quote_date DESC) AS najnowsze_sesje
	FROM daily_quotes q
    JOIN assets a ON a.asset_id = q.asset_id 
	WHERE a.asset_type = 'ETF'
)

SELECT *
FROM wyliczenie_sesji
WHERE quote_date = (SELECT MAX(quote_date) FROM wyliczenie_sesji) LIMIT 5;


-- KLASYFIKACJA ETF-ÓW DO KWARTYLI WSKAŹNIKA SHARPE'A (RISK-ADJUSTED RETURN) NA PODSTAWIE OSTATNICH 12 MIESIĘCY


SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;


-- WYKRYWANIE ANOMALII WOLUMENU (VOLUME SPIKES > 3 SIGMA) POŁĄCZONYCH ZE ZMIANĄ CENY DLA DANEJ GRUPY ETF-ÓW


-- SREDNIA DZIENNA ZMIENNOSC CENOWA SUROWCOW W OSTATNIM MIESIACU
SELECT * FROM assets
WHERE asset_type = 'COMMODITY';
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;

-- SUROWCE Z NAJWYZSZA STOPA ZWROTU W UJECIU ROK DO ROKU (YoY)

-- IDENTYFIKACJA DNI Z ANOMALIA WOLUMENU DLA ROPY NAFTOWEJ I ZLOTA


-- KORELACJA ZMIAN CEN ZLOTA ZE ZMIANAMI KURSU DOLARA (USD)


-- MAX DRAWDOWN ORAZ CZAS TRWANIA SPADKU DLA RYNKU MIEDZI
SELECT * FROM assets
WHERE asset_type = 'COMMODITY';
-- SREDNIA DZIENNA ZMIENNOSC CENOWA SUROWCOW W OSTATNIM MIESIACU


-- SREDNIA DZIENNA ZMIENNOSC DLA TOP 10 KRYPTOWALUT W OSTATNICH 30 DNIACH

SELECT * FROM assets
WHERE asset_type = 'CRYPTO';
-- IDENTYFIKACJA DNI Z ANOMALIA WOLUMENU DLA BITCOINA (BTC-USD)


-- MAKSYMALNE OBSUNIECIE KAPITALU (MAX DRAWDOWN) DLA BITCOINA W UJECIU ROCZNYM


-- KORELACJA ZMIAN DZIENNYCH BITCOINA (BTC-USD) ZE ZMIANAMI INDEKSU S&P 500 (^GSPC)


-- NAJDLUZSZA SERIA DNI WZROSTOWYCH DLA ETHEREUM (ETH-USD) - PROBLEM GAPS AND ISLANDS

-- SREDNIA DZIENNA ZMIENNOSC DLA TOP 10 KRYPTOWALUT W OSTATNICH 30 DNIACH


-- IDENTYFIKACJA DNI Z ANOMALIA WOLUMENU DLA BITCOINA (BTC-USD)




-- 10 NAJBARDZIEJ NIEDOWARTOŚCIOWANYCH SPÓŁEK (NISKI P/E + DOBRY PEG)
SELECT 
    a.symbol, a.name, a.sector, vr.current_price, 
    vr.trailing_pe, vr.forward_pe, vr.peg_ratio
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.trailing_pe > 0 
  AND vr.peg_ratio > 0
ORDER BY vr.peg_ratio ASC
LIMIT 10;

-- SPÓŁKI O NAJWYŻSZEJ STOPIE DYWIDENDY Z BEZPIECZNYM PAYOUT RATIO
SELECT 
    a.symbol, a.name, a.sector, 
    vr.dividend_yield * 100 AS proc_stopa_dywidendy, 
    vr.payout_ratio * 100 AS proc_czest_wyplat,
    vr.current_price
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.dividend_yield > 0 
  AND vr.payout_ratio BETWEEN 0.1 AND 0.7
ORDER BY vr.dividend_yield DESC
LIMIT 10;

-- SPÓŁKI Z SEKTORA TECHNOLOGY ZE STOPĄ DYWIDENDY > 2%
SELECT a.symbol, a.name, a.sector, vr.dividend_yield 
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.dividend_yield > 0.02 
  AND a.sector = 'Technology' 
ORDER BY vr.dividend_yield DESC;

-- SPÓŁKI MAKSYMALNIE 5% OD 52-TYGODNIOWEGO DOŁKA
SELECT a.symbol, a.name, vr.current_price, vr.fifty_two_week_low 
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.fifty_two_week_low > 0 
  AND vr.current_price >= vr.fifty_two_week_low 
  AND vr.current_price <= vr.fifty_two_week_low * 1.05;

-- ŚREDNI I MEDIANA P/E DLA KAŻDEGO SEKTORA
WITH podzial_sektor AS (
    SELECT 
        a.sector,
        vr.trailing_pe,
        COUNT(*) OVER (PARTITION BY a.sector) AS ilosc,
        ROW_NUMBER() OVER (PARTITION BY a.sector ORDER BY vr.trailing_pe ASC) AS rank_wzr,
        ROW_NUMBER() OVER (PARTITION BY a.sector ORDER BY vr.trailing_pe DESC) AS rank_mal
    FROM assets a
    JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
    WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
      AND vr.trailing_pe > 0 
      AND a.sector IS NOT NULL 
      AND a.sector != ''
)
SELECT 
    sector,
    COUNT(*) AS ilosc_spolek,
    ROUND(AVG(trailing_pe), 2) AS srednia_pe,
    ROUND(AVG(CASE WHEN rank_wzr IN (rank_mal, rank_mal + 1, rank_mal - 1) THEN trailing_pe END), 2) AS mediana_pe
FROM podzial_sektor
GROUP BY sector
ORDER BY mediana_pe ASC;

-- SPÓŁKI 'TECHNOLOGY' O KAPITALIZACJI WYŻSZEJ NIŻ ŚREDNIA SEKTORA
WITH wartosc_sektor AS (
    SELECT symbol, name, market_cap AS kapitalizacja_rynkowa, 
           AVG(market_cap) OVER(PARTITION BY sector) AS srednia_wartosc_sektora
    FROM assets
    WHERE sector = 'Technology' AND market_cap IS NOT NULL
)
SELECT symbol, name, kapitalizacja_rynkowa, ROUND(srednia_wartosc_sektora, 0) AS srednia_wartosc_sektora
FROM wartosc_sektor
WHERE kapitalizacja_rynkowa > srednia_wartosc_sektora
ORDER BY kapitalizacja_rynkowa DESC;

-- SPÓŁKI PŁACĄCE DYWIDENDĘ > 3% Z PAYOUT RATIO 20%-60%
SELECT a.symbol, a.name, vr.payout_ratio, vr.dividend_yield 
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.dividend_yield > 0.03 
  AND vr.payout_ratio BETWEEN 0.2 AND 0.6
ORDER BY vr.dividend_yield DESC;

-- 5 SPÓŁEK O NAJWIĘKSZEJ MARŻY ZYSKU Z KAPITALIZACJĄ > 50 MLD USD
SELECT a.symbol, a.name, vr.profit_margin, a.market_cap   
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND a.market_cap > 50000000000 
ORDER BY vr.profit_margin DESC 
LIMIT 5;

-- PRZYCHODY I ZYSK NETTO DLA AAPL I GOOGL
SELECT a.symbol, a.name, fis.period_end, fis.line_item, fis.value
FROM assets a
JOIN financial_reports fis ON a.asset_id = fis.asset_id
WHERE a.symbol IN ('AAPL', 'GOOGL') 
  AND fis.statement_type = 'INCOME'
  AND fis.period_type = 'YEAR'
  AND LOWER(fis.line_item) IN ('total revenue', 'net income common stock', 'net income');

-- PRZYCHODY ZA OSTATNI ROK DLA SEKTORA 'TECHNOLOGY'
WITH rank_sektor AS (
    SELECT a.symbol, a.name, a.sector, fis.period_end, fis.line_item, fis.value,
           ROW_NUMBER() OVER(PARTITION BY a.asset_id ORDER BY fis.period_end DESC) AS ranking
    FROM assets a
    JOIN financial_reports fis ON a.asset_id = fis.asset_id
    WHERE fis.statement_type = 'INCOME'
      AND LOWER(fis.line_item) = 'total revenue' 
      AND fis.value IS NOT NULL 
      AND fis.period_type = 'YEAR'
      AND a.sector = 'Technology'
)
SELECT symbol, name, sector, period_end, line_item, value
FROM rank_sektor
WHERE ranking = 1
ORDER BY value DESC;

-- SPÓŁKI Z PEG RATIO < 1.0 I FORWARD PE < TRAILING PE
SELECT a.symbol, a.name, vr.peg_ratio, vr.forward_pe, vr.trailing_pe 
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.peg_ratio > 0 AND vr.peg_ratio < 1.0 
  AND vr.forward_pe > 0 AND vr.trailing_pe > 0 
  AND vr.forward_pe < vr.trailing_pe;

-- SEKTORY ZE ŚREDNIĄ KAPITALIZACJĄ > 100 MLD USD
SELECT 
    sector, 
    COUNT(symbol) AS ilosc_spolek, 
    ROUND(AVG(market_cap) / 1000000000, 2) AS srednia_kapitalizacji_mld
FROM assets
WHERE sector IS NOT NULL AND sector != ''
GROUP BY sector
HAVING AVG(market_cap) > 100000000000
ORDER BY srednia_kapitalizacji_mld DESC;

-- SPÓŁKI, KTÓRYCH DŁUG (TOTAL DEBT) JEST WIĘKSZY NIŻ KAPITALIZACJA
SELECT a.symbol, a.name, a.sector, a.market_cap, vr.total_debt, (vr.total_debt - a.market_cap) AS roznica
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.total_debt > a.market_cap 
  AND vr.total_debt IS NOT NULL 
  AND a.market_cap IS NOT NULL
ORDER BY roznica DESC;

-- SPÓŁKI 'TECHNOLOGY' Z TRAILING PE WYŻSZYM NIŻ ŚREDNIA SEKTORA
WITH sredni_trailingPE_sektor AS (
    SELECT a.symbol, a.sector, vr.trailing_pe, 
           AVG(vr.trailing_pe) OVER (PARTITION BY a.sector) AS sredni_pe_sektora
    FROM assets a
    JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
    WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
      AND a.sector = 'Technology' 
      AND vr.trailing_pe > 0
)
SELECT symbol, sector, trailing_pe, ROUND(sredni_pe_sektora, 2) AS sredni_pe_sektora
FROM sredni_trailingPE_sektor
WHERE trailing_pe > sredni_pe_sektora
ORDER BY trailing_pe DESC;

-- PIVOT BILANSU (GOTÓWKA I DŁUG DŁUGOTERMINOWY) DLA SEKTORA 'TECHNOLOGY'
WITH najnowsze_raporty AS (
    SELECT a.symbol, a.sector, fbs.period_end, fbs.line_item, fbs.value,
           ROW_NUMBER() OVER(PARTITION BY a.asset_id, fbs.line_item ORDER BY fbs.period_end DESC) AS najnowszy_raport
    FROM assets a
    JOIN financial_reports fbs ON a.asset_id = fbs.asset_id
    WHERE a.sector = 'Technology' 
      AND fbs.statement_type = 'BALANCE'
      AND fbs.period_type = 'YEAR'
      AND LOWER(fbs.line_item) IN ('cash and cash equivalents', 'long term debt')
)
SELECT 
    symbol, 
    MAX(period_end) AS okres_sprawozdawczy, 
    SUM(CASE WHEN LOWER(line_item) = 'cash and cash equivalents' THEN value ELSE 0 END) AS suma_pieniedzy,
    SUM(CASE WHEN LOWER(line_item) = 'long term debt' THEN value ELSE 0 END) AS dlugoterminowy_dlug,
    (SUM(CASE WHEN LOWER(line_item) = 'long term debt' THEN value ELSE 0 END) - 
     SUM(CASE WHEN LOWER(line_item) = 'cash and cash equivalents' THEN value ELSE 0 END)) AS net_debt
FROM najnowsze_raporty
WHERE najnowszy_raport = 1
GROUP BY symbol
ORDER BY net_debt DESC;

-- SPÓŁKI Z PRZEPŁYWAMI OPERACYJNYMI > 5 MLD USD
WITH najnowszy_cf AS (
    SELECT a.symbol, a.name, fcf.value AS operating_cash_flow,
           ROW_NUMBER() OVER (PARTITION BY a.asset_id ORDER BY fcf.period_end DESC) AS rn
    FROM assets a
    JOIN financial_reports fcf ON a.asset_id = fcf.asset_id
    WHERE fcf.statement_type = 'CASH_FLOW'
      AND LOWER(fcf.line_item) LIKE '%operating cash flow%'
      AND fcf.period_type = 'YEAR'
)
SELECT symbol, name, operating_cash_flow
FROM najnowszy_cf
WHERE rn = 1 AND operating_cash_flow > 5000000000
ORDER BY operating_cash_flow DESC;

-- 50 SPÓŁEK Z NAJWYŻSZYM FREE CASH FLOW
WITH najnowszy_fcf AS (
    SELECT a.symbol, a.sector, vr.current_price, fcf.value AS free_cash_flow,
           ROW_NUMBER() OVER (PARTITION BY a.asset_id ORDER BY fcf.period_end DESC) AS rn
    FROM assets a
    JOIN valuation_ratios vr ON a.asset_id = vr.asset_id AND vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
    JOIN financial_reports fcf ON a.asset_id = fcf.asset_id
    WHERE fcf.statement_type = 'CASH_FLOW'
      AND LOWER(fcf.line_item) LIKE '%free cash flow%'
      AND fcf.period_type = 'YEAR'
)
SELECT symbol, sector, free_cash_flow, current_price
FROM najnowszy_fcf
WHERE rn = 1 AND free_cash_flow IS NOT NULL
ORDER BY free_cash_flow DESC 
LIMIT 50;

-- RANKING SPÓŁEK OD NAJTAŃSZEJ DO NAJDROŻSZEJ (P/E) W KAŻDYM SEKTORZE
SELECT 
    a.symbol, a.name, a.sector, vr.trailing_pe,
    DENSE_RANK() OVER(PARTITION BY a.sector ORDER BY vr.trailing_pe ASC) AS ranking_najtansza 
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.trailing_pe > 0 
  AND a.sector IS NOT NULL
ORDER BY a.sector, ranking_najtansza ASC;

-- 3 NAJWIĘKSZE SPÓŁKI (POD WZGLĘDEM KAPITAŁU) DLA KAŻDEGO SEKTORA
WITH podzial_sektor AS (
    SELECT symbol, name, sector, market_cap / 1000000000000 AS kapital_bln,
           DENSE_RANK() OVER(PARTITION BY sector ORDER BY market_cap DESC) AS najwiekszy_kapital
    FROM assets
    WHERE sector IS NOT NULL
)
SELECT * 
FROM podzial_sektor
WHERE najwiekszy_kapital <= 3;

-- ŚREDNIA CENA DOCELOWA > 30% I LICZBA REKOMENDACJI > 30 ORAZ PROCENTOWY WZROST WEDŁUG ANALITYKÓW
SELECT a.symbol, a.name, vr.current_price, vr.target_mean_price,
	   ROUND(ABS((vr.target_mean_price - vr.current_price) / vr.current_price ) * 100, 2) AS proc_wzrostu, vr.analyst_count
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
  AND vr.target_mean_price >= vr.current_price * 1.3 
  AND vr.analyst_count > 30
ORDER BY vr.analyst_count DESC;

-- KLASYFIKACJA SPÓŁEK NA 3 KATEGORIE RYZYKA Z LICZBĄ REKOMENDACJI < 30
SELECT a.symbol, a.sector, vr.debt_to_equity, vr.analyst_count,
    CASE 
        WHEN vr.debt_to_equity < 50 THEN 'BEZPIECZNA'
        WHEN vr.debt_to_equity BETWEEN 50 AND 100 THEN 'UMIARKOWANA' 
        ELSE 'RYZYKOWNA'
    END AS profil_ryzyka
 FROM assets a
 JOIN valuation_ratios vr ON a.asset_id = vr.asset_id
 WHERE vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios) AND vr.debt_to_equity IS NOT NULL
   AND vr.analyst_count < 30
 ORDER BY vr.debt_to_equity;

-- SPÓŁKI Z ROA 5-KROTNIE WYŻSZYM NIŻ ŚREDNIA DLA SEKTORA
WITH spolka_roa AS (
    SELECT a.symbol, a.name, a.sector,
        (fis.value / fbs.value) AS roa
    FROM assets a
    JOIN financial_reports fis ON a.asset_id = fis.asset_id AND fis.statement_type = 'INCOME'
    JOIN financial_reports fbs ON a.asset_id = fbs.asset_id AND fbs.statement_type = 'BALANCE'
        AND fis.period_end = fbs.period_end 
        AND fis.period_type = fbs.period_type
    WHERE fis.period_type = 'YEAR'
      AND fis.line_item = 'Net Income'
      AND fbs.line_item = 'Total Assets'
      AND a.sector IS NOT NULL
), srednia_roa_sektor AS (
    SELECT sector, 
           AVG(roa) AS sred_roa_sektor
    FROM spolka_roa
    GROUP BY sector
)
SELECT 
    c.symbol, c.name, c.sector, c.roa, s.sred_roa_sektor
FROM spolka_roa c
JOIN srednia_roa_sektor s ON c.sector = s.sector
WHERE c.roa >= 5 * s.sred_roa_sektor
ORDER BY c.roa DESC;

-- SPÓŁKI, KTÓRYCH OPERATING MARGIN > 0.20 I DŁUG STANOWI PONAD 50% PRZYCHODÓW
WITH najnowsze_przychody AS (
    SELECT asset_id, value AS total_revenue,
           ROW_NUMBER() OVER(PARTITION BY asset_id ORDER BY period_end DESC) as rn
    FROM financial_reports
    WHERE statement_type = 'INCOME' 
      AND period_type = 'YEAR' 
      AND LOWER(line_item) = 'total revenue'
)
SELECT a.symbol, a.name, vr.operating_margin, vr.total_debt, np.total_revenue
FROM assets a
JOIN valuation_ratios vr ON a.asset_id = vr.asset_id AND vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
JOIN najnowsze_przychody np ON a.asset_id = np.asset_id AND np.rn = 1
WHERE vr.operating_margin > 0.20 
  AND vr.total_debt > (0.50 * np.total_revenue)
ORDER BY vr.operating_margin DESC;


-- OBLICZYĆ ROCZNĄ ZMIANĘ PRZYCHODÓW W DOKŁADNYCH KWOTACH DLA SPÓŁKI 'AAPL' DLA KAŻDEGO ROKU W BAZIE.
SELECT a.asset_id, a.symbol, fr.statement_type, fr.period_type, fr.period_end, fr.line_item, fr.value,
	   fr.value - LAG(fr.value) OVER(PARTITION BY asset_id ORDER BY period_end) AS roznica_poprzedni_rok
FROM financial_reports fr
JOIN assets a ON a.asset_id = fr.asset_id
WHERE a.symbol = 'AAPL' 
	AND fr.statement_type = 'INCOME'
	AND fr.period_type = 'YEAR'
	AND fr.line_item = 'Total Revenue';

-- ZNAJDŹ SPÓŁKI, DLA KTÓRYCH TOTALCASH - TOTALDEBT > 0. WYŚWIETL SYMBOL, NET CASH (GOTÓWKA MINUS DŁUG), 
-- CENE ORAZ TRAILINGPE. POSORTUJ OD NAJWIĘKSZEJ KWOTY CZYS TEJ GOTÓWKI
SELECT a.name, vr.total_cash, vr.total_debt,  
	   vr.total_cash - vr.total_debt AS net_cash, vr.current_price, vr.trailing_pe
FROM valuation_ratios vr 
JOIN assets a ON a.asset_id = vr.asset_id
WHERE vr.total_cash - vr.total_debt > 0 
	AND snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
ORDER BY net_cash DESC;

-- 10 SPÓŁEK Z NAJWYŻSZYM FCF YIELD (FREECASHFLOW / MARKETCAP) KTÓRYCH KAPITALIZACJA PRZEKRACZA 20 MLD USD
WITH najnowsze_raporty_spolek AS (
SELECT a.asset_id, a.name, a.market_cap, fr.period_end, fr.period_type, fr.statement_type, fr.line_item, 
	   ROUND((fr.value / a.market_cap ) * 100, 2) AS FCF_yield_proc,
	   ROW_NUMBER() OVER(PARTITION BY asset_id ORDER BY period_end DESC) AS ostatni_raport
FROM financial_reports fr
JOIN assets a ON a.asset_id = fr.asset_id
WHERE statement_type = 'CASH_FLOW'
	AND period_type = 'YEAR'
    AND line_item = 'Free Cash Flow'
    AND market_cap > 20000000000
)

SELECT name, FCF_yield_proc FROM najnowsze_raporty_spolek
WHERE ostatni_raport = 1
ORDER BY FCF_yield_proc DESC LIMIT 10;

-- WYŚWIETL DLA SPÓŁKI 'MSFT' ORAZ 'GOOGL' ZA TEN SAM ROK: PRZYCHODY (TOTAL REVENUE), 
-- ZYSK NETTO (NET INCOME COMMON STOCK) ORAZ WOLNE PRZEPŁYWY (FREE CASH FLOW).
SELECT a.name, report_year,
       MAX(CASE WHEN line_item = 'Total Revenue' THEN value / 1000000000 END) AS przychody_mld,
	   MAX(CASE WHEN line_item = 'Net Income' THEN value / 1000000000 END) AS zysk_netto_mld,
	   MAX(CASE WHEN line_item = 'Free Cash Flow' THEN value / 1000000000 END) AS wolne_przeplywy_mld
FROM financial_reports fr
JOIN assets a ON a.asset_id = fr.asset_id
WHERE a.symbol = 'MSFT' OR a.symbol = 'GOOGL'
GROUP BY report_year, name;

-- OBLICZ DZIENNĄ HISTORYCZNĄ ZMIENNOŚĆ LOGARYTMICZNĄ W UŁAMKU DLA WSZYSTKICH AKTYWÓW (KORELATYWNE PODZAPYTANIE)
SELECT a.symbol,
       STDDEV(LN(q.adj_close / prev.adj_close)) AS vol_dzienna
FROM daily_quotes q
JOIN daily_quotes prev
  ON prev.asset_id = q.asset_id
 AND prev.quote_date = (
       SELECT MAX(p2.quote_date) FROM daily_quotes p2
       WHERE p2.asset_id = q.asset_id AND p2.quote_date < q.quote_date
     )
JOIN assets a ON a.asset_id = q.asset_id
WHERE q.adj_close > 0 AND prev.adj_close > 0
GROUP BY a.symbol;

-- OBLICZ DZIENNĄ ZMIENNOŚĆ PROCENTOWĄ CEN AKCJI ZA OSTATNIE 3 MIESIACE
WITH roznica_dzienna AS (
SELECT asset_id, quote_date, adj_close,
	   LAG(adj_close, 1) OVER (PARTITION BY asset_id ORDER BY quote_date) AS prev_adj_close
FROM daily_quotes
WHERE quote_date > CURRENT_DATE() - INTERVAL 90 DAY
)

SELECT a.name,
       STDDEV(LN(rd.adj_close / rd.prev_adj_close)) * 100 AS proc_dzienna_zmiennosc 
FROM roznica_dzienna rd
JOIN assets a ON a.asset_id = rd.asset_id
WHERE adj_close > 0 AND prev_adj_close > 0
GROUP BY name
ORDER BY proc_dzienna_zmiennosc DESC;

-- OBLICZ DZIENNĄ ZMIENNOŚĆ PROCENTOWĄ CEN AKCJI ZA OSTATNI ROK
WITH roznica_dzienna AS (
SELECT asset_id, quote_date, adj_close,
	   LAG(adj_close, 1) OVER (PARTITION BY asset_id ORDER BY quote_date) AS prev_adj_close
FROM daily_quotes
WHERE quote_date > CURRENT_DATE() - INTERVAL 365 DAY
)

SELECT a.name,
       STDDEV((LN(rd.adj_close / rd.prev_adj_close)) * SQRT(252)) * 100 AS proc_dzienna_zmiennosc_rok 
FROM roznica_dzienna rd
JOIN assets a ON a.asset_id = rd.asset_id
WHERE adj_close > 0 AND prev_adj_close > 0
GROUP BY name
ORDER BY proc_dzienna_zmiennosc_rok DESC;

-- OBLICZ CAŁKOWITY PRZYCHÓD SEKTORA ORAZ PROCENTOWY UDZIAŁ KAŻDEJ SPÓŁKI W PRZYCHODACH JEJ SEKTORA.
WITH najnowszy_raport AS (
SELECT a.asset_id, a.name, a.sector, fr.period_end, fr.statement_type, fr.line_item, fr.value,
	   ROW_NUMBER() OVER (PARTITION BY asset_id ORDER BY period_end DESC) AS kolejnosc_raportow
FROM assets a
JOIN financial_reports fr ON a.asset_id = fr.asset_id
WHERE period_type = 'YEAR' AND line_item = 'Total Revenue' AND sector IS NOT NULL
)

SELECT name, sector, SUM(value) OVER(PARTITION BY sector) AS suma_przychodu_sektor,
	   value /  SUM(value) OVER(PARTITION BY sector) * 100 AS udzial_procent
FROM najnowszy_raport
WHERE kolejnosc_raportow = 1
ORDER BY sector, udzial_procent DESC;

-- ZNAJDŹ SPÓŁKI, KTÓRYCH WSKAŹNIK FORWARDPE JEST O CO NAJMNIEJ 25% NIŻSZY OD ICH WSKAŹNIKA TRAILINGPE,
--  A JEDNOCZEŚNIE ICH EARNINGSGROWTH JEST DODATNIE
SELECT a.symbol, a.name, vr.trailing_pe, vr.forward_pe
FROM valuation_ratios vr 
JOIN assets a ON a.asset_id = vr.asset_id
WHERE forward_pe <= trailing_pe * 0.75 AND
	vr.snapshot_date = (SELECT MAX(snapshot_date) FROM valuation_ratios)
    AND trailing_pe > 0 AND forward_pe > 0
ORDER BY trailing_pe - forward_pe / trailing_pe DESC;

-- DLA KAŻDEGO SEKTORA PODZIEL SPÓŁKI NA 4 KWARTYLE (NTILE(4)) WEDŁUG WSKAŹNIKA DEBTTOEQUITY. WYŚWIETL SEKTOR, NUMER KWARTYLU, MINIMALNY ORAZ MAKSYMALNY DŁUG DO KAPITAŁU WŁASNEGO W TYM KWARTYLU ORAZ LICZBĘ SPÓŁEK. ODRZUĆ REKORDY Z NULL LUB UJEMNYM DŁUGIEM.
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;
-- OBLICZ ŚREDNIĄ MARŻĘ ZYSKU (GROSSMARGINS) ORAZ JEJ ODCHYLENIE STANDARDOWE W RAMACH KAŻDEGO SEKTORA. WYŚWIETL SYMBOL, SEKTOR I MARŻĘ SPÓŁEK, KTÓRYCH GROSSMARGINS JEST O CO NAJMNIEJ 2 ODCHYLENIA STANDARDOWE WYŻSZE OD ŚREDNIEJ DLA ICH SEKTORA

-- STWÓRZ METRYKĘ 'STABILITY_SCORE' (OD 0 DO 4) DLA SPÓŁEK NA PODSTAWIE WARUNKÓW (KAŻDY SPARSOWANY DODAJE 1 PKT): CURRENTRATIO > 1.5, DEBTTOEQUITY < 80, PROFITMARGINS > 0.12, OPERATINGCASHFLOW > 0. WYŚWIETL SYMBOL, SEKTOR, PUNKTY ORAZ KLASYFIKACJĘ ('ELITE' DLA 4 PKT, 'SOLID' DLA 3 PKT, 'RISKY' DLA <=2).
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;
-- POŁĄCZ FMP_COMPANY_PROFILES Z FMP_INCOME_STATEMENTS. DLA POZYCJI LINE_ITEM = 'Total Revenue' OBLICZ ROCZNĄ ZMIANĘ PRZYCHODÓW (YOY) DLA KAŻDEJ SPÓŁKI Z UŻYCIEM LAG(). ZNAJDŹ SPÓŁKI, KTÓRE WYKAZAŁY DODATNI WZROST PRZYCHODÓW W KAŻDYM Z 3 OSTATNICH RAPORTO WANYCH OKRESÓW.

-- PODOBNIE JAK W DZIELENIU DANYCH DLA GRACZY Z TRZYLETNIM CIĄGŁYM WZROSTEM PENSJI, ZNAJDŹ SPÓŁKI W FMP_INCOME_STATEMENTS, KTÓRE MAJĄ ZAPISANE CO NAJMNIEJ 3 LATA DANYCH DLA POZYCJI 'Diluted EPS' LUB 'Basic EPS' I WYKAZUJĄ CIĄGŁY WZROST ZYSKU NA AKCJĘ ROK DO ROKU.
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;
-- OBLICZ SKUMULOWANY DŁUG (TOTALDEBT) SPÓŁEK W RAMACH KAŻDEGO SEKTORA (PARTITION BY SECTOR ORDER BY TOTALDEBT DESC). DOKONAJ PODZIAŁU TAK, ABY DOWIEDZIEĆ SIĘ, KTÓRE SPÓŁKI W DANYM SEKTORZE ODPOWIADAJĄ ZA PIERWSZE 50% CAŁKOWITEGO ZADŁUŻENIA TEGO SEKTORA

-- OBLICZ MEDIANĘ KAPITALIZACJI RYNKOWEJ (MARKETCAP) DLA KAŻDEJ BRANŻY (INDUSTRY). WYŚWIETL SPÓŁKI, KTÓRYCH KAPITALIZACJA JEST CO NAJMNIEJ 3-KROTNIE WYŻSZA OD MEDIANY DLA JEJ BRANŻY
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;


-- UŻYJ CUME_DIST(), ABY OBLICZONA DYSTRYBUANTA WYCENY (TRAILINGPE) W RAMACH KAŻDEGO SEKTORA WSKAŻAŁA SPÓŁKI ZNAJDUJĄCE SIĘ W NAJNIŻSZYCH 10% DANEGO SEKTORA (NAJTAŃSZE DLA SEKTORA), ALE JEDNOCZEŚNIE POSIADAJĄCE MARŻĘ OPERACYJNĄ (OPERATINGMARGINS) W NAJWYŻSZYCH 25% TEGO SAMEGO SEKTORA

-- NAPISZ ZAPYTANIE ZNAJDUJĄCE SPÓŁKI Z TEGO SAMEGO SEKTORA, KTÓRE MAJĄ SĄSIADUJĄCE DANE DOTYCZĄCE CZYSTEJ GOTÓWKI (TOTALCASH), UŻYWAJĄC SPOSOBU Z SELF JOIN A NASTĘPNIE Z UŻYCIEM LEAD()/LAG(). PRZEPROWADŹ TEST EXPLAIN ANALYZE I PORÓWNAJ KOSZT ORAZ CZAS EXECUTION TAK JAK DLA ZAWODNIKÓW MLB
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;


-- NAPISZ ZAPYTANIE WITH RECURSIVE, KTÓRE WYGENERUJE CIĄG SYMULATORA PROCESU PROCENTU SKŁADANEGO DLA KAPITAŁU POCZĄTKOWEGO 10 000 USD PRZY ŚREDNIEJ STOPOCIE ZWROTU SEKTORA 'TECHNOLOGY' (OBLICZONEJ Z BAZY). WYGENERUJE ROK PO ROKU PROGNOZĘ KAPITAŁU NA 20 LAT PRZÓD.

-- STWÓRZ ZAPYTANIE, KTÓRE ZWRÓCI DLA KAŻDEGO SEKTORA JEDEN REKORD Z DOWOLNYMI INFORMACJAMI O SEKTORZE ORAZ POLE COMPANIES_JSON ZAWIERAJĄCE TABLICĘ OBIEKTÓW JSON DLA WSZYSTKICH SPÓŁEK W DANYM SEKTORZE (SYMBOL, MARKETCAP, CURRENTPRICE)
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;
-- WYKONAJ AGREGACJĘ ŚREDNIEJ KAPITALIZACJI ORAZ MEDIANY WSKAŹNIKA TRAILINGPE Z GRUPOWANIEM SECTOR, INDUSTRY WITH ROLLUP. UŻYJ GROUPING() LUB COALESCE(), ABY OZNACZYĆ WYNIKI SUMA CZĘŚCIOWA SEKTORA ORAZ SUMA CAŁKOWITA BAZY

-- OBLICZ KOWARIANCJĘ ORAZ KORELACJĘ LINIOWĄ PEERSONA POMIĘDZY MARŻĄ ZYSKU (PROFITMARGINS) A WSKAŹNIKIEM TRAILINGPE W RAMACH CAŁEJ BAZY LUB SEKTORA 'FINANCIAL SERVICES' (UŻYJ COVAR_POP, STDDEV_POP I FORMULA NA KORELACJĘ LUB WBUDOWANEJ CORR JEŚLI TWOJA WERSJA BAZY WSPERA).
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;


-- OBLICZ KOWARIANCJĘ ORAZ KORELACJĘ LINIOWĄ PEERSONA POMIĘDZY MARŻĄ ZYSKU (PROFITMARGINS) A WSKAŹNIKIEM TRAILINGPE W RAMACH CAŁEJ BAZY LUB SEKTORA 'FINANCIAL SERVICES' (UŻYJ COVAR_POP, STDDEV_POP I FORMULA NA KORELACJĘ LUB WBUDOWANEJ CORR JEŚLI TWOJA WERSJA BAZY WSPERA).
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;
-- STWÓRZ TABELĘ TEMPORALNĄ/LOGÓW PRICE_HISTORY_LOG ORAZ TRIGGER BEFORE_PRICE_UPDATE, KTÓRY W MOMENCIE ZMIANY CURRENTPRICE W TABELI PROFILI ZAPISZE DANE: SYMBOL, STARA CENA, NOWA CENA, PROCENTOWA ZMIANA ORAZ CZAS MODYFIKACJI

-- NAPISZ PROCEDURĘ SKŁADOWANĄ (CREATE PROCEDURE), KTÓRA POBIERZE DANE Z FMP_INCOME_STATEMENTS DLA WYBRANEJ SPÓŁKI I PRZY UŻYCIU DYNAMICZNEGO SQL (PREPARE stmt FROM ...) PRZEKSZTAŁCI JE W TABELĘ GDZIE KOLUMNAMI SĄ LATA, A WIERSZAMI POZYCJE SPRAWOZDANIA
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;


-- UTWÓRZ W ZAPYTANIU POLE BITOWE FLAGS (SUMUJĄC POTĘGI DWÓJKI DLA WARUNKÓW) DLA SPÓŁKI. ZASTOSUJ OPERATOR BITOWY MASKOWANIA (&), ABY WYZNACZYĆ TYLKO TE SPÓŁKI, KTÓRE POSIADAJĄ JEDNOCZEŚNIE USTAWIONY BIT 2 (HIGH GROWTH) I BIT 8 (TECH SECTOR)

-- ZAMIAST JEDNEGO WIELKIEGO ZAPYTANIA Z CTE, STWÓRZ CREATE TEMPORARY TABLE ZAWIERAJĄCĄ PRZETWORZONE I INDEKSOWANE WSKAŹNIKI ZYSKOWNOSCI, A NASTĘPNIE WYKONAJ NA NIEJ POŁĄCZENIE KROTNE Z BAZĄ GŁÓWNĄ, PORÓWNUJĄC WYDAJNOŚĆ W EXPLAIN ANALYZE W STOSUNKU DO PODEJŚCIA CTE.

-- NAPISZ PROCEDURĘ SPRAWDZAJĄCĄ POPRAWNOŚĆ SPRAWOZDANIA FINANSOWEGO, KTÓRA PRZEGLĄDA BAZĘ I JEŚLI ZNAJDZIE SPÓŁKĘ Z PRZYCHODAMI MNIEJSZYMI NIŻ ZYSK NETTO (TOTAL REVENUE < NET INCOME), RZUCI WYJĄTEK BAZODANOWY PRZY UŻYCIU `SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'BŁĄD SPRAWOZDANIA FINANSOWEGO!
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;
-- WYKORZYSTAJ FUNKCJĘ REGEXP_SUBSTR LUB REGEXP_LIKE, ABY PRZESZUKAĆ POLE LONGBUSINESSSUMMARY W TABELI PROFILI I ZNALEŹĆ SPÓŁKI, KTÓRE W OPISIE POSIADAJĄ SŁOWA 'ARTIFICIAL INTELLIGENCE', 'AI', 'CLOUD' LUB 'SAAS'. STWÓRZ NOWĄ KOLUMNĘ FOUND_TECH, KTÓRA WYCIĄGNIE DOKŁADNIE PASUJĄCY FRAGMENT TEKSTU

-- NA PODSTAWIE DAT W RACHUNKACH WYNIKÓW (PERIOD_END) OBLICZ ILE DNI DZIELI DATĘ SPRAWOZDANIA OD OSTATNIEGO DNIA DANEGO MIESIĄCA (LAST_DAY()) ORAZ W JAKI DZIEŃ TYGODNIA (DAYNAME()) NAJCCZĘŚCIEJ SPÓŁKI ZAMYKAJĄ SWÓJ ROK OBROTOWY

-- NAPISZ ZAPYTANIE DO TABELI SYSTEMOWEJ INFORMATION_SCHEMA.TABLES, KTÓRE ZWRÓCI NAZWY WSZYSTKICH TABEL W TWOJEJ BAZIE STOCK_INVESTING, LICZBĘ WIERSZY W KAŻDEJ Z NICH ORAZ ROZMIAR TABELI W MEGABAJTACH (MB) ZWIĄZANY Z DANYMI I INDEKSAMI (DATA_LENGTH + INDEX_LENGTH)

-- UŻYJ FUNKCJI GROUP_CONCAT(), ABY DLA KAŻDEGO SEKTORA UTWORZYĆ JEDEN CIĄG TEKSTOWY ZAWIERAJĄCY SYMBOLE 5 NAJWIĘKSZYCH SPÓŁEK (POD WZGLĘDEM MARKETCAP), SEPAROWANE MYŚLNIKIEM Z ICH KAPITALIZACJĄ W NAWIASIE (NP. 'AAPL ($3.0T) - MSFT ($2.8T)'
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;
-- UŻYJ OPERATORA ZBIORÓW (EXCEPT LUB ZAPYTANIA Z NOT IN / NOT EXISTS), ABY WYZNACZYĆ SEKTORY LUB BRANŻE, KTÓRE POSIADAJĄ SPÓŁKI W TABELI PROFILI FMP_COMPANY_PROFILES, ALE NIE POSIADAJĄ ŻADNYCH ZAPISANYCH SPRAWOZDAŃ FINANSOWYCH W TABELI FMP_INCOME_STATEMENTS

-- NAPISZ BLOK TRANSAKCYJNY (START TRANSACTION), W KTÓRYM WYKONASZ ODCZYT SPÓŁKI Z BLOKADĄ WIERSZA DO EDYCJI (SELECT ... FOR UPDATE), ZWIĘKSZYSZ JEJ WSKAŹNIK CURRENTPRICE O 5%, A NASTĘPNIE ZATWIERDZISZ TRANSAKCJĘ LUB JEJ ZANIECHASZ (COMMIT / ROLLBACK)
SELECT * FROM assets;
SELECT * FROM daily_quotes;
SELECT * FROM financial_reports;
SELECT * FROM valuation_ratios;
-- OBLICZ DLA KAŻDEJ SPÓŁKI W RAMACH JEJ SEKTORA SUMĘ KAPITALIZACJI WSZYSTKICH SPÓŁEK, KTÓRE SĄ OD NIEJ MAŁO MNIEJSZE (UŻYWAJĄC KLUDI ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) ORAZ STOSUNEK JEJ DOKŁADNEJ KAPITALIZACJI DO CAŁOŚCI POZOSTAŁYCH SPÓŁEK W SEKTORZE.

-- DLA KAŻDEGO SEKTORA WYŚWIETL W JEDNYM WIERSZU: NAZWĘ SEKTORA, SPÓŁKĘ O NAJNIŻSZYM C/Z (TRAILINGPE) ORAZ SPÓŁKĘ O NAJWYŻSZYM C/Z, UŻYWAJĄC JEDYNIE FUNKCJI OKNA FIRST_VALUE() I LAST_VALUE() Z ODPOWIEDNIM RAMKOWANIEM (OVER (PARTITION BY sector ORDER BY trailingpe ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)).

-- STWÓRZ INDEKS NA COLUMNIE SECTOR ORAZ MARKETCAP W TABELI PROFILI. NAPISZ ZAPYTANIE Z DOWOLNYM FILTREM, A NASTĘPNIE PORÓWNAJ W EXPLAIN ANALYZE CZAS WYKONANIA BEZ INDEKSU, Z INDEKSEM DOMYŚLNYM ORAZ Z PRZYMUSOWYM UŻYCIEM INDEKSU (FORCE INDEX(...)).

-- STWÓRZ WIDOK (CREATE VIEW v_public_stock_metrics), KTÓRY UDOSTĘPNIA WSZYSTKIE METRYKI FINANSOWE SPÓŁEK, ALE ZAMIAST DOKŁADNEJ KWOTY DŁUGU (TOTALDEBT) ORAZ PRZYCHODÓW (TOTALREVENUE), MASKUJE JE I PRZEKSZTAŁCA NA SKALĘ LOGARYTMICZNĄ LUB ZAOKRĄGLA DO PEŁNYCH MILIARDÓW, UKRYWAJĄC WRAŻLIWE DANE PRZED ZEWNĘTRZNYMI UŻYTKOWNIKAMI.

-- DLA KAŻDEGO SEKTORA OBLICZ MEDIANĘ WSKAŹNIKA FORWARDPE, A NASTĘPNIE OBLICZ ŚREDNIE BEZWZGLĘDNE ODCHYLENIE (AVG(ABS(forwardPE - mediana))) DLA KAŻDEJ SPÓŁKI W TYM SEKTORZE

-- PRZESZUKAJ BAZĘ POD KĄTEM POL ZAPISANYCH W FORMATOWANIU JSON. UŻYJ FUNKCJI JSON_VALID(), ABY PREFILTROWAC POPRAWNE REKORDY, A NASTĘPNIE WYCIĄGNIJ Z POLA JSON SPÓŁKI ATRIBUTY 'OFFICERS' LUB 'EXECUTIVES' I POLICZ LICZBĘ ZARZĄDU DLA KAŻDEJ SPÓŁKI

-- STWÓRZ WIDOK V_HEALTHY_LARGE_CAPS, KTÓRY FILTRUJE SPÓŁKI O KAPITALIZACJI > 10 MLD USD ORAZ WSKAŹNIKU DEBTTOEQUITY < 100. DODAJ KLAUZULĘ WITH CHECK OPTION I PRZEPROWADŹ PRÓBĘ WSTAWIENIA/EDYCJI REKORDU NIEPASTUJĄCEGO DO WARUNKU.

-- WYZNACZ SPÓŁKI, KTÓRYCH WSKAŹNIK EARNINGSGROWTH JEST W TOP 10% DLA DANEGO SEKTORA (PERCENT_RANK() >= 0.90), ALE JECH QUARTERLYREVENUEGROWTH JEST UJEMNY. WYŚWIETL DIVERGENCJĘ DANYCH ORAZ ZROB CLASSIFICATION ZALECENIA ANITYCZNEGO

-- OZNACZ ISTNIEJĄCY INDEKS NA TABELI PROFILI JAKO NIEWIDOCZNY (ALTER TABLE ... ALTER INDEX ... INVISIBLE), WYKONAJ ZAPYTANIE Z EXPLAIN ANALYZE, A NASTĘPNIE PRZYWRÓĆ JEGO WIDOCZNOŚĆ (VISIBLE) I PORÓWNAJ PLAN EXECUTION

-- NAPISZ ZAPYTANIE Z CTE, KTÓRE DLA KAŻDEGO SEKTORA ZWRÓCI POJEDYNCZY WIERSZ ZAWIERAJĄCY: ŚREDNI DŁUG SEKTORA ORAZ POLE TEKSTOWE WARNING_COMPANIES Z LISTĄ POŁĄCZONYCH PRZECINKAMI SPÓŁEK, KTÓRYCH DŁUG PRZEKRACZA 300% ŚREDNIEJ SEKTOROWEJ.

-- NAPISZ ZŁOŻONY BLOK TRANSAKCJI (START TRANSACTION), W KTÓRYM UTWORZYSZ PUNKT ZAPISU (SAVEPOINT sp1), WYKONASZ AKTUALIZACJĘ CEN SPÓŁEK Z SEKTORA 'ENERGY', A W PRZYPADKU WYSTĄPIENIA WARUNKU BŁĘDU COFNIESZ TRANSAKCJĘ TYLKO DO PUNKTU sp1 (ROLLBACK TO sp1).

-- DODAJ DO TABELI PROFILI KOLUMNĘ GENEROWANĄ NET_CASH_POSITION AS (totalCash - totalDebt) STORED, NAŁÓŻ NA NIĄ INDEKS I SPRAWDŹ PRZYSPIESZENIE WIZUALNE W EXPLAIN ANALYZE PRZY FILTROWANIU SPÓŁEK Z DODATNIĄ POZYCJĄ GOTÓWKOWĄ

-- POŁĄCZ BAZĘ PROFILI ZE SPRAWOZDANIAMI FINANSOWYMI I OBLICZ KROCZĄCĄ ŚREDNIĄ ZYSKU NETTO DLA KAŻDEJ SPÓŁKI UŻYWAJĄC KLUDI RANGE BETWEEN INTERVAL 1 YEAR PRECEDING AND CURRENT ROW W FUNKCJI OKNA

-- STWÓRZ PROCEDURĘ SKŁADOWANĄ Z UŻYCIEM KURSORA (DECLARE CURSOR FOR ...), KTÓRA PRZEITERUJE PO WSZYSTKICH TABELACH BAZY DANYCH STOCK_INVESTING I AUTOMATYCZNIE WYKONASZ NA NICH POLECENIE OPTYMALIZACYJNE OPTIMIZE TABLE W CELE DEFRAGMANTACJI PAMIĘC












