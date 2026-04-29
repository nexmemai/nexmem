/*
  AI Memory Layer - Migration SQL
  Run these commands in your Supabase SQL Editor
  https://app.supabase.com/project/_/sql
*/

-- ==========================================
-- Migration 003: Add consolidated_at to episodic_memory
-- ==========================================

ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS consolidated_at TIMESTAMPTZ;

COMMENT ON COLUMN episodic_memory.consolidated_at IS 'Timestamp when memory was consolidated (NULL = not yet consolidated)';


-- ==========================================
-- Migration 004: Add FTS tsvector columns and GIN indexes
-- ==========================================

-- Add tsvector columns
ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS text_search TSVECTOR;
ALTER TABLE semantic_memory ADD COLUMN IF NOT EXISTS text_search TSVECTOR;

-- Create GIN indexes for full-text search
CREATE INDEX IF NOT EXISTS idx_episodic_text_search ON episodic_memory USING GIN(text_search);
CREATE INDEX IF NOT EXISTS idx_semantic_text_search ON semantic_memory USING GIN(text_search);

-- Populate initial data (run once)
UPDATE episodic_memory SET text_search = to_tsvector('english', COALESCE(content, '')) WHERE text_search IS NULL;
UPDATE semantic_memory SET text_search = to_tsvector('english', COALESCE(summary, '')) WHERE text_search IS NULL;

COMMENT ON COLUMN episodic_memory.text_search IS 'Full-text search vector for keyword matching';
COMMENT ON COLUMN semantic_memory.text_search IS 'Full-text search vector for keyword matching';


-- ==========================================
-- Migration 005: Add app_id to procedural_memory
-- ==========================================

ALTER TABLE procedural_memory ADD COLUMN IF NOT EXISTS app_id UUID REFERENCES NULL;

CREATE INDEX IF NOT EXISTS idx_procedural_app_id ON procedural_memory(app_id);

COMMENT ON COLUMN procedural_memory.app_id IS 'Optional app_id for multi-app memory scoping (NULL = global/profile level)';


-- ==========================================
-- Verify migrations applied correctly
-- ==========================================

-- Check columns exist
SELECT column_name, data_type FROM information_schema.columns 
WHERE table_name IN ('episodic_memory', 'semantic_memory', 'procedural_memory') 
AND column_name IN ('consolidated_at', 'text_search', 'app_id')
ORDER BY table_name, column_name;

-- Check indexes exist
SELECT indexname, tablename FROM pg_indexes 
WHERE indexname IN ('idx_episodic_text_search', 'idx_semantic_text_search', 'idx_procedural_app_id');
