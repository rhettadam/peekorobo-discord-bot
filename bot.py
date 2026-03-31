"""
Peekorobo Discord bot: slash commands that call the Peekorobo HTTP API.

Environment:
  DISCORD_TOKEN       — Bot token from Discord Developer Portal
  PEEKOROBO_API_KEY   — Same X-API-Key as peekorobo.com / Swagger
  PEEKOROBO_API_BASE  — API origin (default https://api.peekorobo.com), no trailing slash

Install from repo root: pip install -e ".[discord]"
Run from repo root: python discord_bot/bot.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from dotenv import load_dotenv
import httpx

_bot_dir = Path(__file__).resolve().parent
load_dotenv(_bot_dir.parent / ".env")
load_dotenv(_bot_dir / ".env")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
API_KEY = os.environ.get("PEEKOROBO_API_KEY")
API_BASE = (os.environ.get("PEEKOROBO_API_BASE") or "https://api.peekorobo.com").rstrip("/")

# Styling (Peekorobo docs accent + Discord-friendly semantic colors)
COLOR_BRAND = 0x3366CC
COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_ERR = 0xED4245
FOOTER_TEXT = "Peekorobo · FRC data"
LOGO_URL = "https://www.peekorobo.com/assets/logo.png"
SITE_URL = "https://www.peekorobo.com"

# Default API limit for /peek_events (still paginated in Discord)
DEFAULT_EVENTS_LIMIT = 24

# Items per embed page (pagination buttons)
PAGE_RANKINGS = 15
PAGE_EVENTS = 3
PAGE_EVENT_KEYS = 30
PAGE_AWARDS = 10
PAGE_TEAM_EVENTS = 15
PAGE_EVENT_TEAMS = 45
PAGE_MATCHES = 8
PAGE_EVENT_PERFS = 12
PAGE_TEAM_SEASONS = 5
MAX_TEAM_SEASON_EVENT_PERFS = 12
MAX_REGISTERED_EVENTS_PER_SEASON = 50
EMBED_DESC_SAFE = 3800


def _require_env() -> None:
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if not API_KEY:
        missing.append("PEEKOROBO_API_KEY")
    if missing:
        print("Missing env: " + ", ".join(missing), file=sys.stderr)
        sys.exit(1)


def _base_embed(*, title: str | None = None, description: str | None = None, color: int = COLOR_BRAND) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color, url=SITE_URL)
    e.set_author(name="Peekorobo", url=SITE_URL, icon_url=LOGO_URL)
    e.set_footer(text=FOOTER_TEXT, icon_url=LOGO_URL)
    return e


def _fmt_num(x: Any, digits: int = 2) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
        if v == int(v):
            return str(int(v))
        return f"{v:.{digits}f}"
    except (TypeError, ValueError):
        return str(x)


def _short_iso_date(s: Any) -> str:
    if s is None:
        return "—"
    t = str(s)
    if len(t) >= 10:
        return t[:10]
    return t


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _normalize_website_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://")):
        return s
    return f"https://{s}"


def _year_from_event_key(event_key: str) -> int | None:
    """FRC event keys start with the season year, e.g. 2025txdal → 2025."""
    s = event_key.strip()
    if len(s) >= 4 and s[:4].isdigit():
        return int(s[:4])
    return None


def _detail_from_api(data: Any) -> str:
    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])
    return str(data)


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0 or not items:
        return [items] if items else []
    return [items[i : i + size] for i in range(0, len(items), size)]


def _build_event_general_embed(ev: dict[str, Any], event_key: str) -> discord.Embed:
    """Single-event card from GET /events/{year} `EventData` row."""
    meta = ev.get("event_data") or {}
    loc = ev.get("location_info") or {}
    name = str(meta.get("name") or event_key).strip()
    title = _truncate(name, 240) if name else f"Event · {event_key}"
    sd = _short_iso_date(meta.get("start_date"))
    ed = _short_iso_date(meta.get("end_date"))
    et = str(meta.get("event_type") or "").strip()
    city = str(loc.get("city") or "").strip()
    st = str(loc.get("state_prov") or "").strip()
    ctry = str(loc.get("country") or "").strip()
    loc_line = ", ".join(x for x in [city, st, ctry] if x)
    lines: list[str] = [f"`{event_key}`", f"{sd} → {ed}"]
    if et:
        lines.append(f"Type: **{et}**")
    if loc_line:
        lines.append(loc_line)
    site = ev.get("website")
    if site and str(site).strip():
        lines.append(f"[Event website]({_normalize_website_url(str(site))})")
    wt = ev.get("webcast_type")
    wch = ev.get("webcast_channel")
    if wt or wch:
        wc = " · ".join(str(x).strip() for x in [wt, wch] if x and str(x).strip())
        if wc:
            lines.append(f"Webcast: {wc}")
    body = "\n".join(lines)
    event_url = f"{SITE_URL}/event/{event_key}"
    e = discord.Embed(
        title=title,
        description=_truncate(body, EMBED_DESC_SAFE),
        color=COLOR_BRAND,
        url=event_url,
    )
    e.set_author(name="Peekorobo", url=SITE_URL, icon_url=LOGO_URL)
    e.set_footer(text=FOOTER_TEXT, icon_url=LOGO_URL)
    return e


def _embed_with_page_footer(
    *,
    title: str | None,
    description: str,
    color: int,
    page: int,
    total_pages: int,
    url: str | None = SITE_URL,
) -> discord.Embed:
    e = discord.Embed(title=title, description=_truncate(description, EMBED_DESC_SAFE), color=color, url=url)
    e.set_author(name="Peekorobo", url=SITE_URL, icon_url=LOGO_URL)
    e.set_footer(text=f"{FOOTER_TEXT} · Page {page}/{total_pages}", icon_url=LOGO_URL)
    return e


class EmbedPaginatorView(discord.ui.View):
    """◀ / page count / ▶ — only the command invoker can click."""

    def __init__(self, author_id: int, pages: list[discord.Embed], *, timeout: float = 600.0) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.pages = pages
        self.index = 0
        self.message: discord.Message | discord.WebhookMessage | None = None
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user is None or interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use these buttons.", ephemeral=True
            )
            return False
        return True

    def _sync_buttons(self) -> None:
        self.prev_btn.disabled = self.index <= 0
        self.next_btn.disabled = self.index >= len(self.pages) - 1
        mid = self.children[1]
        if isinstance(mid, discord.ui.Button):
            mid.label = f"{self.index + 1} / {len(self.pages)}"

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, row=0, disabled=True)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, discord.NotFound):
                pass


async def send_paginated(interaction: discord.Interaction, pages: list[discord.Embed], *, ephemeral: bool = False) -> None:
    if not pages:
        return
    assert interaction.user is not None
    if len(pages) == 1:
        await interaction.followup.send(embed=pages[0], ephemeral=ephemeral)
        return
    view = EmbedPaginatorView(interaction.user.id, pages)
    msg = await interaction.followup.send(embed=pages[0], view=view, ephemeral=ephemeral, wait=True)
    view.message = msg


def _build_ranking_pages(rows: list[dict[str, Any]], ek: str) -> list[discord.Embed]:
    chunks = _chunk(rows, PAGE_RANKINGS)
    if not chunks:
        return []
    n = len(chunks)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        lines = [f"{'#':>3}  {'Team':>5}  {'W–L–T':>9}  {'DQ':>2}"]
        for r in chunk:
            rank = r.get("rank")
            tn = r.get("team_number")
            w, l, t = r.get("wins"), r.get("losses"), r.get("ties")
            dq = r.get("dq", 0)
            lines.append(f"{rank:>3}  {tn:>5}  {w}–{l}–{t}  {dq:>2}")
        body = "```\n" + "\n".join(lines) + "\n```"
        if n > 1 or len(rows) > len(chunk):
            body += f"\n_{len(rows)} teams total._"
        out.append(
            _embed_with_page_footer(
                title=f"Rankings · {ek}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=f"{SITE_URL}/event/{ek}",
            )
        )
    return out


def _team_perf_year_key(p: dict[str, Any]) -> int:
    try:
        return int(p.get("year"))
    except (TypeError, ValueError):
        return -9999


def _format_team_profile(info: dict[str, Any] | None) -> str:
    """Markdown block from GET /teams `TeamData` (nickname, location, website, district)."""
    if not info:
        return ""
    lines: list[str] = []
    nick = str(info.get("nickname") or "").strip()
    if nick:
        lines.append(f"**{nick}**")
    city = str(info.get("city") or "").strip()
    st = str(info.get("state_prov") or "").strip()
    ctry = str(info.get("country") or "").strip()
    loc = ", ".join(x for x in [city, st, ctry] if x)
    if loc:
        lines.append(loc)
    site = _normalize_website_url(str(info.get("website") or ""))
    if site:
        lines.append(f"[Website]({site})")
    dk = info.get("district_key")
    if dk:
        lines.append(f"District `{dk}`")
    if not lines:
        return ""
    return "\n".join(lines) + "\n\n"


def _team_embed_title(tn: int, info: dict[str, Any] | None) -> str:
    if not info:
        return f"Team {tn}"
    nick = str(info.get("nickname") or "").strip()
    if nick:
        return _truncate(f"Team {tn} · {nick}", 240)
    return f"Team {tn}"


def _format_single_event_perf_line(ep: dict[str, Any]) -> str:
    ek = str(ep.get("event_key") or "?").strip()
    ace = _fmt_num(ep.get("ace"))
    raw = _fmt_num(ep.get("raw"))
    auto = _fmt_num(ep.get("auto_raw"))
    tele = _fmt_num(ep.get("teleop_raw"))
    end = _fmt_num(ep.get("endgame_raw"))
    return (
        f"[{ek}]({SITE_URL}/event/{ek}) · ACE {ace} · RAW {raw} · Auto/Tele/End {auto}/{tele}/{end}"
    )


def _parse_team_key_for_api(raw: str | None) -> str | None:
    """Return numeric team key for API path, or None if missing/invalid."""
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip().lower()
    if s.startswith("frc"):
        s = s[3:]
    return s if s.isdigit() else None


def _bucket_event_keys_by_year(event_keys: list[str]) -> dict[int, list[str]]:
    """Group event keys by season year prefix (e.g. 2025txdal → 2025)."""
    out: dict[int, list[str]] = {}
    for ek in event_keys:
        s = str(ek).strip()
        if len(s) >= 4 and s[:4].isdigit():
            y = int(s[:4])
            out.setdefault(y, []).append(s)
    for y in out:
        out[y].sort()
    return out


def _format_registered_events_for_year(
    season_year: int, events_by_year: dict[int, list[str]]
) -> str:
    keys = events_by_year.get(season_year) or []
    if not keys:
        return ""
    shown = keys[:MAX_REGISTERED_EVENTS_PER_SEASON]
    lines = [f"[{ek}]({SITE_URL}/event/{ek})" for ek in shown]
    extra = ""
    if len(keys) > MAX_REGISTERED_EVENTS_PER_SEASON:
        extra = f"\n_… and {len(keys) - MAX_REGISTERED_EVENTS_PER_SEASON} more._"
    return "_Registered for this season:_\n" + "\n".join(lines) + extra


def _format_season_event_perfs_block(event_perf: Any) -> str:
    if not event_perf or not isinstance(event_perf, list):
        return ""
    lines: list[str] = []
    for ep in event_perf[:MAX_TEAM_SEASON_EVENT_PERFS]:
        if isinstance(ep, dict):
            lines.append(_format_single_event_perf_line(ep))
    if not lines:
        return ""
    extra = ""
    if len(event_perf) > MAX_TEAM_SEASON_EVENT_PERFS:
        extra = f"\n_… and {len(event_perf) - MAX_TEAM_SEASON_EVENT_PERFS} more events._"
    return "_Event perfs:_\n" + "\n".join(lines) + extra


def _build_team_season_pages(
    perfs: list[dict[str, Any]],
    tn: int,
    team_info: dict[str, Any] | None = None,
    events_by_year: dict[int, list[str]] | None = None,
) -> list[discord.Embed]:
    # Newest season first (API order is not guaranteed)
    perfs = sorted(perfs, key=_team_perf_year_key, reverse=True)
    team_url = f"{SITE_URL}/team/{tn}"
    profile = _format_team_profile(team_info)
    title_base = _team_embed_title(tn, team_info)
    chunks = _chunk(perfs, PAGE_TEAM_SEASONS)
    n = len(chunks)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        lines: list[str] = []
        for p in chunk:
            y = p.get("year")
            try:
                yi = int(y) if y is not None else None
            except (TypeError, ValueError):
                yi = None
            raw = _fmt_num(p.get("raw"))
            ace = _fmt_num(p.get("ace"))
            conf = _fmt_num(p.get("confidence"))
            w, l, ti = p.get("wins"), p.get("losses"), p.get("ties")
            ar = _fmt_num(p.get("auto_raw"))
            tr = _fmt_num(p.get("teleop_raw"))
            eg = _fmt_num(p.get("endgame_raw"))
            block = (
                f"**{y}** · RAW {raw} · ACE {ace} · σ {conf}\n"
                f"Record **{w}–{l}–{ti}** · Auto {ar} · Teleop {tr} · Endgame {eg}"
            )
            if events_by_year and yi is not None:
                reg = _format_registered_events_for_year(yi, events_by_year)
                if reg:
                    block += "\n" + reg
            evp = _format_season_event_perfs_block(p.get("event_perf"))
            if evp:
                block += "\n" + evp
            lines.append(block)
        body = "\n\n".join(lines)
        if n > 1:
            body += f"\n\n_{len(perfs)} seasons total._"
        if i == 1 and profile:
            body = profile + body
        elif i > 1 and profile:
            body = "_Season metrics (continued)_\n\n" + body
        out.append(
            _embed_with_page_footer(
                title=title_base,
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=team_url,
            )
        )
    return out


def _build_events_list_pages(events: list[dict[str, Any]], year: int) -> list[discord.Embed]:
    chunks = _chunk(events, PAGE_EVENTS)
    if not chunks:
        return []
    n = len(chunks)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        parts: list[str] = []
        for ev in chunk:
            ek = ev.get("event_key", "?")
            meta = ev.get("event_data") or {}
            loc = ev.get("location_info") or {}
            name = meta.get("name", ek)
            sd = _short_iso_date(meta.get("start_date"))
            ed = _short_iso_date(meta.get("end_date"))
            et = meta.get("event_type", "")
            city = loc.get("city", "")
            st = loc.get("state_prov", "")
            parts.append(f"**[{ek}]({SITE_URL}/event/{ek})**\n{name}\n_{sd} → {ed}_ · {et}\n{city}, {st}")
        body = "\n\n".join(parts)
        if n > 1:
            body += f"\n\n_{len(events)} events._"
        out.append(
            _embed_with_page_footer(
                title=f"Events · {year}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
            )
        )
    return out


def _build_event_keys_pages(keys: list[str], year: int) -> list[discord.Embed]:
    chunks = _chunk(keys, PAGE_EVENT_KEYS)
    if not chunks:
        return []
    n = len(chunks)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        body = ", ".join(chunk)
        if n > 1:
            body += f"\n\n_{len(keys)} keys total._"
        out.append(
            _embed_with_page_footer(
                title=f"Event keys · {year}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
            )
        )
    return out


def _build_team_awards_pages(awards: list[dict[str, Any]], team_number: int) -> list[discord.Embed]:
    chunks = _chunk(awards, PAGE_AWARDS)
    n = len(chunks)
    team_url = f"{SITE_URL}/team/{team_number}"
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        lines = []
        for a in chunk:
            ek = a.get("event_key", "")
            an = a.get("award_name", "")
            lines.append(f"**{an}** — [`{ek}`]({SITE_URL}/event/{ek})")
        body = "\n".join(lines)
        if n > 1:
            body += f"\n\n_{len(awards)} awards total._"
        out.append(
            _embed_with_page_footer(
                title=f"Awards · Team {team_number}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=team_url,
            )
        )
    return out


def _build_team_events_pages(evs: list[str], team_number: int) -> list[discord.Embed]:
    chunks = _chunk(evs, PAGE_TEAM_EVENTS)
    n = len(chunks)
    team_url = f"{SITE_URL}/team/{team_number}"
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        lines = [f"[{k}]({SITE_URL}/event/{k})" for k in chunk]
        body = " · ".join(lines)
        if n > 1:
            body += f"\n\n_{len(evs)} events total._"
        out.append(
            _embed_with_page_footer(
                title=f"Events · Team {team_number}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=team_url,
            )
        )
    return out


def _build_event_teams_pages(nums: list[int], ek: str) -> list[discord.Embed]:
    chunks = _chunk(nums, PAGE_EVENT_TEAMS)
    n = len(chunks)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        body = f"**{len(nums)}** teams\n\n`" + ", ".join(str(x) for x in chunk) + "`"
        if n > 1:
            body += f"\n\n_Page {i}: teams {chunk[0]}–{chunk[-1]}._"
        out.append(
            _embed_with_page_footer(
                title=f"Teams · {ek}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=f"{SITE_URL}/event/{ek}",
            )
        )
    return out


def _build_match_lines(matches: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for m in matches:
        cl = m.get("comp_level", "")
        mn = m.get("match_number")
        red = m.get("red_teams") or []
        blue = m.get("blue_teams") or []
        rs = m.get("red_score")
        bs = m.get("blue_score")
        win = m.get("winning_alliance", "")
        rts = ",".join(str(x) for x in red)
        bts = ",".join(str(x) for x in blue)
        lines.append(f"`{cl}{mn}` **{rs}**–**{bs}** {win}\n  R {rts} vs B {bts}")
    return lines


def _build_event_matches_pages(matches: list[dict[str, Any]], ek: str, total_count: int) -> list[discord.Embed]:
    # Newest first (reverse API order)
    rev = list(reversed(matches))
    chunks = _chunk(rev, PAGE_MATCHES)
    if not chunks:
        return []
    n = len(chunks)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        hdr = f"_{total_count} matches · **newest first**._\n\n"
        body = hdr + "\n\n".join(_build_match_lines(chunk))
        out.append(
            _embed_with_page_footer(
                title=f"Matches · {ek}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=f"{SITE_URL}/event/{ek}",
            )
        )
    return out


def _build_event_awards_pages(rows: list[dict[str, Any]], ek: str) -> list[discord.Embed]:
    chunks = _chunk(rows, PAGE_AWARDS)
    n = len(chunks)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        lines = [f"**{r.get('team_number')}** — {r.get('award_name', '')}" for r in chunk]
        body = "\n".join(lines)
        if n > 1:
            body += f"\n\n_{len(rows)} awards total._"
        out.append(
            _embed_with_page_footer(
                title=f"Awards · {ek}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=f"{SITE_URL}/event/{ek}",
            )
        )
    return out


def _build_event_perfs_pages(
    sorted_perfs: list[dict[str, Any]],
    ek: str,
    total: int,
    *,
    team_filter: int | None = None,
) -> list[discord.Embed]:
    chunks = _chunk(sorted_perfs, PAGE_EVENT_PERFS)
    n = len(chunks)
    title = f"Event metrics · {ek}"
    if team_filter is not None:
        title = f"Event metrics · {ek} · Team {team_filter}"
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        lines = [f"{'Team':>5}  {'ACE':>8}  {'RAW':>8}  {'Auto':>7}  {'Tele':>7}  {'End':>7}"]
        for p in chunk:
            tn = p.get("team_number")
            lines.append(
                f"{tn:>5}  {_fmt_num(p.get('ace')):>8}  {_fmt_num(p.get('raw')):>8}  "
                f"{_fmt_num(p.get('auto_raw')):>7}  {_fmt_num(p.get('teleop_raw')):>7}  {_fmt_num(p.get('endgame_raw')):>7}"
            )
        body = "```\n" + "\n".join(lines) + "\n```"
        if team_filter is not None:
            body += f"\n_Filtered to team **{team_filter}**._"
        else:
            body += f"\n_Sorted by ACE · **{total}** teams._"
        out.append(
            _embed_with_page_footer(
                title=title,
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=f"{SITE_URL}/event/{ek}",
            )
        )
    return out


class PeekoroboApi:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"X-API-Key": API_KEY},
            timeout=httpx.Timeout(45.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict | None = None) -> tuple[int, dict]:
        r = await self._client.get(path, params=params)
        try:
            data = r.json()
        except Exception:
            data = {}
        return r.status_code, data if isinstance(data, dict) else {}

    async def authorize(self) -> tuple[int, dict]:
        return await self._get_json("/authorize")

    async def team_perfs(self, team_number: int, year: int | None) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        return await self._get_json(f"/team_perfs/{team_number}", params or None)

    async def team_lookup(self, team_number: int) -> tuple[int, dict]:
        """GET /teams?team_number=&limit=1 — nickname, location, website, district."""
        return await self._get_json("/teams", {"team_number": team_number, "limit": 1})

    async def event_rankings(self, event_key: str) -> tuple[int, dict]:
        return await self._get_json(f"/event/{event_key}/rankings")

    async def events_for_year(
        self,
        year: int,
        *,
        limit: int | None = None,
        state_prov: str | None = None,
        district_key: str | None = None,
        country: str | None = None,
        city: str | None = None,
    ) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if state_prov:
            params["state_prov"] = state_prov
        if district_key:
            params["district_key"] = district_key
        if country:
            params["country"] = country
        if city:
            params["city"] = city
        return await self._get_json(f"/events/{year}", params or None)

    async def event_keys(
        self,
        year: int,
        *,
        state_prov: str | None = None,
        district_key: str | None = None,
    ) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if state_prov:
            params["state_prov"] = state_prov
        if district_key:
            params["district_key"] = district_key
        return await self._get_json(f"/events/{year}/keys", params or None)

    async def team_awards(self, team_number: int, year: int | None = None) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        return await self._get_json(f"/team/{team_number}/awards", params or None)

    async def team_events(self, team_number: int, year: int | None = None) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        return await self._get_json(f"/team/{team_number}/events", params or None)

    async def event_teams(self, event_key: str) -> tuple[int, dict]:
        return await self._get_json(f"/event/{event_key}/teams")

    async def event_matches(self, event_key: str, team_number: int | None = None) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if team_number is not None:
            params["team_number"] = str(team_number)
        return await self._get_json(f"/event/{event_key}/matches", params or None)

    async def event_awards(self, event_key: str, team_number: int | None = None) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if team_number is not None:
            params["team_number"] = team_number
        return await self._get_json(f"/event/{event_key}/awards", params or None)

    async def event_perfs(self, event_key: str) -> tuple[int, dict]:
        return await self._get_json(f"/event/{event_key}/event_perfs")

    async def event_perf_for_team(self, event_key: str, team_key: str) -> tuple[int, dict]:
        """GET /event/{event_key}/event_perfs/{team_key} — team_key e.g. 254 or frc254"""
        return await self._get_json(f"/event/{event_key}/event_perfs/{team_key}")


class PeekoroboClient(discord.Client):
    """Slash-only: use Client + CommandTree (no prefix commands, so no Message Content intent)."""

    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)


client = PeekoroboClient()
api = PeekoroboApi()


@client.event
async def on_ready() -> None:
    assert client.user is not None
    print(f"Logged in as {client.user} ({client.user.id})")
    synced = await client.tree.sync()
    print(f"Synced {len(synced)} command(s)")


@client.event
async def on_close() -> None:
    await api.close()


@client.tree.command(name="peek_ping", description="Verify API connectivity and your API key")
async def peek_ping(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)
    status, data = await api.authorize()
    if status == 200 and data.get("authorized"):
        e = _base_embed(title="API connected", color=COLOR_SUCCESS)
        e.description = f"**Base:** `{API_BASE}`\n**Status:** authorized"
        await interaction.followup.send(embed=e)
    elif status == 401:
        e = _base_embed(title="Unauthorized", color=COLOR_ERR)
        e.description = "The API rejected `X-API-Key`. Check `PEEKOROBO_API_KEY` in `.env`."
        await interaction.followup.send(embed=e)
    else:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)


@client.tree.command(
    name="peek_team",
    description="Team profile, season stats, all registered events per season, and event_perfs when available",
)
@app_commands.describe(team_number="FRC team number (e.g. 254)", year="Filter to one season (optional)")
async def peek_team(interaction: discord.Interaction, team_number: int, year: int | None = None) -> None:
    await interaction.response.defer(thinking=True)
    (status, data), (st_meta, meta), (st_ev, evd) = await asyncio.gather(
        api.team_perfs(team_number, year),
        api.team_lookup(team_number),
        api.team_events(team_number, year=None),
    )
    team_info: dict[str, Any] | None = None
    if st_meta == 200 and isinstance(meta, dict):
        rows = meta.get("team_info") or []
        if rows:
            team_info = rows[0]

    events_by_year: dict[int, list[str]] | None = None
    if st_ev == 200 and isinstance(evd, dict):
        ev_keys = evd.get("events") or []
        if isinstance(ev_keys, list):
            events_by_year = _bucket_event_keys_by_year([str(x) for x in ev_keys])

    if status == 404:
        e = _base_embed(title=_team_embed_title(team_number, team_info), color=COLOR_WARN)
        e.url = f"{SITE_URL}/team/{team_number}"
        if team_info:
            e.description = _truncate(
                _format_team_profile(team_info) + "No team performance data found for this team.",
                EMBED_DESC_SAFE,
            )
        else:
            e.description = "No team performance data found for this team."
        await interaction.followup.send(embed=e)
        return
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    perfs = data.get("team_perfs") or []
    if not perfs:
        tn = data.get("team_number", team_number)
        e = _base_embed(title=_team_embed_title(tn, team_info), color=COLOR_WARN)
        e.url = f"{SITE_URL}/team/{tn}"
        if team_info:
            e.description = _truncate(
                _format_team_profile(team_info) + "No performance rows returned for this filter.",
                EMBED_DESC_SAFE,
            )
        else:
            e.description = "No performance rows returned."
        await interaction.followup.send(embed=e)
        return

    tn = data.get("team_number", team_number)
    pages = _build_team_season_pages(perfs, tn, team_info, events_by_year)
    await send_paginated(interaction, pages)


@client.tree.command(name="peek_event", description="General info for one event (name, dates, location, website, webcast)")
@app_commands.describe(event_key="Event key, e.g. 2025txdal or 2024cmp")
async def peek_event(interaction: discord.Interaction, event_key: str) -> None:
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    year = _year_from_event_key(key)
    if year is None:
        e = _base_embed(title="Invalid event key", color=COLOR_WARN)
        e.description = "Could not read a 4-digit season year at the start of the key (e.g. `2025txdal`)."
        await interaction.followup.send(embed=e)
        return

    status, data = await api.events_for_year(year, limit=None)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    events = data.get("events") or []
    match = next((ev for ev in events if ev.get("event_key") == key), None)
    if match is None:
        e = _base_embed(title=f"Event `{key}`", color=COLOR_WARN)
        e.url = f"{SITE_URL}/event/{key}"
        e.description = (
            f"No metadata found for this key in the **{year}** event list. "
            "Check the key or try again after the API has synced."
        )
        await interaction.followup.send(embed=e)
        return

    await interaction.followup.send(embed=_build_event_general_embed(match, key))


@client.tree.command(name="peek_rankings", description="Full event rankings (W–L–T, DQ)")
@app_commands.describe(event_key="Event key, e.g. 2025txdal or 2024cmp")
async def peek_rankings(interaction: discord.Interaction, event_key: str) -> None:
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    status, data = await api.event_rankings(key)
    if status == 404:
        e = _base_embed(title="No rankings", color=COLOR_WARN)
        e.description = f"No rankings for `{key}`."
        await interaction.followup.send(embed=e)
        return
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    rows = data.get("event_rankings") or []
    if not rows:
        e = _base_embed(title=data.get("event_key", key), color=COLOR_WARN)
        e.description = "No ranking rows."
        await interaction.followup.send(embed=e)
        return

    ek = data.get("event_key", key)
    pages = _build_ranking_pages(rows, ek)
    await send_paginated(interaction, pages)


@client.tree.command(name="peek_events", description="List FRC events for a season (location filters optional)")
@app_commands.describe(
    year="Season year, e.g. 2025",
    limit="Max events from API (default 24; results are paginated here)",
    state_prov="Filter by state/province code",
    district_key="District key filter",
)
async def peek_events(
    interaction: discord.Interaction,
    year: int,
    limit: int = DEFAULT_EVENTS_LIMIT,
    state_prov: str | None = None,
    district_key: str | None = None,
) -> None:
    await interaction.response.defer(thinking=True)
    status, data = await api.events_for_year(year, limit=limit, state_prov=state_prov, district_key=district_key)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    events = data.get("events") or []
    if not events:
        e = _base_embed(title=f"Events {year}", color=COLOR_WARN)
        e.description = "No events match your filters."
        await interaction.followup.send(embed=e)
        return

    pages = _build_events_list_pages(events, year)
    await send_paginated(interaction, pages)


@client.tree.command(name="peek_event_keys", description="Event keys for a year (compact list for scripts / search)")
@app_commands.describe(year="Season year", state_prov="Optional state filter", district_key="Optional district filter")
async def peek_event_keys(
    interaction: discord.Interaction,
    year: int,
    state_prov: str | None = None,
    district_key: str | None = None,
) -> None:
    await interaction.response.defer(thinking=True)
    status, data = await api.event_keys(year, state_prov=state_prov, district_key=district_key)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    keys = data.get("keys") or []
    if not keys:
        e = _base_embed(title=f"Event keys {year}", color=COLOR_WARN)
        e.description = "No keys returned."
        await interaction.followup.send(embed=e)
        return

    pages = _build_event_keys_pages(keys, year)
    await send_paginated(interaction, pages)


@client.tree.command(name="peek_team_awards", description="Awards for a team (optionally one season)")
@app_commands.describe(team_number="FRC team number", year="Optional season year filter")
async def peek_team_awards(interaction: discord.Interaction, team_number: int, year: int | None = None) -> None:
    await interaction.response.defer(thinking=True)
    status, data = await api.team_awards(team_number, year=year)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    awards = data.get("awards") or []
    if not awards:
        e = _base_embed(title=f"Awards · Team {data.get('team_number', team_number)}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/team/{team_number}"
        e.description = "No awards in this filter."
        await interaction.followup.send(embed=e)
        return

    pages = _build_team_awards_pages(awards, data.get("team_number", team_number))
    await send_paginated(interaction, pages)


@client.tree.command(name="peek_team_events", description="Event keys a team has played (optional year filter)")
@app_commands.describe(team_number="FRC team number", year="Optional season year")
async def peek_team_events(interaction: discord.Interaction, team_number: int, year: int | None = None) -> None:
    await interaction.response.defer(thinking=True)
    status, data = await api.team_events(team_number, year=year)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    evs = data.get("events") or []
    e = _base_embed(title=f"Events · Team {data.get('team_number', team_number)}", color=COLOR_BRAND)
    e.url = f"{SITE_URL}/team/{team_number}"
    if not evs:
        e.description = "No events in this filter."
        await interaction.followup.send(embed=e)
        return

    pages = _build_team_events_pages(evs, data.get("team_number", team_number))
    await send_paginated(interaction, pages)


@client.tree.command(name="peek_event_teams", description="All team numbers registered at an event")
@app_commands.describe(event_key="Event key, e.g. 2025txdal")
async def peek_event_teams(interaction: discord.Interaction, event_key: str) -> None:
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    status, data = await api.event_teams(key)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    teams = data.get("teams") or []
    ek = data.get("event_key", key)
    if not teams:
        e = _base_embed(title=f"Teams · {ek}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/event/{ek}"
        e.description = "No teams listed."
        await interaction.followup.send(embed=e)
        return

    nums = sorted(int(t) for t in teams if t is not None)
    pages = _build_event_teams_pages(nums, ek)
    await send_paginated(interaction, pages)


@client.tree.command(name="peek_event_matches", description="Matches at an event, paginated (newest first; optional team filter)")
@app_commands.describe(event_key="Event key", team_number="Only matches involving this team")
async def peek_event_matches(
    interaction: discord.Interaction,
    event_key: str,
    team_number: int | None = None,
) -> None:
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    status, data = await api.event_matches(key, team_number=team_number)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    matches = data.get("matches") or []
    ek = data.get("event_key", key)
    if not matches:
        e = _base_embed(title=f"Matches · {ek}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/event/{ek}"
        e.description = "No matches returned."
        await interaction.followup.send(embed=e)
        return

    pages = _build_event_matches_pages(matches, ek, len(matches))
    await send_paginated(interaction, pages)


@client.tree.command(name="peek_event_awards", description="Awards at an event (Blue Banner, etc.)")
@app_commands.describe(event_key="Event key", team_number="Only awards for this team")
async def peek_event_awards(
    interaction: discord.Interaction,
    event_key: str,
    team_number: int | None = None,
) -> None:
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    status, data = await api.event_awards(key, team_number=team_number)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    rows = data.get("teams_and_awards") or []
    ek = data.get("event_key", key)
    if not rows:
        e = _base_embed(title=f"Awards · {ek}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/event/{ek}"
        e.description = "No awards returned."
        await interaction.followup.send(embed=e)
        return

    pages = _build_event_awards_pages(rows, ek)
    await send_paginated(interaction, pages)


@client.tree.command(
    name="peek_event_perfs",
    description="ACE/RAW breakdown at an event (all teams, or one team if team_number is set)",
)
@app_commands.describe(
    event_key="Event key",
    team_key="Optional: 254 or frc254 — show only that team’s metrics",
)
async def peek_event_perfs(
    interaction: discord.Interaction,
    event_key: str,
    team_key: str | None = None,
) -> None:
    await interaction.response.defer(thinking=True)
    key = event_key.strip()

    def ace_key(p: dict) -> float:
        v = p.get("ace")
        try:
            return float(v) if v is not None else float("-inf")
        except (TypeError, ValueError):
            return float("-inf")

    tk = _parse_team_key_for_api(team_key)
    if team_key is not None and str(team_key).strip() and tk is None:
        e = _base_embed(title="Invalid team key", color=COLOR_WARN)
        e.description = "Use a team number like `254` or `frc254`."
        await interaction.followup.send(embed=e)
        return

    if tk is not None:
        status, data = await api.event_perf_for_team(key, tk)
        if status == 404:
            ek = key
            e = _base_embed(title=f"Event metrics · {ek} · Team {tk}", color=COLOR_WARN)
            e.url = f"{SITE_URL}/event/{ek}"
            e.description = f"No metrics for team **{tk}** at this event."
            await interaction.followup.send(embed=e)
            return
        if status != 200:
            e = _base_embed(title="API error", color=COLOR_ERR)
            e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
            await interaction.followup.send(embed=e)
            return
        ek = str(data.get("event_key", key))
        perfs = [data]
        sorted_p = perfs
        pages = _build_event_perfs_pages(sorted_p, ek, len(perfs), team_filter=int(tk))
        await send_paginated(interaction, pages)
        return

    status, data = await api.event_perfs(key)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await interaction.followup.send(embed=e)
        return

    perfs = data.get("perfs") or []
    ek = data.get("event_key", key)
    if not perfs:
        e = _base_embed(title=f"Event metrics · {ek}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/event/{ek}"
        e.description = "No per-team metrics."
        await interaction.followup.send(embed=e)
        return

    sorted_p = sorted(perfs, key=ace_key, reverse=True)
    pages = _build_event_perfs_pages(sorted_p, ek, len(perfs))
    await send_paginated(interaction, pages)


def main() -> None:
    _require_env()
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
