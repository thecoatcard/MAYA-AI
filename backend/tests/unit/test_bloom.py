import pytest

from app.auth.bloom import BloomFilter


def test_bloom_filter_empty() -> None:
    bf = BloomFilter(size=1000, hash_count=5)
    assert bf.items_added == 0
    assert bf.set_bits_count() == 0
    assert bf.contains("alice") is False
    assert bf.contains("alice@example.com") is False
    assert bf.false_positive_probability() == 0.0


def test_bloom_filter_add_and_contains() -> None:
    bf = BloomFilter(size=10_000, hash_count=7)
    bf.add("alice")
    bf.add("alice@example.com")

    assert bf.items_added == 2
    assert bf.set_bits_count() > 0
    # Case insensitivity and whitespace stripping
    assert bf.contains("alice") is True
    assert bf.contains("ALICE") is True
    assert bf.contains("  alice  ") is True
    assert bf.contains("alice@example.com") is True
    assert bf.contains("ALICE@EXAMPLE.COM") is True

    # Non-existent items
    assert bf.contains("bob") is False
    assert bf.contains("charlie@example.com") is False


def test_bloom_filter_zero_false_negatives() -> None:
    bf = BloomFilter(size=50_000, hash_count=7)
    items = [f"user_{i}@example.com" for i in range(500)]
    bf.add_many(items)

    for item in items:
        assert bf.contains(item) is True, f"False negative occurred for {item}"


def test_bloom_filter_clear() -> None:
    bf = BloomFilter(size=1000, hash_count=5)
    bf.add("alice")
    assert bf.contains("alice") is True
    bf.clear()
    assert bf.items_added == 0
    assert bf.set_bits_count() == 0
    assert bf.contains("alice") is False


def test_bloom_filter_invalid_parameters() -> None:
    with pytest.raises(ValueError, match="size must be positive"):
        BloomFilter(size=0)
    with pytest.raises(ValueError, match="hash_count must be positive"):
        BloomFilter(size=100, hash_count=0)
