#!/usr/bin/env python3
"""Reusable HTTP connection layer with optional proxy support.

The module is intentionally independent from linux.org.ru-specific logic.
It can be reused in other projects that need a configured requests.Session,
polite request pacing, retries, cookies and HTTP/HTTPS/SOCKS proxy support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from http.cookiejar import LoadError, MozillaCookieJar
import random
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests
import yaml


class ConnectionError(RuntimeError):
    """Configuration or network-layer error."""


@dataclass(frozen=True)
class ProxyConfig:
    enabled: bool = False
    default: str = ""
    http: str = ""
    https: str = ""
    no_proxy: str = ""
    username: str = ""
    password: str = ""
    http_username: str = ""
    http_password: str = ""
    https_username: str = ""
    https_password: str = ""


@dataclass(frozen=True)
class ConnectionConfig:
    timeout: float = 60.0
    verify_ssl: bool = True
    trust_env: bool = False
    user_agent: str = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    accept_language: str = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
    request_min_interval: float = 1.2
    request_jitter: float = 0.6
    retry_count: int = 2
    retry_backoff: float = 2.0
    cookies_file: Path = Path("data/http-cookies.txt")
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> "ConnectionConfig":
        path = Path(path)
        raw = _read_yaml(path)
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "ConnectionConfig":
        raw = raw or {}
        proxy_raw = raw.get("proxy") or {}
        cookies_raw = raw.get("cookies") or {}
        return cls(
            timeout=float(raw.get("timeout", 60)),
            verify_ssl=bool(raw.get("verify-ssl", raw.get("verify_ssl", True))),
            trust_env=bool(raw.get("trust-env", raw.get("trust_env", False))),
            user_agent=str(raw.get("user-agent", raw.get("user_agent", cls.user_agent))).strip(),
            accept_language=str(raw.get("accept-language", raw.get("accept_language", cls.accept_language))).strip(),
            request_min_interval=float(raw.get("request-min-interval", raw.get("request_min_interval", 1.2))),
            request_jitter=float(raw.get("request-jitter", raw.get("request_jitter", 0.6))),
            retry_count=int(raw.get("retry-count", raw.get("retry_count", 2))),
            retry_backoff=float(raw.get("retry-backoff", raw.get("retry_backoff", 2.0))),
            cookies_file=Path(cookies_raw.get("file", raw.get("cookies-file", "data/http-cookies.txt"))).expanduser(),
            proxy=ProxyConfig(
                enabled=bool(proxy_raw.get("enabled", False)),
                default=str(proxy_raw.get("default", "")).strip(),
                http=str(proxy_raw.get("http", "")).strip(),
                https=str(proxy_raw.get("https", "")).strip(),
                no_proxy=str(proxy_raw.get("no-proxy", proxy_raw.get("no_proxy", ""))).strip(),
                username=str(proxy_raw.get("username", "")).strip(),
                password=str(proxy_raw.get("password", "")).strip(),
                http_username=str(proxy_raw.get("http-username", proxy_raw.get("http_username", "")).strip()),
                http_password=str(proxy_raw.get("http-password", proxy_raw.get("http_password", "")).strip()),
                https_username=str(proxy_raw.get("https-username", proxy_raw.get("https_username", "")).strip()),
                https_password=str(proxy_raw.get("https-password", proxy_raw.get("https_password", "")).strip()),
            ),
        )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConnectionError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConnectionError(f"Config file must contain a mapping: {path}")
    return data


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"\d{1,6}", value):
        return max(0.0, float(value))
    try:
        retry_at = parsedate_to_datetime(value)
        return max(0.0, retry_at.timestamp() - time.time())
    except (TypeError, ValueError, OSError):
        return None


def proxy_url_with_auth(proxy_url: str, username: str = "", password: str = "") -> str:
    proxy_url = str(proxy_url or "").strip()
    if not proxy_url:
        return ""

    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        raise ConnectionError(f"Invalid proxy URL: {proxy_url}")
    if parsed.scheme not in {"http", "https", "socks5", "socks5h", "socks4", "socks4a"}:
        raise ConnectionError(f"Unsupported proxy scheme {parsed.scheme!r}; use http, https or socks5/socks5h")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ConnectionError(f"Invalid proxy port: {proxy_url}") from exc

    if parsed.username is not None:
        return proxy_url
    if not username and not password:
        return proxy_url
    if not username:
        raise ConnectionError(f"Proxy password is set but username is empty for {proxy_url}")

    auth = quote(username, safe="")
    if password:
        auth += ":" + quote(password, safe="")

    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{auth}@{host}"
    if port is not None:
        netloc += f":{port}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def build_proxies(config: ProxyConfig) -> dict[str, str]:
    if not config.enabled:
        return {}

    proxies: dict[str, str] = {}
    default = proxy_url_with_auth(config.default, config.username, config.password) if config.default else ""
    if default:
        proxies["http"] = default
        proxies["https"] = default

    if config.http:
        proxies["http"] = proxy_url_with_auth(
            config.http,
            config.http_username or config.username,
            config.http_password or config.password,
        )
    if config.https:
        proxies["https"] = proxy_url_with_auth(
            config.https,
            config.https_username or config.username,
            config.https_password or config.password,
        )
    if config.no_proxy:
        proxies["no_proxy"] = config.no_proxy
    return proxies


def sanitize_proxy_url(proxy_url: str) -> str:
    parsed = urlsplit(proxy_url)
    if parsed.username is None and parsed.password is None:
        return proxy_url
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = "***:***@" + host
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        netloc += f":{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def describe_proxies(proxies: dict[str, str]) -> str:
    if not proxies:
        return "direct"
    parts: list[str] = []
    for key in ("http", "https", "no_proxy"):
        value = proxies.get(key)
        if not value:
            continue
        parts.append(f"{key}={value if key == 'no_proxy' else sanitize_proxy_url(value)}")
    return ", ".join(parts) if parts else "direct"


class ManagedSession(requests.Session):
    """requests.Session with defaults, retry and polite request interval."""

    RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}
    RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, config: ConnectionConfig) -> None:
        super().__init__()
        self.config = config
        self.trust_env = config.trust_env
        self.verify = config.verify_ssl
        self.proxies.update(build_proxies(config.proxy))
        self.last_request_at = 0.0
        self.last_page_url = ""
        self.cookies = self._load_cookie_jar(config.cookies_file)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        method_upper = str(method or "GET").upper()
        kwargs.setdefault("timeout", self.config.timeout)
        kwargs["headers"] = self._headers(method_upper, str(url), kwargs.get("headers"))

        retryable = method_upper in self.RETRYABLE_METHODS
        attempts = max(0, self.config.retry_count) + 1
        last_exc: requests.RequestException | None = None

        for attempt in range(1, attempts + 1):
            self._polite_pause()
            try:
                response = super().request(method_upper, url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                if not retryable or attempt >= attempts:
                    raise
                self._sleep_before_retry(attempt, None)
                continue

            self._remember_page(response, method_upper)
            if not retryable or response.status_code not in self.RETRYABLE_STATUSES or attempt >= attempts:
                return response
            self._sleep_before_retry(attempt, response)

        if last_exc is not None:
            raise last_exc
        raise ConnectionError("HTTP request failed")

    def save_cookies(self) -> None:
        jar = self.cookies
        if not isinstance(jar, MozillaCookieJar):
            return
        self.config.cookies_file.parent.mkdir(parents=True, exist_ok=True)
        jar.save(ignore_discard=True, ignore_expires=True)
        try:
            self.config.cookies_file.chmod(0o600)
        except OSError:
            pass

    def get_cookie(self, name: str) -> str | None:
        for cookie in self.cookies:
            if cookie.name == name:
                return cookie.value
        return None

    @staticmethod
    def _load_cookie_jar(path: Path) -> MozillaCookieJar:
        jar = MozillaCookieJar(str(path))
        if path.exists():
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
            except (LoadError, OSError) as exc:
                backup = path.with_suffix(path.suffix + ".broken")
                try:
                    path.rename(backup)
                    print(f"WARNING: broken cookie file moved to {backup}: {exc}", file=sys.stderr)
                except OSError:
                    print(f"WARNING: broken cookie file ignored: {path}: {exc}", file=sys.stderr)
        return jar

    def _headers(self, method: str, url: str, headers: object) -> dict[str, str]:
        supplied: dict[str, str] = dict(headers or {})  # type: ignore[arg-type]
        lower = {key.casefold() for key in supplied}
        result: dict[str, str] = {}
        if "user-agent" not in lower:
            result["User-Agent"] = self.config.user_agent
        if "accept-language" not in lower:
            result["Accept-Language"] = self.config.accept_language
        if "accept-encoding" not in lower:
            result["Accept-Encoding"] = "gzip, deflate"
        if "connection" not in lower:
            result["Connection"] = "keep-alive"
        if "accept" not in lower:
            result["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        if method == "GET" and "cache-control" not in lower:
            result["Cache-Control"] = "max-age=0"
        result.update(supplied)
        return result

    def _polite_pause(self) -> None:
        min_interval = max(0.0, self.config.request_min_interval)
        jitter = max(0.0, self.config.request_jitter)
        target = min_interval + (random.uniform(0, jitter) if jitter else 0)
        if target <= 0:
            self.last_request_at = time.monotonic()
            return
        now = time.monotonic()
        elapsed = now - self.last_request_at if self.last_request_at else target
        wait = target - elapsed
        if wait > 0:
            time.sleep(wait)
        self.last_request_at = time.monotonic()

    def _sleep_before_retry(self, attempt: int, response: requests.Response | None) -> None:
        retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After") if response is not None else None)
        if retry_after is None:
            retry_after = max(0.0, self.config.retry_backoff) * attempt
        if retry_after > 0:
            time.sleep(min(retry_after, 120.0))

    def _remember_page(self, response: requests.Response, method: str) -> None:
        if method == "GET" and response.ok and "text/html" in response.headers.get("content-type", "").casefold():
            self.last_page_url = response.url


class Connection:
    """Small facade that owns a configured ManagedSession."""

    def __init__(self, config: ConnectionConfig) -> None:
        self.config = config
        self.session = ManagedSession(config)

    @classmethod
    def from_file(cls, path: str | Path = "configs/conn.yml") -> "Connection":
        return cls(ConnectionConfig.from_file(path))

    @property
    def proxies(self) -> dict[str, str]:
        return dict(self.session.proxies)

    def proxy_status(self) -> str:
        return describe_proxies(self.proxies)

    def save_cookies(self) -> None:
        self.session.save_cookies()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        return self.session.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)
