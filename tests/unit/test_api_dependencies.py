from starlette.requests import Request

from backend.app.api.dependencies import rate_limit_subject


def _request(*, forwarded_for: str | None) -> Request:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": ("169.254.8.1", 1234),
            "server": ("testserver", 443),
            "scheme": "https",
            "query_string": b"",
        }
    )


def test_rate_limit_subject_uses_google_appended_client_hop() -> None:
    request = _request(forwarded_for="203.0.113.99, 219.254.21.212, 34.120.0.1")

    assert rate_limit_subject(request) == "219.254.21.212"


def test_rate_limit_subject_falls_back_when_forwarded_chain_is_untrusted() -> None:
    request = _request(forwarded_for="spoofed-value, also-not-an-ip")

    assert rate_limit_subject(request) == "169.254.8.1"


def test_rate_limit_subject_falls_back_without_google_forwarding_hops() -> None:
    assert rate_limit_subject(_request(forwarded_for=None)) == "169.254.8.1"
