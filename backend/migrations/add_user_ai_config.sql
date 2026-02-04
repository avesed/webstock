-- Add user AI config columns to user_settings table
-- Run this migration to add configurable AI parameters per user

-- Add openai_max_tokens column
ALTER TABLE user_settings
ADD COLUMN IF NOT EXISTS openai_max_tokens INTEGER;

-- Add openai_temperature column
ALTER TABLE user_settings
ADD COLUMN IF NOT EXISTS openai_temperature FLOAT;

-- Add openai_system_prompt column
ALTER TABLE user_settings
ADD COLUMN IF NOT EXISTS openai_system_prompt TEXT;
