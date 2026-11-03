from datetime import UTC, datetime

from teleping.expiry import _parse_rdap, _registration_domain, check_expiry


def test_rdap_requires_explicit_expiration_event_and_aware_date():
    now = datetime(2030, 1, 1, tzinfo=UTC).timestamp()
    assert (
        _parse_rdap({"events": [{"eventAction": "registration", "eventDate": "2030-01-11T00:00:00Z"}]}, now)[
            "status"
        ]
        == "unknown"
    )
    assert _parse_rdap(
        {"events": [{"eventAction": "expiration", "eventDate": "2030-01-11T00:00:00Z"}]}, now
    ) == {"status": "ok", "expires_at": "2030-01-11T00:00:00+00:00", "days": 10}
    assert (
        _parse_rdap({"events": [{"eventAction": "expiration", "eventDate": "not-a-date"}]}, now)["status"]
        == "unknown"
    )


def test_registration_domain_uses_public_suffix_not_last_two_labels():
    assert _registration_domain("https://www.store.example.co.uk/page") == "example.co.uk"
    assert _registration_domain("https://tenant.blogspot.com") == "blogspot.com"
    assert _registration_domain("https://127.0.0.1") is None
    assert _registration_domain("http://example.invalid") is None


async def test_expiry_unknown_is_not_guessed_and_certificate_authority_keeps_port(monkeypatch):
    from teleping import expiry

    seen = []

    async def rdap(domain, now):
        assert domain == "example.co.uk"
        return {"status": "unknown", "expires_at": None, "days": None}

    async def certificate(host, now):
        seen.append(host)
        return {"host": host, "status": "unknown", "expires_at": None, "days": None}

    monkeypatch.setattr(expiry, "_domain_expiry", rdap)
    monkeypatch.setattr(expiry, "_certificate_expiry", certificate)
    result = await check_expiry("http://www.example.co.uk/path", ["secure.example.co.uk:8443"], now=0)
    assert result["domain"]["status"] == "unknown"
    assert result["certificates"][0]["status"] == "unknown"
    assert seen == ["secure.example.co.uk:8443"]


async def test_iana_bootstrap_selects_tld_endpoint_and_preserves_unknown(monkeypatch):
    from teleping import expiry

    async def data(client, url):
        if url == expiry.IANA_RDAP_BOOTSTRAP:
            return {
                "services": [
                    [["uk"], ["https://rdap.example.test/uk/"]],
                    [["com"], ["https://rdap.example.test/com/"]],
                ]
            }
        if url == "https://rdap.example.test/uk/domain/example.co.uk":
            return {"events": [{"eventAction": "expiration", "eventDate": "2030-01-11T00:00:00Z"}]}
        return {"events": []}

    monkeypatch.setattr(expiry, "_safe_json", data)
    now = datetime(2030, 1, 1, tzinfo=UTC).timestamp()
    assert (await expiry.check_expiry("http://shop.example.co.uk", [], now=now))["domain"]["days"] == 10
    assert (await expiry.check_expiry("http://example.com", [], now=now))["domain"]["status"] == "unknown"


async def test_certificate_sni_nonstandard_port_and_expiration_from_real_tls(tmp_path, monkeypatch):
    import ssl

    from fixtures.tls_site import tls_site

    from teleping import expiry

    default_context = ssl.create_default_context
    async with tls_site(tmp_path) as site:

        async def addresses(host, port, allow_private):
            if host != "example.test" or port != site["port"]:
                raise ValueError("UNEXPECTED_FIXTURE_ADDRESS")
            return ["127.0.0.1"]

        monkeypatch.setattr(expiry, "_addresses", addresses)
        monkeypatch.setattr(ssl, "create_default_context", lambda: default_context(cafile=site["cafile"]))
        result = await expiry._certificate_expiry(f"example.test:{site['port']}", site["now"])
    assert result["status"] == "ok"
    assert result["days"] == 10
    assert site["sni"] == ["example.test"]


async def test_expired_certificate_metadata_is_expired_not_accessibility(tmp_path, monkeypatch):
    import ssl

    from fixtures.tls_site import tls_site

    from teleping import expiry

    default_context = ssl.create_default_context
    async with tls_site(tmp_path, expired=True) as site:

        async def addresses(host, port, allow_private):
            return ["127.0.0.1"]

        monkeypatch.setattr(expiry, "_addresses", addresses)
        monkeypatch.setattr(ssl, "create_default_context", lambda: default_context(cafile=site["cafile"]))
        result = await expiry._certificate_expiry(f"example.test:{site['port']}", site["now"])
    assert result["status"] == "expired"
    assert result["days"] is None
    assert result["expires_at"] is None
    assert site["sni"] == ["example.test"]


async def test_certificate_uses_second_validated_address_after_first_connection_fails(tmp_path, monkeypatch):
    import socket
    import ssl

    from fixtures.tls_site import tls_site

    from teleping import expiry

    default_context = ssl.create_default_context
    real_connect = socket.create_connection
    tried = []
    async with tls_site(tmp_path) as site:

        async def addresses(host, port, allow_private):
            return ["8.8.8.8", "1.1.1.1"]

        def connect(address, *args, **kwargs):
            tried.append(address[0])
            if address[0] == "8.8.8.8":
                raise ConnectionRefusedError("FIXTURE_FIRST_ADDRESS_REFUSED")
            if address != ("1.1.1.1", site["port"]):
                raise ValueError("UNEXPECTED_FIXTURE_ADDRESS")
            return real_connect(("127.0.0.1", site["port"]), *args, **kwargs)

        monkeypatch.setattr(expiry, "_addresses", addresses)
        monkeypatch.setattr(socket, "create_connection", connect)
        monkeypatch.setattr(ssl, "create_default_context", lambda: default_context(cafile=site["cafile"]))
        result = await expiry._certificate_expiry(f"example.test:{site['port']}", site["now"])
    assert result["status"] == "ok"
    assert result["days"] == 10
    assert tried == ["8.8.8.8", "1.1.1.1"]
    assert site["sni"] == ["example.test"]
