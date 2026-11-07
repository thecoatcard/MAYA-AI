"""Unit tests for Anti-SSRF and DNS-Rebinding Egress Security Filter (Decision D14, Phase 13)."""

from __future__ import annotations

import pytest

from app.sandbox.egress_filter import (
    EgressSecurityFilter,
    SecurityEgressViolationError,
)


@pytest.fixture
def filter_instance() -> EgressSecurityFilter:
    return EgressSecurityFilter()


class TestEgressSecurityFilter:
    def test_blocks_disallowed_schemes(self, filter_instance: EgressSecurityFilter) -> None:
        disallowed = [
            "file:///etc/passwd",
            "ftp://ftp.example.com/file.txt",
            "gopher://gopher.example.com",
            "data:text/html,<script>alert(1)</script>",
            "javascript:alert(1)",
            "dict://dict.org",
            "ldap://127.0.0.1",
            "//example.com",
        ]
        for url in disallowed:
            with pytest.raises(SecurityEgressViolationError):
                filter_instance.validate_url(url)

    def test_blocks_localhost_and_loopback(self, filter_instance: EgressSecurityFilter) -> None:
        loopback_urls = [
            "http://localhost",
            "http://localhost:8080",
            "http://127.0.0.1",
            "http://127.0.0.2:9000/api",
            "http://127.255.255.254",
            "http://[::1]",
            "http://[::1]:3000",
            "http://[0000:0000:0000:0000:0000:0000:0000:0001]",
        ]
        for url in loopback_urls:
            with pytest.raises(SecurityEgressViolationError):
                filter_instance.validate_url(url)

    def test_blocks_cloud_metadata_endpoints(self, filter_instance: EgressSecurityFilter) -> None:
        metadata_urls = [
            "http://169.254.169.254/latest/meta-data/",
            "http://169.254.169.254:80",
            "http://169.254.170.2/v2/credentials",  # ECS task metadata
            "http://169.254.1.1",
            "http://[fe80::1]",  # IPv6 link-local
        ]
        for url in metadata_urls:
            with pytest.raises(SecurityEgressViolationError):
                filter_instance.validate_url(url)

    def test_blocks_rfc1918_private_ranges(self, filter_instance: EgressSecurityFilter) -> None:
        private_urls = [
            "http://10.0.0.1",
            "http://10.255.255.254:8080",
            "http://172.16.0.1",
            "http://172.31.255.255",
            "http://192.168.0.1",
            "http://192.168.1.254:3000",
            "http://[fc00::1]",  # IPv6 ULA
            "http://[fd12:3456:789a::1]",
        ]
        for url in private_urls:
            with pytest.raises(SecurityEgressViolationError):
                filter_instance.validate_url(url)

    def test_blocks_zero_address_and_broadcast(self, filter_instance: EgressSecurityFilter) -> None:
        urls = [
            "http://0.0.0.0",
            "http://0.0.0.0:8000",
            "http://255.255.255.255",
        ]
        for url in urls:
            with pytest.raises(SecurityEgressViolationError):
                filter_instance.validate_url(url)

    def test_blocks_obfuscated_and_decimal_ips(self, filter_instance: EgressSecurityFilter) -> None:
        # 127.0.0.1 in decimal integer = 2130706433
        # 169.254.169.254 in decimal integer = 2852039166
        # 10.0.0.1 in decimal integer = 167772161
        obfuscated_urls = [
            "http://2130706433",
            "http://2852039166",
            "http://167772161",
            "http://0177.0.0.1",  # Octal representation
            "http://0x7f.1",  # Hex representation
            "http://0x7f000001",
        ]
        for url in obfuscated_urls:
            with pytest.raises(SecurityEgressViolationError):
                filter_instance.validate_url(url)

    def test_blocks_ipv4_mapped_ipv6(self, filter_instance: EgressSecurityFilter) -> None:
        mapped_urls = [
            "http://[::ffff:127.0.0.1]",
            "http://[::ffff:169.254.169.254]",
            "http://[::ffff:10.0.0.1]",
            "http://[::ffff:192.168.1.1]",
            "http://[::ffff:7f00:1]",
        ]
        for url in mapped_urls:
            with pytest.raises(SecurityEgressViolationError):
                filter_instance.validate_url(url)

    def test_dns_rebinding_rejection_with_mock_resolver(self) -> None:
        # Resolver returns a public IP and a private IP (or only private)
        def rebinding_resolver(host: str) -> list[str]:
            if host == "rebind.example.com":
                return ["93.184.216.34", "127.0.0.1"]
            if host == "metadata.example.com":
                return ["169.254.169.254"]
            return ["93.184.216.34"]

        filter_custom = EgressSecurityFilter(dns_resolver=rebinding_resolver)

        with pytest.raises(SecurityEgressViolationError) as exc_info:
            filter_custom.validate_url("http://rebind.example.com")
        err_msg = str(exc_info.value).lower()
        assert "blocked" in err_msg or "forbidden" in err_msg

        with pytest.raises(SecurityEgressViolationError):
            filter_custom.validate_url("http://metadata.example.com/latest")

    def test_allows_valid_public_domain_with_mock_resolver(self) -> None:
        def public_resolver(host: str) -> list[str]:
            return ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]

        filter_custom = EgressSecurityFilter(dns_resolver=public_resolver)
        url = "https://example.com/test-path?param=value#anchor"
        validated = filter_custom.validate_url(url)
        assert validated == url

    def test_is_ip_allowed_predicate(self, filter_instance: EgressSecurityFilter) -> None:
        assert filter_instance.is_ip_allowed("8.8.8.8") is True
        assert filter_instance.is_ip_allowed("1.1.1.1") is True
        assert filter_instance.is_ip_allowed("93.184.216.34") is True
        assert filter_instance.is_ip_allowed("2606:2800:220:1:248:1893:25c8:1946") is True

        assert filter_instance.is_ip_allowed("127.0.0.1") is False
        assert filter_instance.is_ip_allowed("10.0.0.1") is False
        assert filter_instance.is_ip_allowed("172.16.5.1") is False
        assert filter_instance.is_ip_allowed("192.168.1.1") is False
        assert filter_instance.is_ip_allowed("169.254.169.254") is False
        assert filter_instance.is_ip_allowed("::1") is False
        assert filter_instance.is_ip_allowed("fc00::1") is False
        assert filter_instance.is_ip_allowed("fe80::1") is False
        assert filter_instance.is_ip_allowed("0.0.0.0") is False

    def test_blocks_empty_or_malformed_urls(self, filter_instance: EgressSecurityFilter) -> None:
        malformed = [
            "",
            "   ",
            ":///foo",
            "http://",
            "http://:8080",
            "http:///path",
        ]
        for url in malformed:
            with pytest.raises(SecurityEgressViolationError):
                filter_instance.validate_url(url)
