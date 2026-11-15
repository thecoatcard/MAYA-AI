"""Unit tests for CAPTCHA & Anti-Bot Challenge Detector (Phase 13)."""

from __future__ import annotations

import pytest

from app.automation.anti_bot_detector import (
    AntiBotDetector,
    CaptchaDetectedException,
)


@pytest.fixture
def detector() -> AntiBotDetector:
    return AntiBotDetector()


class TestAntiBotDetector:
    def test_detects_clean_html_without_challenge(self, detector: AntiBotDetector) -> None:
        clean_html = """
        <!DOCTYPE html>
        <html>
        <head><title>Normal Page</title></head>
        <body>
            <h1>Welcome to Maya AI</h1>
            <form action="/login" method="post">
                <input type="text" name="username" id="username"/>
                <input type="password" name="password" id="password"/>
                <button type="submit">Log In</button>
            </form>
        </body>
        </html>
        """
        result = detector.detect_challenge(clean_html)
        assert result.is_challenge_present is False
        assert result.challenge_type is None
        assert result.selector is None

        # assert_no_challenge should not raise
        detector.assert_no_challenge(clean_html)

    def test_detects_cloudflare_turnstile_class(self, detector: AntiBotDetector) -> None:
        turnstile_html = """
        <html>
        <body>
            <div class="cf-turnstile" data-sitekey="0x4AAAAAAABcd"></div>
            <script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
        </body>
        </html>
        """
        result = detector.detect_challenge(turnstile_html)
        assert result.is_challenge_present is True
        assert result.challenge_type == "cloudflare_turnstile"
        assert result.selector == ".cf-turnstile"

        with pytest.raises(CaptchaDetectedException) as exc_info:
            detector.assert_no_challenge(turnstile_html)
        assert exc_info.value.result.challenge_type == "cloudflare_turnstile"

    def test_detects_cloudflare_challenge_script_and_meta(self, detector: AntiBotDetector) -> None:
        cf_challenge_html = """
        <html>
        <head>
            <script>window._cf_chl_opt = { cType: 'non-interactive' };</script>
        </head>
        <body>
            <div id="cf-challenge-running">Checking your browser...</div>
        </body>
        </html>
        """
        result = detector.detect_challenge(cf_challenge_html)
        assert result.is_challenge_present is True
        assert result.challenge_type == "cloudflare_turnstile"

    def test_detects_recaptcha_v2(self, detector: AntiBotDetector) -> None:
        recaptcha_v2_html = """
        <html>
        <body>
            <div class="g-recaptcha" data-sitekey="6Le-wvkSAAAAAPBMRTvw0Q44WGNxTX01W47El-Ky"></div>
            <script src="https://www.google.com/recaptcha/api.js" async defer></script>
        </body>
        </html>
        """
        result = detector.detect_challenge(recaptcha_v2_html)
        assert result.is_challenge_present is True
        assert result.challenge_type == "recaptcha_v2"
        assert result.selector == ".g-recaptcha"

    def test_detects_recaptcha_v3_script(self, detector: AntiBotDetector) -> None:
        recaptcha_v3_html = """
        <html>
        <head>
            <script src="https://www.google.com/recaptcha/api.js?render=6Ld_xxxx"></script>
            <script>
                grecaptcha.ready(function() {
                    grecaptcha.execute('6Ld_xxxx', {action: 'submit'}).then(function(token) {});
                });
            </script>
        </head>
        <body><p>Protected Content</p></body>
        </html>
        """
        result = detector.detect_challenge(recaptcha_v3_html)
        assert result.is_challenge_present is True
        assert result.challenge_type in {"recaptcha_v2", "recaptcha_v3"}

    def test_detects_hcaptcha(self, detector: AntiBotDetector) -> None:
        hcaptcha_html = """
        <html>
        <body>
            <div class="h-captcha" data-sitekey="10000000-ffff-ffff-ffff-000000000001"></div>
            <script src="https://js.hcaptcha.com/1/api.js" async defer></script>
        </body>
        </html>
        """
        result = detector.detect_challenge(hcaptcha_html)
        assert result.is_challenge_present is True
        assert result.challenge_type == "hcaptcha"
        assert result.selector == ".h-captcha"

    def test_detects_altcha(self, detector: AntiBotDetector) -> None:
        altcha_html = """
        <html>
        <body>
            <altcha-widget challengeurl="/api/altcha-challenge"></altcha-widget>
            <script async defer src="/altcha.min.js" type="module"></script>
        </body>
        </html>
        """
        result = detector.detect_challenge(altcha_html)
        assert result.is_challenge_present is True
        assert result.challenge_type == "altcha"
        assert result.selector == "altcha-widget"

    def test_detects_datadome_via_headers_and_script(self, detector: AntiBotDetector) -> None:
        datadome_html = """
        <html>
        <head>
            <script src="https://js.datadome.co/tags.js"></script>
        </head>
        <body>Blocked by DataDome</body>
        </html>
        """
        result = detector.detect_challenge(datadome_html)
        assert result.is_challenge_present is True
        assert result.challenge_type == "datadome"

        # Via HTTP header
        header_result = detector.detect_challenge(
            "<html><body>Wait</body></html>",
            headers={"X-DataDome-Response": "403"},
        )
        assert header_result.is_challenge_present is True
        assert header_result.challenge_type == "datadome"

    def test_detects_aws_waf_captcha(self, detector: AntiBotDetector) -> None:
        aws_waf_html = """
        <html>
        <head>
            <script src="https://example.awswaf.com/token/captcha.js"></script>
        </head>
        <body id="aws-waf-captcha">Please solve the puzzle</body>
        </html>
        """
        result = detector.detect_challenge(aws_waf_html)
        assert result.is_challenge_present is True
        assert result.challenge_type == "aws_waf_captcha"

    def test_human_takeover_recommendation(self, detector: AntiBotDetector) -> None:
        turnstile_html = '<div class="cf-turnstile"></div>'
        with pytest.raises(CaptchaDetectedException) as exc_info:
            detector.assert_no_challenge(turnstile_html)
        assert exc_info.value.action_type == "human_browser_takeover"
        assert exc_info.value.message is not None
