# Phase 13 — Browser and Computer Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement, integrate, test, and certify Phase 13 (Browser and Computer Automation) to production standards, including the containerized/sandboxed Playwright runner, pre-request Anti-SSRF & DNS-rebinding egress security filter, CAPTCHA/bot challenge detector with mandatory human-in-the-loop takeover (never automated bypass), 4-tier permission gating (READ navigation, APPROVAL_REQUIRED clicks/forms, RESTRICTED payment), and audit trail capture.

**Architecture:** A security-hardened browser automation subsystem:
1. `BrowserRunner` (`planned: backend/app/sandbox/browser_runner.py`): executes headless browser automation with ephemeral contexts, screenshot capture, DOM serialization, and action timeouts;
2. `EgressSecurityFilter` (`planned: backend/app/sandbox/egress_filter.py`): validates destination URLs against private IP subnets (RFC 1918), loopback, and link-local cloud metadata (`169.254.169.254`), protecting against SSRF and DNS-rebinding attacks;
3. `AntiBotDetector` (`planned: backend/app/automation/anti_bot_detector.py`): detects Cloudflare Turnstile, reCAPTCHA, hCaptcha, and Altcha signatures. Immediately halts automation and yields to human takeover (`awaiting_approval`, `human_browser_takeover`);
4. Browser MCP Server / Tool Suite (`planned: backend/app/mcp/servers/browser.py`): registers `browser_navigate`, `browser_screenshot`, `browser_get_dom` (READ), `browser_click`, `browser_fill_form` (APPROVAL_REQUIRED), and `browser_submit_payment` (RESTRICTED);
5. Browser API & Streaming (`planned: backend/app/api/v1/browser.py`): exposes endpoints for status, screenshots, and human handoff takeover.

**Tech Stack:** Python 3.12, FastAPI, Playwright (and mock browser runner for headless/unit testing), Pydantic 2, SQLAlchemy 2, Redis 7.4, Pytest.

**Spec:** `MAYA_AI_FINAL_PLAN.md` (§11.4, §15, §19, §24, Decisions D4, D5, D14), `docs/implementation/IMPLEMENTATION_PHASES/PHASE_13_BROWSER_AUTOMATION.md`.

## Global Constraints

- **Decision D4 (Permission Classification):** Read actions (`navigate`, `screenshot`, `get_dom`) are `READ`. Form mutations and clicks are `APPROVAL_REQUIRED`. Billing/credentials are `RESTRICTED`.
- **Decision D5 (Payload Binding & Approvals):** Mutating browser actions require valid, single-use, RFC 8785 payload-bound tokens consumed atomically via PostgreSQL CAS.
- **Decision D14 (Sandbox & Socket Isolation):** Browser runner executes in an isolated environment; host Docker socket is never accessible.
- **Anti-SSRF & DNS-Rebinding Protection:** Destination URLs resolving to RFC 1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.0/8`), or link-local cloud metadata (`169.254.169.254`) must be blocked with `SecurityEgressViolationError`.
- **Anti-Bot & CAPTCHA Human Handoff:** Programmatic solving or bypassing of CAPTCHAs is STRICTLY PROHIBITED. When detected, execution immediately pauses and triggers human takeover.
- **Zero Regressions:** All existing backend tests, frontend Vitest tests, and monorepo governance checks must pass.

---

### Task 1: Anti-SSRF & DNS-Rebinding Egress Security Filter

**Files:**
- Create: `planned: backend/app/sandbox/egress_filter.py`
- Test: `planned: backend/tests/unit/test_egress_security.py`

**Interfaces:**
- `EgressSecurityFilter`: `validate_url(url: str) -> str` (resolves host, checks IP against blocked ranges, raises `SecurityEgressViolationError`), `is_ip_allowed(ip: str) -> bool`.
- Blocked ranges: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, `0.0.0.0/8`, `::1`, `fc00::/7`, `fe80::/10`.

- [ ] **Step 1: Write unit tests verifying rejection of localhost, private IPs, AWS metadata `169.254.169.254`, decimal IP obfuscation, and acceptance of valid public domains**
- [ ] **Step 2: Implement `planned: backend/app/sandbox/egress_filter.py`**
- [ ] **Step 3: Run unit tests to verify 100% pass rate**

---

### Task 2: CAPTCHA & Bot Challenge Detector

**Files:**
- Create: `planned: backend/app/automation/anti_bot_detector.py`
- Test: `planned: backend/tests/unit/test_captcha_detection.py`

**Interfaces:**
- `AntiBotDetector`: `detect_challenge(dom_content: str, headers: dict = {}) -> ChallengeDetectionResult` (identifies `cloudflare_turnstile`, `recaptcha_v2`, `recaptcha_v3`, `hcaptcha`, `altcha`, `datadome`).
- Raises: `CaptchaDetectedException` triggering pause and human takeover without any bypass attempt.

- [ ] **Step 1: Write unit tests asserting detection of Turnstile, reCAPTCHA, and hCaptcha DOM elements and headers**
- [ ] **Step 2: Implement `planned: backend/app/automation/anti_bot_detector.py`**
- [ ] **Step 3: Run unit tests to verify 100% pass rate**

---

### Task 3: Browser Sandbox Runner & Action Schemas

**Files:**
- Create: `planned: backend/app/sandbox/browser_schemas.py`
- Create: `planned: backend/app/sandbox/browser_runner.py`
- Test: `planned: backend/tests/unit/test_browser_runner.py`

**Interfaces:**
- `BrowserActionRequest`: `action_type: Literal["navigate", "click", "fill", "screenshot", "get_dom"]`, `url`, `selector`, `input_text`.
- `BrowserActionResult`: `success`, `current_url`, `dom_summary`, `screenshot_base64`, `bot_detected`, `bot_type`.
- `BrowserRunner`: manages browser lifecycle, executes actions through `EgressSecurityFilter` and `AntiBotDetector`, caps timeouts (default 30s), enforces ephemeral context cleanup.

- [ ] **Step 1: Write unit tests verifying action execution, SSRF blocking, CAPTCHA interception, and timeout enforcement**
- [ ] **Step 2: Implement schemas in `browser_schemas.py` and runner in `browser_runner.py`**
- [ ] **Step 3: Run unit tests to verify 100% pass rate**

---

### Task 4: Browser Tool Suite & 4-Tier Permission Gate

**Files:**
- Create: `planned: backend/app/mcp/servers/browser.py`
- Test: `planned: backend/tests/integration/test_browser_permissions.py`

**Interfaces:**
- Exposes tools: `browser_navigate`, `browser_screenshot`, `browser_get_dom` (READ), `browser_click`, `browser_fill_form` (APPROVAL_REQUIRED), `browser_submit_payment` (RESTRICTED).
- Enforces: single-use approval token verification and atomic CAS consumption for mutating actions.

- [ ] **Step 1: Write integration tests verifying read operations execute autonomously while click/form actions require approval tokens**
- [ ] **Step 2: Implement `planned: backend/app/mcp/servers/browser.py` registering tools with `ToolPermissionRegistry`**
- [ ] **Step 3: Run integration tests to verify they pass**

---

### Task 5: Browser REST API Endpoints & Human Handoff Bridge

**Files:**
- Create: `planned: backend/app/api/v1/browser.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `planned: backend/tests/integration/test_browser_api.py`

**Interfaces:**
- `POST /api/v1/browser/action`: executes browser action under tenant RLS.
- `GET /api/v1/browser/session/{session_id}/takeover`: provides takeover status and screenshot stream for human resolution.
- `POST /api/v1/browser/session/{session_id}/resume`: human signals challenge completion, resuming automation.

- [ ] **Step 1: Write API integration tests testing action dispatch, approval gating, and human takeover flow**
- [ ] **Step 2: Implement `planned: backend/app/api/v1/browser.py` and register in `router.py`**
- [ ] **Step 3: Run integration tests to verify they pass**

---

### Task 6: Documentation & Governance

**Files:**
- Create: `planned: docs/security/BROWSER_SANDBOX.md`
- Create: `planned: docs/integrations/BROWSER_AUTOMATION.md`
- Modify: `docs/implementation/IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Author `planned: docs/security/BROWSER_SANDBOX.md` documenting anti-SSRF filtering, anti-bot policy, and human takeover**
- [ ] **Step 2: Author `planned: docs/integrations/BROWSER_AUTOMATION.md` documenting tool schemas and permission mapping**
- [ ] **Step 3: Update `docs/implementation/IMPLEMENTATION_STATUS.md` and `CHANGELOG.md`**
- [ ] **Step 4: Run `npm run docs:check`**

---

### Task 7: Full Regression & Integration Verification

- [ ] **Step 1: Run `ruff check backend` and `ruff format --check backend`**
- [ ] **Step 2: Run `mypy .`**
- [ ] **Step 3: Run full backend pytest suite**
- [ ] **Step 4: Run frontend Vitest suite**
- [ ] **Step 5: Run monorepo fast validation (`npm run validate:fast`)**
