"""Configuration loading and validation."""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_REQUIRED_HEADERS = {
    # header name -> (level if missing, explanation)
    "strict-transport-security": ("WARN", "HSTS tells browsers to always use HTTPS"),
    "x-content-type-options": ("WARN", "should be 'nosniff'"),
    "referrer-policy": ("WARN", "controls how much of the URL is sent to other sites"),
    "content-security-policy": ("INFO", "limits where scripts and resources can load from"),
    "permissions-policy": ("INFO", "limits browser features such as camera and location"),
}


class ConfigError(ValueError):
    """The configuration file is missing something or has a wrong value."""


@dataclass
class Config:
    site: str
    expect_indexable: bool = False
    public_paths: List[str] = field(default_factory=lambda: ["/"])
    protected_paths: List[str] = field(default_factory=list)
    login_hosts: List[str] = field(default_factory=list)  # substrings expected in a redirect to a login page
    preview_hosts: List[str] = field(default_factory=list)  # extra origins to test the protected paths on
    canonical_host: Optional[str] = None  # e.g. "example.com": other hosts must redirect here
    forbidden_strings: List[str] = field(default_factory=list)
    max_links: int = 40
    timeout: float = 15.0

    @property
    def origin(self) -> str:
        return self.site.rstrip("/")


def _origin(value: str, key: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConfigError(f"'{key}' must be an http(s) URL with a host, got: {value!r}")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ConfigError(f"'{key}' must be an origin only (no path or query), got: {value!r}")
    return f"{parts.scheme}://{parts.netloc}"


def _paths(value, key: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(p, str) and p.startswith("/") for p in value):
        raise ConfigError(f"'{key}' must be a list of paths that start with '/'")
    return value


def _strings(value, key: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
        raise ConfigError(f"'{key}' must be a list of non-empty strings")
    return value


def from_dict(data: Dict) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("the configuration must be a JSON object")
    known = {"site", "expect_indexable", "public_paths", "protected_paths", "login_hosts",
             "preview_hosts", "canonical_host", "forbidden_strings", "max_links", "timeout"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"unknown key(s): {', '.join(unknown)}")
    if "site" not in data:
        raise ConfigError("'site' is required, for example \"https://example.com\"")
    cfg = Config(site=_origin(data["site"], "site"))
    if "expect_indexable" in data:
        if not isinstance(data["expect_indexable"], bool):
            raise ConfigError("'expect_indexable' must be true or false")
        cfg.expect_indexable = data["expect_indexable"]
    if "public_paths" in data:
        cfg.public_paths = _paths(data["public_paths"], "public_paths")
    if "protected_paths" in data:
        cfg.protected_paths = _paths(data["protected_paths"], "protected_paths")
    if "login_hosts" in data:
        cfg.login_hosts = _strings(data["login_hosts"], "login_hosts")
    if "preview_hosts" in data:
        cfg.preview_hosts = [_origin(h, "preview_hosts") for h in _strings(data["preview_hosts"], "preview_hosts")]
    if "canonical_host" in data and data["canonical_host"] is not None:
        host = data["canonical_host"]
        if not isinstance(host, str) or "/" in host or not host:
            raise ConfigError("'canonical_host' must be a bare host name such as \"example.com\"")
        cfg.canonical_host = host.lower()
    if "forbidden_strings" in data:
        cfg.forbidden_strings = _strings(data["forbidden_strings"], "forbidden_strings")
    if "max_links" in data:
        if not isinstance(data["max_links"], int) or not 0 <= data["max_links"] <= 500:
            raise ConfigError("'max_links' must be an integer between 0 and 500")
        cfg.max_links = data["max_links"]
    if "timeout" in data:
        if not isinstance(data["timeout"], (int, float)) or not 1 <= data["timeout"] <= 120:
            raise ConfigError("'timeout' must be a number of seconds between 1 and 120")
        cfg.timeout = float(data["timeout"])
    return cfg


def load(path: str) -> Config:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:  # tolerate the BOM that Windows editors add
            data = json.load(f)
    except FileNotFoundError:
        raise ConfigError(f"configuration file not found: {path}")
    except json.JSONDecodeError as e:
        raise ConfigError(f"the configuration file is not valid JSON: {e}")
    return from_dict(data)
