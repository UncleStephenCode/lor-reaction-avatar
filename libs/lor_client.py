#!/usr/bin/env python3
"""Minimal LOR client for reaction-rate avatar updates.

Kept intentionally narrow:
  * login and keep cookies;
  * read first /notifications page without resetting notifications;
  * detect configured hype reactions;
  * render configured reaction rates, including +0 inactive rows, on the avatar;
  * upload the generated avatar through the LOR userpic form.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from io import BytesIO
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import requests
import yaml

from .connection import Connection, ConnectionError


REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_REACTIONS = ["👍", "😊", "☕☕", "🎉"]


class LorError(RuntimeError):
    """LOR-specific error."""


@dataclass(frozen=True)
class AvatarUploadConfig:
    form_url: str = "/addphoto.jsp"
    file_field: str = "file"
    submit_field: str = ""
    submit_value: str = ""
    extra_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AvatarConfig:
    source_dir: Path = Path("avatar")
    output_dir: Path = Path("data/generated-avatar")
    default_size: tuple[int, int] = (300, 300)
    font: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    emoji_font: str = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
    emoji_font_size: int = 30
    emoji_spacing: int = 2
    font_size: int = 28
    font_color: str = "#00a000"
    emoji_color: str = "#111111"
    right_padding: int = 14
    top_padding: int = 72
    line_spacing: int = 10
    output_format: str = "png"
    max_file_size_kb: int = 100
    jpeg_quality: int = 90
    upload: AvatarUploadConfig = field(default_factory=AvatarUploadConfig)


@dataclass(frozen=True)
class RunnerConfig:
    runs_per_hour: int = 4
    max_runs: int = 0
    run_on_start: bool = True
    dry_run: bool = False


@dataclass(frozen=True)
class LorConfig:
    base_url: str = "https://www.linux.org.ru"
    username: str = "your_username"
    password: str = "change_me"
    reactions: list[str] = field(default_factory=lambda: list(DEFAULT_REACTIONS))
    notifications_path: str = "/notifications"
    state_file: Path = Path("data/reaction-state.json")
    history_hours: int = 3
    avatar: AvatarConfig = field(default_factory=AvatarConfig)
    runner: RunnerConfig = field(default_factory=RunnerConfig)

    @classmethod
    def from_file(cls, path: str | Path = "configs/user.yml") -> "LorConfig":
        path = Path(path)
        if not path.exists():
            raise LorError(f"Config file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise LorError(f"Config file must contain a mapping: {path}")
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "LorConfig":
        lor_raw = raw.get("lor") or {}
        avatar_raw = raw.get("avatar") or {}
        upload_raw = avatar_raw.get("upload") or {}
        runner_raw = raw.get("runner") or {}
        state_raw = raw.get("state") or {}

        default_size_raw = avatar_raw.get("default-size", avatar_raw.get("default_size", [300, 300]))
        try:
            default_size = (int(default_size_raw[0]), int(default_size_raw[1]))
        except Exception as exc:
            raise LorError("avatar.default-size must be a two-item list, for example [300, 300]") from exc

        reactions = lor_raw.get("reactions", DEFAULT_REACTIONS)
        if isinstance(reactions, str):
            reactions = [item.strip() for item in reactions.split(",") if item.strip()]
        if not reactions:
            reactions = list(DEFAULT_REACTIONS)

        return cls(
            base_url=normalize_base_url(str(lor_raw.get("base-url", lor_raw.get("base_url", "https://www.linux.org.ru")))),
            username=str(lor_raw.get("username", "your_username")).strip(),
            password=str(lor_raw.get("password", "change_me")),
            reactions=[str(item) for item in reactions],
            notifications_path=str(lor_raw.get("notifications-path", lor_raw.get("notifications_path", "/notifications"))).strip() or "/notifications",
            state_file=Path(state_raw.get("file", "data/reaction-state.json")).expanduser(),
            history_hours=int(state_raw.get("history-hours", state_raw.get("history_hours", 3))),
            avatar=AvatarConfig(
                source_dir=Path(avatar_raw.get("source-dir", avatar_raw.get("source_dir", "avatar"))).expanduser(),
                output_dir=Path(avatar_raw.get("output-dir", avatar_raw.get("output_dir", "data/generated-avatar"))).expanduser(),
                default_size=default_size,
                font=str(avatar_raw.get("font", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")),
                emoji_font=str(avatar_raw.get("emoji-font", avatar_raw.get("emoji_font", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"))),
                emoji_font_size=max(8, int(avatar_raw.get("emoji-font-size", avatar_raw.get("emoji_font_size", 30)))),
                emoji_spacing=max(0, int(avatar_raw.get("emoji-spacing", avatar_raw.get("emoji_spacing", 2)))),
                font_size=int(avatar_raw.get("font-size", avatar_raw.get("font_size", 28))),
                font_color=str(avatar_raw.get("font-color", avatar_raw.get("font_color", "#00a000"))),
                emoji_color=str(avatar_raw.get("emoji-color", avatar_raw.get("emoji_color", "#111111"))),
                right_padding=int(avatar_raw.get("right-padding", avatar_raw.get("right_padding", 14))),
                top_padding=int(avatar_raw.get("top-padding", avatar_raw.get("top_padding", 72))),
                line_spacing=int(avatar_raw.get("line-spacing", avatar_raw.get("line_spacing", 10))),
                output_format=str(avatar_raw.get("output-format", avatar_raw.get("output_format", "png"))).strip().lower() or "png",
                max_file_size_kb=max(1, int(avatar_raw.get("max-file-size-kb", avatar_raw.get("max_file_size_kb", 100)))),
                jpeg_quality=max(35, min(95, int(avatar_raw.get("jpeg-quality", avatar_raw.get("jpeg_quality", 90))))),
                upload=AvatarUploadConfig(
                    form_url=str(upload_raw.get("form-url", upload_raw.get("form_url", "/addphoto.jsp"))).strip() or "/addphoto.jsp",
                    file_field=str(upload_raw.get("file-field", upload_raw.get("file_field", "file"))).strip() or "file",
                    submit_field=str(upload_raw.get("submit-field", upload_raw.get("submit_field", ""))).strip(),
                    submit_value=str(upload_raw.get("submit-value", upload_raw.get("submit_value", ""))),
                    extra_fields={str(k): str(v) for k, v in (upload_raw.get("extra-fields", upload_raw.get("extra_fields", {})) or {}).items()},
                ),
            ),
            runner=RunnerConfig(
                runs_per_hour=max(1, int(runner_raw.get("runs-per-hour", runner_raw.get("runs_per_hour", 4)))),
                max_runs=max(0, int(runner_raw.get("max-runs", runner_raw.get("max_runs", 0)))),
                run_on_start=bool(runner_raw.get("run-on-start", runner_raw.get("run_on_start", True))),
                dry_run=bool(runner_raw.get("dry-run", runner_raw.get("dry_run", False))),
            ),
        )


@dataclass(frozen=True)
class ReactionStats:
    counts: dict[str, int]
    rates: dict[str, int]
    avatar_path: Path | None = None
    uploaded: bool = False
    changed: bool = False


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        raise LorError(f"base-url must be absolute URL, got: {value!r}")
    return value


def strip_html(text: str, limit: int = 1500) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)


class LorClient:
    def __init__(self, config: LorConfig, connection: Connection) -> None:
        self.config = config
        self.connection = connection
        self.session = connection.session

    @classmethod
    def from_files(
        cls,
        user_config_path: str | Path = "configs/user.yml",
        connection_config_path: str | Path = "configs/conn.yml",
    ) -> "LorClient":
        return cls(LorConfig.from_file(user_config_path), Connection.from_file(connection_config_path))

    def make_url(self, path: str) -> str:
        return urljoin(self.config.base_url.rstrip("/") + "/", path.lstrip("/"))

    @staticmethod
    def origin_from_url(url: str) -> str:
        parsed = urlsplit(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def get_cookie(self, name: str) -> str | None:
        return self.session.get_cookie(name)

    def save_cookies(self) -> None:
        self.connection.save_cookies()

    def fetch_csrf(self, path: str) -> tuple[str, str]:
        response = self.session.get(self.make_url(path))
        response.raise_for_status()
        token = self.get_cookie("CSRF_TOKEN")
        if not token:
            soup = BeautifulSoup(response.text, "html.parser")
            node = soup.find("input", attrs={"name": "csrf"})
            token = str(node.get("value") or "") if node is not None else ""
        if not token:
            raise LorError("LOR did not return CSRF token; check base-url/auth/proxy")
        return token, response.url

    def login(self) -> None:
        if not self.config.username or self.config.username == "your_username":
            raise LorError("Set lor.username in configs/user.yml")
        if not self.config.password or self.config.password == "change_me":
            raise LorError("Set lor.password in configs/user.yml")

        csrf, referer = self.fetch_csrf("/login.jsp")
        post_url = urljoin(referer, "/login_process")
        response = self.session.post(
            post_url,
            data={
                "nick": self.config.username,
                "passwd": self.config.password,
                "redirectUrl": "/",
                "csrf": csrf,
            },
            headers={"Referer": referer, "Origin": self.origin_from_url(referer)},
            allow_redirects=False,
        )
        if response.status_code in REDIRECT_STATUSES:
            response = self.session.get(urljoin(referer, response.headers.get("Location", "/")), headers={"Referer": referer})
        response.raise_for_status()
        if not self.get_cookie("remember_me"):
            raise LorError("Login failed: remember_me cookie was not set. Server response: " + strip_html(response.text))
        self.save_cookies()

    def ensure_authorized(self) -> None:
        if self.get_cookie("remember_me"):
            return
        self.login()

    def fetch_notifications_html(self) -> tuple[str, str]:
        self.ensure_authorized()
        response = self.session.get(self.make_url(self.config.notifications_path))
        response.raise_for_status()
        if urlsplit(response.url).path.endswith("/login.jsp"):
            raise LorError("/notifications returned login page; check credentials/cookies")
        self.save_cookies()
        return response.text, response.url

    def scan_reactions_from_notifications(self) -> dict[str, int]:
        html_text, page_url = self.fetch_notifications_html()
        return parse_reaction_notifications(html_text, page_url, self.config.reactions)

    def calculate_rates(self, counts: dict[str, int]) -> tuple[dict[str, int], dict[str, Any], list[dict[str, Any]]]:
        """Calculate hourly rates without writing state yet.

        On the very first run, when reaction-state.json does not exist yet, the
        current total counters are treated as rates. This makes the first
        generated avatar show the already accumulated reaction totals instead of
        an empty +0 baseline.
        """
        now = time.time()
        state_file_exists = self.config.state_file.exists()
        state = load_json(self.config.state_file)
        history = [item for item in state.get("history", []) if isinstance(item, dict)]
        keep_after = now - max(2, self.config.history_hours) * 3600
        history = [item for item in history if float(item.get("at", 0) or 0) >= keep_after]

        first_state_run = not state_file_exists and not history
        if first_state_run:
            rates = {
                reaction: max(0, int(counts.get(reaction, 0) or 0))
                for reaction in self.config.reactions
            }
            state["first_run"] = True
        else:
            baseline = choose_baseline(history, now - 3600)
            rates = {}
            if baseline is not None:
                baseline_counts = baseline.get("counts", {}) if isinstance(baseline.get("counts", {}), dict) else {}
                for reaction in self.config.reactions:
                    delta = int(counts.get(reaction, 0)) - int(baseline_counts.get(reaction, 0) or 0)
                    rates[reaction] = max(0, delta)
            else:
                rates = {reaction: 0 for reaction in self.config.reactions}
            state["first_run"] = False

        history.append({"at": now, "counts": {reaction: int(counts.get(reaction, 0)) for reaction in self.config.reactions}})
        return rates, state, history

    def save_reaction_state(self, counts: dict[str, int], rates: dict[str, int], state: dict[str, Any], history: list[dict[str, Any]]) -> None:
        state["history"] = history
        state["last_counts"] = counts
        state["last_rates"] = rates
        save_json(self.config.state_file, state)

    def update_state_and_get_rates(self, counts: dict[str, int]) -> dict[str, int]:
        rates, state, history = self.calculate_rates(counts)
        self.save_reaction_state(counts, rates, state, history)
        return rates

    def render_avatar(self, rates: dict[str, int]) -> Path:
        base_path = self.get_or_create_base_avatar()
        output_ext = normalize_image_format(self.config.avatar.output_format)
        output_path = self.config.avatar.output_dir / f"{self.config.username}.{output_ext}"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        render_rate_avatar(
            base_path=base_path,
            output_path=output_path,
            rates=rates,
            reactions=self.config.reactions,
            avatar_config=self.config.avatar,
        )
        return output_path

    def get_or_create_base_avatar(self) -> Path:
        path = self.config.avatar.source_dir / f"{self.config.username}.jpg"
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", self.config.avatar.default_size, "white")
        image.save(path, "JPEG", quality=95)
        return path

    def upload_avatar(self, image_path: Path) -> None:
        self.ensure_authorized()
        upload_cfg = self.config.avatar.upload
        response = self._get_avatar_upload_form(upload_cfg)
        response.raise_for_status()
        if urlsplit(response.url).path.endswith("/login.jsp"):
            raise LorError("Avatar upload form returned login page; check authorization/cookies")

        soup = BeautifulSoup(response.text, "html.parser")
        form = find_upload_form(soup, upload_cfg.file_field)
        if form is None:
            raise LorError(f"Could not find avatar upload form with file input on {response.url}")

        data = extract_form_fields(form)
        data.update(upload_cfg.extra_fields)
        csrf = self.get_cookie("CSRF_TOKEN")
        if csrf and "csrf" not in data:
            data["csrf"] = csrf
        if upload_cfg.submit_field:
            data[upload_cfg.submit_field] = upload_cfg.submit_value or "true"

        file_field = upload_cfg.file_field or find_file_field(form) or "file"
        action = str(form.get("action") or response.url)
        post_url = urljoin(response.url, action)

        with image_path.open("rb") as fh:
            files = {file_field: (image_path.name, fh, mime_type_for_path(image_path))}
            post_response = self.session.post(
                post_url,
                data=data,
                files=files,
                headers={"Referer": response.url, "Origin": self.origin_from_url(response.url)},
                allow_redirects=False,
            )

        if post_response.status_code in REDIRECT_STATUSES:
            self.save_cookies()
            return

        post_response.raise_for_status()
        plain = strip_html(post_response.text)
        if "ошиб" in plain.casefold() or urlsplit(post_response.url).path.endswith("/login.jsp"):
            raise LorError("Avatar upload may have failed: " + plain)
        self.save_cookies()

    def _get_avatar_upload_form(self, upload_cfg: AvatarUploadConfig) -> requests.Response:
        """Open the LOR userpic upload form.

        Current lorsource maps userpic upload to /addphoto.jsp, not
        /edit-profile.jsp.  Keep a fallback so old configs with
        avatar.upload.form-url: /edit-profile.jsp do not break the runner.
        """
        configured_url = self.make_url(upload_cfg.form_url)
        response = self.session.get(configured_url)
        if response.status_code != 404:
            return response

        configured_path = urlsplit(configured_url).path.rstrip("/")
        if configured_path == "/edit-profile.jsp":
            fallback_url = self.make_url("/addphoto.jsp")
            fallback_response = self.session.get(
                fallback_url,
                headers={"Referer": configured_url},
            )
            if fallback_response.status_code != 404:
                return fallback_response

        raise LorError(
            f"Avatar upload form returned HTTP 404: {configured_url}. "
            "According to current lorsource, avatar upload must use /addphoto.jsp "
            "with multipart field 'file'. Check configs/user.yml avatar.upload.form-url."
        )

    def run_once(self) -> ReactionStats:
        counts = self.scan_reactions_from_notifications()
        rates, state, history = self.calculate_rates(counts)
        normalized_rates = normalize_rates_for_reactions(rates, self.config.reactions)
        has_positive_rate = any(value > 0 for value in normalized_rates.values())
        update_activity_state(state, has_positive_rate)
        log_activity_state(state, normalized_rates)

        # Always render all configured reactions.  When there is no hourly rate,
        # the local avatar still shows every configured emoji with +0, but it is
        # not uploaded to LOR again.  This keeps the generated file/state/logs up
        # to date without hammering the profile upload endpoint when nothing
        # changed from the user's perspective.
        avatar_path = self.render_avatar(normalized_rates)
        uploaded = False
        if has_positive_rate and not self.config.runner.dry_run:
            try:
                self.upload_avatar(avatar_path)
                uploaded = True
            except (LorError, requests.RequestException) as exc:
                # Do not crash/restart after the avatar has already been rendered.
                # The local avatar path is still returned in JSON for diagnostics
                # and manual upload.
                print(f"WARNING: avatar upload skipped: {exc}", flush=True)
        elif not has_positive_rate:
            print("avatar upload skipped: no reaction rate changes", flush=True)
        self.save_reaction_state(counts, normalized_rates, state, history)
        remember_uploaded_avatar(self.config.state_file, avatar_path, uploaded)
        return ReactionStats(
            counts=counts,
            rates=normalized_rates,
            avatar_path=avatar_path,
            uploaded=uploaded,
            changed=has_positive_rate,
        )


def parse_reaction_notifications(html_text: str, page_url: str, reactions: list[str]) -> dict[str, int]:
    soup = BeautifulSoup(html_text, "html.parser")
    result = {reaction: 0 for reaction in reactions}

    # New LOR UI commonly wraps events into .notifications-item; old pages may use rows.
    nodes = soup.select(".notifications-item, tr, li, article, .event")
    if not nodes:
        nodes = [soup.body or soup]

    # Longest first: ☕☕ must be detected before a single coffee glyph.
    ordered_reactions = sorted(reactions, key=len, reverse=True)

    for node in nodes:
        text = clean_text(node.get_text(" "))
        if not text:
            continue
        for reaction in ordered_reactions:
            if reaction not in text:
                continue
            result[reaction] += count_reaction_in_text(text, reaction, node)
    return result


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def count_reaction_in_text(text: str, reaction: str, node: Any) -> int:
    escaped = re.escape(reaction)
    patterns = [
        rf"(?:\+|плюс\s*)?(\d{{1,5}})\s*{escaped}",
        rf"{escaped}\s*(?:\+|x|×)?\s*(\d{{1,5}})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return max(1, int(match.group(1)))

    count_node = None
    try:
        count_node = node.select_one(".notifications-number, .count, .number")
    except Exception:
        count_node = None
    if count_node is not None:
        number = first_int(clean_text(count_node.get_text(" ")))
        if number is not None:
            return max(1, number)
    return max(1, text.count(reaction))


def first_int(text: str) -> int | None:
    match = re.search(r"\d{1,6}", text or "")
    return int(match.group(0)) if match else None


def choose_baseline(history: list[dict[str, Any]], target_ts: float) -> dict[str, Any] | None:
    if not history:
        return None
    older = [item for item in history if float(item.get("at", 0) or 0) <= target_ts]
    if older:
        return max(older, key=lambda item: float(item.get("at", 0) or 0))
    # Before the first full hour, use the oldest previous snapshot. If there is
    # only the current run, caller has not appended it yet, so this returns None.
    return min(history, key=lambda item: float(item.get("at", 0) or 0)) if len(history) >= 1 else None


def normalize_rates_for_reactions(rates: dict[str, int], reactions: list[str]) -> dict[str, int]:
    return {reaction: max(0, int(rates.get(reaction, 0) or 0)) for reaction in reactions}


def update_activity_state(state: dict[str, Any], has_positive_rate: bool) -> None:
    now = time.time()
    state["last_scan_at"] = now
    if has_positive_rate:
        state["last_activity_at"] = now


def log_activity_state(state: dict[str, Any], rates: dict[str, int]) -> None:
    now = float(state.get("last_scan_at") or time.time())
    last_activity_at = state.get("last_activity_at")
    rate_sum = sum(max(0, int(value or 0)) for value in rates.values())

    if last_activity_at:
        last_activity_at = float(last_activity_at)
        inactive_for = max(0.0, now - last_activity_at)
        inactive_text = format_duration(inactive_for)
        last_activity_text = format_local_time(last_activity_at)
    else:
        inactive_text = "never"
        last_activity_text = "never"

    print(
        "activity: "
        f"at={format_local_time(now)} "
        f"active={'yes' if rate_sum > 0 else 'no'} "
        f"rate_sum={rate_sum} "
        f"last_activity_at={last_activity_text} "
        f"inactive_for={inactive_text}",
        flush=True,
    )


def format_local_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S %Z")


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return "".join(parts)


def render_rate_avatar(
    *,
    base_path: Path,
    output_path: Path,
    rates: dict[str, int],
    reactions: list[str],
    avatar_config: AvatarConfig,
) -> None:
    """Render reaction rates with site-like color emoji.

    Color emoji fonts such as NotoColorEmoji have unusual metrics in Pillow:
    drawing them directly into the final image often shifts/clips glyphs.  To
    keep the result close to what the browser shows on LOR, each emoji is first
    rendered into a large transparent scratch canvas with embedded colors, then
    cropped by its real alpha bbox and pasted into the final avatar as an image.
    """
    image = Image.open(base_path).convert("RGBA")
    draw = ImageDraw.Draw(image)
    number_font = load_font(avatar_config.font, avatar_config.font_size)
    emoji_font, emoji_loaded_from_color_font = load_emoji_font(
        avatar_config.emoji_font,
        avatar_config.emoji_font_size or avatar_config.font_size,
    )

    lines = [(reaction, int(rates.get(reaction, 0) or 0)) for reaction in reactions]
    y = avatar_config.top_padding
    right = image.width - avatar_config.right_padding
    gap = max(4, avatar_config.font_size // 5)

    for reaction, value in lines:
        number_text = f"+{value:>2}"
        number_box = draw.textbbox((0, 0), number_text, font=number_font)
        number_width = number_box[2] - number_box[0]
        number_height = number_box[3] - number_box[1]

        emoji_image = render_reaction_emoji_image(
            reaction,
            emoji_font,
            avatar_config.emoji_font_size or avatar_config.font_size,
            fallback_color=avatar_config.emoji_color,
            spacing=avatar_config.emoji_spacing,
        )
        emoji_width, emoji_height = emoji_image.size
        line_height = max(number_height, emoji_height)
        total_width = number_width + gap + emoji_width
        x = max(0, right - total_width)

        text_y = y + max(0, (line_height - number_height) // 2) - 2
        emoji_y = y + max(0, (line_height - emoji_height) // 2)
        draw.text((x, text_y), number_text, font=number_font, fill=avatar_config.font_color)
        image.alpha_composite(emoji_image, (int(x + number_width + gap), int(emoji_y)))
        y += line_height + avatar_config.line_spacing

    save_avatar_image(image, output_path, avatar_config)


def normalize_image_format(value: str) -> str:
    fmt = str(value or "png").strip().lower().lstrip(".")
    aliases = {"jpeg": "jpg", "tif": "tiff"}
    fmt = aliases.get(fmt, fmt)
    if fmt not in {"png", "jpg", "tiff"}:
        raise LorError(f"Unsupported avatar output-format {value!r}. Supported: png, jpg, tiff")
    return fmt


def mime_type_for_path(path: Path) -> str:
    suffix = normalize_image_format(path.suffix or "png")
    if suffix == "jpg":
        return "image/jpeg"
    if suffix == "png":
        return "image/png"
    if suffix == "tiff":
        return "image/tiff"
    return "application/octet-stream"


def save_avatar_image(image: Image.Image, output_path: Path, avatar_config: AvatarConfig) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = normalize_image_format(output_path.suffix or avatar_config.output_format)
    limit = max(1, avatar_config.max_file_size_kb) * 1024

    if fmt == "png":
        candidate = image.convert("RGBA")
        candidate.save(output_path, "PNG", optimize=True, compress_level=9)
        if output_path.stat().st_size > limit:
            # Palette PNG often keeps a 300x300 avatar under 100 KiB.
            palette = candidate.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
            palette.save(output_path, "PNG", optimize=True, compress_level=9)
        if output_path.stat().st_size > limit:
            palette = candidate.convert("P", palette=Image.Palette.ADAPTIVE, colors=128)
            palette.save(output_path, "PNG", optimize=True, compress_level=9)
    elif fmt == "jpg":
        rgb = image.convert("RGB")
        quality = avatar_config.jpeg_quality
        while True:
            rgb.save(output_path, "JPEG", quality=quality, optimize=True, progressive=True)
            if output_path.stat().st_size <= limit or quality <= 45:
                break
            quality -= 5
    else:
        image.convert("RGB").save(output_path, "TIFF", compression="tiff_deflate")

    size = output_path.stat().st_size
    if size > limit:
        raise LorError(
            f"Generated avatar {output_path} is {size} bytes, limit is {limit} bytes. "
            "Use output-format: png or jpg, reduce font-size, or simplify the base image."
        )


def load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if font_path:
        candidates.append(font_path)
    candidates.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ])

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def load_emoji_font(font_path: str, display_size: int) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, bool]:
    """Load emoji font, handling bitmap-only NotoColorEmoji sizes.

    NotoColorEmoji is often available only at fixed bitmap strikes.  Pillow may
    raise "invalid pixel size" for 28/30 px.  We therefore try common bitmap
    strikes and downscale the cropped glyph image afterwards.
    """
    candidates: list[tuple[str, int]] = []
    paths = []
    if font_path:
        paths.append(font_path)
    paths.extend([
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "/usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf",
        "NotoColorEmoji.ttf",
    ])
    sizes = [display_size, 109, 136, 128, 72, 64, 48, 32, 30, 28]
    for path in paths:
        for size in sizes:
            candidates.append((path, size))

    seen: set[tuple[str, int]] = set()
    for path, size in candidates:
        key = (path, size)
        if key in seen:
            continue
        seen.add(key)
        try:
            return ImageFont.truetype(path, size=size), "NotoColorEmoji" in path
        except OSError:
            continue

    return load_font("", display_size), False


def render_reaction_emoji_image(
    reaction: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    display_size: int,
    *,
    fallback_color: str,
    spacing: int = 2,
) -> Image.Image:
    """Render a full LOR reaction as one image without clipping.

    Some LOR reactions are made from several emoji codepoints, for example
    ``☕☕``.  Rendering that string in one Pillow call is unreliable with
    NotoColorEmoji: the bitmap strike can be much wider than the scratch canvas,
    so the second glyph may be clipped before we crop it.  Render every emoji
    unit separately and then compose them horizontally.
    """
    units = split_reaction_emoji_units(reaction)
    pieces = [
        render_emoji_image(unit, font, display_size, fallback_color=fallback_color)
        for unit in units
    ]
    pieces = [piece for piece in pieces if piece.width > 0 and piece.height > 0]
    if not pieces:
        return render_emoji_image(reaction, font, display_size, fallback_color=fallback_color)

    spacing = max(0, int(spacing))
    width = sum(piece.width for piece in pieces) + spacing * (len(pieces) - 1)
    height = max(piece.height for piece in pieces)
    result = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    x = 0
    for piece in pieces:
        y = max(0, (height - piece.height) // 2)
        result.alpha_composite(piece, (int(x), int(y)))
        x += piece.width + spacing
    return result


def split_reaction_emoji_units(reaction: str) -> list[str]:
    """Split a reaction into emoji display units.

    This deliberately avoids a heavyweight dependency.  It handles the cases
    used by LOR reactions, including variation selectors, skin-tone modifiers
    and simple ZWJ sequences, while splitting ``☕☕`` into two cups.
    """
    text = str(reaction or "")
    if not text:
        return []

    units: list[str] = []
    current = ""
    join_next = False

    for char in text:
        code = ord(char)
        is_variation_selector = 0xFE00 <= code <= 0xFE0F
        is_skin_tone = 0x1F3FB <= code <= 0x1F3FF
        is_combining = 0x0300 <= code <= 0x036F

        if not current:
            current = char
        elif join_next or is_variation_selector or is_skin_tone or is_combining:
            current += char
        else:
            units.append(current)
            current = char

        join_next = char == "\u200d"

    if current:
        units.append(current)
    return units


def render_emoji_image(
    reaction: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    display_size: int,
    *,
    fallback_color: str,
) -> Image.Image:
    display_size = max(10, int(display_size))
    # Use a very generous scratch canvas.  Color emoji fonts often load as a
    # fixed 109/136 px bitmap strike even when the configured display size is
    # 22-32 px, and drawing near the canvas edge clips the glyph before crop.
    canvas_size = max(display_size * 16, 512)
    scratch = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    d = ImageDraw.Draw(scratch)
    draw_at = (canvas_size // 3, canvas_size // 3)
    try:
        d.text(draw_at, reaction, font=font, fill=fallback_color, embedded_color=True)
    except TypeError:
        d.text(draw_at, reaction, font=font, fill=fallback_color)

    bbox = scratch.getbbox()
    if bbox is None:
        # Last-resort visible placeholder; should happen only if font lookup is
        # broken or the glyph is missing.
        fallback = Image.new("RGBA", (display_size, display_size), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fallback)
        fd.rounded_rectangle((0, 0, display_size - 1, display_size - 1), radius=max(2, display_size // 5), outline=fallback_color)
        fd.text((display_size // 4, 0), "?", fill=fallback_color)
        return fallback

    glyph = scratch.crop(bbox)
    # Normalize height to configured emoji-font-size while preserving aspect.
    if glyph.height != display_size:
        new_width = max(1, round(glyph.width * (display_size / glyph.height)))
        glyph = glyph.resize((new_width, display_size), Image.Resampling.LANCZOS)

    # Add transparent breathing room so antialiasing and browser-like color
    # glyphs never touch the image edge.  This also prevents the final paste
    # from looking clipped when LOR recompresses the avatar.
    pad = max(1, round(display_size * 0.08))
    padded = Image.new("RGBA", (glyph.width + pad * 2, glyph.height + pad * 2), (0, 0, 0, 0))
    padded.alpha_composite(glyph, (pad, pad))
    return padded


def find_upload_form(soup: BeautifulSoup, preferred_file_field: str = "") -> Any | None:
    forms = soup.find_all("form")
    if preferred_file_field:
        for form in forms:
            if form.find("input", attrs={"name": preferred_file_field}):
                return form
    for form in forms:
        if form.find("input", attrs={"type": re.compile("file", re.I)}):
            return form
    return None


def find_file_field(form: Any) -> str:
    node = form.find("input", attrs={"type": re.compile("file", re.I)})
    return str(node.get("name") or "") if node is not None else ""


def extract_form_fields(form: Any) -> dict[str, str]:
    data: dict[str, str] = {}
    for input_node in form.find_all("input"):
        name = str(input_node.get("name") or "").strip()
        if not name:
            continue
        input_type = str(input_node.get("type") or "text").casefold()
        if input_type in {"file", "submit", "button", "image", "reset"}:
            continue
        if input_type in {"checkbox", "radio"} and input_node.get("checked") is None:
            continue
        data[name] = str(input_node.get("value") or "")

    for textarea in form.find_all("textarea"):
        name = str(textarea.get("name") or "").strip()
        if name:
            data[name] = textarea.get_text()

    for select in form.find_all("select"):
        name = str(select.get("name") or "").strip()
        if not name:
            continue
        selected = select.find("option", selected=True) or select.find("option")
        if selected is not None:
            data[name] = str(selected.get("value") or selected.get_text() or "")
    return data


def remember_uploaded_avatar(state_file: Path, avatar_path: Path, uploaded: bool) -> None:
    state = load_json(state_file)
    try:
        digest = hashlib.sha256(avatar_path.read_bytes()).hexdigest()
    except OSError:
        digest = ""
    state["last_avatar"] = {
        "path": str(avatar_path),
        "sha256": digest,
        "uploaded": uploaded,
        "at": time.time(),
    }
    save_json(state_file, state)