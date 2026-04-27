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
    user_id         UUID        NOT NULL,
    app_id          UUID        DEFAULT NULL,
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
CREATE INDEX IF NOT EXISTS idx_episodic_app_id
    ON episodic_memory (app_id);
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
    user_id          UUID        NOT NULL,
    app_id           UUID        DEFAULT NULL,
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

CREATE INDEX IF NOT EXISTS idx_semantic_app_id
    ON semantic_memory (app_id);

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
    user_id           UUID        NOT NULL UNIQUE,
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
    user_id           UUID        NOT NULL,
    app_id            UUID        DEFAULT NULL,
    label             TEXT        NOT NULL,
    type              TEXT        NOT NULL,
    properties        JSONB       DEFAULT '{}',
    store_associative BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_node_user_id
    ON knowledge_nodes (user_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_node_app_id
    ON knowledge_nodes (app_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_node_label
    ON knowledge_nodes USING gin (label gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_knowledge_node_type
    ON knowledge_nodes (type);


CREATE TABLE IF NOT EXISTS knowledge_edges (
    id                UUID        NOT NULL DEFAULT uuid_generate_v4(),
    user_id           UUID        NOT NULL,
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
    COALESCE(ep.user_id, kn.user_id, ke.user_id) AS user_id,
    (SELECT COUNT(*) FROM episodic_memory WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id)) AS episodic_count,
    (SELECT COUNT(*) FROM semantic_memory WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id)) AS semantic_count,
    (SELECT COUNT(*) FROM procedural_memory WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id)) AS procedural_count,
    (SELECT COUNT(*) FROM knowledge_nodes WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id)) AS graph_node_count,
    (SELECT COUNT(*) FROM knowledge_edges WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id)) AS graph_edge_count,
    (
        (SELECT COUNT(*) FROM episodic_memory WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id)) +
        (SELECT COUNT(*) FROM semantic_memory WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id)) +
        (SELECT COUNT(*) FROM procedural_memory WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id)) +
        (SELECT COUNT(*) FROM knowledge_nodes WHERE user_id = COALESCE(ep.user_id, kn.user_id, ke.user_id))
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
-- END OF INITIAL SCHEMA
-- ============================================================