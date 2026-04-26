-- ============================================================
-- Decentralized AI Memory Layer — Initial Schema
-- Target: Supabase-hosted PostgreSQL + pgvector extension
-- Retention: episodic decay = MEMORY_DECAY_DAYS (env var, default 30)
--            semantic + procedural = never (manual delete only)
--            associative = never (manual delete only)
-- Scoping: user_id is the single source of truth for isolation
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For fuzzy text search on graph labels

-- ----------------------------------------------------------
-- EPISODIC MEMORY (time-stamped conversation history)
-- Decay: MEMORY_DECAY_DAYS = 30 (default global, enforced by
-- a scheduled job — see cleanup job below)
-- ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS episodic_memory (
    id              UUID        NOT NULL DEFAULT uuid_generate_v4(),
    user_id         TEXT        NOT NULL,
    session_id      TEXT        NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content         TEXT        NOT NULL,
    metadata        JSONB       DEFAULT '{}',
    tags            TEXT[]      DEFAULT '{}',
    store_episodic  BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
);

-- Indexes for fast retrieval by user + time range
CREATE INDEX IF NOT EXISTS idx_episodic_user_id
    ON episodic_memory (user_id);
CREATE INDEX IF NOT EXISTS idx_episodic_timestamp
    ON episodic_memory (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_user_session
    ON episodic_memory (user_id, session_id);

COMMENT ON TABLE episodic_memory IS
    'Episodic memory: time-stamped conversation history. '
    'Retention enforced by a scheduled cleanup job (default 30 days).';


-- ----------------------------------------------------------
-- SEMANTIC MEMORY (vector embeddings for meaning retrieval)
-- Uses pgvector with 1536-dim vectors (text-embedding-3-small)
-- Decay: never (manual delete only)
-- ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS semantic_memory (
    id               UUID        NOT NULL DEFAULT uuid_generate_v4(),
    user_id          TEXT        NOT NULL,
    episodic_id      UUID        REFERENCES episodic_memory(id)
                                 ON DELETE SET NULL,
    vector           VECTOR(1536) NOT NULL,
    embedding_model  TEXT        NOT NULL DEFAULT 'text-embedding-3-small',
    summary          TEXT,
    content_preview  TEXT,
    metadata         JSONB       DEFAULT '{}',
    index_semantic   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
);

-- IVFFlat index for approximate nearest-neighbor search
-- lists = 100 is a reasonable default for <1M rows; adjust as needed
CREATE INDEX IF NOT EXISTS idx_semantic_vector
    ON semantic_memory USING ivfflat (vector vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_semantic_user_id
    ON semantic_memory (user_id);

COMMENT ON TABLE semantic_memory IS
    'Semantic memory: 1536-dim vector embeddings via text-embedding-3-small. '
    'Never auto-deleted; manual removal only.';


-- ----------------------------------------------------------
-- PROCEDURAL MEMORY (user preferences, settings, workflows)
-- Stored as structured JSONB
-- Decay: never (manual delete only)
-- ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS procedural_memory (
    id                UUID        NOT NULL DEFAULT uuid_generate_v4(),
    user_id           TEXT        NOT NULL UNIQUE,
    settings          JSONB       DEFAULT '{}',
    workflows         JSONB       DEFAULT '[]',
    store_procedural  BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_procedural_user_id
    ON procedural_memory (user_id);

COMMENT ON TABLE procedural_memory IS
    'Procedural memory: user preferences, settings, and workflows. '
    'Never auto-deleted; upsert semantics (one row per user).';


-- ----------------------------------------------------------
-- ASSOCIATIVE MEMORY (knowledge graph — nodes + edges)
-- Decay: never (manual delete only)
-- ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id                UUID        NOT NULL DEFAULT uuid_generate_v4(),
    user_id           TEXT        NOT NULL,
    label             TEXT        NOT NULL,
    type              TEXT        NOT NULL,
    properties        JSONB       DEFAULT '{}',
    store_associative BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_node_user_id
    ON knowledge_nodes (user_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_node_label
    ON knowledge_nodes USING gin (label gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_knowledge_node_type
    ON knowledge_nodes (type);


CREATE TABLE IF NOT EXISTS knowledge_edges (
    id                UUID        NOT NULL DEFAULT uuid_generate_v4(),
    user_id           TEXT        NOT NULL,
    from_node_id      UUID        NOT NULL
                                 REFERENCES knowledge_nodes(id)
                                 ON DELETE CASCADE,
    to_node_id        UUID        NOT NULL
                                 REFERENCES knowledge_nodes(id)
                                 ON DELETE CASCADE,
    relation          TEXT        NOT NULL,
    weight            FLOAT       DEFAULT 1.0,
    metadata          JSONB       DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT no_self_loop CHECK (from_node_id != to_node_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_edge_user_id
    ON knowledge_edges (user_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_edge_from
    ON knowledge_edges (from_node_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_edge_to
    ON knowledge_edges (to_node_id);

COMMENT ON TABLE knowledge_nodes IS
    'Associative memory: knowledge graph nodes. Never auto-deleted.';
COMMENT ON TABLE knowledge_edges IS
    'Associative memory: knowledge graph edges. Never auto-deleted.';


-- ----------------------------------------------------------
-- MEMORY STATS VIEW (for dashboard stats bar)
-- ----------------------------------------------------------

CREATE OR REPLACE VIEW memory_stats AS
SELECT
    user_id,
    (SELECT COUNT(*) FROM episodic_memory WHERE user_id = ep.user_id) AS episodic_count,
    (SELECT COUNT(*) FROM semantic_memory WHERE user_id = ep.user_id) AS semantic_count,
    (SELECT COUNT(*) FROM procedural_memory WHERE user_id = ep.user_id) AS procedural_count,
    (SELECT COUNT(*) FROM knowledge_nodes WHERE user_id = kn.user_id) AS graph_node_count,
    (SELECT COUNT(*) FROM knowledge_edges WHERE user_id = ke.user_id) AS graph_edge_count,
    (
        (SELECT COUNT(*) FROM episodic_memory WHERE user_id = ep.user_id) +
        (SELECT COUNT(*) FROM semantic_memory WHERE user_id = ep.user_id) +
        (SELECT COUNT(*) FROM procedural_memory WHERE user_id = ep.user_id) +
        (SELECT COUNT(*) FROM knowledge_nodes WHERE user_id = kn.user_id)
    ) AS total_memories
FROM episodic_memory ep
FULL OUTER JOIN knowledge_nodes kn ON ep.user_id = kn.user_id
FULL OUTER JOIN knowledge_edges ke ON ep.user_id = ke.user_id
GROUP BY ep.user_id, kn.user_id, ke.user_id;


-- ----------------------------------------------------------
-- CLEANUP FUNCTION (episodic memory decay)
-- Deletes episodic memories older than MEMORY_DECAY_DAYS
-- ----------------------------------------------------------

CREATE OR REPLACE FUNCTION cleanup_expired_episodic_memory()
RETURNS int
LANGUAGE plpgsql
AS $$
DECLARE
    decay_days INT := COALESCE(
        NULLIF(current_setting('app.memory_decay_days', true), '')::INT,
        30
    );
    deleted_count INT;
BEGIN
    DELETE FROM episodic_memory
    WHERE timestamp < NOW() - (decay_days || ' days')::INTERVAL
      AND store_episodic = TRUE;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    RAISE NOTICE 'Cleaned up % episodic_memory rows older than % days', deleted_count, decay_days;
    RETURN deleted_count;
END;
$$;

COMMENT ON FUNCTION cleanup_expired_episodic_memory IS
    'Deletes episodic memories older than MEMORY_DECAY_DAYS (default 30). '
    'Respects store_episodic flag — rows with FALSE are not touched. '
    'Returns the number of deleted rows.';


-- ----------------------------------------------------------
-- RECENT MEMORIES VIEW (for Live Memory Feed)
-- ----------------------------------------------------------

CREATE OR REPLACE VIEW recent_memories AS
SELECT
    'episodic' AS memory_type,
    id,
    user_id,
    content AS text_content,
    timestamp AS created_at,
    metadata
FROM episodic_memory
WHERE store_episodic = TRUE

UNION ALL

SELECT
    'semantic' AS memory_type,
    id,
    user_id,
    COALESCE(summary, content_preview) AS text_content,
    created_at,
    metadata
FROM semantic_memory
WHERE index_semantic = TRUE

UNION ALL

SELECT
    'procedural' AS memory_type,
    id,
    user_id,
    'Settings: ' || settings::text AS text_content,
    updated_at AS created_at,
    '{}'::jsonb AS metadata
FROM procedural_memory
WHERE store_procedural = TRUE

ORDER BY created_at DESC
LIMIT 50;


-- ============================================================
-- SEED DATA
-- 1 user (user_id = "demo_user")
-- 5 episodic items, 5 semantic items,
-- 3 procedural preferences
-- ============================================================

-- Clean existing seed data for demo_user (idempotent)
DELETE FROM knowledge_edges WHERE user_id = 'demo_user';
DELETE FROM knowledge_nodes WHERE user_id = 'demo_user';
DELETE FROM semantic_memory WHERE user_id = 'demo_user';
DELETE FROM episodic_memory WHERE user_id = 'demo_user';
DELETE FROM procedural_memory WHERE user_id = 'demo_user';

-- Seed procedural memory
INSERT INTO procedural_memory (user_id, settings, workflows)
VALUES (
    'demo_user',
    '{
        "theme": "dark",
        "language": "en",
        "notifications": true,
        "timezone": "UTC",
        "response_style": "concise",
        "preferred_model": "gpt-4o"
    }',
    '[
        {
            "id": "wf_001",
            "name": "daily_briefing",
            "trigger": "8am",
            "actions": ["fetch_news", "summarize", "send_notification"],
            "enabled": true
        },
        {
            "id": "wf_002",
            "name": "weekly_review",
            "trigger": "friday_afternoon",
            "actions": ["collect_logs", "generate_report", "email_summary"],
            "enabled": true
        },
        {
            "id": "wf_003",
            "name": "project_tracker",
            "trigger": "on_mention",
            "actions": ["log_task", "update_board", "notify_team"],
            "enabled": true
        }
    ]'::jsonb
);

-- Seed episodic memories
INSERT INTO episodic_memory (user_id, session_id, timestamp, content, metadata, tags, store_episodic)
VALUES
    ('demo_user', 'session_001', NOW() - INTERVAL '20 days',
     'User asked about project management tools and showed interest in Notion and Linear for tracking their AI startup projects.',
     '{"source": "chat", "sentiment": "positive", "entities": ["Notion", "Linear"]}'::jsonb,
     ARRAY['project-management', 'notion', 'linear'], TRUE),

    ('demo_user', 'session_001', NOW() - INTERVAL '19 days',
     'User prefers concise bullet-point responses over long paragraphs. Values efficiency in AI interactions.',
     '{"source": "chat", "sentiment": "neutral", "preference_type": "communication"}'::jsonb,
     ARRAY['preference', 'response-style', 'communication'], TRUE),

    ('demo_user', 'session_001', NOW() - INTERVAL '15 days',
     'User is building a new startup in the AI infrastructure space, focusing on memory systems for LLMs.',
     '{"source": "chat", "sentiment": "excited", "entities": ["AI infrastructure", "LLMs"]}'::jsonb,
     ARRAY['context', 'startup', 'ai-infrastructure'], TRUE),

    ('demo_user', 'session_002', NOW() - INTERVAL '10 days',
     'User asked for help debugging a Python async issue with FastAPI and asyncpg. Resolved the connection pool timeout.',
     '{"source": "chat", "language": "python", "framework": "fastapi", "issue": "async"}'::jsonb,
     ARRAY['coding', 'fastapi', 'debugging', 'async'], TRUE),

    ('demo_user', 'session_002', NOW() - INTERVAL '8 days',
     'User wants to set up a persistent memory layer for their AI agent. Discussed pgvector and PostgreSQL as the storage backend.',
     '{"source": "chat", "topic": "ai-memory-layer", "technology": "pgvector"}'::jsonb,
     ARRAY['ai', 'pgvector', 'memory-layer', 'postgresql'], TRUE);


-- Seed semantic memories
-- Note: In production, replace the zero vector with real embeddings from text-embedding-3-small
-- For MVP, we use a normalized random vector that will return random similarity scores
INSERT INTO semantic_memory (user_id, episodic_id, vector, summary, content_preview, metadata, index_semantic)
SELECT
    'demo_user',
    e.id,
    -- Generate a pseudo-random normalized vector for demo purposes
    -- In production, replace with actual OpenAI embeddings
    (
        SELECT array_fill(
            (random() - 0.5)::float / sqrt(1536.0),
            ARRAY[1536]
        )::vector(1536)
    ),
    'Interest in project management tools — Notion and Linear',
    'User asked about project management tools and showed interest in Notion and Linear...',
    '{"topic": "project-management", "tools": ["Notion", "Linear"]}'::jsonb,
    TRUE
FROM episodic_memory e
WHERE e.content LIKE '%project management%'
LIMIT 1;

INSERT INTO semantic_memory (user_id, episodic_id, vector, summary, content_preview, metadata, index_semantic)
SELECT
    'demo_user',
    e.id,
    (
        SELECT array_fill(
            (random() - 0.5)::float / sqrt(1536.0),
            ARRAY[1536]
        )::vector(1536)
    ),
    'User prefers concise bullet-point communication style',
    'User prefers concise bullet-point responses over long paragraphs...',
    '{"preference": "response-style", "communication": "concise"}'::jsonb,
    TRUE
FROM episodic_memory e
WHERE e.content LIKE '%concise%'
LIMIT 1;

INSERT INTO semantic_memory (user_id, episodic_id, vector, summary, content_preview, metadata, index_semantic)
SELECT
    'demo_user',
    e.id,
    (
        SELECT array_fill(
            (random() - 0.5)::float / sqrt(1536.0),
            ARRAY[1536]
        )::vector(1536)
    ),
    'Building AI infrastructure startup focused on LLM memory systems',
    'User is building a new startup in the AI infrastructure space...',
    '{"context": "startup", "domain": "ai-infrastructure"}'::jsonb,
    TRUE
FROM episodic_memory e
WHERE e.content LIKE '%startup%'
LIMIT 1;

INSERT INTO semantic_memory (user_id, episodic_id, vector, summary, content_preview, metadata, index_semantic)
SELECT
    'demo_user',
    e.id,
    (
        SELECT array_fill(
            (random() - 0.5)::float / sqrt(1536.0),
            ARRAY[1536]
        )::vector(1536)
    ),
    'Python async debugging with FastAPI — connection pool timeout',
    'User asked for help debugging a Python async issue with FastAPI...',
    '{"topic": "debugging", "framework": "fastapi", "language": "python"}'::jsonb,
    TRUE
FROM episodic_memory e
WHERE e.content LIKE '%FastAPI%'
LIMIT 1;

INSERT INTO semantic_memory (user_id, episodic_id, vector, summary, content_preview, metadata, index_semantic)
SELECT
    'demo_user',
    e.id,
    (
        SELECT array_fill(
            (random() - 0.5)::float / sqrt(1536.0),
            ARRAY[1536]
        )::vector(1536)
    ),
    'AI memory layer using pgvector and PostgreSQL',
    'User wants to set up a persistent memory layer for their AI agent...',
    '{"topic": "ai-memory-layer", "technology": "pgvector"}'::jsonb,
    TRUE
FROM episodic_memory e
WHERE e.content LIKE '%memory layer%'
LIMIT 1;


-- Seed knowledge graph nodes
INSERT INTO knowledge_nodes (user_id, label, type, properties)
VALUES
    ('demo_user', 'AI Infrastructure', 'domain',
     '{"description": "The field of AI infrastructure and tooling", "relevance": 0.95}'::jsonb),

    ('demo_user', 'pgvector', 'technology',
     '{"description": "PostgreSQL vector extension for similarity search", "language": "SQL", "relevance": 0.9}'::jsonb),

    ('demo_user', 'Memory Layer', 'concept',
     '{"description": "Persistent memory system for AI agents", "relevance": 0.98}'::jsonb),

    ('demo_user', 'FastAPI', 'framework',
     '{"description": "Modern Python web framework for building APIs", "language": "Python", "relevance": 0.85}'::jsonb),

    ('demo_user', 'Notion', 'tool',
     '{"description": "All-in-one workspace and project management tool", "category": "productivity", "relevance": 0.7}'::jsonb),

    ('demo_user', 'AI Agent', 'concept',
     '{"description": "Autonomous or semi-autonomous AI system", "relevance": 0.95}'::jsonb);


-- Seed knowledge graph edges
INSERT INTO knowledge_edges (user_id, from_node_id, to_node_id, relation, weight, metadata)
SELECT
    'demo_user',
    (SELECT id FROM knowledge_nodes WHERE label = 'Memory Layer' AND user_id = 'demo_user'),
    (SELECT id FROM knowledge_nodes WHERE label = 'pgvector' AND user_id = 'demo_user'),
    'implemented_with', 0.95,
    '{"confidence": "high"}'::jsonb;

INSERT INTO knowledge_edges (user_id, from_node_id, to_node_id, relation, weight, metadata)
SELECT
    'demo_user',
    (SELECT id FROM knowledge_nodes WHERE label = 'Memory Layer' AND user_id = 'demo_user'),
    (SELECT id FROM knowledge_nodes WHERE label = 'AI Infrastructure' AND user_id = 'demo_user'),
    'part_of', 0.9,
    '{"confidence": "high"}'::jsonb;

INSERT INTO knowledge_edges (user_id, from_node_id, to_node_id, relation, weight, metadata)
SELECT
    'demo_user',
    (SELECT id FROM knowledge_nodes WHERE label = 'AI Agent' AND user_id = 'demo_user'),
    (SELECT id FROM knowledge_nodes WHERE label = 'Memory Layer' AND user_id = 'demo_user'),
    'uses', 0.95,
    '{"confidence": "high"}'::jsonb;

INSERT INTO knowledge_edges (user_id, from_node_id, to_node_id, relation, weight, metadata)
SELECT
    'demo_user',
    (SELECT id FROM knowledge_nodes WHERE label = 'pgvector' AND user_id = 'demo_user'),
    (SELECT id FROM knowledge_nodes WHERE label = 'AI Infrastructure' AND user_id = 'demo_user'),
    'enables', 0.85,
    '{"confidence": "medium"}'::jsonb;

INSERT INTO knowledge_edges (user_id, from_node_id, to_node_id, relation, weight, metadata)
SELECT
    'demo_user',
    (SELECT id FROM knowledge_nodes WHERE label = 'FastAPI' AND user_id = 'demo_user'),
    (SELECT id FROM knowledge_nodes WHERE label = 'Memory Layer' AND user_id = 'demo_user'),
    'hosts', 0.8,
    '{"confidence": "medium"}'::jsonb;

INSERT INTO knowledge_edges (user_id, from_node_id, to_node_id, relation, weight, metadata)
SELECT
    'demo_user',
    (SELECT id FROM knowledge_nodes WHERE label = 'Notion' AND user_id = 'demo_user'),
    (SELECT id FROM knowledge_nodes WHERE label = 'AI Agent' AND user_id = 'demo_user'),
    'integrates_with', 0.6,
    '{"confidence": "low"}'::jsonb;


-- ============================================================
-- VERIFICATION QUERIES
-- ============================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Decentralized AI Memory Layer - Migration Complete';
    RAISE NOTICE '========================================';
    RAISE NOTICE '';
    RAISE NOTICE 'Seed Data Verification for demo_user:';
    RAISE NOTICE '  episodic_memory:   % rows', (SELECT COUNT(*) FROM episodic_memory WHERE user_id = 'demo_user');
    RAISE NOTICE '  semantic_memory:   % rows', (SELECT COUNT(*) FROM semantic_memory WHERE user_id = 'demo_user');
    RAISE NOTICE '  procedural_memory: % rows', (SELECT COUNT(*) FROM procedural_memory WHERE user_id = 'demo_user');
    RAISE NOTICE '  knowledge_nodes:   % rows', (SELECT COUNT(*) FROM knowledge_nodes WHERE user_id = 'demo_user');
    RAISE NOTICE '  knowledge_edges:   % rows', (SELECT COUNT(*) FROM knowledge_edges WHERE user_id = 'demo_user');
    RAISE NOTICE '';
    RAISE NOTICE 'Next steps:';
    RAISE NOTICE '  1. Start backend: cd backend && uvicorn app.main:app --reload';
    RAISE NOTICE '  2. Start frontend: cd frontend && streamlit run app.py';
    RAISE NOTICE '  3. Open http://localhost:8501';
    RAISE NOTICE '';
    RAISE NOTICE '========================================';
END $$;
