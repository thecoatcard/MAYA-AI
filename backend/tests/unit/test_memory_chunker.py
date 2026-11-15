"""Unit tests for SemanticChunker."""

from app.memory.ingestion import SemanticChunker


def test_chunk_empty_text() -> None:
    chunker = SemanticChunker()
    assert chunker.chunk_text("") == []
    assert chunker.chunk_text("   \n\n  ") == []


def test_chunk_short_text() -> None:
    chunker = SemanticChunker()
    short = "MAYA AI is a voice-first multi-agent system."
    chunks = chunker.chunk_text(short, max_tokens=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0] == short


def test_chunk_multi_paragraphs() -> None:
    chunker = SemanticChunker()
    p1 = "Paragraph 1: Architecture overview of the distributed microservices."
    p2 = "Paragraph 2: PostgreSQL 17 provides pgvector and row-level security."
    p3 = "Paragraph 3: Redis provides 24-hour fast working memory session state."
    text = f"{p1}\n\n{p2}\n\n{p3}"

    # Set small max_tokens so each paragraph forms a chunk
    chunks = chunker.chunk_text(text, max_tokens=20, overlap=5)
    assert len(chunks) >= 3
    assert any("Paragraph 1" in c for c in chunks)
    assert any("Paragraph 2" in c for c in chunks)
    assert any("Paragraph 3" in c for c in chunks)


def test_chunk_sentences_within_paragraph() -> None:
    chunker = SemanticChunker()
    sentences = [
        "First sentence sets the foundational premise.",
        "Second sentence elaborates on operational mechanics.",
        "Third sentence details validation and testing protocols.",
        "Fourth sentence summarizes deployment guarantees.",
    ]
    text = " ".join(sentences)

    chunks = chunker.chunk_text(text, max_tokens=15, overlap=4)
    assert len(chunks) > 1
    # Check that sentences aren't chopped in the middle of words
    for c in chunks:
        assert not c.startswith(" ")
        assert not c.endswith(" ")


def test_chunk_overlap_continuity() -> None:
    chunker = SemanticChunker()
    # Create long text of 10 sentences
    sentences = [f"Sentence number {i} provides detailed narrative context." for i in range(15)]
    text = " ".join(sentences)

    chunks = chunker.chunk_text(text, max_tokens=30, overlap=10)
    assert len(chunks) >= 2
    # Ensure there is textual overlap between consecutive chunks
    for i in range(len(chunks) - 1):
        words_c1 = set(chunks[i].split())
        words_c2 = set(chunks[i + 1].split())
        assert len(words_c1.intersection(words_c2)) > 0
