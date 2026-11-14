#!/usr/bin/env python3
"""Render the animated SVG cards for the GitHub profile README.

    python scripts/render_cards.py            # animated cards  -> cards/
    STATIC=1 python scripts/render_cards.py   # final frame     -> cards-static/ (+ preview.html)

Cards are displayed through <img>, so only self-contained SVG is used:
an inline <style> (CSS keyframes), SMIL <animate>, and Fira Code subset with
pyftsubset and embedded as base64 data URIs. No scripts, no external requests.

Reduced motion: CSS animations are switched off with a media query, and every
SMIL animation lives inside a .deco group that the same media query hides.
Anything that carries information is animated with CSS only.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import urllib.request
from collections import defaultdict
from pathlib import Path

# --------------------------------------------------------------------------- config
ROOT = Path(__file__).resolve().parent.parent
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("cards-static" if STATIC else "cards")
DATA = ROOT / "data" / "contributions.json"
FONT_SRC = ROOT / "assets" / "fonts" / "FiraCode-VF.ttf"
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/firacode/FiraCode%5Bwght%5D.ttf"
PORTRAIT_GLOB = "assets/portrait.*"  # optional: png / jpg / webp

NAME = "Numan Faisal"
TAGLINE = "Full-stack & AI engineer. Production systems, not prototypes."
LOCATION = "Jamshedpur, India"

# Palette lifted from the current README (capsule header gradient, #B983FF / #8A2BE2 accents).
C = dict(
    bg_a="#0f0c29", bg_b="#302b63", bg_c="#24243e", ink="#0d1117",
    brand="#b983ff", brand2="#8a2be2", soft="#e0c3fc", deep="#4b0082",
    text="#c9d1d9", muted="#8b949e",
)
# Graph levels 0-4, from empty to brand colour.
LEVELS = ["#1a1730", "#3d2a6e", "#6a35b8", "#8a2be2", "#b983ff"]

TILES = [  # links live in README.md; the SVG only draws the tile
    dict(slug="linkedin", icon="in", label="LinkedIn", sub="in/numan-faisal-2580a3219"),
    dict(slug="email", icon="mail", label="Email", sub="numanfaisal980@gmail.com"),
    dict(slug="repos", icon="code", label="Repositories", sub="github.com/NumanFaisal"),
]

# Featured repos. `desc` / `lang` here win over whatever data/contributions.json has,
# so private repos work too (they just have no star/fork counts).
FEATURED = [
    dict(repo="NumanFaisal/Mystery-Message", desc="Anonymous messaging app with AI-suggested prompts. Next.js, NextAuth, MongoDB, Resend.", lang="TypeScript"),
    dict(repo="NumanFaisal/medium", desc="Medium clone UI built with TypeScript.", lang="TypeScript"),
]

LANG_COLORS = {
    "TypeScript": "#3178c6", "JavaScript": "#f1e05a", "Python": "#3572a5", "HTML": "#e34c26",
    "CSS": "#563d7c", "Java": "#b07219", "Ruby": "#701516", "Go": "#00add8", "Rust": "#dea584",
}

# Page timeline: each card starts at its own base delay so the page plays top to bottom.
T = dict(hero=0.0, tiles=0.9, activity=1.5, repos=2.5, repo_step=0.25, graph=3.3)

W = 900
FONT_STACK = "'Fira Code',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

# --------------------------------------------------------------------------- helpers
esc = lambda s: html.escape(str(s), quote=True)


def fit(text: str, avail: float, size: float) -> float:
    """Largest font size <= size that fits `text` in `avail` px (Fira Code advance = 0.6em)."""
    return min(size, avail / (0.6 * max(len(text), 1)))


def tw(text: str, size: float) -> float:
    return 0.6 * size * len(text)


def dly(t: float) -> str:
    return "" if STATIC else f' style="animation-delay:{t:.2f}s"'


def deco(inner: str) -> str:
    """SMIL lives here. Hidden by the reduced-motion query, absent in STATIC mode."""
    return "" if STATIC else f'<g class="deco">{inner}</g>'


# --------------------------------------------------------------------------- fonts
try:
    import brotli  # noqa: F401
    FLAVOR, MIME = "woff2", "font/woff2"
except ImportError:  # still valid, just bigger
    FLAVOR, MIME = "woff", "font/woff"

_TMP = Path(tempfile.mkdtemp(prefix="cards-"))
_STATIC_FONTS: dict[int, Path] = {}


def static_fonts() -> dict[int, Path]:
    """Pin the variable font to two static weights once per run."""
    if _STATIC_FONTS:
        return _STATIC_FONTS
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    if not FONT_SRC.exists():
        FONT_SRC.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(FONT_URL, FONT_SRC)
    for w in (400, 700):
        f = TTFont(FONT_SRC)
        if "fvar" in f:
            f = instancer.instantiateVariableFont(f, {"wght": w})
        f.recalcTimestamp = False  # keeps output byte-stable between runs
        p = _TMP / f"fira-{w}.ttf"
        f.save(p)
        _STATIC_FONTS[w] = p
    return _STATIC_FONTS


def font_faces(chars: str) -> str:
    chars = "".join(sorted(set(chars) | set(" 0123456789")))
    txt = _TMP / "chars.txt"
    txt.write_text(chars, encoding="utf-8")
    faces = []
    for weight, src in static_fonts().items():
        out = _TMP / f"subset-{weight}.{FLAVOR}"
        subprocess.run(
            [sys.executable, "-m", "fontTools.subset", str(src), f"--text-file={txt}",
             f"--flavor={FLAVOR}", "--layout-features=", "--no-hinting", "--desubroutinize",
             f"--output-file={out}"],
            check=True, capture_output=True,
        )
        b64 = base64.b64encode(out.read_bytes()).decode()
        faces.append(
            f"@font-face{{font-family:'Fira Code';font-weight:{weight};"
            f"src:url(data:{MIME};base64,{b64}) format('{FLAVOR}')}}"
        )
    return "".join(faces)


# --------------------------------------------------------------------------- svg shell
ANIM_CSS = """
.fade{animation:fade .7s ease-out both}
.up{animation:up .7s cubic-bezier(.2,.7,.2,1) both}
.pop{animation:pop .55s cubic-bezier(.2,.9,.3,1.2) both;transform-box:fill-box;transform-origin:center}
.grow{animation:grow .8s cubic-bezier(.2,.7,.2,1) both;transform-box:fill-box;transform-origin:50% 100%}
.draw{stroke-dasharray:1;animation:draw 1.1s ease-out both}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes up{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes pop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
@keyframes grow{from{transform:scaleY(0)}to{transform:none}}
@keyframes draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.deco{display:none}}
"""

DEFS = f"""<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{C['bg_a']}"/><stop offset=".6" stop-color="#1b1840"/><stop offset="1" stop-color="{C['ink']}"/></linearGradient>
<linearGradient id="hero" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{C['bg_a']}"/><stop offset=".55" stop-color="{C['bg_b']}"/><stop offset="1" stop-color="{C['bg_c']}"/></linearGradient>
<linearGradient id="brandText" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{C['soft']}"/><stop offset="1" stop-color="{C['brand']}"/></linearGradient>
<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C['brand2']}" stop-opacity=".95"/><stop offset="1" stop-color="{C['deep']}" stop-opacity=".55"/></linearGradient>
<linearGradient id="barHi" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{C['soft']}"/><stop offset=".35" stop-color="{C['brand']}"/><stop offset="1" stop-color="{C['brand2']}"/></linearGradient>
<linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".07"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
</defs>"""


def used_chars(markup: str) -> str:
    return html.unescape("".join(re.findall(r">([^<>]+)<", markup)))


def svg_doc(w: int, h: int, body: str, title: str, desc: str = "") -> str:
    css = f"text{{font-family:{FONT_STACK}}}.b{{font-weight:700}}" + ("" if STATIC else ANIM_CSS)
    faces = font_faces(used_chars(body))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{esc(title)}">\n<title>{esc(title)}</title><desc>{esc(desc or title)}</desc>\n'
        f"{DEFS}\n<style>{faces}{css}</style>\n{body}\n</svg>\n"
    )


def card_bg(w: int, h: int, fill: str = "bg", sweep_at: float | None = None) -> str:
    out = (
        f'<clipPath id="clip"><rect width="{w}" height="{h}" rx="18"/></clipPath>'
        f'<rect width="{w}" height="{h}" rx="18" fill="url(#{fill})"/>'
        f'<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="17.5" fill="none" stroke="{C["brand"]}" stroke-opacity=".22"/>'
    )
    if sweep_at is not None:
        out += deco(
            f'<g clip-path="url(#clip)"><rect y="0" width="140" height="{h}" fill="url(#sweep)">'
            f'<animate attributeName="x" from="-160" to="{w+20}" dur="7s" begin="{sweep_at:.2f}s" repeatCount="indefinite"/>'
            f"</rect></g>"
        )
    return out


# --------------------------------------------------------------------------- data
def load_data() -> dict:
    raw = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    days: dict[dt.date, int] = {}
    if "days" in raw:
        for d in raw["days"]:
            days[dt.date.fromisoformat(d["date"])] = int(d["count"])
    else:  # also accept a raw GraphQL dump of contributionCalendar
        def find(o):
            if isinstance(o, dict):
                if "weeks" in o:
                    return o
                for v in o.values():
                    r = find(v)
                    if r:
                        return r
            return None
        cal = find(raw)
        for wk in (cal or {}).get("weeks", []):
            for d in wk["contributionDays"]:
                days[dt.date.fromisoformat(d["date"])] = int(d["contributionCount"])
    return dict(days=days, repos=raw.get("repos", {}))


def monthly(days: dict[dt.date, int], last: dt.date) -> list[tuple[str, int]]:
    y, m = last.year, last.month
    keys = []
    for _ in range(12):
        keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    sums: dict[tuple[int, int], int] = defaultdict(int)
    for d, n in days.items():
        sums[(d.year, d.month)] += n
    return [(dt.date(y, m, 1).strftime("%b"), sums[(y, m)]) for y, m in keys]


def streaks(days: dict[dt.date, int], last: dt.date) -> tuple[int, int]:
    if not days:
        return 0, 0
    first = min(days)
    longest = run = 0
    d = first
    while d <= last:
        run = run + 1 if days.get(d, 0) > 0 else 0
        longest = max(longest, run)
        d += dt.timedelta(days=1)
    d = last if days.get(last, 0) > 0 else last - dt.timedelta(days=1)  # today may not be over
    current = 0
    while days.get(d, 0) > 0:
        current += 1
        d -= dt.timedelta(days=1)
    return current, longest


# --------------------------------------------------------------------------- hero
def portrait_uri() -> str | None:
    files = sorted(ROOT.glob(PORTRAIT_GLOB))
    if not files:
        return None
    p = files[0]
    try:
        from PIL import Image
        im = Image.open(p).convert("RGB")
        s = min(im.size)
        l, t = (im.width - s) // 2, (im.height - s) // 2
        im = im.crop((l, t, l + s, t + s)).resize((320, 320), Image.LANCZOS)
        buf = _TMP / "portrait.jpg"
        im.save(buf, "JPEG", quality=82, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.read_bytes()).decode()
    except ImportError:
        mime = {"png": "image/png", "webp": "image/webp"}.get(p.suffix[1:].lower(), "image/jpeg")
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def card_hero() -> str:
    h = 300
    t0 = T["hero"]
    pic = portrait_uri()
    cx, cy, r = 745, 150, 88
    text_w = (cx - r - 50 - 60) if pic else (W - 120)

    label = "~/numanfaisal"
    cursor_x = 60 + tw(label, 15) + 5
    name_size = fit(NAME, text_w, 62)
    name_w = tw(NAME, name_size)
    tag_size = fit(TAGLINE, text_w, 18)

    b = card_bg(W, h, "hero")
    b += (
        f'<pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="{C["soft"]}" fill-opacity=".12"/></pattern>'
        f'<rect width="{W}" height="{h}" rx="18" fill="url(#dots)"/>'
    )
    b += deco(
        f'<circle cx="{cx}" cy="{cy}" r="150" fill="{C["brand2"]}" fill-opacity=".14">'
        f'<animate attributeName="r" values="140;170;140" dur="7s" repeatCount="indefinite"/>'
        f'<animate attributeName="fill-opacity" values=".10;.20;.10" dur="7s" repeatCount="indefinite"/></circle>'
    )
    # text column
    b += f'<g class="fade"{dly(t0 + .10)}><text x="60" y="96" font-size="15" fill="{C["brand"]}">{label}</text></g>'
    b += deco(
        f'<rect x="{cursor_x:.1f}" y="83" width="9" height="16" fill="{C["brand"]}">'
        f'<animate attributeName="opacity" values="1;1;0;0" keyTimes="0;.5;.5;1" dur="1.1s" begin="{t0+.8:.2f}s" repeatCount="indefinite"/></rect>'
    )
    b += (
        f'<g class="up"{dly(t0 + .20)}><text x="58" y="172" font-size="{name_size:.1f}" class="b" fill="url(#brandText)">{esc(NAME)}</text></g>'
        f'<line class="draw" pathLength="1" x1="60" y1="194" x2="{60 + min(name_w, 360):.0f}" y2="194" stroke="{C["brand"]}" stroke-width="3" stroke-linecap="round"{dly(t0 + .55)}/>'
        f'<g class="up"{dly(t0 + .45)}><text x="60" y="232" font-size="{tag_size:.1f}" fill="{C["text"]}">{esc(TAGLINE)}</text></g>'
        f'<g class="up"{dly(t0 + .60)}><circle cx="64" cy="261" r="4" fill="{C["brand2"]}"/><text x="78" y="266" font-size="13" fill="{C["muted"]}">{esc(LOCATION)}</text></g>'
    )
    # right column
    if pic:
        b += (
            f'<clipPath id="pc"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>'
            f'<g class="pop"{dly(t0 + .30)}><image href="{pic}" x="{cx-r}" y="{cy-r}" width="{2*r}" height="{2*r}" preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/></g>'
            f'<circle class="draw" pathLength="1" cx="{cx}" cy="{cy}" r="{r+8}" fill="none" stroke="{C["brand"]}" stroke-width="3" stroke-linecap="round" transform="rotate(-90 {cx} {cy})"{dly(t0 + .50)}/>'
        )
        b += deco(
            f'<circle cx="{cx}" cy="{cy}" r="{r+22}" fill="none" stroke="{C["soft"]}" stroke-opacity=".35" stroke-dasharray="3 9">'
            f'<animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" to="360 {cx} {cy}" dur="40s" repeatCount="indefinite"/></circle>'
        )
    else:
        for i, rr in enumerate((40, 70, 100)):
            b += f'<circle class="draw" pathLength="1" cx="{cx}" cy="{cy}" r="{rr}" fill="none" stroke="{C["brand"]}" stroke-opacity="{.55 - i*.15:.2f}" stroke-width="2"{dly(t0 + .35 + i*.15)}/>'
        b += f'<g class="pop"{dly(t0 + .30)}><circle cx="{cx}" cy="{cy}" r="9" fill="{C["brand"]}"/></g>'
        b += deco(
            f'<circle cx="{cx}" cy="{cy}" r="100" fill="none" stroke="{C["soft"]}" stroke-opacity=".4" stroke-dasharray="3 9">'
            f'<animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" to="360 {cx} {cy}" dur="40s" repeatCount="indefinite"/></circle>'
        )
    return svg_doc(W, h, b, f"{NAME}: {TAGLINE}", f"{NAME}, {TAGLINE} Based in {LOCATION}.")


# --------------------------------------------------------------------------- tiles
def icon(kind: str) -> str:
    s = C["brand"]
    if kind == "in":
        return f'<text x="50" y="64" text-anchor="middle" font-size="22" class="b" fill="{s}">in</text>'
    if kind == "mail":
        return (f'<rect x="38" y="46" width="24" height="18" rx="3" fill="none" stroke="{s}" stroke-width="2"/>'
                f'<path d="M38 49l12 9 12-9" fill="none" stroke="{s}" stroke-width="2" stroke-linejoin="round"/>')
    return f'<text x="50" y="63" text-anchor="middle" font-size="17" class="b" fill="{s}">&lt;/&gt;</text>'


def card_tile(i: int, t: dict) -> str:
    w, h = 300, 112
    t0 = T["tiles"] + 0.12 * i
    sub_size = fit(t["sub"], w - 96 - 22, 12)
    b = f'<rect width="{w}" height="{h}" rx="16" fill="url(#bg)" class="fade"{dly(t0)}/>'
    b += (
        f'<rect class="draw" pathLength="1" x=".75" y=".75" width="{w-1.5}" height="{h-1.5}" rx="15.5" fill="none" '
        f'stroke="{C["brand"]}" stroke-opacity=".5" stroke-width="1.5"{dly(t0)}/>'
    )
    b += (
        f'<g class="up"{dly(t0 + .15)}>'
        f'<rect x="22" y="28" width="56" height="56" rx="14" fill="{C["brand"]}" fill-opacity=".12" stroke="{C["brand"]}" stroke-opacity=".35"/>'
        f'{icon(t["icon"])}'
        f'<text x="96" y="55" font-size="17" class="b" fill="{C["soft"]}">{esc(t["label"])}</text>'
        f'<text x="96" y="77" font-size="{sub_size:.1f}" fill="{C["muted"]}">{esc(t["sub"])}</text>'
        f"</g>"
    )
    b += deco(
        f'<g><path d="M266 38l14-14M268 24h12v12" fill="none" stroke="{C["brand"]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<animateTransform attributeName="transform" type="translate" values="0 0;2 -2;0 0" dur="2.4s" begin="{t0+1:.2f}s" repeatCount="indefinite"/></g>'
    )
    return svg_doc(w, h, b, f"{t['label']}: {t['sub']}")


# --------------------------------------------------------------------------- activity
def card_activity(data: dict) -> str:
    h = 350
    days = data["days"]
    last = max(days) if days else dt.date.today()
    t0 = T["activity"]
    months = monthly(days, last)
    cur, longest = streaks(days, last)
    year = [d for d in days if d > last - dt.timedelta(days=365)]
    active = sum(1 for d in year if days[d] > 0)
    total = sum(n for _, n in months)
    busy_d = max(days, key=lambda d: (days[d], d)) if days else None
    busy_n = days[busy_d] if busy_d else 0

    b = card_bg(W, h, sweep_at=t0 + 1.5)
    b += f'<g class="up"{dly(t0)}><text x="40" y="52" font-size="18" class="b" fill="{C["soft"]}">Contributions per month</text>'
    b += f'<text x="40" y="74" font-size="12" fill="{C["muted"]}">{total:,} across these 12 calendar months</text></g>'

    x0, slot, bw = 40, 46, 28
    base, top = 292, 124
    mx = max((n for _, n in months), default=0) or 1
    for k in (0, .5, 1):
        y = base - (base - top) * k
        b += f'<line x1="{x0-6}" x2="{x0 + 12*slot - 10}" y1="{y:.1f}" y2="{y:.1f}" stroke="{C["brand"]}" stroke-opacity=".12" stroke-dasharray="3 5"/>'
    hi = max(range(12), key=lambda i: months[i][1])
    for i, (lab, n) in enumerate(months):
        x = x0 + i * slot
        bh = max(3, (base - top) * n / mx)
        d = t0 + .35 + i * .06
        fill = "barHi" if i == hi else "bar"
        b += f'<rect class="grow" x="{x}" y="{base-bh:.1f}" width="{bw}" height="{bh:.1f}" rx="6" fill="url(#{fill})"{dly(d)}/>'
        vc = C["soft"] if i == hi else C["muted"]
        wt = ' class="b"' if i == hi else ""
        b += f'<g class="fade"{dly(d + .45)}><text x="{x + bw/2}" y="{base-bh-8:.1f}" text-anchor="middle" font-size="11"{wt} fill="{vc}">{n}</text></g>'
        b += f'<text x="{x + bw/2}" y="{base+20}" text-anchor="middle" font-size="11" fill="{C["muted"]}">{lab}</text>'

    # stat column
    b += f'<line x1="626" x2="626" y1="40" y2="{h-40}" stroke="{C["brand"]}" stroke-opacity=".18"/>'
    stats = [
        ("CURRENT STREAK", str(cur), "days" if cur != 1 else "day", f"longest {longest} days"),
        ("ACTIVE DAYS", str(active), "days", f"{round(100*active/365)}% of the last year"),
        ("BUSIEST DAY", str(busy_n), "contributions", busy_d.strftime("%b %-d, %Y") if busy_d else "n/a"),
    ]
    for i, (lab, val, unit, sub) in enumerate(stats):
        y = 92 + i * 90
        d = t0 + .6 + i * .18
        b += (
            f'<g class="up"{dly(d)}>'
            f'<text x="660" y="{y}" font-size="11" fill="{C["muted"]}" letter-spacing="1.5">{lab}</text>'
            f'<text x="660" y="{y+38}" font-size="36" class="b" fill="url(#brandText)">{val}</text>'
            f'<text x="{660 + tw(val, 36) + 10:.0f}" y="{y+38}" font-size="13" fill="{C["text"]}">{unit}</text>'
            f'<text x="660" y="{y+60}" font-size="12" fill="{C["muted"]}">{sub}</text></g>'
        )
    # streak flame, pulsing
    b += deco(
        f'<circle cx="850" cy="{92+30}" r="6" fill="{C["brand"]}"><animate attributeName="r" values="5;8;5" dur="1.8s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values="1;.4;1" dur="1.8s" repeatCount="indefinite"/></circle>'
    )
    desc = (f"Contributions per month over the last 12 months, {total} in total. Current streak {cur} days, "
            f"{active} active days, busiest day {busy_n} contributions.")
    return svg_doc(W, h, b, "Contributions per month", desc)


# --------------------------------------------------------------------------- repos
def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _star(cx: float, cy: float, r: float = 6.5) -> str:
    pts = []
    for k in range(10):
        rad = r if k % 2 == 0 else r * 0.45
        a = -math.pi / 2 + k * math.pi / 5
        pts.append(f"{cx + rad*math.cos(a):.1f},{cy + rad*math.sin(a):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="none" stroke="{C["muted"]}" stroke-width="1.3" stroke-linejoin="round"/>'


def card_repo(i: int, cfg: dict, meta: dict) -> str:
    h = 150
    t0 = T["repos"] + i * T["repo_step"]
    owner, name = cfg["repo"].split("/")
    desc = cfg.get("desc") or meta.get("description") or ""
    lang = cfg.get("lang") or meta.get("language")
    private = cfg.get("private") or meta.get("private")
    topics = list(meta.get("topics", []))[:3]

    b = card_bg(W, h, sweep_at=t0 + 1.2)
    b += f'<line class="draw" pathLength="1" x1="2" x2="2" y1="30" y2="{h-30}" stroke="{C["brand"]}" stroke-width="4" stroke-linecap="round"{dly(t0)}/>'
    # chips (right aligned)
    chips = (["private"] if private else []) + topics
    cx = W - 40
    chip_svg = ""
    for c in reversed(chips):
        cw = tw(c, 11) + 20
        cx -= cw
        chip_svg += (f'<rect x="{cx:.0f}" y="30" width="{cw:.0f}" height="22" rx="11" fill="{C["brand"]}" fill-opacity=".1" stroke="{C["brand"]}" stroke-opacity=".35"/>'
                     f'<text x="{cx + cw/2:.0f}" y="45" text-anchor="middle" font-size="11" fill="{C["soft"]}">{esc(c)}</text>')
        cx -= 8
    avail = max(cx - 80, 200)
    ns = fit(f"{owner}/{name}", avail, 19)
    b += (
        f'<g class="up"{dly(t0 + .1)}>'
        f'<path d="M40 31h10a3 3 0 013 3v14a3 3 0 01-3 3H42a3 3 0 01-3-3V33a2 2 0 011-2z M43 44h8" fill="none" stroke="{C["muted"]}" stroke-width="1.6" stroke-linejoin="round"/>'
        f'<text x="66" y="48" font-size="{ns:.1f}"><tspan fill="{C["muted"]}">{esc(owner)}/</tspan><tspan class="b" fill="{C["brand"]}">{esc(name)}</tspan></text>'
        f"{chip_svg}</g>"
    )
    wrap_w = int(780 / (0.6 * 13.5))
    lines = textwrap.wrap(desc, width=wrap_w)
    if len(lines) > 2:
        lines = [lines[0], lines[1][:-3].rstrip() + "..."]
    for k, ln in enumerate(lines):
        b += f'<g class="up"{dly(t0 + .22 + k*.08)}><text x="40" y="{82 + k*21}" font-size="13.5" fill="{C["text"]}">{esc(ln)}</text></g>'

    # meta row
    x, y = 40, 128
    row = ""
    if lang:
        row += f'<circle cx="{x+5}" cy="{y-4}" r="5" fill="{LANG_COLORS.get(lang, C["brand"])}"/><text x="{x+16}" y="{y}" font-size="12" fill="{C["muted"]}">{esc(lang)}</text>'
        x += 16 + tw(lang, 12) + 26
    if "stars" in meta:
        n = str(meta["stars"])
        row += f'{_star(x + 6, y - 5)}<text x="{x+18}" y="{y}" font-size="12" fill="{C["muted"]}">{n}</text>'
        x += 18 + tw(n, 12) + 22
    if "forks" in meta:
        n = str(meta["forks"])
        row += (f'<g fill="none" stroke="{C["muted"]}" stroke-width="1.4"><circle cx="{x+2}" cy="{y-10}" r="1.8"/><circle cx="{x+10}" cy="{y-10}" r="1.8"/><circle cx="{x+6}" cy="{y-1}" r="1.8"/>'
                f'<path d="M{x+2} {y-8}v2q0 2 4 2t4-2v-2M{x+6} {y-4}v1"/></g>'
                f'<text x="{x+18}" y="{y}" font-size="12" fill="{C["muted"]}">{n}</text>')
    b += f'<g class="fade"{dly(t0 + .45)}>{row}</g>'
    alt = f"{owner}/{name}" + (f": {desc}" if desc else "")
    return svg_doc(W, h, b, alt)


# --------------------------------------------------------------------------- graph
def card_graph(data: dict) -> str:
    days = data["days"]
    last = max(days) if days else dt.date.today()
    t0 = T["graph"]
    pitch, cell = 15, 12
    h = 232
    x0, y0 = 64, 92
    sunday = last - dt.timedelta(days=(last.weekday() + 1) % 7)
    start = sunday - dt.timedelta(weeks=52)

    nz = sorted(n for n in days.values() if n > 0)
    q = [nz[int(len(nz) * p)] for p in (.25, .5, .75)] if nz else [1, 2, 3]

    def level(n: int) -> int:  # quartile buckets over non-zero days, like GitHub
        return 0 if n <= 0 else 1 + sum(n > t for t in q)

    total = sum(n for d, n in days.items() if d > last - dt.timedelta(days=365))
    b = card_bg(W, h, sweep_at=t0 + 1.6)
    b += f'<g class="up"{dly(t0)}><text x="40" y="48" font-size="18" class="b" fill="{C["soft"]}">Contribution graph</text>'
    b += f'<text x="40" y="68" font-size="12" fill="{C["muted"]}">{total:,} contributions in the last year</text></g>'

    for r_, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        b += f'<text x="{x0-10}" y="{y0 + r_*pitch + 10}" text-anchor="end" font-size="10" fill="{C["muted"]}">{lab}</text>'
    prev_m = None
    for col in range(53):
        wk = start + dt.timedelta(weeks=col)
        cx_ = x0 + col * pitch
        if (col == 0 or wk.day <= 7) and wk.month != prev_m:
            crowded = col == 0 and (wk + dt.timedelta(weeks=1)).day <= 7  # next column starts a month
            if not crowded and col < 52:
                b += f'<text x="{cx_}" y="{y0-10}" font-size="10" fill="{C["muted"]}">{wk.strftime("%b")}</text>'
                prev_m = wk.month
        cells = ""
        for row in range(7):
            d = wk + dt.timedelta(days=row)
            if d > last:
                continue
            lv = level(days.get(d, 0))
            stroke = f' stroke="{C["soft"]}" stroke-width="1.2"' if (d == last and lv) else ""
            cells += f'<rect x="{cx_}" y="{y0 + row*pitch}" width="{cell}" height="{cell}" rx="3" fill="{LEVELS[lv]}"{stroke}/>'
        b += f'<g class="fade"{dly(t0 + .25 + col*.02)}>{cells}</g>'
    # today marker, pulsing
    tcol = (last - start).days // 7
    trow = (last.weekday() + 1) % 7
    b += deco(
        f'<rect x="{x0 + tcol*pitch - 2}" y="{y0 + trow*pitch - 2}" width="{cell+4}" height="{cell+4}" rx="5" fill="none" stroke="{C["soft"]}" stroke-width="1.5">'
        f'<animate attributeName="stroke-opacity" values="1;.1;1" dur="2s" begin="{t0+1.6:.2f}s" repeatCount="indefinite"/></rect>'
    )
    # legend
    lx = x0 + 53 * pitch - (5 * pitch + 78)
    ly = y0 + 7 * pitch + 22
    b += f'<g class="fade"{dly(t0 + 1.2)}><text x="{lx}" y="{ly+10}" text-anchor="end" font-size="10" fill="{C["muted"]}">Less</text>'
    for k in range(5):
        b += f'<rect x="{lx + 8 + k*pitch}" y="{ly}" width="{cell}" height="{cell}" rx="3" fill="{LEVELS[k]}"/>'
    b += f'<text x="{lx + 8 + 5*pitch + 4}" y="{ly+10}" font-size="10" fill="{C["muted"]}">More</text></g>'
    return svg_doc(W, h, b, "Contribution graph", f"GitHub contribution graph for the last year, {total} contributions.")


# --------------------------------------------------------------------------- main
def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = load_data()
    if not data["days"]:
        print(f"warning: no contribution data in {DATA}; charts will be empty", file=sys.stderr)
    cards: dict[str, str] = {"hero": card_hero()}
    for i, t in enumerate(TILES):
        cards[f"tile-{t['slug']}"] = card_tile(i, t)
    cards["activity"] = card_activity(data)
    for i, cfg in enumerate(FEATURED):
        cards[f"repo-{slug(cfg['repo'].split('/')[1])}"] = card_repo(i, cfg, data["repos"].get(cfg["repo"], {}))
    cards["graph"] = card_graph(data)

    for name, doc in cards.items():
        (OUT / f"{name}.svg").write_text(doc, encoding="utf-8")
        print(f"{OUT.relative_to(ROOT)}/{name}.svg  {len(doc)/1024:.1f} KB")

    if STATIC:  # a page that stacks the cards exactly like README.md, for screenshots
        tiles = "".join(f'<img src="tile-{t["slug"]}.svg" width="32.5%">' for t in TILES)
        repos = "".join(f'<br><img src="repo-{slug(c["repo"].split("/")[1])}.svg" width="100%">' for c in FEATURED)
        (OUT / "preview.html").write_text(
            '<!doctype html><meta charset="utf-8"><body style="background:#0d1117;margin:0;padding:24px">'
            '<div style="width:888px;margin:auto;text-align:center;line-height:0">'
            f'<img src="hero.svg" width="100%"><br>{tiles}<br><img src="activity.svg" width="100%">{repos}'
            '<br><img src="graph.svg" width="100%"></div>',
            encoding="utf-8",
        )
        print(f"open {OUT.relative_to(ROOT)}/preview.html and screenshot it")


if __name__ == "__main__":
    main()
