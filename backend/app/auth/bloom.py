"""Bloom filter for fast negative lookups of usernames and email addresses.

A Bloom filter is a space-efficient probabilistic data structure. It guarantees:
- Zero false negatives: if `contains(item)` is False, the element is definitely not present.
- Possible false positives: if `contains(item)` is True, the element might be present, so an
  authoritative database check must confirm existence.

Double hashing (Kirsch-Mitzenmacher technique) over SHA-256 provides uniformly distributed
hash indices without external binary dependencies.
"""

import hashlib
import math
from collections.abc import Iterable, Iterator


class BloomFilter:
    """In-memory Bloom filter with double hashing over SHA-256."""

    def __init__(self, size: int = 100_000, hash_count: int = 7) -> None:
        if size <= 0:
            raise ValueError("size must be positive")
        if hash_count <= 0:
            raise ValueError("hash_count must be positive")
        self._size = size
        self._hash_count = hash_count
        self._bits = bytearray((size + 7) // 8)
        self._items_added = 0

    @property
    def size(self) -> int:
        return self._size

    @property
    def hash_count(self) -> int:
        return self._hash_count

    @property
    def items_added(self) -> int:
        return self._items_added

    def _normalize(self, item: str) -> str:
        return item.strip().lower()

    def _hashes(self, item: str) -> Iterator[int]:
        normalized = self._normalize(item)
        digest = hashlib.sha256(normalized.encode("utf-8")).digest()
        h1 = int.from_bytes(digest[:8], byteorder="big", signed=False)
        h2 = int.from_bytes(digest[8:16], byteorder="big", signed=False)
        # Avoid h2 == 0 to guarantee hash independence across iterations
        if h2 == 0:
            h2 = 1
        for i in range(self._hash_count):
            yield (h1 + i * h2) % self._size

    def add(self, item: str) -> None:
        """Add an identifier (username or email) to the filter."""
        for index in self._hashes(item):
            self._bits[index // 8] |= 1 << (index % 8)
        self._items_added += 1

    def add_many(self, items: Iterable[str]) -> None:
        """Add multiple identifiers to the filter."""
        for item in items:
            self.add(item)

    def contains(self, item: str) -> bool:
        """Test if the identifier might exist.

        Returns False if definitely NOT present; returns True if it might exist.
        """
        for index in self._hashes(item):
            if not (self._bits[index // 8] & (1 << (index % 8))):
                return False
        return True

    def clear(self) -> None:
        """Reset all bits to zero."""
        for i in range(len(self._bits)):
            self._bits[i] = 0
        self._items_added = 0

    def set_bits_count(self) -> int:
        """Return the number of bits currently set to 1."""
        return sum(bin(byte).count("1") for byte in self._bits)

    def false_positive_probability(self) -> float:
        """Calculate the theoretical false positive probability given current items added."""
        if self._items_added == 0:
            return 0.0
        # p ≈ (1 - e^(-k * n / m))^k
        exponent = -1.0 * self._hash_count * self._items_added / self._size
        return float((1.0 - math.exp(exponent)) ** self._hash_count)
