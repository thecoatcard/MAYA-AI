# Browser Automation Subsystem Integration

The MAYA AI Browser Automation subsystem provides containerized, permission-gated browser capabilities via the Model Context Protocol (MCP) and REST endpoints.

---

## 1. Overview & Architecture

The browser subsystem consists of five core components:

1. **Browser Runner (`backend/app/sandbox/browser_runner.py`):** Ephemeral Playwright runner with fallback simulation engine.
2. **Egress Security Filter (`backend/app/sandbox/egress_filter.py`):** Pre-navigation SSRF and DNS-rebinding guardrail.
3. **Anti-Bot Detector (`backend/app/automation/anti_bot_detector.py`):** Heuristic detector triggering human takeover on bot challenge gates.
4. **Browser MCP Server (`backend/app/mcp/servers/browser.py`):** MCP tool suite registered with `ToolPermissionRegistry`.
5. **REST API & Takeover Bridge (`backend/app/api/v1/browser.py`):** REST API for dispatching actions and coordinating human takeover.

---

## 2. MCP Tool Suite & Permission Model

All browser tools are registered with `ToolPermissionRegistry` (Decision D4):

| Tool Name | Permission Tier | Required Arguments | Description |
| :--- | :--- | :--- | :--- |
| `browser_navigate` | `ToolTier.READ` | `url` | Navigates to an egress-approved URL |
| `browser_screenshot` | `ToolTier.READ` | (none) | Captures viewport screenshot in base64 |
| `browser_get_dom` | `ToolTier.READ` | (none) | Returns current page DOM summary |
| `browser_click` | `ToolTier.APPROVAL_REQUIRED` | `selector` | Clicks interactive element |
| `browser_fill_form` | `ToolTier.APPROVAL_REQUIRED` | `selector`, `value` | Fills text into an input field |
| `browser_submit_payment` | `ToolTier.RESTRICTED` | (none) | Submits payment checkout (requires step-up) |

---

## 3. Approval Flow for Mutating Actions

Mutating actions (`browser_click`, `browser_fill_form`) require an approval token:

1. Agent creates an approval request through the Approval Center.
2. The payload hash is computed canonically according to RFC 8785:
   ```python
   payload_hash = compute_payload_hash(
       tool=tool_name,
       args=args,
       workspace_id=workspace_id,
       user_id=user_id,
       idempotency_key=session_id,
   )
   ```
3. A human reviewer approves the request, generating a signed JWT token (`jwt_signing_key`).
4. The agent passes the `approval_token` in the tool invocation context or REST request body.
5. The gateway verifies token validity, expiration (maximum 300 seconds), and payload binding, atomically consuming the token in PostgreSQL.

---

## 4. REST API Specification

### Dispatch Action
- **Method:** `POST`
- **Path:** `/api/v1/browser/action`
- **Request Body:**
  ```json
  {
    "action_type": "navigate",
    "url": "https://example.com",
    "screenshot_required": true,
    "session_id": "session-uuid"
  }
  ```
- **Responses:**
  - `200 OK`: Action executed successfully.
  - `400 Bad Request`: SSRF or egress security violation.
  - `403 Forbidden`: Mutating action attempted without valid approval token.

### Query Takeover State
- **Method:** `GET`
- **Path:** `/api/v1/browser/session/{session_id}/takeover`
- **Response:**
  ```json
  {
    "session_id": "session-uuid",
    "status": "awaiting_human_takeover",
    "current_url": "https://example.com/login",
    "challenge_detected": true,
    "challenge_type": "cloudflare_turnstile",
    "screenshot_base64": "iVBORw0KGgo...",
    "message": "Anti-bot challenge 'cloudflare_turnstile' requires manual resolution."
  }
  ```

### Resume After Takeover
- **Method:** `POST`
- **Path:** `/api/v1/browser/session/{session_id}/resume`
- **Response:**
  ```json
  {
    "session_id": "session-uuid",
    "status": "resumed",
    "current_url": "https://example.com/login",
    "message": "Browser automation session resumed following human resolution."
  }
  ```
