-- =========================================================================
-- UPIlytics - Star schema for UPI payments analytics (MySQL 8)
--
-- Run order: this file is executed automatically by scripts/02_load_to_mysql.py.
-- You can also run it by hand in MySQL Workbench (File > Open SQL Script).
--
-- Dimensions: dim_date, dim_bank, dim_app
-- Facts:      fact_national_monthly, fact_remitter_performance,
--             fact_beneficiary_performance, fact_app_monthly
--
-- WARNING: this script drops and recreates the tables, so it is safe to
-- re-run but it will erase any data already loaded.
-- =========================================================================

CREATE DATABASE IF NOT EXISTS upilytics
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE upilytics;

-- Drop in dependency order (facts first, then dimensions).
DROP TABLE IF EXISTS fact_app_monthly;
DROP TABLE IF EXISTS fact_beneficiary_performance;
DROP TABLE IF EXISTS fact_remitter_performance;
DROP TABLE IF EXISTS fact_national_monthly;
DROP TABLE IF EXISTS dim_app;
DROP TABLE IF EXISTS dim_bank;
DROP TABLE IF EXISTS dim_date;

-- -------------------------------------------------------------------------
-- Dimension: month (one row per calendar month, keyed by the first day)
-- Fiscal year follows the Indian April-March convention.
-- -------------------------------------------------------------------------
CREATE TABLE dim_date (
    date_key        DATE         NOT NULL,
    year            SMALLINT     NOT NULL,
    month           TINYINT      NOT NULL,
    month_name      VARCHAR(10)  NOT NULL,
    year_month_label CHAR(7)     NOT NULL,
    quarter         TINYINT      NOT NULL,
    fiscal_year     VARCHAR(9)   NOT NULL,
    fiscal_quarter  TINYINT      NOT NULL,
    PRIMARY KEY (date_key),
    UNIQUE KEY uq_dim_date_label (year_month_label)
) ENGINE=InnoDB;

-- -------------------------------------------------------------------------
-- Dimension: bank (used by both remitter and beneficiary facts)
-- bank_category is a rule-based label assigned by the loading script.
-- -------------------------------------------------------------------------
CREATE TABLE dim_bank (
    bank_id        INT           NOT NULL AUTO_INCREMENT,
    bank_name      VARCHAR(100)  NOT NULL,
    bank_category  VARCHAR(30)   NOT NULL,
    PRIMARY KEY (bank_id),
    UNIQUE KEY uq_dim_bank_name (bank_name)
) ENGINE=InnoDB;

-- -------------------------------------------------------------------------
-- Dimension: UPI app
-- app_category is a rule-based label assigned by the loading script.
-- -------------------------------------------------------------------------
CREATE TABLE dim_app (
    app_id        INT           NOT NULL AUTO_INCREMENT,
    app_name      VARCHAR(100)  NOT NULL,
    app_category  VARCHAR(30)   NOT NULL,
    PRIMARY KEY (app_id),
    UNIQUE KEY uq_dim_app_name (app_name)
) ENGINE=InnoDB;

-- -------------------------------------------------------------------------
-- Fact: national monthly totals (volume in millions, value in crores)
-- -------------------------------------------------------------------------
CREATE TABLE fact_national_monthly (
    date_key               DATE           NOT NULL,
    banks_live_on_upi      SMALLINT       NULL,
    volume_mn              DECIMAL(14,2)  NULL,
    value_cr               DECIMAL(16,2)  NULL,
    avg_ticket_size_rs     DECIMAL(10,2)  NULL,
    volume_mom_growth_pct  DECIMAL(14,2)  NULL,
    volume_yoy_growth_pct  DECIMAL(14,2)  NULL,
    PRIMARY KEY (date_key),
    CONSTRAINT fk_national_date FOREIGN KEY (date_key) REFERENCES dim_date (date_key)
) ENGINE=InnoDB;

-- -------------------------------------------------------------------------
-- Fact: remitter (sending) bank performance, top 50 banks per month
-- bd = business decline, td = technical decline
-- -------------------------------------------------------------------------
CREATE TABLE fact_remitter_performance (
    date_key                    DATE           NOT NULL,
    bank_id                     INT            NOT NULL,
    total_volume_mn             DECIMAL(12,2)  NULL,
    approved_pct                DECIMAL(6,2)   NULL,
    bd_pct                      DECIMAL(6,2)   NULL,
    td_pct                      DECIMAL(6,2)   NULL,
    debit_reversal_count_mn     DECIMAL(10,2)  NULL,
    debit_reversal_success_pct  DECIMAL(6,2)   NULL,
    approved_volume_mn          DECIMAL(14,4)  NULL,
    bd_volume_mn                DECIMAL(14,4)  NULL,
    td_volume_mn                DECIMAL(14,4)  NULL,
    volume_rank_in_month        TINYINT        NULL,
    PRIMARY KEY (date_key, bank_id),
    KEY idx_remitter_bank (bank_id),
    CONSTRAINT fk_remitter_date FOREIGN KEY (date_key) REFERENCES dim_date (date_key),
    CONSTRAINT fk_remitter_bank FOREIGN KEY (bank_id)  REFERENCES dim_bank (bank_id)
) ENGINE=InnoDB;

-- -------------------------------------------------------------------------
-- Fact: beneficiary (receiving) bank performance, top 50 banks per month
-- -------------------------------------------------------------------------
CREATE TABLE fact_beneficiary_performance (
    date_key              DATE           NOT NULL,
    bank_id               INT            NOT NULL,
    total_volume_mn       DECIMAL(12,2)  NULL,
    approved_pct          DECIMAL(6,2)   NULL,
    bd_pct                DECIMAL(6,2)   NULL,
    td_pct                DECIMAL(6,2)   NULL,
    deemed_approved_pct   DECIMAL(6,2)   NULL,
    approved_volume_mn    DECIMAL(14,4)  NULL,
    bd_volume_mn          DECIMAL(14,4)  NULL,
    td_volume_mn          DECIMAL(14,4)  NULL,
    volume_rank_in_month  TINYINT        NULL,
    PRIMARY KEY (date_key, bank_id),
    KEY idx_beneficiary_bank (bank_id),
    CONSTRAINT fk_beneficiary_date FOREIGN KEY (date_key) REFERENCES dim_date (date_key),
    CONSTRAINT fk_beneficiary_bank FOREIGN KEY (bank_id)  REFERENCES dim_bank (bank_id)
) ENGINE=InnoDB;

-- -------------------------------------------------------------------------
-- Fact: UPI app volume and value per month
-- B2C and B2B columns are NULL when NPCI did not report them for that app.
-- -------------------------------------------------------------------------
CREATE TABLE fact_app_monthly (
    date_key            DATE           NOT NULL,
    app_id              INT            NOT NULL,
    total_volume_mn     DECIMAL(12,2)  NULL,
    total_value_cr      DECIMAL(16,2)  NULL,
    volume_customer_mn  DECIMAL(12,2)  NULL,
    value_customer_cr   DECIMAL(16,2)  NULL,
    volume_b2c_mn       DECIMAL(12,2)  NULL,
    value_b2c_cr        DECIMAL(16,2)  NULL,
    volume_b2b_mn       DECIMAL(12,2)  NULL,
    value_b2b_cr        DECIMAL(16,2)  NULL,
    volume_share_pct    DECIMAL(8,4)   NULL,
    value_share_pct     DECIMAL(8,4)   NULL,
    PRIMARY KEY (date_key, app_id),
    KEY idx_app_app (app_id),
    CONSTRAINT fk_app_date FOREIGN KEY (date_key) REFERENCES dim_date (date_key),
    CONSTRAINT fk_app_app  FOREIGN KEY (app_id)   REFERENCES dim_app (app_id)
) ENGINE=InnoDB;