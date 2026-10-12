"""The checks. Each takes (config, fetcher) and returns a list of Result.

Levels:
  PASS  the expectation is met
  FAIL  the site is not ready: fix before opening
  WARN  probably worth fixing
  INFO  something worth knowing, not an error
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Callable, List

from .client import Fetcher, Response
from .config import DEFAULT_REQUIRED_HEADERS, Config
from .htmlinfo import parse


@dataclass
class Result:
    level: str  # PASS | FAIL | WARN | INFO
    check: str
    subject: str
    detail: str = ""


# ---------------------------------------------------------------- helpers
def has_noindex(response: Response) -> bool:
    """True if the response says noindex in the X-Robots-Tag header or in a robots meta tag."""
    if "noindex" in response.header("x-robots-tag").lower():
        return True
    if "html" in response.header("content-type").lower() or response.body[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
        robots = parse(response.text).meta.get("robots", "").lower()
        return "noindex" in robots
    return False


def robots_blocks_everything(robots_txt: str) -> bool:
    """True if the group for 'User-agent: *' contains 'Disallow: /'."""
    in_star_group = False
    seen_rule_in_group = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = (s.strip() for s in line.split(":", 1))
        field = field.lower()
        if field == "user-agent":
            if seen_rule_in_group:  # a new group starts after rules
                in_star_group = False
                seen_rule_in_group = False
            if value == "*":
                in_star_group = True
        elif field in ("disallow", "allow"):
            seen_rule_in_group = True
            if in_star_group and field == "disallow" and value == "/":
                return True
    return False


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + path


# ---------------------------------------------------------------- checks
def check_reachability(cfg: Config, fetcher: Fetcher) -> List[Result]:
    out = []
    for path in cfg.public_paths:
        r = fetcher.get(_url(cfg.origin, path))
        if r.status == 0:
            out.append(Result("FAIL", "reachability", path, f"request failed: {r.error}"))
        elif r.status == 200:
            note = f" after {len(r.hops)} redirect(s)" if r.hops else ""
            out.append(Result("PASS", "reachability", path, f"200{note}"))
        else:
            out.append(Result("FAIL", "reachability", path, f"expected 200, got {r.status}"))
    return out


def check_indexing(cfg: Config, fetcher: Fetcher) -> List[Result]:
    out = []
    robots = fetcher.get(_url(cfg.origin, "/robots.txt"))
    blocked = robots.status == 200 and robots_blocks_everything(robots.text)
    for path in cfg.public_paths:
        r = fetcher.get(_url(cfg.origin, path))
        if r.status != 200:
            continue  # already reported by reachability
        noindex = has_noindex(r)
        if cfg.expect_indexable and noindex:
            out.append(Result("FAIL", "indexing", path, "has noindex but the site is meant to be indexable"))
        elif not cfg.expect_indexable and not noindex:
            out.append(Result("FAIL", "indexing", path, "no noindex (header or meta) but the site is meant to stay closed to search engines"))
        else:
            out.append(Result("PASS", "indexing", path, "noindex present" if noindex else "indexable"))
    if cfg.expect_indexable:
        if blocked:
            out.append(Result("FAIL", "robots.txt", "/robots.txt", "'Disallow: /' blocks all crawlers but the site is meant to be indexable"))
        elif robots.status == 200:
            out.append(Result("PASS", "robots.txt", "/robots.txt", "does not block the whole site"))
        else:
            out.append(Result("INFO", "robots.txt", "/robots.txt", f"status {robots.status}; not required, but recommended"))
        sitemap = fetcher.get(_url(cfg.origin, "/sitemap.xml"))
        out.append(Result("PASS" if sitemap.status == 200 else "WARN", "sitemap", "/sitemap.xml",
                          "found" if sitemap.status == 200 else f"status {sitemap.status}; a sitemap helps crawlers"))
    elif blocked:
        out.append(Result("INFO", "robots.txt", "/robots.txt",
                          "'Disallow: /' is present. Crawlers that obey it will not read the noindex tag, "
                          "so a URL may still be listed without content; both together are fine for a quiet launch"))
    return out


def check_https_and_host(cfg: Config, fetcher: Fetcher) -> List[Result]:
    out = []
    parts = urllib.parse.urlsplit(cfg.origin)
    host = parts.netloc
    if parts.scheme != "https":
        return [Result("WARN", "https", cfg.origin, "the site URL is not HTTPS")]
    plain = fetcher.get(f"http://{host}/", follow=False)
    if plain.status == 0:
        out.append(Result("INFO", "https", f"http://{host}/", f"plain HTTP not reachable ({plain.error}); fine if port 80 is closed"))
    elif plain.is_redirect and plain.header("location").lower().startswith("https://"):
        out.append(Result("PASS", "https", f"http://{host}/", f"redirects to HTTPS ({plain.status})"))
    else:
        out.append(Result("FAIL", "https", f"http://{host}/", f"plain HTTP is not redirected to HTTPS (status {plain.status})"))
    if cfg.canonical_host:
        variants = {host.lower(), cfg.canonical_host, "www." + cfg.canonical_host}
        for variant in sorted(variants - {cfg.canonical_host}):
            r = fetcher.get(f"https://{variant}/", follow=False)
            if r.status == 0:
                out.append(Result("INFO", "canonical host", variant, f"not reachable ({r.error})"))
            elif r.is_redirect and cfg.canonical_host in r.header("location").lower():
                out.append(Result("PASS", "canonical host", variant, f"redirects to {cfg.canonical_host}"))
            else:
                out.append(Result("FAIL", "canonical host", variant, f"does not redirect to {cfg.canonical_host} (status {r.status})"))
    return out


def check_protected(cfg: Config, fetcher: Fetcher) -> List[Result]:
    """Private paths must not return content to an anonymous visitor, on the main site and on each preview host."""
    out = []
    origins = [cfg.origin] + cfg.preview_hosts
    for origin in origins:
        for path in cfg.protected_paths:
            url = _url(origin, path)
            subject = f"{origin.split('://', 1)[1]}{path}"
            r = fetcher.get(url)
            if r.status == 0:
                out.append(Result("FAIL", "protected", subject, f"request failed: {r.error}"))
            elif r.external_redirect:
                if cfg.login_hosts and not any(h.lower() in r.external_redirect.lower() for h in cfg.login_hosts):
                    out.append(Result("WARN", "protected", subject, f"redirects to an unexpected place: {r.external_redirect}"))
                else:
                    out.append(Result("PASS", "protected", subject, f"redirects away to a login ({r.status})"))
            elif r.status in (401, 403):
                out.append(Result("PASS", "protected", subject, f"{r.status}: access denied"))
            elif 200 <= r.status < 300:
                out.append(Result("FAIL", "protected", subject, f"EXPOSED: anonymous visitors get {r.status} with content"))
            elif r.status == 404:
                out.append(Result("WARN", "protected", subject, "404: not deployed here, or hidden. Check this is intended"))
            else:
                out.append(Result("WARN", "protected", subject, f"unexpected status {r.status}"))
    return out


def check_security_headers(cfg: Config, fetcher: Fetcher) -> List[Result]:
    r = fetcher.get(_url(cfg.origin, cfg.public_paths[0] if cfg.public_paths else "/"))
    if r.status != 200:
        return []
    out = []
    for name, (level, why) in DEFAULT_REQUIRED_HEADERS.items():
        value = r.header(name)
        if not value:
            out.append(Result(level, "headers", name, f"missing: {why}"))
        elif name == "x-content-type-options" and value.strip().lower() != "nosniff":
            out.append(Result("WARN", "headers", name, f"expected 'nosniff', got {value!r}"))
        else:
            out.append(Result("PASS", "headers", name, "present"))
    return out


def check_page_basics(cfg: Config, fetcher: Fetcher) -> List[Result]:
    out = []
    for path in cfg.public_paths:
        r = fetcher.get(_url(cfg.origin, path))
        if r.status != 200 or "html" not in r.header("content-type").lower():
            continue
        info = parse(r.text)
        title = info.title.strip()
        out.append(Result("PASS" if title else "FAIL", "page", f"{path} <title>", title[:70] if title else "missing or empty"))
        desc = info.meta.get("description", "").strip()
        out.append(Result("PASS" if desc else "WARN", "page", f"{path} meta description", f"{len(desc)} characters" if desc else "missing"))
        out.append(Result("PASS" if info.h1_count == 1 else "WARN", "page", f"{path} <h1>", f"{info.h1_count} found (expected exactly 1)"))
        out.append(Result("PASS" if "viewport" in info.meta else "WARN", "page", f"{path} viewport", "present" if "viewport" in info.meta else "missing: the page may look broken on phones"))
    return out


def check_mixed_content(cfg: Config, fetcher: Fetcher) -> List[Result]:
    if not cfg.origin.startswith("https://"):
        return []
    out = []
    for path in cfg.public_paths:
        r = fetcher.get(_url(cfg.origin, path))
        if r.status != 200 or "html" not in r.header("content-type").lower():
            continue
        insecure = [u for u in parse(r.text).resources if u.lower().startswith("http://")]
        if insecure:
            out.append(Result("FAIL", "mixed content", path, f"{len(insecure)} resource(s) load over HTTP, e.g. {insecure[0]}"))
        else:
            out.append(Result("PASS", "mixed content", path, "no HTTP resources"))
    return out


def check_internal_links(cfg: Config, fetcher: Fetcher) -> List[Result]:
    if cfg.max_links == 0:
        return []
    out = []
    seen = set()
    checked = 0
    home = _url(cfg.origin, cfg.public_paths[0] if cfg.public_paths else "/")
    r = fetcher.get(home)
    if r.status != 200 or "html" not in r.header("content-type").lower():
        return []
    host = urllib.parse.urlsplit(cfg.origin).netloc.lower()
    broken = []
    for href in parse(r.text).links:
        target = urllib.parse.urljoin(home, href)
        parts = urllib.parse.urlsplit(target)
        if parts.scheme not in ("http", "https") or parts.netloc.lower() != host:
            continue
        if parts.path.startswith("/cdn-cgi/"):
            continue  # added by the CDN (e.g. e-mail obfuscation); not part of the site and often 404 on a plain request
        clean = parts._replace(fragment="").geturl()
        if clean in seen:
            continue
        seen.add(clean)
        if checked >= cfg.max_links:
            out.append(Result("INFO", "links", "limit", f"stopped after {cfg.max_links} links (raise 'max_links' to check more)"))
            break
        checked += 1
        lr = fetcher.get(clean)
        if lr.status == 0 or lr.status >= 400:
            broken.append(f"{parts.path or '/'} -> {lr.status or lr.error}")
    if broken:
        out.append(Result("FAIL", "links", "internal", f"{len(broken)} broken: " + "; ".join(broken[:5])))
    else:
        out.append(Result("PASS", "links", "internal", f"{checked} internal link(s) checked on the home page, none broken"))
    return out


def check_forbidden_strings(cfg: Config, fetcher: Fetcher) -> List[Result]:
    if not cfg.forbidden_strings:
        return []
    out = []
    for path in cfg.public_paths:
        r = fetcher.get(_url(cfg.origin, path))
        if r.status != 200:
            continue
        lower = r.text.lower()
        hits = [s for s in cfg.forbidden_strings if s.lower() in lower]
        if hits:
            out.append(Result("FAIL", "forbidden text", path, "found: " + ", ".join(repr(h) for h in hits)))
        else:
            out.append(Result("PASS", "forbidden text", path, "none of the forbidden strings appear"))
    return out


ALL_CHECKS: List[Callable[[Config, Fetcher], List[Result]]] = [
    check_reachability,
    check_indexing,
    check_https_and_host,
    check_protected,
    check_security_headers,
    check_page_basics,
    check_mixed_content,
    check_internal_links,
    check_forbidden_strings,
]


def run_all(cfg: Config, fetcher: Fetcher) -> List[Result]:
    results: List[Result] = []
    for check in ALL_CHECKS:
        results.extend(check(cfg, fetcher))
    return results
