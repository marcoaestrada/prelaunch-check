"""Minimal read-only HTTP client built on the standard library.

Only GET requests are sent. Redirects are never followed by urllib itself: the
`Fetcher` follows them one hop at a time, and only while they stay on the same
host, so a protected page that redirects to a login page is reported as such
instead of fetching the login page.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

USER_AGENT = "prelaunch-check/0.1 (read-only site checker)"
MAX_BODY = 1_000_000  # bytes read from any response


@dataclass
class Response:
    url: str
    status: int  # 0 means the request failed (DNS, timeout, TLS, ...)
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    error: str = ""
    hops: List[str] = field(default_factory=list)  # URLs visited before this response
    external_redirect: Optional[str] = None  # Location of a redirect to another host (not followed)

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def is_redirect(self) -> bool:
        return self.status in (301, 302, 303, 307, 308)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
        return None  # make urllib raise HTTPError for 3xx so we can inspect it


_opener = urllib.request.build_opener(_NoRedirect)


def fetch_once(url: str, timeout: float = 15.0) -> Response:
    """GET one URL without following redirects."""
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with _opener.open(request, timeout=timeout) as r:
            return Response(url, r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(MAX_BODY))
    except urllib.error.HTTPError as e:
        body = e.read(MAX_BODY) if e.fp else b""
        return Response(url, e.code, {k.lower(): v for k, v in e.headers.items()}, body)
    except Exception as e:  # network errors of any kind
        return Response(url, 0, error=str(e))


class Fetcher:
    """Fetches URLs with a small cache and bounded, same-host redirect following."""

    def __init__(self, timeout: float = 15.0, max_redirects: int = 5):
        self.timeout = timeout
        self.max_redirects = max_redirects
        self._cache: Dict[tuple, Response] = {}

    def get(self, url: str, follow: bool = True) -> Response:
        key = (url, follow)
        if key not in self._cache:
            self._cache[key] = self._get(url, follow)
        return self._cache[key]

    def _get(self, url: str, follow: bool) -> Response:
        response = fetch_once(url, self.timeout)
        if not follow:
            return response
        hops: List[str] = []
        for _ in range(self.max_redirects):
            if not response.is_redirect:
                break
            location = response.header("location")
            if not location:
                break
            target = urllib.parse.urljoin(response.url, location)
            if urllib.parse.urlsplit(target).netloc.lower() != urllib.parse.urlsplit(response.url).netloc.lower():
                response.external_redirect = target
                break
            hops.append(response.url)
            response = fetch_once(target, self.timeout)
        response.hops = hops
        return response
