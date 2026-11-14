
#!/usr/bin/env python3
"""Render the animated SVG cards for the GitHub profile README (paper + serif look).
 
    python scripts/render_cards.py            # animated cards  -> cards/
    STATIC=1 python scripts/render_cards.py   # final frame     -> cards-static/ (+ preview.html)
 
Cards are displayed through <img>, so only self-contained SVG is used:
an inline <style> (CSS keyframes), SMIL <animate>, and fonts subset with
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
FONT_DIR = ROOT / "assets" / "fonts"
PORTRAIT_GLOB = "assets/portrait.*"  # optional: png / jpg / webp
 
USER = "NumanFaisal"
NAME = "Numan Faisal"
ROLE = "Full-stack & AI engineer"
TAGLINE = "I build AI products and full-stack systems that run in front of real users."
 
# Paper look. BRAND is your purple from the old README; change it here to recolour everything.
PAPER, INK, MUTED, HAIR = "#f3f3ef", "#111111", "#6f6f69", "#dcdcd4"
BRAND, RED, AMBER = "#8a2be2", "#f0392f", "#ffc21a"
# Graph levels 0-4: empty -> brand colour.
LEVELS = ["#e7e5ee", "#e2d3fa", "#c3a0f3", "#a362ea", BRAND]
 
# Links for the tiles live in README.md; the SVG only draws them.
TILES = [
    dict(slug="build", label="Build", title="LexAI", bg=BRAND, fg="#ffffff",
         desc="WhatsApp-first AI legal assistant for Indian advocates and SMEs."),
    dict(slug="linkedin", label="Connect", title="LinkedIn", bg=RED, fg="#ffffff",
         desc="Open to full-stack and AI engineering roles."),
    dict(slug="email", label="Write", title="Say hello", bg=AMBER, fg=INK,
         desc="numanfaisal980@gmail.com"),
]
 
# Featured repos. `desc` / `lang` here win over data/contributions.json,
# so private repos work too (they just have no star/fork counts).
FEATURED = [
    dict(repo="NumanFaisal/Mystery-Message", desc="Anonymous messaging app with AI-suggested prompts. Next.js, NextAuth, MongoDB, Resend.", lang="TypeScript"),
    dict(repo="NumanFaisal/medium", desc="Medium clone UI built with TypeScript.", lang="TypeScript"),
]
DOTS = [BRAND, RED, AMBER]
 
LANG_COLORS = {
    "TypeScript": "#3178c6", "JavaScript": "#f1e05a", "Python": "#3572a5", "HTML": "#e34c26",
    "CSS": "#563d7c", "Java": "#b07219", "Ruby": "#701516", "Go": "#00add8", "Rust": "#dea584",
}
 
# Page timeline: each card starts at its own base delay so the page plays top to bottom.
T = dict(hero=0.0, tiles=0.9, activity=1.5, repos=2.5, repo_step=0.25, graph=3.3)
 
W = 900
MONO = "'Fira Code',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
SERIF = "'Instrument Serif',Georgia,'Times New Roman',serif"
 
# --------------------------------------------------------------------------- helpers
esc = lambda s: html.escape(str(s), quote=True)
 
 
def tw(text: str, size: float, ls: float = 0.0) -> float:
    """Width of monospace text (Fira Code advance = 0.6em)."""
    return (0.6 * size + ls) * len(text)
 
 
def dly(t: float) -> str:
    return "" if STATIC else f' style="animation-delay:{t:.2f}s"'
 
 
def deco(inner: str) -> str:
    """SMIL lives here. Hidden by the reduced-motion query, absent in STATIC mode."""
    return "" if STATIC else f'<g class="deco">{inner}</g>'
 
 
def lab(x, y, s, fill=None, anchor="start", size=9.5) -> str:
    """Small mono label, uppercase, letter-spaced."""
    return (f'<text x="{x}" y="{y}" font-size="{size}" letter-spacing="1.4" text-anchor="{anchor}" '
            f'fill="{fill or MUTED}">{esc(s.upper())}</text>')
 
 
# --------------------------------------------------------------------------- fonts
FONT_FILES = {
    "InstrumentSerif-Regular.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/instrumentserif/InstrumentSerif-Regular.ttf",
    "InstrumentSerif-Italic.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/instrumentserif/InstrumentSerif-Italic.ttf",
    "FiraCode-VF.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/firacode/FiraCode%5Bwght%5D.ttf",
}
FACES = [  # (css family, weight, style, source file, wght to pin or None)
    ("Instrument Serif", 400, "normal", "InstrumentSerif-Regular.ttf", None),
    ("Instrument Serif", 400, "italic", "InstrumentSerif-Italic.ttf", None),
    ("Fira Code", 400, "normal", "FiraCode-VF.ttf", 400),
    ("Fira Code", 700, "normal", "FiraCode-VF.ttf", 700),
]
 
try:
    import brotli  # noqa: F401
    FLAVOR, MIME = "woff2", "font/woff2"
except ImportError:  # still valid, just bigger
    FLAVOR, MIME = "woff", "font/woff"
 
_TMP = Path(tempfile.mkdtemp(prefix="cards-"))
_PREP: list[Path] = []
_METRICS: dict[int, tuple] = {}
 
 
def prepared() -> list[Path]:
    """One static TTF per face (variable Fira Code is pinned to 400 and 700)."""
    if _PREP:
        return _PREP
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
 
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    for i, (_fam, _wt, _st, fn, wght) in enumerate(FACES):
        src = FONT_DIR / fn
        if not src.exists():
            urllib.request.urlretrieve(FONT_FILES[fn], src)
        f = TTFont(src)
        if wght and "fvar" in f:
            f = instancer.instantiateVariableFont(f, {"wght": wght})
        f.recalcTimestamp = False  # keeps output byte-stable between runs
        p = _TMP / f"face{i}.ttf"
        f.save(p)
        _PREP.append(p)
    return _PREP
 
 
def serif_w(text: str, size: float, italic: bool = False) -> float:
    """Exact width of serif text, read from the font's advance widths."""
    idx = 1 if italic else 0
    if idx not in _METRICS:
        from fontTools.ttLib import TTFont
        f = TTFont(prepared()[idx])
        _METRICS[idx] = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
    cmap, hmtx, upem = _METRICS[idx]
    return sum(hmtx[cmap.get(ord(c), ".notdef")][0] for c in text) * size / upem
 
 
def font_faces(chars: str) -> str:
    chars = "".join(sorted(set(chars) | set(" 0123456789")))
    txt = _TMP / "chars.txt"
    txt.write_text(chars, encoding="utf-8")
    faces = []
    for i, src in enumerate(prepared()):
        fam, wt, st, _fn, _wg = FACES[i]
        out = _TMP / f"subset-{i}.{FLAVOR}"
        subprocess.run(
            [sys.executable, "-m", "fontTools.subset", str(src), f"--text-file={txt}",
             f"--flavor={FLAVOR}", "--layout-features=", "--no-hinting", "--desubroutinize",
             f"--output-file={out}"],
            check=True, capture_output=True,
        )
        b64 = base64.b64encode(out.read_bytes()).decode()
        faces.append(
            f"@font-face{{font-family:'{fam}';font-weight:{wt};font-style:{st};"
            f"src:url(data:{MIME};base64,{b64}) format('{FLAVOR}')}}"
        )
    return "".join(faces)
 
 
# --------------------------------------------------------------------------- svg shell
ANIM_CSS = """
.fade{animation:fade .7s ease-out both}
.up{animation:up .7s cubic-bezier(.2,.7,.2,1) both}
.pop{animation:pop .55s cubic-bezier(.2,.9,.3,1.2) both;transform-box:fill-box;transform-origin:center}
.grow{animation:grow .8s cubic-bezier(.2,.7,.2,1) both;transform-box:fill-box;transform-origin:50% 100%}
.draw{stroke-dasharray:1;animation:draw 1s ease-out both}
@keyframes fade{from{opacity:0}to{opacity:1}}
@keyframes up{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes pop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
@keyframes grow{from{transform:scaleY(0)}to{transform:none}}
@keyframes draw{from{stroke-dashoffset:1}to{stroke-dashoffset:0}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.deco{display:none}}
"""
 
 
def used_chars(markup: str) -> str:
    return html.unescape("".join(re.findall(r">([^<>]+)<", markup)))
 
 
def svg_doc(w: int, h: int, body: str, title: str, desc: str = "") -> str:
    # No fill in the stylesheet: it would beat the fill="" attributes.
    css = (f"text{{font-family:{MONO}}}.s{{font-family:{SERIF};font-weight:400}}"
           ".i{font-style:italic}.b{font-weight:700}" + ("" if STATIC else ANIM_CSS))
    faces = font_faces(used_chars(body))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{esc(title)}">\n<title>{esc(title)}</title><desc>{esc(desc or title)}</desc>\n'
        f"<style>{faces}{css}</style>\n{body}\n</svg>\n"
    )
 
 
def paper(w: int, h: int, r: int = 16, fill: str = PAPER) -> str:
    return (f'<rect width="{w}" height="{h}" rx="{r}" fill="{fill}"/>'
            f'<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="{r-.5}" fill="none" stroke="#000" stroke-opacity=".08"/>')
 
 
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
    files = sorted(ROOT.glob(PORTRAIT_GLOB)) or sorted(ROOT.glob("portrait.*"))  # assets/ or repo root
    if not files:
        return None
    p = files[0]
    try:
        from PIL import Image
        im = Image.open(p).convert("RGB")
        pw, ph = 240, 270  # the frame in the hero card; centre-crop to the same shape
        cw, ch = (im.width, round(im.width * ph / pw)) if im.width * ph <= im.height * pw else (round(im.height * pw / ph), im.height)
        l, t = (im.width - cw) // 2, (im.height - ch) // 2
        im = im.crop((l, t, l + cw, t + ch)).resize((360, 405), Image.LANCZOS)
        buf = _TMP / "portrait.jpg"
        im.save(buf, "JPEG", quality=82, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.read_bytes()).decode()
    except ImportError:
        mime = {"png": "image/png", "webp": "image/webp"}.get(p.suffix[1:].lower(), "image/jpeg")
        return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()
 
 
def card_hero() -> str:
    h = 330
    t0 = T["hero"]
    pic = portrait_uri()
    first, last = NAME.split(" ", 1)
    size = min(104, 104 * 470 / max(serif_w(first, 104), serif_w(last, 104, True), 1))
    cx, cy, r = 730, 165, 112
 
    b = paper(W, h)
    b += (f'<g class="fade"{dly(t0 + .05)}><rect x="44" y="44" width="8" height="8" fill="{BRAND}"/>'
          f'{lab(62, 53, ROLE)}</g>')
    b += (f'<g class="up"{dly(t0 + .15)}><text class="s" x="40" y="148" font-size="{size:.1f}" fill="{INK}">{esc(first)}</text></g>'
          f'<g class="up"{dly(t0 + .30)}><text class="s i" x="40" y="{148 + size*.95:.0f}" font-size="{size:.1f}" fill="{INK}">{esc(last)}</text></g>')
    lines = textwrap.wrap(TAGLINE, width=int(470 / (0.6 * 12.5)))[:2]
    for k, ln in enumerate(lines):
        b += f'<g class="fade"{dly(t0 + .60 + k*.1)}><text x="44" y="{h - 52 + k*19}" font-size="12.5" fill="{MUTED}">{esc(ln)}</text></g>'
 
    if pic:  # your photo, in a rounded frame
        x, y, pw, ph = cx - 120, cy - 135, 240, 270
        b += (f'<clipPath id="pc"><rect x="{x}" y="{y}" width="{pw}" height="{ph}" rx="14"/></clipPath>'
              f'<g class="pop"{dly(t0 + .30)}><image href="{pic}" x="{x}" y="{y}" width="{pw}" height="{ph}" '
              f'preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/></g>')
    else:  # engraved scan-line disc
        n = 0
        for y in range(cy - r + 4, cy + r - 3, 5):
            dy = y - cy
            half = math.sqrt(max(r * r - dy * dy, 0))
            sw = 1 + 2.2 * (1 - abs(dy) / r) ** 0.8 * (0.6 + 0.4 * math.sin(n * 0.9))
            b += (f'<line class="draw" pathLength="1" x1="{cx-half:.1f}" x2="{cx+half:.1f}" y1="{y}" y2="{y}" '
                  f'stroke="{INK}" stroke-width="{sw:.2f}" stroke-linecap="round"{dly(t0 + .30 + n*.02)}/>')
            n += 1
        b += deco(
            f'<clipPath id="art"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>'
            f'<g clip-path="url(#art)"><rect x="{cx-r}" y="{cy-r}" width="{2*r}" height="3" fill="{BRAND}">'
            f'<animate attributeName="y" values="{cy-r};{cy+r};{cy-r}" dur="6s" begin="{t0+1.4:.2f}s" repeatCount="indefinite"/></rect></g>'
        )
    return svg_doc(W, h, b, f"{NAME}: {ROLE}", f"{NAME}, {ROLE}. {TAGLINE}")
 
 
# --------------------------------------------------------------------------- tiles
def card_tile(i: int, t: dict) -> str:
    w, h = 300, 150
    t0 = T["tiles"] + 0.12 * i
    tsize = min(36, 36 * (w - 44) / max(serif_w(t["title"], 36, True), 1))
    b = f'<g class="up"{dly(t0)}><rect width="{w}" height="{h}" rx="16" fill="{t["bg"]}"/>'
    b += lab(22, 34, t["label"], fill=t["fg"], size=9.5)
    b += f'<text class="s i" x="21" y="86" font-size="{tsize:.1f}" fill="{t["fg"]}">{esc(t["title"])}</text>'
    for k, ln in enumerate(textwrap.wrap(t["desc"], width=int((w - 44) / (0.6 * 10.5)))[:2]):
        b += f'<text class="b" x="22" y="{110 + k*16}" font-size="10.5" fill="{t["fg"]}">{esc(ln)}</text>'
    b += "</g>"
    b += deco(
        f'<g><path d="M268 36l12-12M270 24h10v10" fill="none" stroke="{t["fg"]}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<animateTransform attributeName="transform" type="translate" values="0 0;2 -2;0 0" dur="2.4s" begin="{t0+1:.2f}s" repeatCount="indefinite"/></g>'
    )
    return svg_doc(w, h, b, f"{t['title']}: {t['desc']}")
 
 
# --------------------------------------------------------------------------- activity
def card_activity(data: dict) -> str:
    h = 360
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
 
    b = paper(W, h)
    b += f'<g class="up"{dly(t0)}>{lab(40, 46, "The last 12 months on GitHub")}'
    b += f'<text class="s" x="38" y="112" font-size="62" fill="{INK}">{total:,} <tspan class="i">contributions</tspan></text></g>'
 
    x0, slot, bw = 40, 46, 30
    base, top = 318, 156
    mx = max((n for _, n in months), default=0) or 1
    hi = max(range(12), key=lambda i: months[i][1])
    for i, (m, n) in enumerate(months):
        x = x0 + i * slot
        bh = max(3, (base - top) * n / mx)
        d = t0 + .35 + i * .06
        fill = BRAND if i == hi else ("#cfcfc7" if i == 11 else INK)  # grey = month still in progress
        b += f'<rect class="grow" x="{x}" y="{base-bh:.1f}" width="{bw}" height="{bh:.1f}" rx="3" fill="{fill}"{dly(d)}/>'
        if i == hi:
            b += f'<g class="fade"{dly(d + .45)}><text class="b" x="{x + bw/2}" y="{base-bh-8:.1f}" text-anchor="middle" font-size="9.5" fill="{BRAND}">{n:,}</text></g>'
        b += f'<text x="{x + bw/2}" y="{base+20}" text-anchor="middle" font-size="9" fill="{MUTED}">{m}</text>'
 
    b += f'<line x1="628" x2="628" y1="44" y2="{h-44}" stroke="{HAIR}"/>'
    stats = [
        (str(cur), "days" if cur != 1 else "day", f"current streak, longest {longest}"),
        (str(active), "/ 365", "days with a commit"),
        (str(busy_n), "", f"on the busiest day, {busy_d.strftime('%b %-d') if busy_d else 'n/a'}"),
    ]
    for i, (num, unit, cap) in enumerate(stats):
        y0 = 60 + i * 92
        d = t0 + .6 + i * .18
        unit_svg = f' <tspan class="i" font-size="26">{esc(unit)}</tspan>' if unit else ""
        b += (f'<g class="up"{dly(d)}><text class="s" x="660" y="{y0+44}" font-size="46" fill="{INK}">{num}{unit_svg}</text>'
              f'<text x="660" y="{y0+66}" font-size="9.5" fill="{MUTED}">{esc(cap)}</text></g>')
        if i < 2:
            b += f'<line x1="660" x2="860" y1="{y0+82}" y2="{y0+82}" stroke="{HAIR}"/>'
    desc = (f"{total} contributions over the last 12 months. Current streak {cur} days, longest {longest}. "
            f"{active} active days. Busiest day {busy_n} contributions.")
    return svg_doc(W, h, b, "Contributions per month", desc)
 
 
# --------------------------------------------------------------------------- repos
def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
 
 
def _star(cx: float, cy: float, r: float = 6.0) -> str:
    pts = []
    for k in range(10):
        rad = r if k % 2 == 0 else r * 0.45
        a = -math.pi / 2 + k * math.pi / 5
        pts.append(f"{cx + rad*math.cos(a):.1f},{cy + rad*math.sin(a):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="none" stroke="{MUTED}" stroke-width="1.2" stroke-linejoin="round"/>'
 
 
def card_repo(i: int, cfg: dict, meta: dict) -> str:
    h = 132
    t0 = T["repos"] + i * T["repo_step"]
    owner, name = cfg["repo"].split("/")
    title = name if owner == USER else f"{owner}/{name}"
    desc = cfg.get("desc") or meta.get("description") or ""
    lang = cfg.get("lang") or meta.get("language")
    private = cfg.get("private") or meta.get("private")
    chips = (["private"] if private else []) + list(meta.get("topics", []))[:3]
 
    b = f'<g class="up"{dly(t0)}>{paper(W, h)}</g>'
    b += f'<g class="pop"{dly(t0 + .15)}><circle cx="46" cy="41" r="5" fill="{DOTS[i % 3]}"/></g>'
    b += f'<g class="fade"{dly(t0 + .2)}><text class="b" x="60" y="46" font-size="14.5" fill="{INK}">{esc(title)}</text></g>'
 
    cx = W - 40
    for c in reversed(chips):
        cw = tw(c, 9, 1) + 20
        cx -= cw
        b += (f'<g class="fade"{dly(t0 + .3)}><rect x="{cx:.0f}" y="30" width="{cw:.0f}" height="20" rx="10" fill="none" stroke="{HAIR}"/>'
              f'<text x="{cx + cw/2:.0f}" y="43.5" text-anchor="middle" font-size="9" letter-spacing="1" fill="{MUTED}">{esc(c.upper())}</text></g>')
        cx -= 8
 
    lines = textwrap.wrap(desc, width=int(800 / (0.6 * 12)))
    if len(lines) > 2:
        lines = [lines[0], lines[1][:-3].rstrip() + "..."]
    for k, ln in enumerate(lines):
        b += f'<g class="fade"{dly(t0 + .3 + k*.08)}><text x="44" y="{74 + k*19}" font-size="12" fill="#45453f">{esc(ln)}</text></g>'
 
    x, y = 44, 114
    row = ""
    if lang:
        row += f'<circle cx="{x+4}" cy="{y-3.5}" r="4" fill="{LANG_COLORS.get(lang, BRAND)}"/><text x="{x+14}" y="{y}" font-size="10" fill="{MUTED}">{esc(lang)}</text>'
        x += 14 + tw(lang, 10) + 22
    if "stars" in meta:
        n = str(meta["stars"])
        row += f'{_star(x + 6, y - 4)}<text x="{x+16}" y="{y}" font-size="10" fill="{MUTED}">{n}</text>'
        x += 16 + tw(n, 10) + 20
    if "forks" in meta:
        n = str(meta["forks"])
        row += (f'<g fill="none" stroke="{MUTED}" stroke-width="1.3"><circle cx="{x+2}" cy="{y-9}" r="1.7"/><circle cx="{x+10}" cy="{y-9}" r="1.7"/><circle cx="{x+6}" cy="{y-1}" r="1.7"/>'
                f'<path d="M{x+2} {y-7}v2q0 2 4 2t4-2v-2M{x+6} {y-3.5}v1"/></g>'
                f'<text x="{x+16}" y="{y}" font-size="10" fill="{MUTED}">{n}</text>')
    b += f'<g class="fade"{dly(t0 + .45)}>{row}</g>'
    return svg_doc(W, h, b, f"{title}" + (f": {desc}" if desc else ""))
 
 
# --------------------------------------------------------------------------- graph
def card_graph(data: dict) -> str:
    days = data["days"]
    last = max(days) if days else dt.date.today()
    t0 = T["graph"]
    h = 250
    x0, y0 = 40, 98
    pitch = (W - 2 * x0 + 3) / 53
    cell = pitch - 3
    sunday = last - dt.timedelta(days=(last.weekday() + 1) % 7)
    start = sunday - dt.timedelta(weeks=52)
 
    nz = sorted(n for n in days.values() if n > 0)
    q = [nz[int(len(nz) * p)] for p in (.25, .5, .75)] if nz else [1, 2, 3]
 
    def level(n: int) -> int:  # quartile buckets over non-zero days, like GitHub
        return 0 if n <= 0 else 1 + sum(n > t for t in q)
 
    total = sum(n for d, n in days.items() if d > last - dt.timedelta(days=365))
    b = paper(W, h)
    b += (f'<g class="up"{dly(t0)}><text class="s" x="38" y="60" font-size="36" fill="{INK}">Every day, '
          f'<tspan class="i" fill="{BRAND}">in purple</tspan></text>'
          f'{lab(W - 40, 50, "Refreshed daily", anchor="end", size=9)}</g>')
 
    prev_m = None
    for col in range(53):
        wk = start + dt.timedelta(weeks=col)
        cx_ = x0 + col * pitch
        if (col == 0 or wk.day <= 7) and wk.month != prev_m:
            crowded = col == 0 and (wk + dt.timedelta(weeks=1)).day <= 7  # next column starts a month
            if not crowded and col < 52:
                b += f'<text x="{cx_:.1f}" y="{y0-10}" font-size="9" fill="{MUTED}">{wk.strftime("%b")}</text>'
                prev_m = wk.month
        cells = ""
        for row in range(7):
            d = wk + dt.timedelta(days=row)
            if d > last:
                continue
            cells += (f'<rect x="{cx_:.1f}" y="{y0 + row*pitch:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="2.5" '
                      f'fill="{LEVELS[level(days.get(d, 0))]}"/>')
        b += f'<g class="fade"{dly(t0 + .25 + col*.02)}>{cells}</g>'
    tcol = (last - start).days // 7
    trow = (last.weekday() + 1) % 7
    b += deco(
        f'<rect x="{x0 + tcol*pitch - 2:.1f}" y="{y0 + trow*pitch - 2:.1f}" width="{cell+4:.1f}" height="{cell+4:.1f}" rx="4.5" fill="none" stroke="{INK}" stroke-width="1.3">'
        f'<animate attributeName="stroke-opacity" values="1;.1;1" dur="2s" begin="{t0+1.6:.2f}s" repeatCount="indefinite"/></rect>'
    )
    ly = y0 + 7 * pitch + 14
    lx = W - 40 - (5 * (cell + 3) + 62)
    b += f'<g class="fade"{dly(t0 + 1.2)}><text x="{lx}" y="{ly+9}" text-anchor="end" font-size="9" fill="{MUTED}">less</text>'
    for k in range(5):
        b += f'<rect x="{lx + 8 + k*(cell+3):.1f}" y="{ly}" width="{cell:.1f}" height="{cell:.1f}" rx="2.5" fill="{LEVELS[k]}"/>'
    b += f'<text x="{lx + 8 + 5*(cell+3) + 4:.1f}" y="{ly+9}" font-size="9" fill="{MUTED}">more</text></g>'
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
 
    for old in OUT.glob("*.svg"):  # drop cards from earlier designs
        if old.stem not in cards:
            old.unlink()
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
