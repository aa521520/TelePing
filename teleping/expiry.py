"""RDAP registration and chain-host certificate expiration, with explicit unknowns."""

import asyncio
import ipaddress
import math
import socket
import ssl
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
import tldextract
from cryptography import x509

from teleping.checks import CONNECT_ATTEMPT_TIMEOUT, _addresses, _PinnedProxy

IANA_RDAP_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
NETWORK_TIMEOUT = 8
_extract = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=False)


def _unknown():
    return {"status": "unknown", "expires_at": None, "days": None}


def _registration_domain(target):
    try:
        hostname = urlsplit(target).hostname
        if not hostname:
            return None
        try:
            ipaddress.ip_address(hostname)
            return None
        except ValueError:
            pass
        extracted = _extract(hostname.encode("idna").decode("ascii"))
        return extracted.top_domain_under_public_suffix or None
    except (ValueError, UnicodeError):
        return None


def _expiry(date, now, status="ok"):
    if date.tzinfo is None:
        return _unknown()
    date = date.astimezone(UTC)
    return {
        "status": status,
        "expires_at": date.isoformat(),
        "days": math.floor((date.timestamp() - now) / 86400),
    }


def _parse_rdap(payload, now):
    try:
        values = {
            event["eventDate"]
            for event in payload.get("events", [])
            if event.get("eventAction") == "expiration"
        }
        # Ambiguous conflicting events or absent dates are never guessed.
        if len(values) != 1:
            return _unknown()
        return _expiry(datetime.fromisoformat(values.pop()), now)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return _unknown()


async def _safe_json(client, url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("INVALID_RDAP_ENDPOINT")
    await _addresses(parsed.hostname, parsed.port or 443, False)
    response = await client.get(url)
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("RDAP_RESPONSE_TOO_LARGE")
    return response.json()


async def _domain_expiry(domain, now):
    try:
        async with (
            asyncio.timeout(NETWORK_TIMEOUT * 2),
            _PinnedProxy(False, connect_budget=NETWORK_TIMEOUT) as proxy,
            httpx.AsyncClient(
                timeout=NETWORK_TIMEOUT,
                follow_redirects=False,
                trust_env=False,
                proxy=proxy.url,
                headers={"Accept": "application/rdap+json, application/json"},
            ) as client,
        ):
            bootstrap = await _safe_json(client, IANA_RDAP_BOOTSTRAP)
            tld = domain.rsplit(".", 1)[-1]
            for suffixes, servers in bootstrap.get("services", []):
                if tld in suffixes:
                    for server in servers:
                        if server.startswith("https://"):
                            payload = await _safe_json(client, server.rstrip("/") + "/domain/" + domain)
                            return _parse_rdap(payload, now)
                    break
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError, TimeoutError):
        return _unknown()
    return _unknown()


def _authority(authority):
    parsed = urlsplit("https://" + authority)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("INVALID_CERTIFICATE_HOST")
    return parsed.hostname, parsed.port or 443


def _read_certificate(address, host, port, timeout=NETWORK_TIMEOUT):
    expires = time.monotonic() + timeout
    context = ssl.create_default_context()
    with socket.create_connection(
        (address, port), timeout=max(0.001, expires - time.monotonic())
    ) as connection:
        connection.settimeout(max(0.001, expires - time.monotonic()))
        with context.wrap_socket(connection, server_hostname=host) as tls:
            return tls.getpeercert(binary_form=True)


async def _certificate_expiry(authority, now):
    result = {"host": authority, **_unknown()}
    expires = time.monotonic() + NETWORK_TIMEOUT + 1
    try:
        async with asyncio.timeout(NETWORK_TIMEOUT + 1):
            host, port = _authority(authority)
            addresses = await _addresses(host, port, False)
            for index, address in enumerate(addresses):
                remaining = expires - time.monotonic()
                if remaining <= 0:
                    break
                attempt = min(CONNECT_ATTEMPT_TIMEOUT, remaining / (len(addresses) - index))
                try:
                    raw = await asyncio.to_thread(_read_certificate, address, host, port, attempt)
                    certificate = x509.load_der_x509_certificate(raw)
                    expiry = certificate.not_valid_after_utc
                    result.update(_expiry(expiry, now, "expired" if expiry.timestamp() < now else "ok"))
                    return result
                except ssl.SSLCertVerificationError as error:
                    if error.verify_code == 10:
                        # Strict validation establishes expiration, but supplies no trusted date.
                        return {"host": authority, "status": "expired", "expires_at": None, "days": None}
                except (OSError, ValueError):
                    continue
    except (OSError, ValueError, TimeoutError):
        return result
    return result


async def check_expiry(target, https_hosts, *, now=None):
    now = time.time() if now is None else now
    domain = _registration_domain(target)
    hosts = list(dict.fromkeys(https_hosts))
    # Explicit HTTPS entry still has a certificate to inspect after navigation fails.
    parsed = urlsplit(target)
    if parsed.scheme == "https" and parsed.netloc not in hosts:
        hosts.insert(0, parsed.netloc)
    domain_task = asyncio.create_task(_domain_expiry(domain, now)) if domain else None
    limiter = asyncio.Semaphore(5)

    async def certificate(host):
        async with limiter:
            return await _certificate_expiry(host, now)

    certificates = await asyncio.gather(*(certificate(host) for host in hosts))
    return {"domain": await domain_task if domain_task else _unknown(), "certificates": certificates}
