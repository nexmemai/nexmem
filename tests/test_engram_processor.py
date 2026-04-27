"""Tests for Engram Processor."""

import pytest
from app.services.engram_processor import EngramProcessor, decay_score
from datetime import datetime, timedelta, timezone


@pytest.fixture
def processor():
    return EngramProcessor()


@pytest.mark.asyncio
async def test_process_async_returns_valid_engram(processor):
    """Test that process_async returns a valid engram structure."""
    text = "User asked about FastAPI performance and prefers Python for backend development."
    user_id = "test_user_123"

    engram = await processor.process_async(text, user_id)

    assert "engram_id" in engram
    assert len(engram["engram_id"]) == 12
    assert "distilled_text" in engram
    assert "dense_embedding" in engram
    assert len(engram["dense_embedding"]) == 384
    assert "entities" in engram
    assert "actions" in engram
    assert "objects" in engram
    assert "negated_actions" in engram
    assert "salience_scores" in engram
    assert "compression_ratio" in engram
    assert engram["compression_ratio"] >= 0


@pytest.mark.asyncio
async def test_negation_detection(processor):
    """Test that negation is properly detected."""
    text = "User does not prefer verbose comments in code."

    engram = await processor.process_async(text, "test_user")

    negated = engram["negated_actions"]
    assert len(negated) > 0
    assert any("NOT_" in action for action in negated)


@pytest.mark.asyncio
async def test_entities_extraction(processor):
    """Test that named entities are extracted."""
    text = "Microsoft and Google are competing in AI space."

    engram = await processor.process_async(text, "test_user")

    entities = engram["entities"]
    assert len(entities) > 0


@pytest.mark.asyncio
async def test_compression_ratio(processor):
    """Test that compression ratio is calculated."""
    long_text = "This is a test. " * 50

    engram = await processor.process_async(long_text, "test_user")

    assert "compression_ratio" in engram
    assert "original_length" in engram
    assert "compressed_length" in engram
    assert engram["original_length"] > engram["compressed_length"]


@pytest.mark.asyncio
async def test_context_accumulation(processor):
    """Test that context is accumulated across calls."""
    text1 = "User works with FastAPI."
    text2 = "User prefers FastAPI over Flask."

    await processor.process_async(text1, "accum_test")
    engram2 = await processor.process_async(text2, "accum_test")

    context = processor.get_compressed_context("accum_test", "accum_test")
    assert "fastapi" in context.lower()


@pytest.mark.asyncio
async def test_salience_scoring(processor):
    """Test that salience scores are assigned."""
    text = "The AI project with budget of $50000 is important."

    engram = await processor.process_async(text, "test_user")

    scores = engram["salience_scores"]
    assert isinstance(scores, dict)

    high_score = max(scores.values()) if scores else 0
    assert high_score >= 2.0


@pytest.mark.asyncio
async def test_chunking_long_text(processor):
    """Test that long text is properly chunked."""
    long_text = "word " * 300

    engram = await processor.process_async(long_text, "test_user")

    assert "engram_id" in engram
    assert engram["original_length"] >= 300


def test_decay_score():
    """Test temporal decay score calculation."""
    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=1)
    old = now - timedelta(days=30)

    recent_score = decay_score(recent)
    old_score = decay_score(old)

    assert recent_score > old_score
    assert 0.0 < recent_score <= 1.0
    assert 0.0 < old_score <= 1.0


@pytest.mark.asyncio
async def test_graph_summary(processor):
    """Test graph summary generation."""
    await processor.process_async("FastAPI uses Python.", "graph_test")

    summary = processor.get_graph_summary("graph_test")

    assert "nodes" in summary
    assert "edges" in summary
    assert "density" in summary
    assert summary["nodes"] >= 0