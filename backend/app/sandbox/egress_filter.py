"""Anti-SSRF and DNS-Rebinding Egress Security Filter (Decision D14, Phase 13).

Validates destination URLs before outbound browser or HTTP requests, blocking access
to private subnets (RFC 1918), loopback, link-local cloud metadata (169.254.169.254),
multicast, broadcast, unspecified addresses, and obfuscated formats.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit


class SecurityEgressViolationError(Exception):
    """Raised when an egress request targets a prohibited host or private/metadata IP."""

    def __init__(self, message: str, url: str | None = None, ip: str | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.ip = ip


# Prohibited CIDR IP Networks
BLOCKED_IPV4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("0.0.0.0/8"),  # "This" network
    ipaddress.IPv4Network("10.0.0.0/8"),  # RFC 1918 Private
    ipaddress.IPv4Network("100.64.0.0/10"),  # Shared address space / CGNAT (RFC 6598)
    ipaddress.IPv4Network("127.0.0.0/8"),  # Loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # Link-local / Cloud metadata (169.254.169.254)
    ipaddress.IPv4Network("172.16.0.0/12"),  # RFC 1918 Private
    ipaddress.IPv4Network("192.0.0.0/24"),  # IETF Protocol Assignments
    ipaddress.IPv4Network("192.0.2.0/24"),  # TEST-NET-1
    ipaddress.IPv4Network("192.168.0.0/16"),  # RFC 1918 Private
    ipaddress.IPv4Network("198.18.0.0/15"),  # Benchmark testing
    ipaddress.IPv4Network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.IPv4Network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.IPv4Network("224.0.0.0/4"),  # Multicast
    ipaddress.IPv4Network("240.0.0.0/4"),  # Reserved (former Class E)
    ipaddress.IPv4Network("255.255.255.255/32"),  # Broadcast
)

BLOCKED_IPV6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::/128"),  # Unspecified
    ipaddress.IPv6Network("::1/128"),  # Loopback
    ipaddress.IPv6Network("100::/64"),  # Discard prefix
    ipaddress.IPv6Network("fc00::/7"),  # Unique Local Address (ULA)
    ipaddress.IPv6Network("fe80::/10"),  # Link-local unicast
    ipaddress.IPv6Network("ff00::/8"),  # Multicast
)

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


def _parse_candidate_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse string into IPv4 or IPv6 address, supporting decimal, hex, and octal forms."""
    raw = raw.strip("[]").strip()
    if not raw:
        return None

    # Handle standard ipaddress parsing first
    try:
        ip = ipaddress.ip_address(raw)
        # Unwrap IPv4-mapped IPv6 address (e.g. ::ffff:127.0.0.1)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            return ip.ipv4_mapped
        return ip
    except ValueError:
        pass

    # Try decimal integer (e.g. "2130706433" -> 127.0.0.1)
    if raw.isdigit():
        try:
            val = int(raw)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass

    # Try hex integer (e.g. "0x7f000001")
    if raw.lower().startswith("0x"):
        try:
            val = int(raw, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass

    # Try octal or mixed octal/hex dotted notation (e.g. "0177.0.0.1", "0x7f.1")
    parts = raw.split(".")
    if 1 <= len(parts) <= 4:
        try:
            parsed_parts: list[int] = []
            for p in parts:
                p_clean = p.strip()
                if p_clean.lower().startswith("0x"):
                    parsed_parts.append(int(p_clean, 16))
                elif p_clean.startswith("0") and len(p_clean) > 1 and p_clean.isdigit():
                    parsed_parts.append(int(p_clean, 8))
                elif p_clean.isdigit():
                    parsed_parts.append(int(p_clean, 10))
                else:
                    return None

            if len(parsed_parts) == 4:
                if all(0 <= p <= 255 for p in parsed_parts):
                    return ipaddress.IPv4Address(".".join(str(p) for p in parsed_parts))
            elif len(parsed_parts) == 2:  # e.g. 0x7f.1 -> 127.0.0.1
                val = (parsed_parts[0] << 24) | parsed_parts[1]
                if 0 <= val <= 0xFFFFFFFF:
                    return ipaddress.IPv4Address(val)
        except ValueError:
            pass

    return None


class EgressSecurityFilter:
    """Security filter to prevent SSRF and DNS-rebinding attacks on outbound browser traffic."""

    def __init__(
        self,
        dns_resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        """Initialize filter with optional custom DNS resolver for testing or custom DNS engines."""
        self._dns_resolver = dns_resolver or self._default_dns_resolver

    @staticmethod
    def _default_dns_resolver(host: str) -> list[str]:
        """Resolve host to all IPv4 and IPv6 addresses via standard socket getaddrinfo."""
        try:
            results = socket.getaddrinfo(
                host,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
            ips: set[str] = set()
            for res in results:
                sockaddr = res[4]
                ip_str = str(sockaddr[0])
                # Filter out scoped IPv6 zone indices (e.g. fe80::1%eth0)
                if "%" in ip_str:
                    ip_str = ip_str.split("%")[0]
                ips.add(ip_str)
            return list(ips)
        except socket.gaierror as exc:
            raise SecurityEgressViolationError(
                f"DNS resolution failed for host '{host}': {exc}",
                url=host,
            ) from exc

    def is_ip_allowed(self, ip_input: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """Evaluate whether a given IP address is permitted for outbound egress."""
        if isinstance(ip_input, str):
            parsed = _parse_candidate_ip(ip_input)
            if parsed is None:
                return False
            ip: ipaddress.IPv4Address | ipaddress.IPv6Address = parsed
        else:
            ip = ip_input

        # Check native attributes
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False

        # Check explicit IPv4 networks
        if isinstance(ip, ipaddress.IPv4Address):
            for net in BLOCKED_IPV4_NETWORKS:
                if ip in net:
                    return False
            return True

        # Check explicit IPv6 networks
        if isinstance(ip, ipaddress.IPv6Address):
            # Also unwrap IPv4 mapped
            if ip.ipv4_mapped:
                return self.is_ip_allowed(ip.ipv4_mapped)
            for net_v6 in BLOCKED_IPV6_NETWORKS:
                if ip in net_v6:
                    return False
            return True

        return False

    def validate_url(self, url: str) -> str:
        """Validate destination URL against SSRF and DNS-rebinding restrictions.

        Returns normalized URL if valid, or raises SecurityEgressViolationError if forbidden.
        """
        if not url or not url.strip():
            raise SecurityEgressViolationError("URL cannot be empty", url=url)

        cleaned_url = url.strip()

        try:
            parsed = urlsplit(cleaned_url)
        except Exception as exc:
            raise SecurityEgressViolationError(f"Malformed URL '{url}': {exc}", url=url) from exc

        # Scheme check
        scheme = parsed.scheme.lower()
        if not scheme:
            raise SecurityEgressViolationError(f"Missing URL scheme in '{url}'", url=url)
        if scheme not in ALLOWED_SCHEMES:
            raise SecurityEgressViolationError(
                f"URL scheme '{scheme}' is prohibited. Only HTTP and HTTPS are permitted.",
                url=url,
            )

        # Host extraction
        raw_hostname = parsed.hostname
        if not raw_hostname or not raw_hostname.strip():
            raise SecurityEgressViolationError(
                f"Missing or invalid host in URL '{url}'",
                url=url,
            )

        hostname = raw_hostname.strip().lower()

        # Check if the hostname is directly an IP (literal, decimal, octal, hex)
        direct_ip = _parse_candidate_ip(hostname)
        if direct_ip is not None:
            if not self.is_ip_allowed(direct_ip):
                raise SecurityEgressViolationError(
                    f"Outbound connection to prohibited IP '{direct_ip}' blocked by egress policy.",
                    url=url,
                    ip=str(direct_ip),
                )
            return cleaned_url

        # Hostname resolution (DNS resolution)
        resolved_ips = self._dns_resolver(hostname)
        if not resolved_ips:
            raise SecurityEgressViolationError(
                f"DNS resolution for '{hostname}' returned no IP addresses.",
                url=url,
            )

        # Anti-DNS Rebinding: Inspect ALL resolved IP addresses. If ANY address is blocked, reject!
        for resolved_ip_str in resolved_ips:
            resolved_ip = _parse_candidate_ip(resolved_ip_str)
            if resolved_ip is None or not self.is_ip_allowed(resolved_ip):
                raise SecurityEgressViolationError(
                    f"Destination host '{hostname}' resolved to prohibited IP '{resolved_ip_str}'. "
                    "Connection blocked by anti-SSRF/DNS-rebinding policy.",
                    url=url,
                    ip=resolved_ip_str,
                )

        return cleaned_url
