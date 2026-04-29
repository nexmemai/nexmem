-- Apply missing migrations to Supabase
-- Run this in Supabase SQL Editor

-- ==========================================
-- Migration 003: Add consolidated_at to episodic_memory
-- ==========================================

ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS consolidated_at TIMESTAMPTZ;

COMMENT ON COLUMN episodic_memory.consolidated_at IS 'Timestamp when memory was consolidated (NULL = not yet consolidated)';

-- ==========================================
-- Migration 004: Add FTS tsvector columns and GIN indexes
-- ==========================================

ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS text_search TSVECTOR;
ALTER TABLE semantic_memory ADD COLUMN IF NOT EXISTS text_search TSVECTOR;

CREATE INDEX IF NOT EXISTS idx_episodic_text_search ON episodic_memory USING GIN(text_search);
CREATE INDEX IF NOT EXISTS idx_semantic_text_search ON semantic_memory USING GIN(text_search);

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
-- Update alembic_version table to reflect applied migrations
-- ==========================================

-- Check current version
SELECT version_num FROM alembic_version;

-- Insert missing migration versions (only if not already present)
INSERT INTO alembic_version (version_num) 
SELECT '003_add_consolidated_at' WHERE NOT EXISTS (
    SELECT 1 FROM alembic_version WHERE version_num = '003_add_consolidated_at'
);

INSERT INTO alembic_version (version_num) 
SELECT '004_add_fts_columns' WHERE NOT EXISTS (
    SELECT 1 FROM alembic_version WHERE version_num = '004_add_fts_columns'
);

INSERT INTO alembic_version (version_num) 
SELECT '005_add_app_id_procedural' WHERE NOT EXISTS (
    SELECT 1 FROM alembic_version WHERE version_num = '005_add_app_id_procedural'
);

-- Verify final state
SELECT version_num FROM alembic_version ORDER BY version_num;
