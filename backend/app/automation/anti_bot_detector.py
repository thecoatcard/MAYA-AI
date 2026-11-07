"""CAPTCHA and Anti-Bot Challenge Detector (Phase 13, Decisions D4, D14).

Detects bot challenge gates (Cloudflare Turnstile, reCAPTCHA, hCaptcha, Altcha,
DataDome, AWS WAF Captcha) in DOM structures or HTTP response headers.

Repository Safety Invariant:
MAYA AI NEVER attempts to solve, click through, or bypass anti-bot challenges
programmatically. Detection halts automation and immediately yields control
to human browser takeover.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ChallengeDetectionResult:
    """Inspection result indicating presence of an anti-bot challenge gate."""

    is_challenge_present: bool
    challenge_type: str | None = None
    selector: str | None = None
    confidence: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


class CaptchaDetectedException(Exception):
    """Raised when an anti-bot challenge is detected, requiring human takeover."""

    def __init__(
        self,
        result: ChallengeDetectionResult,
        message: str | None = None,
        action_type: str = "human_browser_takeover",
    ) -> None:
        msg = (
            message
            or f"Anti-bot challenge '{result.challenge_type}' detected. "
            "Automation paused for mandatory human browser takeover."
        )
        super().__init__(msg)
        self.result = result
        self.message = msg
        self.action_type = action_type


# Signatures for challenge gate detection
CHALLENGE_SIGNATURES: list[dict[str, Any]] = [
    {
        "type": "cloudflare_turnstile",
        "primary_selector": ".cf-turnstile",
        "dom_patterns": [
            r"class=[\"'][^\"']*\bcf-turnstile\b",
            r"challenges\.cloudflare\.com/turnstile",
            r"name=[\"']cf-turnstile-response[\"']",
            r"_cf_chl_opt",
            r"cf-challenge-running",
            r"cf-browser-verification",
        ],
        "headers": ["cf-mitigated"],
    },
    {
        "type": "recaptcha_v2",
        "primary_selector": ".g-recaptcha",
        "dom_patterns": [
            r"class=[\"'][^\"']*\bg-recaptcha\b",
            r"google\.com/recaptcha/api\.js",
            r"name=[\"']g-recaptcha-response[\"']",
            r"id=[\"']g-recaptcha-response[\"']",
        ],
        "headers": [],
    },
    {
        "type": "recaptcha_v3",
        "primary_selector": ".grecaptcha-badge",
        "dom_patterns": [
            r"grecaptcha\.execute",
            r"grecaptcha\.ready",
            r"class=[\"'][^\"']*\bgrecaptcha-badge\b",
        ],
        "headers": [],
    },
    {
        "type": "hcaptcha",
        "primary_selector": ".h-captcha",
        "dom_patterns": [
            r"class=[\"'][^\"']*\bh-captcha\b",
            r"js\.hcaptcha\.com",
            r"name=[\"']h-captcha-response[\"']",
            r"hcaptcha\.com/1/api\.js",
        ],
        "headers": [],
    },
    {
        "type": "altcha",
        "primary_selector": "altcha-widget",
        "dom_patterns": [
            r"<altcha-widget",
            r"altcha\.min\.js",
            r"altcha\.org",
            r"class=[\"'][^\"']*\baltcha\b",
        ],
        "headers": [],
    },
    {
        "type": "datadome",
        "primary_selector": "#datadome",
        "dom_patterns": [
            r"js\.datadome\.co",
            r"geo\.captcha-delivery\.com",
            r"datadome\.js",
        ],
        "headers": ["x-datadome-response", "x-datadome"],
    },
    {
        "type": "aws_waf_captcha",
        "primary_selector": "#aws-waf-captcha",
        "dom_patterns": [
            r"awswaf\.com",
            r"aws-waf-captcha",
            r"captcha-sdk",
        ],
        "headers": ["x-amzn-waf-action"],
    },
]


class AntiBotDetector:
    """Scans DOM contents and HTTP headers for anti-bot / CAPTCHA challenge gates."""

    def __init__(self, signatures: list[dict[str, Any]] | None = None) -> None:
        self.signatures = signatures or CHALLENGE_SIGNATURES

    def detect_challenge(
        self,
        dom_content: str,
        headers: dict[str, str] | None = None,
    ) -> ChallengeDetectionResult:
        """Inspect DOM content and optional response headers for challenge signatures."""
        normalized_headers = {k.lower(): v for k, v in headers.items()} if headers else {}

        for sig in self.signatures:
            challenge_type: str = sig["type"]
            primary_selector: str = sig.get("primary_selector", "")

            # Check headers first
            for header_name in sig.get("headers", []):
                if header_name.lower() in normalized_headers:
                    return ChallengeDetectionResult(
                        is_challenge_present=True,
                        challenge_type=challenge_type,
                        selector=primary_selector,
                        confidence=1.0,
                        details={
                            "matched_header": header_name,
                            "value": normalized_headers[header_name.lower()],
                        },
                    )

            # Check DOM patterns
            for pattern in sig.get("dom_patterns", []):
                match = re.search(pattern, dom_content, re.IGNORECASE)
                if match:
                    return ChallengeDetectionResult(
                        is_challenge_present=True,
                        challenge_type=challenge_type,
                        selector=primary_selector,
                        confidence=0.95,
                        details={"matched_pattern": pattern, "snippet": match.group(0)},
                    )

        return ChallengeDetectionResult(
            is_challenge_present=False,
            challenge_type=None,
            selector=None,
            confidence=0.0,
        )

    def assert_no_challenge(
        self,
        dom_content: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Raise CaptchaDetectedException if an anti-bot challenge is detected."""
        res = self.detect_challenge(dom_content, headers)
        if res.is_challenge_present:
            raise CaptchaDetectedException(result=res)
