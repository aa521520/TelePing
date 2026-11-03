"""Validate private target input without ever echoing it into errors."""

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


def normalize_target(value: str) -> str:
    try:
        value = value.strip()
        if not value or re.search(r"[\s\x00-\x1f\x7f]", value) or "#" in value:
            raise ValueError
        parsed = urlsplit(value if "://" in value else "http://" + value)
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise ValueError
        host = (parsed.hostname or "").rstrip(".").lower().encode("idna").decode("ascii")
        if not host:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".localhost", ".local", ".internal", ".lan")):
                raise ValueError from None
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                raise ValueError
        else:
            if not address.is_global:
                raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port and port != (443 if parsed.scheme == "https" else 80):
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path or "/", parsed.query, ""))
    except (ValueError, UnicodeError, AttributeError):
        raise ValueError("invalid_target") from None


def parse_targets(text: str) -> list[str]:
    targets = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        target = normalize_target(line)
        if target not in targets:
            targets.append(target)
    return targets
