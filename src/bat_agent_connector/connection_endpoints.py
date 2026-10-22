"""One bounded canonical endpoint validator for setup and source descriptors."""
from urllib.parse import urlsplit


def setup_endpoint(value):
    if not isinstance(value, str) or any(c.isspace() for c in value) or "?" in value or "#" in value:
        raise ValueError("Invalid endpoint")
    normalized = value.rstrip("/") + "/"
    if len(normalized) > 512:
        raise ValueError("Invalid endpoint")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "wss" or not parsed.hostname or not parsed.port or parsed.username or parsed.password
                or parsed.path not in {"", "/"}):
            raise ValueError
    except ValueError:
        raise ValueError("Invalid endpoint") from None
    return normalized
