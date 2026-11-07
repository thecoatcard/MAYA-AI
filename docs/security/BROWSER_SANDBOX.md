# Browser Sandbox & Egress Security Policy

The MAYA AI sandboxed browser automation subsystem executes headless browser workloads within an isolated environment, protecting against Server-Side Request Forgery (SSRF), DNS-rebinding attacks, and credential exfiltration, while enforcing human-in-the-loop takeover for anti-bot challenges.

---

## 1. Threat Model & Security Invariants

As specified in `MAYA_AI_FINAL_PLAN.md` (§11.4, §15, §19, §24, Decisions D4, D5, D14):

1. **Host Isolation:** Browser processes run in an isolated container (`docker/browser/Dockerfile`) as non-root user `browser_runner` (UID/GID 10001). The host Docker socket is never mounted into the browser environment.
2. **Ephemeral Contexts:** Each browser session runs in an incognito context. Cookies, local storage, cached credentials, and DOM states are destroyed when the session completes.
3. **SSRF & DNS-Rebinding Immunity:** All destination URLs and resolved IP addresses are evaluated by `EgressSecurityFilter` before outbound TCP connections are established.
4. **Anti-Bot & CAPTCHA Integrity:** Programmatic solving or automated bypassing of CAPTCHAs and bot challenges is strictly prohibited. Detection immediately halts execution and transitions the session to `awaiting_human_takeover`.

---

## 2. Anti-SSRF & DNS-Rebinding Egress Filter

The `EgressSecurityFilter` (`backend/app/sandbox/egress_filter.py`) intercepts all navigation targets and enforces pre-request network security rules:

### Blocked Destination Ranges

| Address Range | Description / Standard | Enforcement Rationale |
| :--- | :--- | :--- |
| `10.0.0.0/8` | RFC 1918 Private Subnet | Prevents intranet exploration |
| `172.16.0.0/12` | RFC 1918 Private Subnet | Prevents Docker internal network scanning |
| `192.168.0.0/16` | RFC 1918 Private Subnet | Prevents LAN gateway targeting |
| `127.0.0.0/8`, `::1` | Loopback (IPv4/IPv6) | Prevents host port knocking |
| `169.254.0.0/16`, `fe80::/10` | Link-Local Unicast | Blocks cloud metadata access (`169.254.169.254`) |
| `0.0.0.0/8`, `::` | Unspecified / "This" Network | Blocks zero-address aliases |
| `100.64.0.0/10` | Carrier-Grade NAT (RFC 6598) | Prevents CGNAT internal routing |
| `fc00::/7` | Unique Local Address (ULA) | Blocks IPv6 internal routing |

### Obfuscated and Multi-Format IP Normalization

The filter automatically decodes and blocks non-standard IP formats:
- Decimal integer notation (e.g., `http://2130706433/` for `127.0.0.1`)
- Hexadecimal notation (e.g., `http://0x7f000001/`)
- Octal dotted notation (e.g., `http://0177.0.0.1/`)
- IPv4-mapped IPv6 formats (e.g., `http://[::ffff:127.0.0.1]/`)

### Anti-DNS Rebinding Defense

During hostname validation, `EgressSecurityFilter` queries all DNS A and AAAA resource records for the target host. If **any** returned IP address belongs to a prohibited subnet, the entire request is rejected immediately with `SecurityEgressViolationError`. This prevents dual-homed DNS-rebinding attacks where a public IP transitions to an internal IP.

---

## 3. Anti-Bot Challenge Detection & Human Takeover

The `AntiBotDetector` (`backend/app/automation/anti_bot_detector.py`) inspects page DOM contents and HTTP response headers for anti-bot challenge signatures:

- **Cloudflare Turnstile:** `.cf-turnstile`, `challenges.cloudflare.com/turnstile`, `_cf_chl_opt`
- **Google reCAPTCHA (v2/v3):** `.g-recaptcha`, `google.com/recaptcha/api.js`, `grecaptcha.ready`
- **hCaptcha:** `.h-captcha`, `js.hcaptcha.com`, `hcaptcha.com/1/api.js`
- **Altcha:** `<altcha-widget>`, `altcha.min.js`, `altcha.org`
- **DataDome:** `js.datadome.co`, `x-datadome-response` header
- **AWS WAF Captcha:** `awswaf.com`, `#aws-waf-captcha`

### Human Handoff Workflow

```
[Browser Runner] ---> Navigates to Target Page
       |
       v
[AntiBotDetector] ---> Scans DOM & Headers for Challenge
       |
       +---> [No Challenge] ---> Continues Automated Execution
       |
       +---> [Challenge Detected]
                 |
                 +---> 1. Pauses automation immediately (NEVER attempts automated bypass)
                 +---> 2. Sets session state to 'awaiting_human_takeover'
                 +---> 3. Captures viewport screenshot
                 +---> 4. Streams takeover state via GET /api/v1/browser/session/{id}/takeover
                 +---> 5. Human user completes verification in browser UI
                 +---> 6. User calls POST /api/v1/browser/session/{id}/resume to continue
```

---

## 4. 4-Tier Permission Classification

In alignment with Decision D4 and Decision D5:

1. **READ (`ToolTier.READ`):**
   - `browser_navigate`: Loads an egress-filtered URL.
   - `browser_screenshot`: Captures current viewport image.
   - `browser_get_dom`: Returns serialized DOM text summary.
   - *Autonomy:* Fully autonomous under workspace tenant RLS.

2. **APPROVAL_REQUIRED (`ToolTier.APPROVAL_REQUIRED`):**
   - `browser_click`: Clicks element identified by selector.
   - `browser_fill_form`: Enters input text into form field.
   - *Requirement:* Single-use cryptographic approval token signed with `jwt_signing_key` and bound to the RFC 8785 argument hash.

3. **RESTRICTED (`ToolTier.RESTRICTED`):**
   - `browser_submit_payment`: Submits financial transactions or billing modifications.
   - *Requirement:* Blocked from autonomous execution; requires mandatory step-up authentication.
