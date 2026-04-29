-- ==========================================
-- AI Memory Layer - Complete Migration SQL
-- Run ALL of this in Supabase SQL Editor
-- ==========================================

-- 1. Add consolidated_at to episodic_memory
ALTER TABLE IF EXISTS episodic_memory 
ADD COLUMN IF NOT EXISTS consolidated_at TIMESTAMPTZ;

COMMENT ON COLUMN episodic_memory.consolidated_at IS 'Timestamp when memory was consolidated (NULL = not yet consolidated)';

-- 2. Add text_search (tsvector) columns
ALTER TABLE IF EXISTS episodic_memory 
ADD COLUMN IF NOT EXISTS text_search TSVECTOR;

ALTER TABLE IF EXISTS semantic_memory 
ADD COLUMN IF NOT EXISTS text_search TSVECTOR;

COMMENT ON COLUMN episodic_memory.text_search IS 'Full-text search vector for keyword matching';
COMMENT ON COLUMN semantic_memory.text_search IS 'Full-text search vector for keyword matching';

-- 3. Create GIN indexes for FTS
CREATE INDEX IF NOT EXISTS idx_episodic_text_search 
ON episodic_memory USING GIN(text_search);

CREATE INDEX IF NOT EXISTS idx_semantic_text_search 
ON semantic_memory USING GIN(text_search);

-- 4. Populate initial FTS data (run once)
UPDATE episodic_memory 
SET text_search = to_tsvector('english', COALESCE(content, '')) 
WHERE text_search IS NULL;

UPDATE semantic_memory 
SET text_search = to_tsvector('english', COALESCE(summary, '')) 
WHERE text_search IS NULL;

-- 5. Add app_id to procedural_memory
ALTER TABLE IF EXISTS procedural_memory 
ADD COLUMN IF NOT EXISTS app_id UUID;

CREATE INDEX IF NOT EXISTS idx_procedural_app_id 
ON procedural_memory(app_id);

COMMENT ON COLUMN procedural_memory.app_id IS 'Optional app_id for multi-app memory scoping (NULL = global/profile level)';

-- 6. Update alembic_version table
-- First check what's there
SELECT version_num FROM alembic_version ORDER BY version_num;

-- Insert missing versions (only if not already present)
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

-- 7. Verify everything is applied
SELECT 'Migration 003' as check, 
       EXISTS (SELECT 1 FROM information_schema.columns 
              WHERE table_name='episodic_memory' AND column_name='consolidated_at') as applied
UNION ALL
SELECT 'Migration 004' as check,
       EXISTS (SELECT 1 FROM information_schema.columns 
              WHERE table_name='episodic_memory' AND column_name='text_search') as applied
UNION ALL
SELECT 'Migration 005' as check,
       EXISTS (SELECT 1 FROM information_schema.columns 
              WHERE table_name='procedural_memory' AND column_name='app_id') as applied
UNION ALL
SELECT 'Alembic Version' as check,
       EXISTS (SELECT 1 FROM alembic_version 
              WHERE version_num = '005_add_app_id_procedural') as applied;

-- 8. Final version check
SELECT version_num FROM alembic_version ORDER BY version_num;
