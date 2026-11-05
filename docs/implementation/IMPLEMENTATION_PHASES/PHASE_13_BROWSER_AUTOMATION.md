# Phase 13 — Browser and Computer Automation

## A. Phase Overview

- **Phase Number:** Phase 13
- **Phase Name:** Browser and Computer Automation
- **Objective:** Establish the sandboxed browser automation subsystem using `@playwright/mcp` within an isolated container, enforce strict network egress controls with DNS-rebinding protection, inject credentials securely from the vault, implement human-in-the-loop takeover for CAPTCHA/bot challenges, and enforce permission tiers for web interactions.
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §11.4 and §24, unconstrained browser automation presents critical security risks: SSRF attacks targeting cloud metadata (`169.254.169.254`), credential exfiltration through malicious JavaScript, and account lockouts from bot-detection gates. A hardened browser sandbox with egress filtering and mandatory human takeover for anti-bot challenges prevents security compromises.
- **Expected Outcome:** A containerized Playwright automation runner; an egress filtering proxy resolving domain names to block private and cloud metadata IPs; an automated pause-and-handoff protocol for CAPTCHAs; vault-injected credentials; screenshot and DOM inspection tools; and permission gating (navigate = READ, form submission = APPROVAL_REQUIRED, financial/destructive actions = RESTRICTED).
- **Priority:** Medium (Post-MVP Architectural Foundation).
- **Estimated Complexity:** High
- **Estimated Duration:** 12 business days.

---

## B. Prerequisites

- **Required Completed Phases:** Phase 07 (Security, Permissions, and Human Approval) and Phase 08 (MCP Gateway and Integrations).
- **Required Infrastructure:** Dedicated Docker container running Playwright and Chromium dependencies; Squid/Envoy egress proxy; Redis 7.4.
- **Required Credentials or External Services:** Target web service credentials stored in the `SecretsVault`.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §11.4 (Browser sandbox, anti-DNS rebinding, CAPTCHA human handoff) and §24.
- **Required Documentation:** [`governance/SECURITY_POLICY.md`](../../../governance/SECURITY_POLICY.md).

---

## C. Scope

### Included Features

- Isolated Browser Container (`docker/browser/`):
  - Non-root container running headless Chromium and `@playwright/mcp`.
  - Ephemeral browser contexts destroyed upon subtask completion.
- Network Egress Security Proxy:
  - Intercept all outbound HTTP/HTTPS requests.
  - Resolve DNS at the proxy level; verify resolved IP addresses before establishing TCP connections.
  - Block RFC1918 private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.1`), and link-local cloud metadata endpoints (`169.254.169.254`).
- Credential Injection:
  - Credentials pulled from `SecretsVault` and injected directly into form fields by the runner; model never sees raw passwords in its context.
- CAPTCHA and Bot Detection Policy:
  - Programmatic detection of CAPTCHAs (reCAPTCHA, hCaptcha, Cloudflare Turnstile, Altcha).
  - Explicit rule: Automation halts immediately and enters `awaiting_human_takeover` state; model never attempts to defeat or bypass challenges.
  - Interactive VNC or remote browser stream provided to the user to complete verification.
- Permission Tiers for Browser Actions:
  - Page navigation and inspection (`navigate`, `screenshot`, `get_dom`): 🟢 READ.
  - Form submission and button clicks: 🟡 APPROVAL_REQUIRED.
  - Financial checkout, billing changes, or credential modification: 🔴 RESTRICTED (requires step-up).
- Audit Logging:
  - Full screenshot capture, URL visited, and form inputs recorded in `audit_log`.

### Excluded Features

- Autonomous financial trading or unreviewed shopping cart checkout.
- Automated CAPTCHA solving algorithms (strictly prohibited by repository policy).
- Universal OS-level desktop GUI control outside the browser (deferred to post-MVP).

### Deferred Features

- Multi-tab browser session orchestration across distributed worker pools.

### Dependencies on Future Phases

- Phase 14 (Frontend Experience) embeds the live VNC/remote browser view for human takeover when challenges are detected.

---

## D. Implementation Breakdown

### Task P13-T001: Isolated Browser Container and Playwright Runner

- **Task ID:** P13-T001
- **Task Name:** Build Containerized Playwright Automation Sandbox
- **Detailed Description:** Create docker/browser/Dockerfile (planned artifact) packaging Chromium, Node.js, and `@playwright/mcp`. Configure non-root user `browser_runner`. Implement backend/app/sandbox/browser_runner.py (planned artifact) managing the container lifecycle, opening incognito browser contexts, setting timeouts (default 30s per action), and capturing full-page screenshots.
- **Expected Files:** docker/browser/Dockerfile (planned artifact), backend/app/sandbox/browser_runner.py (planned artifact), backend/app/sandbox/browser_schemas.py (planned artifact).
- **Dependencies:** Phase 08.
- **Assigned Agent Role:** Container & Automation Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_browser_sandbox.py`.
- **Documentation Updates:** docs/security/BROWSER_SANDBOX.md (planned artifact).
- **Acceptance Criteria:** Headless browser launches in isolated container; cookies and cache clear upon context destruction; non-root user enforced.

### Task P13-T002: Anti-SSRF Egress Proxy with DNS-Rebinding Protection

- **Task ID:** P13-T002
- **Task Name:** Configure Egress Proxy Blocking Private and Metadata IPs
- **Detailed Description:** Configure egress filtering proxy in `docker/browser/egress_proxy/`. Resolve DNS queries on the proxy; inspect resolved IPv4 and IPv6 addresses; drop connections targeting `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.169.254`, and equivalent IPv6 ranges. Block DNS-rebinding attacks where a public domain resolves to a private IP on subsequent requests.
- **Expected Files:** docker/browser/egress_proxy/proxy.conf (planned artifact), backend/tests/integration/test_egress_security.py (planned artifact).
- **Dependencies:** P13-T001.
- **Assigned Agent Role:** Network Security Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_egress_security.py` (attempt connection to `http://169.254.169.254` and DNS-rebinding domains; assert immediate block).
- **Documentation Updates:** docs/security/BROWSER_SANDBOX.md (planned artifact).
- **Acceptance Criteria:** Egress proxy intercepts 100% of browser traffic; requests to private IPs or cloud metadata endpoints are blocked with HTTP 403.

### Task P13-T003: CAPTCHA Detection and Human Handoff Bridge

- **Task ID:** P13-T003
- **Task Name:** Implement CAPTCHA Detection and Interactive Takeover
- **Detailed Description:** Implement backend/app/automation/anti_bot_detector.py (planned artifact). Scan page DOM and network requests for signatures of reCAPTCHA, hCaptcha, Cloudflare Turnstile, and Altcha. When detected:
  - Pause automation immediately without attempting bypass.
  - Set subtask status to `awaiting_approval` with action type `human_browser_takeover`.
  - Expose a WebSocket proxy to the user UI streaming browser frames and mouse/keyboard events.
  - When user completes challenge, resume automated execution upon human confirmation.
- **Expected Files:** backend/app/automation/anti_bot_detector.py (planned artifact), backend/app/api/v1/browser_stream.py (planned artifact), backend/tests/unit/test_captcha_detection.py (planned artifact).
- **Dependencies:** P13-T001, Phase 07.
- **Assigned Agent Role:** AI Safety & Frontend Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_captcha_detection.py`.
- **Documentation Updates:** docs/security/BROWSER_SANDBOX.md (planned artifact).
- **Acceptance Criteria:** CAPTCHAs are detected programmatically; runner pauses cleanly without attempting automated clicks; human takeover stream enables manual resolution.

### Task P13-T004: Browser Tool Suite and Permission Gate Integration

- **Task ID:** P13-T004
- **Task Name:** Integrate Browser Tools into Permission Gate
- **Detailed Description:** Implement backend/app/mcp/servers/browser.py (planned artifact) registering tools:
  - `browser_navigate(url: str)`: 🟢 READ
  - `browser_screenshot() -> str`: 🟢 READ
  - `browser_click(selector: str)`: 🟡 APPROVAL_REQUIRED
  - `browser_fill_form(selector: str, field_data: dict)`: 🟡 APPROVAL_REQUIRED
  - `browser_submit_payment()`: 🔴 RESTRICTED (requires step-up)
  - Connect all mutating calls through `consume_approval_token()` before dispatch.
- **Expected Files:** backend/app/mcp/servers/browser.py (planned artifact), backend/tests/integration/test_browser_permissions.py (planned artifact).
- **Dependencies:** P13-T001, Phase 07.
- **Assigned Agent Role:** Security & Tooling Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_browser_permissions.py`.
- **Documentation Updates:** docs/integrations/BROWSER_AUTOMATION.md (planned artifact).
- **Acceptance Criteria:** Navigation succeeds autonomously; button clicks and form submissions pause for human approval; unapproved actions are refused.

---

## E. File and Folder Changes

- **Files to Create:**
  - docker/browser/Dockerfile (planned artifact)
  - docker/browser/egress_proxy/proxy.conf (planned artifact)
  - backend/app/sandbox/browser_runner.py (planned artifact)
  - backend/app/sandbox/browser_schemas.py (planned artifact)
  - backend/app/automation/anti_bot_detector.py (planned artifact)
  - backend/app/api/v1/browser_stream.py (planned artifact)
  - backend/app/mcp/servers/browser.py (planned artifact)
  - backend/tests/unit/test_captcha_detection.py (planned artifact)
  - backend/tests/integration/test_browser_sandbox.py (planned artifact)
  - backend/tests/integration/test_egress_security.py (planned artifact)
  - backend/tests/integration/test_browser_permissions.py (planned artifact)
  - docs/security/BROWSER_SANDBOX.md (planned artifact)
  - docs/integrations/BROWSER_AUTOMATION.md (planned artifact)
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - docker/docker-compose.yml (planned artifact)
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `docker/browser/`, `backend/app/sandbox/`, `docs/security/`.

---

## F. API and Data Contracts

### 1. Browser Action Request Contract

```python
class BrowserActionRequest(BaseModel):
    action_type: Literal["navigate", "click", "fill", "screenshot"]
    url: str | None = None
    selector: str | None = None
    input_text: str | None = None
    screenshot_required: bool = True
```

### 2. Browser Action Result Contract

```python
class BrowserActionResult(BaseModel):
    success: bool
    current_url: str
    dom_summary: str
    screenshot_s3_uri: str | None = None
    bot_detected: bool = False
    bot_type: str | None = None
```

---

## G. Security Requirements

- **SSRF Immunity:** Anti-DNS rebinding proxy intercepts all connections; resolved IP addresses in private subnets or link-local ranges are blocked.
- **Anti-Bot Integrity:** Never solve or bypass CAPTCHAs programmatically; always pause and yield control to the human user.
- **Credential Protection:** Passwords injected via encrypted memory references; never exposed in DOM snapshots or logs.

---

## H. Testing Strategy

- **Egress Firewall Tests:** Attempt to navigate to `http://169.254.169.254/latest/meta-data/`; assert proxy terminates connection with HTTP 403.
- **CAPTCHA Trigger Tests:** Load synthetic test page with Cloudflare Turnstile widget; assert anti-bot detector triggers `human_browser_takeover` and halts.
- **Permission Enforcement Tests:** Attempt to dispatch `browser_click` without an approval token; assert rejection by permission gate.

---

## I. Definition of Done

1. Browser sandbox container runs Playwright in non-root environment.
2. Egress proxy blocks private IP ranges and AWS metadata endpoints.
3. CAPTCHA detection triggers automatic pause and human handoff.
4. Browser actions strictly adhere to 4-tier permission model.
5. Screenshots and DOM state persist to audit trail.
6. Documentation in docs/security/BROWSER_SANDBOX.md (planned artifact) is complete.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** `pytest backend/tests/integration/test_egress_security.py` passes 100%; CAPTCHA handoff test proves automation pauses without attempting bypass.
- **Dependent Phase Unlocked:** Phase 14 (Frontend Experience) is cleared to integrate interactive browser takeover controls.
