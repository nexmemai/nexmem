-- Verify AI Memory Layer migrations applied correctly
-- Run this in Supabase SQL Editor: https://app.supabase.com/project/_/sql

-- Check applied migrations
SELECT version_num, applied_at 
FROM alembic_version 
ORDER BY applied_at DESC;

-- Verify new columns exist
SELECT 
    table_name,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name IN ('episodic_memory', 'semantic_memory', 'procedural_memory')
AND column_name IN ('consolidated_at', 'text_search', 'app_id')
ORDER BY table_name, column_name;

-- Verify GIN indexes exist
SELECT 
    indexname,
    tablename,
    indexdef
FROM pg_indexes
WHERE indexname IN ('idx_episodic_text_search', 'idx_semantic_text_search', 'idx_procedural_app_id')
ORDER BY tablename;

-- Check Row count (verify DB is working)
SELECT 
    (SELECT COUNT(*) FROM episodic_memory) as episodic_count,
    (SELECT COUNT(*) FROM semantic_memory) as semantic_count,
    (SELECT COUNT(*) FROM procedural_memory) as procedural_count,
    (SELECT COUNT(*) FROM knowledge_nodes) as graph_nodes_count;
