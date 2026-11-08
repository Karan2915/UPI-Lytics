-- =========================================================================
-- UPIlytics - Analysis queries
--
-- Run 01_create_schema.sql and scripts/02_load_to_mysql.py first.
-- Each query answers one business question. Run them one at a time
-- (select a query, then run the selection).
--
-- Key definitions
--   decline rate  = (business-declined + technical-declined volume) / total volume
--   bd = business decline (for example insufficient funds, wrong PIN)
--   td = technical decline (bank or network side failure)
--   Bank files hold only the top 50 banks each month, so every bank-level
--   query that compares periods keeps only banks present in every month
--   of the window.
-- =========================================================================

USE upilytics;


-- -------------------------------------------------------------------------
-- Q1. How fast is UPI growing month over month?
-- Uses: CTE, LAG, moving average, running total per fiscal year
-- -------------------------------------------------------------------------
WITH monthly AS (
    SELECT f.date_key,
           d.year_month_label,
           d.fiscal_year,
           f.volume_mn,
           f.value_cr
    FROM fact_national_monthly f
    JOIN dim_date d ON d.date_key = f.date_key
    WHERE f.volume_mn > 0
)
SELECT year_month_label                                              AS month,
       volume_mn,
       ROUND((volume_mn / LAG(volume_mn) OVER (ORDER BY date_key) - 1) * 100, 1)
                                                                     AS mom_growth_pct,
       ROUND(AVG(volume_mn) OVER (ORDER BY date_key
             ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 1)           AS moving_avg_3m,
       ROUND(SUM(volume_mn) OVER (PARTITION BY fiscal_year
             ORDER BY date_key), 0)                                  AS fiscal_ytd_volume_mn
FROM monthly
ORDER BY date_key;


-- -------------------------------------------------------------------------
-- Q2. Fiscal-year summary: volume, value, average ticket size, growth
-- The first and last fiscal years are partial, see months_in_data.
-- Uses: CTE, GROUP BY, LAG, NULLIF
-- -------------------------------------------------------------------------
WITH fy AS (
    SELECT d.fiscal_year,
           COUNT(*)            AS months_in_data,
           SUM(f.volume_mn)    AS volume_mn,
           SUM(f.value_cr)     AS value_cr
    FROM fact_national_monthly f
    JOIN dim_date d ON d.date_key = f.date_key
    GROUP BY d.fiscal_year
)
SELECT fiscal_year,
       months_in_data,
       ROUND(volume_mn, 0)                                           AS volume_mn,
       ROUND(value_cr, 0)                                            AS value_cr,
       ROUND(value_cr * 10 / NULLIF(volume_mn, 0), 0)                AS avg_ticket_size_rs,
       CASE WHEN months_in_data = 12 AND
                 LAG(months_in_data) OVER (ORDER BY fiscal_year) = 12
            THEN ROUND((volume_mn / LAG(volume_mn) OVER (ORDER BY fiscal_year) - 1) * 100, 1)
       END                                                           AS volume_growth_pct
FROM fy
ORDER BY fiscal_year;


-- -------------------------------------------------------------------------
-- Q3. Is there seasonality? Average month-over-month growth by calendar month
-- Uses: CTE, LAG, AVG, date dimension join (2019 onward, after the launch phase)
-- -------------------------------------------------------------------------
WITH growth AS (
    SELECT f.date_key,
           d.month,
           d.month_name,
           (f.volume_mn / LAG(f.volume_mn) OVER (ORDER BY f.date_key) - 1) * 100 AS mom_pct
    FROM fact_national_monthly f
    JOIN dim_date d ON d.date_key = f.date_key
    WHERE f.volume_mn > 0
)
SELECT month_name,
       ROUND(AVG(mom_pct), 1) AS avg_mom_growth_pct,
       COUNT(*)               AS years_observed
FROM growth
WHERE date_key >= '2019-01-01'
GROUP BY month, month_name
ORDER BY month;


-- -------------------------------------------------------------------------
-- Q4. Which remitter banks fail the most? (latest 12 months)
-- Banks must appear in all 12 months. Rates are volume-weighted.
-- Uses: CTE, CROSS JOIN, RANK, conditional rates
-- -------------------------------------------------------------------------
WITH latest AS (
    SELECT MAX(date_key) AS last_month
    FROM fact_remitter_performance
),
window12 AS (
    SELECT b.bank_name,
           b.bank_category,
           SUM(f.total_volume_mn) AS volume_mn,
           SUM(f.bd_volume_mn)    AS bd_mn,
           SUM(f.td_volume_mn)    AS td_mn,
           COUNT(*)               AS months_present
    FROM fact_remitter_performance f
    JOIN dim_bank b ON b.bank_id = f.bank_id
    CROSS JOIN latest l
    WHERE f.date_key > DATE_SUB(l.last_month, INTERVAL 12 MONTH)
      AND f.approved_pct IS NOT NULL
    GROUP BY b.bank_name, b.bank_category
    HAVING COUNT(*) = 12
)
SELECT RANK() OVER (ORDER BY (bd_mn + td_mn) / volume_mn DESC)  AS decline_rank,
       bank_name,
       bank_category,
       ROUND(volume_mn, 0)                                       AS volume_mn,
       ROUND(bd_mn / volume_mn * 100, 2)                         AS business_decline_pct,
       ROUND(td_mn / volume_mn * 100, 2)                         AS technical_decline_pct,
       ROUND((bd_mn + td_mn) / volume_mn * 100, 2)               AS total_decline_pct
FROM window12
ORDER BY decline_rank
LIMIT 15;


-- -------------------------------------------------------------------------
-- Q5. Which banks improved or got worse on technical declines?
-- Compares the first 12 months in the data (2022) with the latest 12 months.
-- Uses: CTE, conditional aggregation, CASE, ROW_NUMBER
-- -------------------------------------------------------------------------
WITH latest AS (
    SELECT MAX(date_key) AS last_month
    FROM fact_remitter_performance
),
periods AS (
    SELECT b.bank_name,
           SUM(CASE WHEN f.date_key BETWEEN '2022-01-01' AND '2022-12-01'
                    THEN f.td_volume_mn END)              AS td_early,
           SUM(CASE WHEN f.date_key BETWEEN '2022-01-01' AND '2022-12-01'
                    THEN f.total_volume_mn END)           AS vol_early,
           SUM(CASE WHEN f.date_key > DATE_SUB(l.last_month, INTERVAL 12 MONTH)
                    THEN f.td_volume_mn END)              AS td_late,
           SUM(CASE WHEN f.date_key > DATE_SUB(l.last_month, INTERVAL 12 MONTH)
                    THEN f.total_volume_mn END)           AS vol_late,
           SUM(f.date_key BETWEEN '2022-01-01' AND '2022-12-01')                       AS months_early,
           SUM(f.date_key > DATE_SUB(l.last_month, INTERVAL 12 MONTH))                 AS months_late
    FROM fact_remitter_performance f
    JOIN dim_bank b ON b.bank_id = f.bank_id
    CROSS JOIN latest l
    GROUP BY b.bank_name
),
scored AS (
    SELECT bank_name,
           ROUND(td_early / vol_early * 100, 2)  AS td_pct_2022,
           ROUND(td_late  / vol_late  * 100, 2)  AS td_pct_latest_12m,
           ROUND(td_late / vol_late * 100 - td_early / vol_early * 100, 2) AS change_pp
    FROM periods
    WHERE months_early = 12 AND months_late = 12
)
SELECT ROW_NUMBER() OVER (ORDER BY change_pp)  AS improvement_rank,
       bank_name,
       td_pct_2022,
       td_pct_latest_12m,
       change_pp,
       CASE WHEN change_pp <= -0.10 THEN 'Improved'
            WHEN change_pp >=  0.10 THEN 'Worsened'
            ELSE 'Stable' END                  AS trend
FROM scored
ORDER BY change_pp;


-- -------------------------------------------------------------------------
-- Q6. Do payments banks, small finance banks and others fail differently?
-- Uses: JOIN, GROUP BY, volume-weighted rates by calendar year
-- -------------------------------------------------------------------------
SELECT d.year,
       b.bank_category,
       COUNT(DISTINCT b.bank_id)                                         AS banks,
       ROUND(SUM(f.total_volume_mn), 0)                                  AS volume_mn,
       ROUND(SUM(f.bd_volume_mn) / SUM(f.total_volume_mn) * 100, 2)      AS business_decline_pct,
       ROUND(SUM(f.td_volume_mn) / SUM(f.total_volume_mn) * 100, 2)      AS technical_decline_pct
FROM fact_remitter_performance f
JOIN dim_bank b ON b.bank_id = f.bank_id
JOIN dim_date d ON d.date_key = f.date_key
WHERE f.approved_pct IS NOT NULL
GROUP BY d.year, b.bank_category
ORDER BY d.year, technical_decline_pct DESC;


-- -------------------------------------------------------------------------
-- Q7. Banks with a higher technical decline rate than their category average
-- in the latest month (correlated subquery).
-- Uses: correlated subquery, subquery in WHERE
-- -------------------------------------------------------------------------
SELECT b.bank_name,
       b.bank_category,
       f.total_volume_mn,
       f.td_pct,
       ROUND((SELECT AVG(f2.td_pct)
              FROM fact_remitter_performance f2
              JOIN dim_bank b2 ON b2.bank_id = f2.bank_id
              WHERE f2.date_key = f.date_key
                AND b2.bank_category = b.bank_category), 2) AS category_avg_td_pct
FROM fact_remitter_performance f
JOIN dim_bank b ON b.bank_id = f.bank_id
WHERE f.date_key = (SELECT MAX(date_key) FROM fact_remitter_performance)
  AND f.td_pct > (SELECT AVG(f2.td_pct)
                  FROM fact_remitter_performance f2
                  JOIN dim_bank b2 ON b2.bank_id = f2.bank_id
                  WHERE f2.date_key = f.date_key
                    AND b2.bank_category = b.bank_category)
ORDER BY f.td_pct DESC;


-- -------------------------------------------------------------------------
-- Q8. Which banks recover failed debits worst? (debit reversal success)
-- Weighted by reversal count, latest 12 months, banks present in all 12.
-- Uses: weighted average, HAVING, DENSE_RANK
-- -------------------------------------------------------------------------
WITH latest AS (
    SELECT MAX(date_key) AS last_month
    FROM fact_remitter_performance
)
SELECT DENSE_RANK() OVER (ORDER BY SUM(f.debit_reversal_count_mn * f.debit_reversal_success_pct)
                                   / SUM(f.debit_reversal_count_mn))      AS worst_rank,
       b.bank_name,
       ROUND(SUM(f.debit_reversal_count_mn), 1)                           AS reversals_mn,
       ROUND(SUM(f.debit_reversal_count_mn * f.debit_reversal_success_pct)
             / SUM(f.debit_reversal_count_mn), 2)                         AS reversal_success_pct
FROM fact_remitter_performance f
JOIN dim_bank b ON b.bank_id = f.bank_id
CROSS JOIN latest l
WHERE f.date_key > DATE_SUB(l.last_month, INTERVAL 12 MONTH)
  AND f.debit_reversal_count_mn IS NOT NULL
  AND f.debit_reversal_success_pct IS NOT NULL
GROUP BY b.bank_name
HAVING COUNT(*) = 12
ORDER BY worst_rank
LIMIT 10;


-- -------------------------------------------------------------------------
-- Q9. Bank market share and how it moved in the last year
-- Share is of the top-50 remitter volume, not of all UPI.
-- Uses: CTEs, window SUM, DENSE_RANK, self-join across two dates
-- -------------------------------------------------------------------------
WITH shares AS (
    SELECT f.date_key,
           b.bank_name,
           f.total_volume_mn / SUM(f.total_volume_mn) OVER (PARTITION BY f.date_key) * 100 AS share_pct,
           DENSE_RANK() OVER (PARTITION BY f.date_key ORDER BY f.total_volume_mn DESC)     AS rnk
    FROM fact_remitter_performance f
    JOIN dim_bank b ON b.bank_id = f.bank_id
),
latest AS (
    SELECT MAX(date_key) AS last_month FROM fact_remitter_performance
)
SELECT now_m.bank_name,
       now_m.rnk                                    AS rank_now,
       prev.rnk                                     AS rank_year_ago,
       ROUND(now_m.share_pct, 2)                    AS share_now_pct,
       ROUND(prev.share_pct, 2)                     AS share_year_ago_pct,
       ROUND(now_m.share_pct - prev.share_pct, 2)   AS share_change_pp
FROM shares now_m
CROSS JOIN latest l
LEFT JOIN shares prev
       ON prev.bank_name = now_m.bank_name
      AND prev.date_key  = DATE_SUB(l.last_month, INTERVAL 12 MONTH)
WHERE now_m.date_key = l.last_month
  AND now_m.rnk <= 10
ORDER BY now_m.rnk;


-- -------------------------------------------------------------------------
-- Q10. Which apps are gaining or losing share? (top 10 apps, last 12 months)
-- Uses: CTE, CASE, join across two dates
-- -------------------------------------------------------------------------
WITH latest AS (
    SELECT MAX(date_key) AS last_month FROM fact_app_monthly
)
SELECT a.app_name,
       a.app_category,
       ROUND(now_m.total_volume_mn, 0)                        AS volume_mn_now,
       ROUND(now_m.volume_share_pct, 2)                       AS share_now_pct,
       ROUND(prev.volume_share_pct, 2)                        AS share_year_ago_pct,
       ROUND(now_m.volume_share_pct - COALESCE(prev.volume_share_pct, 0), 2) AS share_change_pp,
       CASE WHEN prev.volume_share_pct IS NULL THEN 'New entrant'
            WHEN now_m.volume_share_pct - prev.volume_share_pct >=  0.5 THEN 'Gaining'
            WHEN now_m.volume_share_pct - prev.volume_share_pct <= -0.5 THEN 'Losing'
            ELSE 'Stable' END                                 AS trend
FROM fact_app_monthly now_m
CROSS JOIN latest l
JOIN dim_app a ON a.app_id = now_m.app_id
LEFT JOIN fact_app_monthly prev
       ON prev.app_id   = now_m.app_id
      AND prev.date_key = DATE_SUB(l.last_month, INTERVAL 12 MONTH)
WHERE now_m.date_key = l.last_month
ORDER BY now_m.total_volume_mn DESC
LIMIT 10;


-- -------------------------------------------------------------------------
-- Q11. How concentrated is the app market? Top-2 share and HHI by month
-- HHI = sum of squared market shares (0 to 10,000); higher means a market
-- dominated by few players. The 'Others' bucket is excluded.
-- Uses: CTE, ROW_NUMBER, conditional SUM, POWER
-- -------------------------------------------------------------------------
WITH ranked AS (
    SELECT f.date_key,
           f.volume_share_pct,
           ROW_NUMBER() OVER (PARTITION BY f.date_key
                              ORDER BY f.total_volume_mn DESC) AS rn
    FROM fact_app_monthly f
    JOIN dim_app a ON a.app_id = f.app_id
    WHERE a.app_name <> 'Others'
)
SELECT d.year_month_label                                          AS month,
       ROUND(SUM(CASE WHEN rn <= 2 THEN volume_share_pct END), 1)  AS top2_share_pct,
       ROUND(SUM(POWER(volume_share_pct, 2)), 0)                   AS hhi
FROM ranked r
JOIN dim_date d ON d.date_key = r.date_key
GROUP BY d.year_month_label, d.date_key
ORDER BY d.date_key;


-- -------------------------------------------------------------------------
-- Q12. Bank apps vs third-party apps: share of UPI volume by quarter
-- Uses: JOIN, window SUM over GROUP BY, quarter from the date dimension
-- -------------------------------------------------------------------------
SELECT d.year,
       d.quarter,
       a.app_category,
       ROUND(SUM(f.total_volume_mn), 0)                                          AS volume_mn,
       ROUND(SUM(f.total_volume_mn) * 100 /
             SUM(SUM(f.total_volume_mn)) OVER (PARTITION BY d.year, d.quarter), 1) AS share_of_quarter_pct
FROM fact_app_monthly f
JOIN dim_app  a ON a.app_id   = f.app_id
JOIN dim_date d ON d.date_key = f.date_key
GROUP BY d.year, d.quarter, a.app_category
ORDER BY d.year, d.quarter, volume_mn DESC;


-- -------------------------------------------------------------------------
-- Q13. Reconciliation: app totals vs the national monthly figure
-- Only months present in both tables (Jan 2022 to Aug 2023) are compared.
-- Uses: CTE, join of aggregates, percentage difference
-- -------------------------------------------------------------------------
WITH app_totals AS (
    SELECT date_key, SUM(total_volume_mn) AS app_volume_mn
    FROM fact_app_monthly
    GROUP BY date_key
)
SELECT d.year_month_label            AS month,
       n.volume_mn                   AS national_volume_mn,
       ROUND(t.app_volume_mn, 2)     AS app_volume_mn,
       ROUND((t.app_volume_mn / n.volume_mn - 1) * 100, 2) AS difference_pct
FROM fact_national_monthly n
JOIN app_totals t ON t.date_key = n.date_key
JOIN dim_date   d ON d.date_key = n.date_key
ORDER BY n.date_key;


-- -------------------------------------------------------------------------
-- Q14. Data quality: how consistently does each bank appear in the top 50?
-- Banks that drop in and out explain gaps in time-series, so check this
-- before trusting any trend for a bank.
-- Uses: GROUP BY, subquery, MIN/MAX on dates
-- -------------------------------------------------------------------------
SELECT b.bank_name,
       COUNT(*)                         AS months_present,
       (SELECT COUNT(DISTINCT date_key) FROM fact_remitter_performance) AS months_total,
       MIN(f.date_key)                  AS first_month,
       MAX(f.date_key)                  AS last_month
FROM fact_remitter_performance f
JOIN dim_bank b ON b.bank_id = f.bank_id
GROUP BY b.bank_name
HAVING COUNT(*) < (SELECT COUNT(DISTINCT date_key) FROM fact_remitter_performance)
ORDER BY months_present;


-- -------------------------------------------------------------------------
-- Q15. Data quality: missing values and duplicates in the fact tables
-- Expect zero duplicates; the NULL counts match docs/data_quality_log.md.
-- Uses: UNION ALL, GROUP BY HAVING in derived tables
-- -------------------------------------------------------------------------
SELECT 'remitter: missing approved_pct'  AS check_name, COUNT(*) AS issue_rows
FROM fact_remitter_performance WHERE approved_pct IS NULL
UNION ALL
SELECT 'remitter: missing debit_reversal_success_pct', COUNT(*)
FROM fact_remitter_performance WHERE debit_reversal_success_pct IS NULL
UNION ALL
SELECT 'beneficiary: missing approved_pct', COUNT(*)
FROM fact_beneficiary_performance WHERE approved_pct IS NULL
UNION ALL
SELECT 'apps: missing total_volume_mn', COUNT(*)
FROM fact_app_monthly WHERE total_volume_mn IS NULL
UNION ALL
SELECT 'remitter: duplicate bank-month rows', COUNT(*) FROM (
    SELECT date_key, bank_id
    FROM fact_remitter_performance
    GROUP BY date_key, bank_id
    HAVING COUNT(*) > 1
) AS dup_remitter
UNION ALL
SELECT 'apps: duplicate app-month rows', COUNT(*) FROM (
    SELECT date_key, app_id
    FROM fact_app_monthly
    GROUP BY date_key, app_id
    HAVING COUNT(*) > 1
) AS dup_apps;