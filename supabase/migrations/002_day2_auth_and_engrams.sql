-- ============================================================
-- Day 2 Migration — Auth Layer + Engram Table
-- Adds: users, api_keys, engrams tables
-- Adds: app_id column to all memory tables
-- Keeps: user_id as TEXT in existing tables (no breaking change)
-- Target: Supabase-hosted PostgreSQL + pgvector extension
-- Run:    paste into Supabase SQL Editor → Run
-- ============================================================

-- ── Extensions (idempotent) ─────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ============================================================
-- USERS TABLE
-- Supports: email+password login, wallet-address login, or both
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id               UUID        NOT NULL DEFAULT uuid_generate_v4(),
    email            TEXT        UNIQUE,
    wallet_address   TEXT        UNIQUE,
    hashed_password  TEXT,
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    -- At least one identifier must be present
    CONSTRAINT user_has_identifier CHECK (
        email IS NOT NULL OR wallet_address IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_users_email          ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_wallet         ON users (wallet_address);

COMMENT ON TABLE users IS
    'Registered users. Supports email/password and wallet-address auth. '
    'Created in Day 2 of the 7-day roadmap sprint.';


-- ============================================================
-- API KEYS TABLE
-- Raw key shown exactly once at creation — only hash is stored.
-- Format: mem_<32 random url-safe bytes>
-- ============================================================

CREATE TABLE IF NOT EXISTS api_keys (
    id            UUID    NOT NULL DEFAULT uuid_generate_v4(),
    user_id       UUID    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash      TEXT    NOT NULL UNIQUE,    -- SHA-256 of raw key
    name          TEXT    NOT NULL,           -- e.g. "Telegram Bot", "VS Code"
    scopes        TEXT    NOT NULL DEFAULT 'read,write',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id  ON api_keys (user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash     ON api_keys (key_hash);

COMMENT ON TABLE api_keys IS
    'Named API keys per user. key_hash is SHA-256 of the raw key. '
    'Raw key returned exactly once at creation; never stored in plain text.';


-- ============================================================
-- ENGRAMS TABLE
-- Compressed, embedded memory units produced by EngramProcessor.
-- dense_embedding is 384-dim (all-MiniLM-L6-v2) — populated in Day 4.
-- ============================================================

CREATE TABLE IF NOT EXISTS engrams (
    id                 UUID    NOT NULL DEFAULT uuid_generate_v4(),
    user_id            UUID    NOT NULL,           -- UUID ref to users.id
    engram_id          TEXT    NOT NULL,           -- short fingerprint e.g. "a1b2c3d4e5f6"
    distilled_text     TEXT    NOT NULL,
    dense_embedding    VECTOR(384),               -- MiniLM-L6-v2 output (Day 4)
    actions            JSONB   NOT NULL DEFAULT '[]',
    objects            JSONB   NOT NULL DEFAULT '[]',
    entities           JSONB   NOT NULL DEFAULT '[]',
    negated_actions    JSONB   NOT NULL DEFAULT '[]',
    salience_scores    JSONB   NOT NULL DEFAULT '{}',
    connections        JSONB   NOT NULL DEFAULT '[]',  -- list of related engram_ids
    original_length    INTEGER,
    compressed_length  INTEGER,
    compression_ratio  FLOAT,
    source_type        TEXT,                      -- "episodic" | "api" | "rag"
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at   TIMESTAMPTZ,
    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_engrams_user_id    ON engrams (user_id);
CREATE INDEX IF NOT EXISTS idx_engrams_engram_id  ON engrams (engram_id);
CREATE INDEX IF NOT EXISTS idx_engrams_created_at ON engrams (created_at DESC);

-- HNSW index for engram similarity search (Day 4: populated after embeddings exist)
-- Uncomment once you have >100 engram rows:
-- CREATE INDEX idx_engrams_hnsw ON engrams
--     USING hnsw (dense_embedding vector_cosine_ops)
--     WITH (m = 16, ef_construction = 64);

COMMENT ON TABLE engrams IS
    'Compressed memory units produced by EngramProcessor. '
    'dense_embedding column populated in Day 4 (all-MiniLM-L6-v2, 384-dim).';


-- ============================================================
-- ADD app_id TO EXISTING MEMORY TABLES (multi-app scoping)
-- Idempotent: uses DO block to avoid "column already exists" error
-- ============================================================

DO $$
BEGIN
    -- episodic_memory
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='episodic_memory' AND column_name='app_id'
    ) THEN
        ALTER TABLE episodic_memory ADD COLUMN app_id UUID;
        CREATE INDEX idx_episodic_app_id ON episodic_memory (app_id);
        RAISE NOTICE 'Added app_id to episodic_memory';
    END IF;

    -- semantic_memory
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='semantic_memory' AND column_name='app_id'
    ) THEN
        ALTER TABLE semantic_memory ADD COLUMN app_id UUID;
        CREATE INDEX idx_semantic_app_id ON semantic_memory (app_id);
        RAISE NOTICE 'Added app_id to semantic_memory';
    END IF;

    -- knowledge_nodes
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='knowledge_nodes' AND column_name='app_id'
    ) THEN
        ALTER TABLE knowledge_nodes ADD COLUMN app_id UUID;
        CREATE INDEX idx_knowledge_nodes_app_id ON knowledge_nodes (app_id);
        RAISE NOTICE 'Added app_id to knowledge_nodes';
    END IF;

    -- knowledge_edges (for completeness)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='knowledge_edges' AND column_name='app_id'
    ) THEN
        ALTER TABLE knowledge_edges ADD COLUMN app_id UUID;
        RAISE NOTICE 'Added app_id to knowledge_edges';
    END IF;
END $$;


-- ============================================================
-- SEED DEMO USER + API KEY
-- Creates demo@memorylayer.dev if not already present.
-- Raw API key hash below is SHA-256 of: mem_DEMO_KEY_REPLACE_IN_PROD
-- Replace this with output from scripts/seed_demo.py in real usage.
-- ============================================================

DO $$
DECLARE
    v_user_id UUID;
BEGIN
    -- Insert demo user (skip if exists)
    INSERT INTO users (email, is_active)
    VALUES ('demo@memorylayer.dev', TRUE)
    ON CONFLICT (email) DO NOTHING;

    SELECT id INTO v_user_id FROM users WHERE email = 'demo@memorylayer.dev';

    -- Insert a placeholder demo API key (replace hash with real one from seed_demo.py)
    INSERT INTO api_keys (user_id, key_hash, name, scopes)
    VALUES (
        v_user_id,
        'PLACEHOLDER_RUN_seed_demo_py_to_get_real_hash',
        'Demo CLI Key',
        'read,write'
    )
    ON CONFLICT (key_hash) DO NOTHING;

    RAISE NOTICE 'Demo user ready: %', v_user_id;
    RAISE NOTICE 'Run: python scripts/seed_demo.py to generate a real API key';
END $$;


-- ============================================================
-- VERIFICATION
-- ============================================================

DO $$
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Day 2 Migration Complete';
    RAISE NOTICE '========================================';
    RAISE NOTICE 'Tables created / verified:';
    RAISE NOTICE '  users:     % rows', (SELECT COUNT(*) FROM users);
    RAISE NOTICE '  api_keys:  % rows', (SELECT COUNT(*) FROM api_keys);
    RAISE NOTICE '  engrams:   % rows', (SELECT COUNT(*) FROM engrams);
    RAISE NOTICE '';
    RAISE NOTICE 'app_id column added to:';
    RAISE NOTICE '  episodic_memory, semantic_memory,';
    RAISE NOTICE '  knowledge_nodes, knowledge_edges';
    RAISE NOTICE '';
    RAISE NOTICE 'Next step:';
    RAISE NOTICE '  Run: python scripts/seed_demo.py';
    RAISE NOTICE '  (generates a real API key for demo_user)';
    RAISE NOTICE '========================================';
END $$;
