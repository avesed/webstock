-- Migration: Add news_source column to user_settings table
-- Run this SQL to add the news source preference column

-- For PostgreSQL:
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS news_source VARCHAR(50) DEFAULT 'yfinance';

-- For SQLite:
-- ALTER TABLE user_settings ADD COLUMN news_source VARCHAR(50) DEFAULT 'yfinance';

-- For MySQL:
-- ALTER TABLE user_settings ADD COLUMN news_source VARCHAR(50) DEFAULT 'yfinance';
