# Memory Hygiene and Security Policy

The MAYA AI memory system implements strict data hygiene, deterministic scrubbing, and lifecycle compliance to prevent sensitive personal and authentication data from persisting in long-term vector stores or LLM contexts.

---

## 1. Pre-Ingestion Deterministic Scrubbing

All documents and memories entering the system pass through `PiiScrubber` (`app.memory.scrubber`) before text chunking, embedding generation, or database insertion.

### Redaction Rules

| Data Category | Detection Mechanism | Validation | Redaction Replacement |
| :--- | :--- | :--- | :--- |
| **Credit Card Numbers** | Regex `\b(?:\d[ -]*?){13,19}\b` | Luhn Checksum Algorithm (`verify_luhn`) | `[REDACTED_CREDIT_CARD]` |
| **PEM Private Keys** | Regex `-----BEGIN ... PRIVATE KEY-----` | Multiline block detection | `[REDACTED_SECRET]` |
| **JSON Web Tokens (JWT)** | Regex `\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b` | Standard base64-encoded JWT structure | `[REDACTED_SECRET]` |
| **Bearer Tokens** | Regex `\bBearer\s+[A-Za-z0-9_\-\.~+/]+=*` | Token header pattern | `Bearer [REDACTED_SECRET]` |
| **AWS Access Keys** | Regex `\b(?:AKIA\|ABIA\|ACCA\|ASIA)[0-9A-Z]{16}\b` | Key prefix and length | `[REDACTED_SECRET]` |
| **GitHub Tokens** | Regex `\bgh[pousr]_[A-Za-z0-9_]{36,255}\b` | Token prefix | `[REDACTED_SECRET]` |
| **Social Security Numbers** | Regex `\b\d{3}-\d{2}-\d{4}\b` | US SSN format | `[REDACTED_SSN]` |

### Luhn Algorithm Validation
To avoid redacting normal serial numbers, order IDs, or telephone numbers, potential credit card strings are strictly validated using the Luhn algorithm ($mod\ 10$). Strings failing the Luhn checksum are preserved verbatim.

---

## 2. GDPR Article 17 Compliance ("Right to Erasure")

Under GDPR Article 17 and CCPA:
- Users have the right to request complete erasure of personal data.
- The `DELETE /api/v1/memory/purge` endpoint provides atomic erasure across:
  1. **PostgreSQL 17:** Deletes all rows from `memory_items` matching `workspace_id` and `user_id`.
  2. **Redis 7.4:** Finds all active session IDs associated with the user and purges working memory keys (`memory:working:{workspace_id}:{session_id}`).
- Purging is irreversible and guarantees zero residual data in database tables or caches.

---

## 3. Stale Memory Archival Lifecycle

Non-permanent memories (`permanent=False`) that remain unaccessed for over 180 days are marked as expired by setting `expires_at = now()`. Expired items are excluded from hybrid retrieval and listing queries. Permanent items (such as user preferences and core project directives) are exempt from expiration.
