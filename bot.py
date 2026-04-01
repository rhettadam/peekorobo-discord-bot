from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.utils import escape_markdown
from dotenv import load_dotenv
import httpx

from auth_store import delete_key, get_stored_key, init_db, save_key

_bot_dir = Path(__file__).resolve().parent
init_db()
load_dotenv(_bot_dir.parent / ".env")
load_dotenv(_bot_dir / ".env")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
# Optional: used only for Discord user IDs listed in BOT_OWNER_DISCORD_IDS (comma-separated).
API_KEY = os.environ.get("PEEKOROBO_API_KEY")
API_BASE = (os.environ.get("PEEKOROBO_API_BASE") or "https://api.peekorobo.com").rstrip("/")
# Optional: set to your server ID for instant slash-command updates (global sync can take ~1 hour).
DISCORD_GUILD_ID_RAW = os.environ.get("DISCORD_GUILD_ID")

# Styling — brand accent #FFDD00 + semantic colors for success / warn / error
COLOR_BRAND = 0xFFDD00
COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFFB800
COLOR_ERR = 0xED4245
FOOTER_TEXT = "Peekorobo · FRC data"
LOGO_URL = "https://www.peekorobo.com/assets/logo.png"
SITE_URL = "https://www.peekorobo.com"


def _team_avatar_url(team_number: int) -> str:
    """Peekorobo CDN team avatar; same pattern as https://peekorobo.com/assets/avatars/1.png"""
    return f"{SITE_URL}/assets/avatars/{int(team_number)}.png"


def _apply_team_thumbnail(embed: discord.Embed, team_number: int) -> None:
    embed.set_thumbnail(url=_team_avatar_url(team_number))


# Default API limit for /peek_events (still paginated in Discord)
DEFAULT_EVENTS_LIMIT = 24
# GET /teams allows up to 100 per request
DEFAULT_TEAMS_LIMIT = 24
MAX_TEAMS_API_LIMIT = 100

# Items per embed page (pagination buttons)
PAGE_RANKINGS = 15
PAGE_EVENTS = 3
PAGE_TEAMS_LIST = 8
PAGE_EVENT_KEYS = 30
PAGE_AWARDS = 10
PAGE_TEAM_EVENTS = 15
# Rich roster lines (link + nickname + location) stay within embed limits
PAGE_EVENT_TEAMS_DETAIL = 14
PAGE_MATCHES = 5
PAGE_EVENT_PERFS = 12
PAGE_TEAM_SEASONS = 5
MAX_TEAM_SEASON_EVENT_PERFS = 12
MAX_REGISTERED_EVENTS_PER_SEASON = 50
EMBED_DESC_SAFE = 3800
FOOTER_TEXT_MAX = 400


def _polish_footer_text() -> str:
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")
    return _truncate(f"{FOOTER_TEXT} · Data as of {ts}", FOOTER_TEXT_MAX)


def _apply_polish_footer(embed: discord.Embed, *, page_suffix: str | None = None) -> None:
    base = _polish_footer_text()
    if page_suffix:
        text = _truncate(f"{base} · {page_suffix}", 2048)
    else:
        text = _truncate(base, 2048)
    embed.set_footer(text=text, icon_url=LOGO_URL)


def _rankings_to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["rank", "team_number", "wins", "losses", "ties", "dq"])
    for r in rows:
        w.writerow(
            [
                r.get("rank"),
                r.get("team_number"),
                r.get("wins"),
                r.get("losses"),
                r.get("ties"),
                r.get("dq", 0),
            ]
        )
    return buf.getvalue().rstrip("\n")


def _rankings_to_json(rows: list[dict[str, Any]], ek: str) -> str:
    return json.dumps({"event_key": ek, "event_rankings": rows}, indent=2, default=str)


def _event_perfs_to_csv(perfs: list[dict[str, Any]], ek: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["event_key", "team_number", "ace", "confidence", "raw", "auto_raw", "teleop_raw", "endgame_raw"])
    for p in perfs:
        w.writerow(
            [
                p.get("event_key") or ek,
                p.get("team_number"),
                p.get("ace"),
                p.get("confidence"),
                p.get("raw"),
                p.get("auto_raw"),
                p.get("teleop_raw"),
                p.get("endgame_raw"),
            ]
        )
    return buf.getvalue().rstrip("\n")


def _event_perfs_to_json(perfs: list[dict[str, Any]], ek: str) -> str:
    return json.dumps({"event_key": ek, "perfs": perfs}, indent=2, default=str)


def _json_export(obj: Any) -> str:
    """JSON for exports; normalizes dict keys to str (e.g. year keys in events_by_year)."""

    def normalize(o: Any) -> Any:
        if isinstance(o, dict):
            return {str(k): normalize(v) for k, v in o.items()}
        if isinstance(o, list):
            return [normalize(x) for x in o]
        return o

    return json.dumps(normalize(obj), indent=2, default=str)


def _dicts_to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(keys)
    for r in rows:
        w.writerow([r.get(k) for k in keys])
    return buf.getvalue().rstrip("\n")


def _single_column_csv(values: list[Any], header: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([header])
    for v in values:
        w.writerow([v])
    return buf.getvalue().rstrip("\n")


def _event_row_flat(ev: dict[str, Any]) -> dict[str, Any]:
    meta = ev.get("event_data") or {}
    loc = ev.get("location_info") or {}
    return {
        "event_key": ev.get("event_key"),
        "name": meta.get("name"),
        "start_date": str(meta.get("start_date")),
        "end_date": str(meta.get("end_date")),
        "event_type": meta.get("event_type"),
        "city": loc.get("city"),
        "state_prov": loc.get("state_prov"),
        "country": loc.get("country"),
        "week": ev.get("week"),
        "district_key": ev.get("district_key"),
        "district_name": ev.get("district_name"),
        "website": ev.get("website"),
    }


def _team_row_flat(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "team_number": t.get("team_number"),
        "nickname": t.get("nickname"),
        "city": t.get("city"),
        "state_prov": t.get("state_prov"),
        "country": t.get("country"),
        "website": t.get("website"),
        "district_key": t.get("district_key"),
    }


def _team_season_rows_csv(perfs: list[dict[str, Any]], team_number: int) -> str:
    keys = [
        "year",
        "team_number",
        "wins",
        "losses",
        "ties",
        "raw",
        "ace",
        "confidence",
        "auto_raw",
        "teleop_raw",
        "endgame_raw",
    ]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(keys)
    for p in perfs:
        row = []
        for k in keys:
            if k == "team_number":
                row.append(p.get("team_number", team_number))
            else:
                row.append(p.get(k))
        w.writerow(row)
    return buf.getvalue().rstrip("\n")


def _spoiler_safe(s: str) -> str:
    """Avoid breaking Discord spoiler markers."""
    return s.replace("||", "| |")


async def _send_export_button_response(
    interaction: discord.Interaction,
    text: str,
    *,
    label: str,
    filename: str,
) -> None:
    """Ephemeral reply for CSV/JSON button: spoiler if short, else file attachment."""
    block = f"**{label}** (tap to reveal)\n||{_spoiler_safe(text)}||"
    if len(block) <= 2000:
        await interaction.response.send_message(content=block, ephemeral=True)
    else:
        await interaction.response.send_message(
            content=f"{label} attached (too long for spoiler). UTF-8.",
            ephemeral=True,
            file=discord.File(io.BytesIO(text.encode("utf-8")), filename=filename),
        )


def _owner_discord_ids() -> set[str]:
    raw = os.environ.get("BOT_OWNER_DISCORD_IDS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def get_effective_api_key(user_id: int) -> str | None:
    """Per-user key from SQLite, or env PEEKOROBO_API_KEY only for BOT_OWNER_DISCORD_IDS."""
    k = get_stored_key(user_id)
    if k:
        return k
    if API_KEY and str(user_id) in _owner_discord_ids():
        return API_KEY
    return None


async def _require_api_key(interaction: discord.Interaction) -> str | None:
    key = get_effective_api_key(interaction.user.id)
    if key:
        return key
    await interaction.response.send_message(
        "Link your Peekorobo API key with **`/peek_auth`** (private modal). "
        f"Your key is stored on the bot host only for your Discord user ID. "
        f"Get access at {SITE_URL} if you have it.",
        ephemeral=True,
    )
    return None


def _require_env() -> None:
    missing = []
    if not DISCORD_TOKEN:
        missing.append("DISCORD_TOKEN")
    if missing:
        print("Missing env: " + ", ".join(missing), file=sys.stderr)
        sys.exit(1)


def _base_embed(*, title: str | None = None, description: str | None = None, color: int = COLOR_BRAND) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color, url=SITE_URL)
    e.set_author(name="Peekorobo", url=SITE_URL, icon_url=LOGO_URL)
    _apply_polish_footer(e)
    return e


def _build_help_embed() -> discord.Embed:
    """Static copy of slash command summaries (keep in sync when adding commands)."""
    body = """**General**
`/peek` or `/peek_help` — Show this list of commands.
`/peek_auth` — Save your Peekorobo API key (private modal; required for data commands).
`/peek_auth_clear` — Remove your saved key from this bot.
`/peek_ping` — Verify the API accepts your key.

**Teams**
`/peek_team` — Team profile, season ACE/RAW/record, registered events per season, and event-perf lines (optional `year`).
`/peek_teams` — Search teams by season; optional `country`, `state_prov`, `district_key`, `city`, `limit` (paginated).
`/peek_team_awards` — Awards for a team, newest season first (optional `year`).
`/peek_team_events` — Event keys a team has played (optional `year`).

**Finding events**
`/peek_event` — One event: name, dates, week, district, location, website, webcast (`event_key` e.g. `2025txdal`).
`/peek_events` — Events for a season; optional filters `country`, `state_prov`, `district_key`, `limit` (paginated).
`/peek_event_keys` — Compact event keys for a year (same location filters; paginated).

**At one event** (`event_key`)
`/peek_rankings` — Full rankings (W–L–T, DQ).
`/peek_event_teams` — Registered teams (links, nicknames, locations).
`/peek_event_matches` — Matches, newest first (optional `team_number`).
`/peek_event_awards` — Awards (optional `team_number`).
`/peek_event_perfs` — ACE, σ, RAW, auto/tele/end breakdown (optional `team_key`: `254` or `frc254`).

_All replies with data include **CSV** and **JSON** export buttons (tap for a private copy). Footers show when data was fetched (UTC). Long replies use ◀ / page / ▶; only you can use the buttons._"""
    e = _base_embed(title="Peekorobo commands", description=_truncate(body, EMBED_DESC_SAFE))
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


def _event_week_display(week: Any) -> str | None:
    """FRC stores week as 0-based; show as Week 1, Week 2, … (matches Peekorobo site)."""
    if week is None:
        return None
    try:
        wi = int(week)
    except (TypeError, ValueError):
        return None
    if wi < 0:
        return None
    return f"Week {wi + 1}"


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
    wk = _event_week_display(ev.get("week"))
    if wk:
        lines.append(wk)
    dk = str(ev.get("district_key") or "").strip()
    dn = str(ev.get("district_name") or "").strip()
    if dk or dn:
        if dk and dn:
            lines.append(f"District: `{dk}` · **{dn}**")
        elif dk:
            lines.append(f"District: `{dk}`")
        else:
            lines.append(f"District: **{dn}**")
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
    _apply_polish_footer(e)
    return e


def _embed_with_page_footer(
    *,
    title: str | None,
    description: str,
    color: int,
    page: int,
    total_pages: int,
    url: str | None = SITE_URL,
    thumbnail_url: str | None = None,
) -> discord.Embed:
    e = discord.Embed(title=title, description=_truncate(description, EMBED_DESC_SAFE), color=color, url=url)
    e.set_author(name="Peekorobo", url=SITE_URL, icon_url=LOGO_URL)
    if thumbnail_url:
        e.set_thumbnail(url=thumbnail_url)
    _apply_polish_footer(e, page_suffix=f"Page {page}/{total_pages}")
    return e


class EmbedPaginatorView(discord.ui.View):
    """◀ / page / ▶ and optional CSV / JSON on one row (max 5 buttons). Only the invoker can click."""

    def __init__(
        self,
        author_id: int,
        pages: list[discord.Embed],
        *,
        timeout: float = 600.0,
        export_csv: str | None = None,
        export_json: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.pages = pages
        self.index = 0
        self.message: discord.Message | discord.WebhookMessage | None = None
        self._export_csv = export_csv
        self._export_json = export_json
        self._sync_buttons()
        if export_csv:
            csv_data = export_csv

            async def _on_csv(interaction: discord.Interaction) -> None:
                await _send_export_button_response(
                    interaction, csv_data, label="CSV", filename="peekorobo_export.csv"
                )

            b = discord.ui.Button(label="CSV", style=discord.ButtonStyle.secondary, row=0)
            b.callback = _on_csv
            self.add_item(b)
        if export_json:
            json_data = export_json

            async def _on_json(interaction: discord.Interaction) -> None:
                await _send_export_button_response(
                    interaction, json_data, label="JSON", filename="peekorobo_export.json"
                )

            b = discord.ui.Button(label="JSON", style=discord.ButtonStyle.secondary, row=0)
            b.callback = _on_json
            self.add_item(b)

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


async def send_paginated(
    interaction: discord.Interaction,
    pages: list[discord.Embed],
    *,
    ephemeral: bool = False,
    export_csv: str | None = None,
    export_json: str | None = None,
) -> None:
    if not pages:
        return
    assert interaction.user is not None
    if export_csv or export_json:
        view = EmbedPaginatorView(
            interaction.user.id,
            pages,
            export_csv=export_csv,
            export_json=export_json,
        )
        msg = await interaction.followup.send(embed=pages[0], view=view, ephemeral=ephemeral, wait=True)
        view.message = msg
        return
    if len(pages) == 1:
        await interaction.followup.send(embed=pages[0], ephemeral=ephemeral)
        return
    view = EmbedPaginatorView(interaction.user.id, pages)
    msg = await interaction.followup.send(embed=pages[0], view=view, ephemeral=ephemeral, wait=True)
    view.message = msg


async def send_embed_with_export(
    interaction: discord.Interaction,
    embed: discord.Embed,
    *,
    export_csv: str | None = None,
    export_json: str | None = None,
    ephemeral: bool = False,
) -> None:
    """Single embed with optional CSV/JSON buttons (same as one-page send_paginated)."""
    await send_paginated(
        interaction,
        [embed],
        export_csv=export_csv,
        export_json=export_json,
        ephemeral=ephemeral,
    )


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
    conf = _fmt_num(ep.get("confidence"))
    raw = _fmt_num(ep.get("raw"))
    auto = _fmt_num(ep.get("auto_raw"))
    tele = _fmt_num(ep.get("teleop_raw"))
    end = _fmt_num(ep.get("endgame_raw"))
    return (
        f"[{ek}]({SITE_URL}/event/{ek}) · ACE {ace} · σ {conf} · RAW {raw} · Auto/Tele/End {auto}/{tele}/{end}"
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
                thumbnail_url=_team_avatar_url(tn),
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
            date_line = f"_{sd} → {ed}_ · {et}"
            wk = _event_week_display(ev.get("week"))
            if wk:
                date_line += f" · {wk}"
            block = f"**[{ek}]({SITE_URL}/event/{ek})**\n{name}\n{date_line}\n{city}, {st}"
            dk = str(ev.get("district_key") or "").strip()
            dn = str(ev.get("district_name") or "").strip()
            if dk or dn:
                if dk and dn:
                    block += f"\nDistrict: `{dk}` · **{dn}**"
                elif dk:
                    block += f"\nDistrict: `{dk}`"
                else:
                    block += f"\nDistrict: **{dn}**"
            parts.append(block)
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


def _format_team_list_entry(t: dict[str, Any]) -> str:
    """One team block: linked number, nickname, location, district (for /peek_teams)."""
    try:
        tn = int(t.get("team_number"))
    except (TypeError, ValueError):
        return ""
    url = f"{SITE_URL}/team/{tn}"
    nick = _truncate(str(t.get("nickname") or "").strip(), 80)
    city = str(t.get("city") or "").strip()
    st = str(t.get("state_prov") or "").strip()
    ctry = str(t.get("country") or "").strip()
    dk = str(t.get("district_key") or "").strip()
    loc = ", ".join(x for x in [city, st, ctry] if x)
    head = f"**[{tn}]({url})**"
    if nick:
        head += f" · **{escape_markdown(nick)}**"
    lines = [head]
    if loc:
        lines.append(escape_markdown(_truncate(loc, 120)))
    if dk:
        lines.append(f"`{dk}`")
    return "\n".join(lines)


def _build_teams_list_pages(rows: list[dict[str, Any]], year: int) -> list[discord.Embed]:
    chunks = _chunk(rows, PAGE_TEAMS_LIST)
    if not chunks:
        return []
    n = len(chunks)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        parts: list[str] = []
        for t in chunk:
            block = _format_team_list_entry(t)
            if block:
                parts.append(block)
        body = "\n\n".join(parts)
        if n > 1:
            body += f"\n\n_{len(rows)} teams in this result._"
        out.append(
            _embed_with_page_footer(
                title=f"Teams · {year}",
                description=body,
                color=COLOR_BRAND,
                page=i,
                total_pages=n,
                url=f"{SITE_URL}/teams",
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


def _dedupe_team_awards(awards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per (event_key, award_name); keeps first occurrence (API order)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for a in awards:
        ek = str(a.get("event_key") or "").strip()
        an = str(a.get("award_name") or "").strip()
        key = (ek, an)
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def _sort_team_awards_newest_first(awards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order by season year (event_key prefix) descending, then event_key, then award name."""
    return sorted(
        awards,
        key=lambda a: (
            -(_year_from_event_key(str(a.get("event_key") or "").strip()) or 0),
            str(a.get("event_key") or ""),
            str(a.get("award_name") or "").lower(),
        ),
    )


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
                thumbnail_url=_team_avatar_url(team_number),
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
                thumbnail_url=_team_avatar_url(team_number),
            )
        )
    return out


def _coerce_event_teams_rows(raw: Any) -> list[dict[str, Any]]:
    """Normalize GET /event/…/teams payload: list of objects, or legacy list of team numbers."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            try:
                tn = int(item.get("team_number"))
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "team_number": tn,
                    "nickname": str(item.get("nickname") or "").strip(),
                    "city": str(item.get("city") or "").strip(),
                    "state_prov": str(item.get("state_prov") or "").strip(),
                    "country": str(item.get("country") or "").strip(),
                }
            )
        else:
            try:
                n = int(item)
                out.append(
                    {
                        "team_number": n,
                        "nickname": "",
                        "city": "",
                        "state_prov": "",
                        "country": "",
                    }
                )
            except (TypeError, ValueError):
                continue
    out.sort(key=lambda r: int(r["team_number"]))
    return out


def _format_team_location_compact(city: str, state_prov: str, country: str) -> str:
    parts = [x.strip() for x in (city, state_prov, country) if x and str(x).strip()]
    return ", ".join(parts)


def _format_event_team_line(row: dict[str, Any]) -> str:
    try:
        tn = int(row["team_number"])
    except (KeyError, TypeError, ValueError):
        return ""
    url = f"{SITE_URL}/team/{tn}"
    link = f"[**{tn}**]({url})"
    nick = _truncate(str(row.get("nickname") or "").strip(), 72)
    loc = _format_team_location_compact(
        str(row.get("city") or ""),
        str(row.get("state_prov") or ""),
        str(row.get("country") or ""),
    )
    loc = _truncate(loc, 120)
    parts: list[str] = [link]
    if nick:
        parts.append(f"**{escape_markdown(nick)}**")
    if loc:
        parts.append(escape_markdown(loc))
    return " · ".join(parts)


def _build_event_teams_pages(rows: list[dict[str, Any]], ek: str) -> list[discord.Embed]:
    chunks = _chunk(rows, PAGE_EVENT_TEAMS_DETAIL)
    n = len(chunks)
    total = len(rows)
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        lines = [_format_event_team_line(r) for r in chunk]
        lines = [ln for ln in lines if ln]
        body = f"**{total}** teams\n\n" + "\n".join(lines)
        if n > 1:
            t0 = int(chunk[0]["team_number"])
            t1 = int(chunk[-1]["team_number"])
            body += f"\n\n_Page {i}: teams {t0}–{t1}._"
        body = _truncate(body, EMBED_DESC_SAFE)
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


def _fmt_win_prob_display(x: Any) -> str:
    if x is None:
        return "—"
    try:
        v = float(x)
        if 0 <= v <= 1:
            return f"{v * 100:.1f}%"
        return f"{v:.2f}"
    except (TypeError, ValueError):
        return str(x)


def _format_match_code_label(m: dict[str, Any]) -> str:
    """Human-readable match code (same idea as layouts `k` suffix): qm12, sf1m3, f1m1 — not raw cl+mn."""
    mk = str(m.get("match_key") or "").strip()
    if mk and "_" in mk:
        return mk.split("_")[-1].upper()
    cl = str(m.get("comp_level") or "").lower()
    sn = m.get("set_number")
    mn = m.get("match_number")
    if cl in ("qf", "sf", "f") and sn is not None and mn is not None:
        return f"{cl}{sn}m{mn}".upper()
    if mn is not None and cl:
        return f"{cl}{mn}".upper()
    return (cl or "?").upper()


# TBA elimination order (oldest → newest). API sorts lexicographically, so reversing the list
# does not put finals last-in-time first; we sort explicitly by round recency, then set/match desc.
_COMP_LEVEL_RECENCY: dict[str, int] = {
    "qm": 0,
    "ef": 1,
    "qf": 2,
    "sf": 3,
    "f": 4,
}


def _sort_matches_newest_first(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(m: dict[str, Any]) -> tuple[int, int, int]:
        cl = str(m.get("comp_level") or "").lower().strip()
        rec = _COMP_LEVEL_RECENCY.get(cl, 0)
        try:
            sn = int(m.get("set_number") or 0)
        except (TypeError, ValueError):
            sn = 0
        try:
            mn = int(m.get("match_number") or 0)
        except (TypeError, ValueError):
            mn = 0
        # Higher recency / set / match = played later → sort first (negate for descending)
        return (-rec, -sn, -mn)

    return sorted(matches, key=key)


def _build_match_lines(matches: list[dict[str, Any]]) -> list[str]:
    """One block per match: short code from match_key, alliances, full key, YT + win probs."""
    lines: list[str] = []
    for m in matches:
        mk = str(m.get("match_key") or "").strip()
        code = _format_match_code_label(m)
        red = m.get("red_teams") or []
        blue = m.get("blue_teams") or []
        rs = m.get("red_score")
        bs = m.get("blue_score")
        win = str(m.get("winning_alliance") or "").strip()
        yk = str(m.get("youtube_key") or "").strip()
        rwp = _fmt_win_prob_display(m.get("red_win_prob"))
        bwp = _fmt_win_prob_display(m.get("blue_win_prob"))

        rts = ",".join(str(x) for x in red)
        bts = ",".join(str(x) for x in blue)

        # Line 1: TBA-style code (from match_key suffix) + score + winner
        head = f"**`{code}`** · **{rs}**–**{bs}**"
        if win:
            head += f" · {win}"
        # Line 2: alliances (explicit labels)
        alliances = f"**Red** `{rts}` · **Blue** `{bts}`"
        # Line 3: full key for search / scripts
        key_line = f"`{mk}`" if mk else ""
        # Line 4: optional YT + model probs (no predicted schedule time)
        tail_parts: list[str] = []
        if yk:
            tail_parts.append(f"[YouTube](https://www.youtube.com/watch?v={yk})")
        tail_parts.append(f"P(red) {rwp} · P(blue) {bwp}")
        tail = " · ".join(tail_parts)

        block = head + "\n" + alliances
        if key_line:
            block += "\n" + key_line
        block += "\n" + tail
        lines.append(block)
    return lines


def _build_event_matches_pages(
    matches: list[dict[str, Any]],
    ek: str,
    total_count: int,
    *,
    team_number: int | None = None,
) -> list[discord.Embed]:
    # Caller passes matches already ordered newest first (see _sort_matches_newest_first).
    thumb = _team_avatar_url(team_number) if team_number is not None else None
    chunks = _chunk(matches, PAGE_MATCHES)
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
                thumbnail_url=thumb,
            )
        )
    return out


def _build_event_awards_pages(
    rows: list[dict[str, Any]],
    ek: str,
    *,
    team_number: int | None = None,
) -> list[discord.Embed]:
    thumb = _team_avatar_url(team_number) if team_number is not None else None
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
                thumbnail_url=thumb,
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
    thumb = _team_avatar_url(team_filter) if team_filter is not None else None
    out: list[discord.Embed] = []
    for i, chunk in enumerate(chunks, start=1):
        lines = [f"{'Team':>5}  {'ACE':>8}  {'σ':>6}  {'RAW':>8}  {'Auto':>7}  {'Tele':>7}  {'End':>7}"]
        for p in chunk:
            tn = p.get("team_number")
            lines.append(
                f"{tn:>5}  {_fmt_num(p.get('ace')):>8}  {_fmt_num(p.get('confidence')):>6}  {_fmt_num(p.get('raw')):>8}  "
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
                thumbnail_url=thumb,
            )
        )
    return out


class PeekoroboApi:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(45.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict | None = None, *, api_key: str) -> tuple[int, dict]:
        r = await self._client.get(
            path,
            params=params,
            headers={"X-API-Key": api_key.strip()},
        )
        try:
            data = r.json()
        except Exception:
            data = {}
        return r.status_code, data if isinstance(data, dict) else {}

    async def authorize(self, *, api_key: str) -> tuple[int, dict]:
        return await self._get_json("/authorize", api_key=api_key)

    async def team_perfs(self, team_number: int, year: int | None, *, api_key: str) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        return await self._get_json(f"/team_perfs/{team_number}", params or None, api_key=api_key)

    async def team_lookup(self, team_number: int, *, api_key: str) -> tuple[int, dict]:
        """GET /teams?team_number=&limit=1 — nickname, location, website, district."""
        return await self._get_json("/teams", {"team_number": team_number, "limit": 1}, api_key=api_key)

    async def event_rankings(self, event_key: str, *, api_key: str) -> tuple[int, dict]:
        return await self._get_json(f"/event/{event_key}/rankings", api_key=api_key)

    async def events_for_year(
        self,
        year: int,
        *,
        limit: int | None = None,
        state_prov: str | None = None,
        district_key: str | None = None,
        country: str | None = None,
        city: str | None = None,
        api_key: str,
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
        return await self._get_json(f"/events/{year}", params or None, api_key=api_key)

    async def teams_list(
        self,
        *,
        year: int,
        limit: int | None = None,
        city: str | None = None,
        state_prov: str | None = None,
        district_key: str | None = None,
        country: str | None = None,
        api_key: str,
    ) -> tuple[int, dict]:
        params: dict[str, Any] = {"year": year}
        if limit is not None:
            params["limit"] = limit
        if city:
            params["city"] = city
        if state_prov:
            params["state_prov"] = state_prov
        if district_key:
            params["district_key"] = district_key
        if country:
            params["country"] = country
        return await self._get_json("/teams", params, api_key=api_key)

    async def event_keys(
        self,
        year: int,
        *,
        state_prov: str | None = None,
        district_key: str | None = None,
        country: str | None = None,
        api_key: str,
    ) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if state_prov:
            params["state_prov"] = state_prov
        if district_key:
            params["district_key"] = district_key
        if country:
            params["country"] = country
        return await self._get_json(f"/events/{year}/keys", params or None, api_key=api_key)

    async def team_awards(self, team_number: int, year: int | None = None, *, api_key: str) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        return await self._get_json(f"/team/{team_number}/awards", params or None, api_key=api_key)

    async def team_events(self, team_number: int, year: int | None = None, *, api_key: str) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        return await self._get_json(f"/team/{team_number}/events", params or None, api_key=api_key)

    async def event_teams(self, event_key: str, *, api_key: str) -> tuple[int, dict]:
        return await self._get_json(f"/event/{event_key}/teams", api_key=api_key)

    async def event_matches(self, event_key: str, team_number: int | None = None, *, api_key: str) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if team_number is not None:
            params["team_number"] = str(team_number)
        return await self._get_json(f"/event/{event_key}/matches", params or None, api_key=api_key)

    async def event_awards(self, event_key: str, team_number: int | None = None, *, api_key: str) -> tuple[int, dict]:
        params: dict[str, Any] = {}
        if team_number is not None:
            params["team_number"] = team_number
        return await self._get_json(f"/event/{event_key}/awards", params or None, api_key=api_key)

    async def event_perfs(self, event_key: str, *, api_key: str) -> tuple[int, dict]:
        return await self._get_json(f"/event/{event_key}/event_perfs", api_key=api_key)

    async def event_perf_for_team(self, event_key: str, team_key: str, *, api_key: str) -> tuple[int, dict]:
        """GET /event/{event_key}/event_perfs/{team_key} — team_key e.g. 254 or frc254"""
        return await self._get_json(f"/event/{event_key}/event_perfs/{team_key}", api_key=api_key)


class PeekoroboAuthModal(discord.ui.Modal, title="Link Peekorobo API key"):
    api_key_field = discord.ui.TextInput(
        label="X-API-Key",
        placeholder="Paste your key (only you see this)",
        style=discord.TextStyle.short,
        required=True,
        max_length=256,
    )

    def __init__(self) -> None:
        super().__init__(timeout=300.0)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        key = str(self.api_key_field.value).strip()
        if not key:
            await interaction.response.send_message("Empty key.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        status, data = await api.authorize(api_key=key)
        if status == 200 and data.get("authorized"):
            save_key(interaction.user.id, key)
            e = _base_embed(title="API key saved", color=COLOR_SUCCESS)
            e.description = (
                "Your key is stored for this bot and used for your slash commands. "
                "Remove it anytime with **`/peek_auth_clear`**."
            )
            await interaction.followup.send(embed=e, ephemeral=True)
        elif status == 401:
            e = _base_embed(title="Invalid key", color=COLOR_ERR)
            e.description = "The API rejected this key. It was **not** saved."
            await interaction.followup.send(embed=e, ephemeral=True)
        else:
            e = _base_embed(title="API error", color=COLOR_ERR)
            e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
            await interaction.followup.send(embed=e, ephemeral=True)


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
    if DISCORD_GUILD_ID_RAW and str(DISCORD_GUILD_ID_RAW).strip().isdigit():
        gid = int(str(DISCORD_GUILD_ID_RAW).strip())
        synced = await client.tree.sync(guild=discord.Object(id=gid))
        print(f"Synced {len(synced)} guild command(s) to guild {gid} (instant in this server)")
    else:
        synced = await client.tree.sync()
        print(
            f"Synced {len(synced)} global command(s) (may take up to ~1 hour to appear everywhere; "
            "set DISCORD_GUILD_ID for instant sync while testing)"
        )


async def _send_help_response(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=False)
    he = _build_help_embed()
    await send_embed_with_export(
        interaction,
        he,
        export_csv=_single_column_csv([he.description or ""], "help_markdown"),
        export_json=_json_export({"peek_help_markdown": he.description}),
    )


@client.event
async def on_close() -> None:
    await api.close()


@client.tree.command(name="peek_ping", description="Verify API connectivity and your API key")
async def peek_ping(interaction: discord.Interaction) -> None:
    key = await _require_api_key(interaction)
    if not key:
        return
    await interaction.response.defer(thinking=True)
    status, data = await api.authorize(api_key=key)
    ex = _json_export({"http_status": status, "api_base": API_BASE, "response": data})
    if status == 200 and data.get("authorized"):
        e = _base_embed(title="API connected", color=COLOR_SUCCESS)
        e.description = f"**Base:** `{API_BASE}`\n**Status:** authorized"
        await send_embed_with_export(interaction, e, export_json=ex)
    elif status == 401:
        e = _base_embed(title="Unauthorized", color=COLOR_ERR)
        e.description = "The API rejected `X-API-Key`. Update it with **`/peek_auth`**."
        await send_embed_with_export(interaction, e, export_json=ex)
    else:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(interaction, e, export_json=ex)


@client.tree.command(name="peek", description="Peekorobo slash command list (help)")
async def peek(interaction: discord.Interaction) -> None:
    await _send_help_response(interaction)


@client.tree.command(name="peek_help", description="Same as /peek — list all Peekorobo slash commands")
async def peek_help(interaction: discord.Interaction) -> None:
    await _send_help_response(interaction)


@client.tree.command(name="peek_auth", description="Save your Peekorobo API key for this bot (private modal)")
async def peek_auth(interaction: discord.Interaction) -> None:
    await interaction.response.send_modal(PeekoroboAuthModal())


@client.tree.command(name="peek_auth_clear", description="Remove your saved Peekorobo API key from this bot")
async def peek_auth_clear(interaction: discord.Interaction) -> None:
    delete_key(interaction.user.id)
    await interaction.response.send_message(
        "Your saved API key has been removed from this bot. Use **`/peek_auth`** to add one again.",
        ephemeral=True,
    )


@client.tree.command(
    name="peek_team",
    description="Team profile, season stats, all registered events per season, and event_perfs when available",
)
@app_commands.describe(team_number="FRC team number (e.g. 254)", year="Filter to one season (optional)")
async def peek_team(interaction: discord.Interaction, team_number: int, year: int | None = None) -> None:
    key = await _require_api_key(interaction)
    if not key:
        return
    await interaction.response.defer(thinking=True)
    (status, data), (st_meta, meta), (st_ev, evd) = await asyncio.gather(
        api.team_perfs(team_number, year, api_key=key),
        api.team_lookup(team_number, api_key=key),
        api.team_events(team_number, year=None, api_key=key),
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
        _apply_team_thumbnail(e, team_number)
        if team_info:
            e.description = _truncate(
                _format_team_profile(team_info) + "No team performance data found for this team.",
                EMBED_DESC_SAFE,
            )
        else:
            e.description = "No team performance data found for this team."
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export(
                {"team_number": team_number, "team_info": team_info, "http_status": status, "response": data}
            ),
        )
        return
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"http_status": status, "response": data}),
        )
        return

    perfs = data.get("team_perfs") or []
    if not perfs:
        tn = data.get("team_number", team_number)
        e = _base_embed(title=_team_embed_title(tn, team_info), color=COLOR_WARN)
        e.url = f"{SITE_URL}/team/{tn}"
        _apply_team_thumbnail(e, int(tn))
        if team_info:
            e.description = _truncate(
                _format_team_profile(team_info) + "No performance rows returned for this filter.",
                EMBED_DESC_SAFE,
            )
        else:
            e.description = "No performance rows returned."
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export(
                {
                    "team_number": tn,
                    "team_info": team_info,
                    "events_by_year": events_by_year,
                    "team_perfs": [],
                }
            ),
        )
        return

    tn = data.get("team_number", team_number)
    pages = _build_team_season_pages(perfs, tn, team_info, events_by_year)
    await send_paginated(
        interaction,
        pages,
        export_csv=_team_season_rows_csv(perfs, tn),
        export_json=_json_export(
            {"team_number": tn, "team_info": team_info, "events_by_year": events_by_year, "team_perfs": perfs}
        ),
    )


@client.tree.command(
    name="peek_event",
    description="General info for one event (name, dates, week, district, location, website, webcast)",
)
@app_commands.describe(event_key="Event key, e.g. 2025txdal or 2024cmp")
async def peek_event(interaction: discord.Interaction, event_key: str) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    year = _year_from_event_key(key)
    if year is None:
        e = _base_embed(title="Invalid event key", color=COLOR_WARN)
        e.description = "Could not read a 4-digit season year at the start of the key (e.g. `2025txdal`)."
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "error": "invalid_event_key_year"}),
        )
        return

    status, data = await api.events_for_year(year, limit=None, api_key=ak)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "http_status": status, "response": data}),
        )
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
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "year": year, "events_in_response": len(events)}),
        )
        return

    ge = _build_event_general_embed(match, key)
    await send_embed_with_export(
        interaction,
        ge,
        export_csv=_dicts_to_csv([_event_row_flat(match)]),
        export_json=_json_export({"event_key": key, "event": match}),
    )


@client.tree.command(name="peek_rankings", description="Full event rankings (W–L–T, DQ)")
@app_commands.describe(event_key="Event key, e.g. 2025txdal or 2024cmp")
async def peek_rankings(interaction: discord.Interaction, event_key: str) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    status, data = await api.event_rankings(key, api_key=ak)
    if status == 404:
        e = _base_embed(title="No rankings", color=COLOR_WARN)
        e.description = f"No rankings for `{key}`."
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "http_status": status, "response": data}),
        )
        return
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "http_status": status, "response": data}),
        )
        return

    rows = data.get("event_rankings") or []
    if not rows:
        e = _base_embed(title=data.get("event_key", key), color=COLOR_WARN)
        e.description = "No ranking rows."
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": data.get("event_key", key), "event_rankings": []}),
        )
        return

    ek = data.get("event_key", key)
    pages = _build_ranking_pages(rows, ek)
    await send_paginated(
        interaction,
        pages,
        export_csv=_rankings_to_csv(rows),
        export_json=_rankings_to_json(rows, ek),
    )


@client.tree.command(
    name="peek_events",
    description="List FRC season events (week, district, location; optional country/state/district filters)",
)
@app_commands.describe(
    year="Season year, e.g. 2025",
    limit="Max events from API (default 24; paginated in Discord)",
    state_prov="Filter by state/province code",
    district_key="District key filter",
    country="Filter by country (e.g. USA, Canada)",
)
async def peek_events(
    interaction: discord.Interaction,
    year: int,
    limit: int = DEFAULT_EVENTS_LIMIT,
    state_prov: str | None = None,
    district_key: str | None = None,
    country: str | None = None,
) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    status, data = await api.events_for_year(
        year,
        limit=limit,
        state_prov=state_prov,
        district_key=district_key,
        country=country,
        api_key=ak,
    )
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"year": year, "http_status": status, "response": data}),
        )
        return

    events = data.get("events") or []
    if not events:
        e = _base_embed(title=f"Events {year}", color=COLOR_WARN)
        e.description = "No events match your filters."
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export(
                {
                    "year": year,
                    "filters": {
                        "state_prov": state_prov,
                        "district_key": district_key,
                        "country": country,
                        "limit": limit,
                    },
                    "events": [],
                }
            ),
        )
        return

    pages = _build_events_list_pages(events, year)
    filt = {"state_prov": state_prov, "district_key": district_key, "country": country, "limit": limit}
    await send_paginated(
        interaction,
        pages,
        export_csv=_dicts_to_csv([_event_row_flat(ev) for ev in events]),
        export_json=_json_export({"year": year, "filters": filt, "events": events}),
    )


@client.tree.command(
    name="peek_teams",
    description="Search teams by season (location/district filters; paginated in Discord)",
)
@app_commands.describe(
    year="Season year, e.g. 2025 (teams with EPA data for this season)",
    limit="Max teams from API (1–100; default 24)",
    city="Filter by city name",
    state_prov="Filter by state/province code",
    district_key="District key filter",
    country="Filter by country (e.g. USA, Canada)",
)
async def peek_teams(
    interaction: discord.Interaction,
    year: int,
    limit: int = DEFAULT_TEAMS_LIMIT,
    city: str | None = None,
    state_prov: str | None = None,
    district_key: str | None = None,
    country: str | None = None,
) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    lim = max(1, min(int(limit), MAX_TEAMS_API_LIMIT))
    status, data = await api.teams_list(
        year=year,
        limit=lim,
        city=(city.strip() if city else None),
        state_prov=(state_prov.strip() if state_prov else None),
        district_key=(district_key.strip() if district_key else None),
        country=(country.strip() if country else None),
        api_key=ak,
    )
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"year": year, "http_status": status, "response": data}),
        )
        return

    rows = data.get("team_info") or []
    if not rows:
        e = _base_embed(title=f"Teams · {year}", color=COLOR_WARN)
        e.url = f"{SITE_URL}/teams"
        e.description = "No teams match your filters."
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export(
                {
                    "year": year,
                    "filters": {
                        "city": city,
                        "state_prov": state_prov,
                        "district_key": district_key,
                        "country": country,
                        "limit": lim,
                    },
                    "team_info": [],
                    "next": data.get("next"),
                }
            ),
        )
        return

    pages = _build_teams_list_pages(rows, year)
    filt = {
        "city": city,
        "state_prov": state_prov,
        "district_key": district_key,
        "country": country,
        "limit": lim,
    }
    export_obj = {"year": year, "filters": filt, "team_info": rows, "next": data.get("next")}
    await send_paginated(
        interaction,
        pages,
        export_csv=_dicts_to_csv([_team_row_flat(t) for t in rows]),
        export_json=_json_export(export_obj),
    )


@client.tree.command(name="peek_event_keys", description="Event keys for a year (compact list for scripts / search)")
@app_commands.describe(
    year="Season year",
    state_prov="Optional state filter",
    district_key="Optional district filter",
    country="Optional country filter (e.g. USA, Canada)",
)
async def peek_event_keys(
    interaction: discord.Interaction,
    year: int,
    state_prov: str | None = None,
    district_key: str | None = None,
    country: str | None = None,
) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    status, data = await api.event_keys(
        year,
        state_prov=state_prov,
        district_key=district_key,
        country=country,
        api_key=ak,
    )
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"year": year, "http_status": status, "response": data}),
        )
        return

    keys = data.get("keys") or []
    if not keys:
        e = _base_embed(title=f"Event keys {year}", color=COLOR_WARN)
        e.description = "No keys returned."
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"year": year, "filters": {"state_prov": state_prov, "district_key": district_key, "country": country}, "keys": []}),
        )
        return

    pages = _build_event_keys_pages(keys, year)
    await send_paginated(
        interaction,
        pages,
        export_csv=_single_column_csv(keys, "event_key"),
        export_json=_json_export(data),
    )


@client.tree.command(name="peek_team_awards", description="Awards for a team, newest season first (optional year filter)")
@app_commands.describe(team_number="FRC team number", year="Optional season year filter")
async def peek_team_awards(interaction: discord.Interaction, team_number: int, year: int | None = None) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    status, data = await api.team_awards(team_number, year=year, api_key=ak)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"team_number": team_number, "year": year, "http_status": status, "response": data}),
        )
        return

    awards = _sort_team_awards_newest_first(_dedupe_team_awards(list(data.get("awards") or [])))
    if not awards:
        e = _base_embed(title=f"Awards · Team {data.get('team_number', team_number)}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/team/{team_number}"
        _apply_team_thumbnail(e, int(data.get("team_number", team_number)))
        e.description = "No awards in this filter."
        await send_embed_with_export(interaction, e, export_json=_json_export(data))
        return

    export_payload: Any = {**data, "awards": awards} if isinstance(data, dict) else data
    pages = _build_team_awards_pages(awards, data.get("team_number", team_number))
    await send_paginated(
        interaction,
        pages,
        export_csv=_dicts_to_csv(awards),
        export_json=_json_export(export_payload),
    )


@client.tree.command(name="peek_team_events", description="Event keys a team has played (optional year filter)")
@app_commands.describe(team_number="FRC team number", year="Optional season year")
async def peek_team_events(interaction: discord.Interaction, team_number: int, year: int | None = None) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    status, data = await api.team_events(team_number, year=year, api_key=ak)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"team_number": team_number, "year": year, "http_status": status, "response": data}),
        )
        return

    evs = data.get("events") or []
    e = _base_embed(title=f"Events · Team {data.get('team_number', team_number)}", color=COLOR_BRAND)
    e.url = f"{SITE_URL}/team/{team_number}"
    _apply_team_thumbnail(e, int(data.get("team_number", team_number)))
    if not evs:
        e.description = "No events in this filter."
        await send_embed_with_export(interaction, e, export_json=_json_export(data))
        return

    pages = _build_team_events_pages(evs, data.get("team_number", team_number))
    await send_paginated(
        interaction,
        pages,
        export_csv=_single_column_csv(evs, "event_key"),
        export_json=_json_export(data),
    )


@client.tree.command(
    name="peek_event_teams",
    description="Registered teams at an event (links, nicknames, locations; CSV/JSON export)",
)
@app_commands.describe(event_key="Event key, e.g. 2025txdal")
async def peek_event_teams(interaction: discord.Interaction, event_key: str) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    status, data = await api.event_teams(key, api_key=ak)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "http_status": status, "response": data}),
        )
        return

    rows = _coerce_event_teams_rows(data.get("teams"))
    ek = data.get("event_key", key)
    if not rows:
        e = _base_embed(title=f"Teams · {ek}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/event/{ek}"
        e.description = "No teams listed."
        await send_embed_with_export(interaction, e, export_json=_json_export(data))
        return

    export_payload: Any = {**data, "teams": rows} if isinstance(data, dict) else data
    pages = _build_event_teams_pages(rows, ek)
    await send_paginated(
        interaction,
        pages,
        export_csv=_dicts_to_csv(rows),
        export_json=_json_export(export_payload),
    )


@client.tree.command(name="peek_event_matches", description="Matches at an event, paginated (newest first; optional team filter)")
@app_commands.describe(event_key="Event key", team_number="Only matches involving this team")
async def peek_event_matches(
    interaction: discord.Interaction,
    event_key: str,
    team_number: int | None = None,
) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    status, data = await api.event_matches(key, team_number=team_number, api_key=ak)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "team_number": team_number, "http_status": status, "response": data}),
        )
        return

    matches = _sort_matches_newest_first(list(data.get("matches") or []))
    ek = data.get("event_key", key)
    if not matches:
        e = _base_embed(title=f"Matches · {ek}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/event/{ek}"
        e.description = "No matches returned."
        await send_embed_with_export(interaction, e, export_json=_json_export(data))
        return

    pages = _build_event_matches_pages(matches, ek, len(matches), team_number=team_number)
    export_payload: Any = {**data, "matches": matches} if isinstance(data, dict) else data
    await send_paginated(
        interaction,
        pages,
        export_csv=_dicts_to_csv(matches),
        export_json=_json_export(export_payload),
    )


@client.tree.command(name="peek_event_awards", description="Awards at an event (Blue Banner, etc.)")
@app_commands.describe(event_key="Event key", team_number="Only awards for this team")
async def peek_event_awards(
    interaction: discord.Interaction,
    event_key: str,
    team_number: int | None = None,
) -> None:
    ak = await _require_api_key(interaction)
    if not ak:
        return
    await interaction.response.defer(thinking=True)
    key = event_key.strip()
    status, data = await api.event_awards(key, team_number=team_number, api_key=ak)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "team_number": team_number, "http_status": status, "response": data}),
        )
        return

    rows = data.get("teams_and_awards") or []
    ek = data.get("event_key", key)
    if not rows:
        e = _base_embed(title=f"Awards · {ek}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/event/{ek}"
        e.description = "No awards returned."
        await send_embed_with_export(interaction, e, export_json=_json_export(data))
        return

    pages = _build_event_awards_pages(rows, ek, team_number=team_number)
    await send_paginated(
        interaction,
        pages,
        export_csv=_dicts_to_csv(rows),
        export_json=_json_export(data),
    )


@client.tree.command(
    name="peek_event_perfs",
    description="ACE, σ (confidence), RAW, and component breakdown at an event (all teams, or one if team_key is set)",
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
    ak = await _require_api_key(interaction)
    if not ak:
        return
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
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "team_key_input": team_key, "error": "invalid_team_key"}),
        )
        return

    if tk is not None:
        status, data = await api.event_perf_for_team(key, tk, api_key=ak)
        if status == 404:
            ek = key
            e = _base_embed(title=f"Event metrics · {ek} · Team {tk}", color=COLOR_WARN)
            e.url = f"{SITE_URL}/event/{ek}"
            _apply_team_thumbnail(e, int(tk))
            e.description = f"No metrics for team **{tk}** at this event."
            await send_embed_with_export(
                interaction,
                e,
                export_json=_json_export({"event_key": ek, "team_number": tk, "http_status": status, "response": data}),
            )
            return
        if status != 200:
            e = _base_embed(title="API error", color=COLOR_ERR)
            e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
            await send_embed_with_export(
                interaction,
                e,
                export_json=_json_export({"event_key": key, "team_number": tk, "http_status": status, "response": data}),
            )
            return
        ek = str(data.get("event_key", key))
        perfs = [data]
        sorted_p = perfs
        pages = _build_event_perfs_pages(sorted_p, ek, len(perfs), team_filter=int(tk))
        await send_paginated(
            interaction,
            pages,
            export_csv=_event_perfs_to_csv(sorted_p, ek),
            export_json=_event_perfs_to_json(sorted_p, ek),
        )
        return

    status, data = await api.event_perfs(key, api_key=ak)
    if status != 200:
        e = _base_embed(title="API error", color=COLOR_ERR)
        e.description = _truncate(f"HTTP **{status}** — `{_detail_from_api(data)}`", EMBED_DESC_SAFE)
        await send_embed_with_export(
            interaction,
            e,
            export_json=_json_export({"event_key": key, "http_status": status, "response": data}),
        )
        return

    perfs = data.get("perfs") or []
    ek = data.get("event_key", key)
    if not perfs:
        e = _base_embed(title=f"Event metrics · {ek}", color=COLOR_BRAND)
        e.url = f"{SITE_URL}/event/{ek}"
        e.description = "No per-team metrics."
        await send_embed_with_export(interaction, e, export_json=_json_export(data))
        return

    sorted_p = sorted(perfs, key=ace_key, reverse=True)
    pages = _build_event_perfs_pages(sorted_p, ek, len(perfs))
    await send_paginated(
        interaction,
        pages,
        export_csv=_event_perfs_to_csv(sorted_p, ek),
        export_json=_event_perfs_to_json(sorted_p, ek),
    )


def main() -> None:
    _require_env()
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
