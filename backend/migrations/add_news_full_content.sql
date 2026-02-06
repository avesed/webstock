-- Migration: Add news full content columns
-- Adds columns to user_settings and news tables for full content support

-- User settings: full content configuration
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS full_content_source VARCHAR(20) DEFAULT 'scraper';
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS polygon_api_key TEXT;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS news_retention_days INTEGER DEFAULT 30;

-- User settings: AI processing configuration (allows custom OpenAI-compatible endpoints)
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS news_openai_base_url VARCHAR(500);
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS news_openai_api_key TEXT;
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS news_embedding_model VARCHAR(100) DEFAULT 'text-embedding-3-small';
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS news_filter_model VARCHAR(100) DEFAULT 'gpt-4o-mini';

-- News table: full content reference fields
ALTER TABLE news ADD COLUMN IF NOT EXISTS content_file_path VARCHAR(500);
ALTER TABLE news ADD COLUMN IF NOT EXISTS content_status VARCHAR(20) DEFAULT 'pending';
ALTER TABLE news ADD COLUMN IF NOT EXISTS content_fetched_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE news ADD COLUMN IF NOT EXISTS content_error VARCHAR(500);
ALTER TABLE news ADD COLUMN IF NOT EXISTS language VARCHAR(10);
ALTER TABLE news ADD COLUMN IF NOT EXISTS authors JSONB;
ALTER TABLE news ADD COLUMN IF NOT EXISTS keywords JSONB;
ALTER TABLE news ADD COLUMN IF NOT EXISTS top_image VARCHAR(1024);

-- Add index for content_status (if not exists)
CREATE INDEX IF NOT EXISTS ix_news_content_status ON news(content_status);
