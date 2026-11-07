# Memory and Knowledge API Specification

- **Base Path:** `/api/v1/memory`
- **Authentication:** Bearer JWT required on all endpoints
- **Authorization:** PostgreSQL Row-Level Security (RLS) workspace isolation and user-scoped data access

---

## Endpoints

### 1. Ingest Memory Item
- **Endpoint:** `POST /api/v1/memory/items`
- **Status Code:** `201 Created`
- **Description:** Pre-scrubs PII/secrets, segments text into semantic chunks, generates 768-dimensional embeddings via ModelGateway (`gemini-embedding-2`), and stores chunks in PostgreSQL.
- **Request Body:**
  ```json
  {
    "category": "knowledge",
    "content": "Deployment guide for AWS ECS clusters and worker tasks.",
    "metadata": { "guide_id": "ecs-01", "version": "1.2" },
    "permanent": false
  }
  ```
- **Response:** `list[MemoryItemResponse]`
  ```json
  [
    {
      "id": "e4b2d3c1-1111-2222-3333-444455556666",
      "workspace_id": "a1b2c3d4-...",
      "user_id": "b2c3d4e5-...",
      "category": "knowledge",
      "content": "Deployment guide for AWS ECS clusters and worker tasks.",
      "metadata": {
        "guide_id": "ecs-01",
        "version": "1.2",
        "chunk_index": 0,
        "total_chunks": 1,
        "redactions_count": 0,
        "detected_types": []
      },
      "permanent": false,
      "access_count": 1,
      "last_accessed_at": "2026-11-07T12:00:00Z",
      "expires_at": null,
      "created_at": "2026-11-07T12:00:00Z"
    }
  ]
  ```

---

### 2. List Memory Items
- **Endpoint:** `GET /api/v1/memory/items`
- **Query Parameters:**
  - `category` (optional, string): Filter by category (`preference`, `project`, `episodic`, `knowledge`).
  - `limit` (optional, integer, default 50, max 100): Maximum items to return.
  - `offset` (optional, integer, default 0): Pagination offset.
- **Status Code:** `200 OK`
- **Response:** `list[MemoryItemResponse]`

---

### 3. Hybrid Search
- **Endpoint:** `POST /api/v1/memory/search`
- **Status Code:** `200 OK`
- **Description:** Hybrid dense (pgvector) and sparse (`tsvector`) search enforcing Defect #16 raw threshold ($S_{sim} \ge 0.78$) and time-decay ranking ($e^{-0.005 \cdot \Delta\text{days}}$). Automatically increments `access_count` and updates `last_accessed_at`.
- **Request Body:**
  ```json
  {
    "query": "How are ECS tasks deployed and scaled?",
    "categories": ["knowledge", "project"],
    "limit": 5,
    "raw_threshold": 0.78
  }
  ```
- **Response:**
  ```json
  {
    "query": "How are ECS tasks deployed and scaled?",
    "results": [
      {
        "id": "e4b2d3c1-1111-2222-3333-444455556666",
        "category": "knowledge",
        "content": "Deployment guide for AWS ECS clusters and worker tasks.",
        "metadata": { "guide_id": "ecs-01" },
        "raw_similarity": 0.895,
        "effective_score": 0.841,
        "source_id": "ecs-01",
        "created_at": "2026-11-07T12:00:00Z"
      }
    ],
    "total_results": 1
  }
  ```

---

### 4. Delete Single Memory Item
- **Endpoint:** `DELETE /api/v1/memory/items/{item_id}`
- **Status Code:** `200 OK`
- **Response:**
  ```json
  {
    "deleted": true,
    "id": "e4b2d3c1-1111-2222-3333-444455556666"
  }
  ```
- **Error Codes:**
  - `404 Not Found`: If item does not exist or belongs to another user/tenant.

---

### 5. Purge User Memories (GDPR Article 17)
- **Endpoint:** `DELETE /api/v1/memory/purge`
- **Status Code:** `200 OK`
- **Description:** Completely removes user records from `memory_items` and purges user working memory keys from Redis.
- **Request Body:**
  ```json
  {
    "scope": "all"
  }
  ```
- **Response:**
  ```json
  {
    "status": "purged",
    "scope": "all",
    "records_deleted": 12
  }
  ```
