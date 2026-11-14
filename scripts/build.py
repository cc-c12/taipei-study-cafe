#!/usr/bin/env python3
"""台北讀書咖啡誌：檢查規則並產生網站。

用法：
    py scripts/build.py           檢查規則，全部通過後產生 docs/
    py scripts/build.py --check   只檢查（健檢用），不產生任何檔案

規則的文字版在 AGENTS.md，這支程式負責「執行」那些規則。
只用 Python 標準函式庫，任何裝了 Python 3.9 以上的電腦都能直接跑。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
RAW = ROOT / "raw"
ASSETS = ROOT / "assets"
OUT = ROOT / "docs"
TODAY = dt.date.today()
UNKNOWN = "未確認"

# ── 計分規則（AGENTS.md 第 4 節）。要改公式，先改 AGENTS.md 再改這裡。 ──
SCORE_ITEMS = [
    # (欄位, 顯示名稱, {值: 分數})
    ("outlets", "插座", {"多": 2, "部分": 1, "幾乎沒有": 0, UNKNOWN: 0}),
    ("wifi", "Wi-Fi", {"穩定": 1, "不穩": 0, "無": 0, UNKNOWN: 0}),
    ("quiet", "安靜", {"安靜": 2, "普通": 1, "吵": 0, UNKNOWN: 0}),
]
LATE_AFTER = "21:00"                    # 平日打烊不早於這個時間 → 晚上 +1
LATE_POINTS = 1
MAX_TOTAL = 6
STARS_BY_TOTAL = {6: 5, 5: 4, 4: 3, 3: 2, 2: 2, 1: 1, 0: 1}
CAP_MINUTES, CAP_STARS = 90, 2          # 限時 ≤ 90 分鐘 → 最多 2 星
STALE_DAYS = 90                         # 最後確認超過幾天算「可能過時」

# ── 內容規則（AGENTS.md 第 3、5 節） ──
CAFE_REQUIRED = ["name", "slug", "station", "walk_min", "address", "hours",
                 "closes_weekday", "outlets", "wifi", "quiet", "time_limit",
                 "min_charge", "visited", "verified", "sources", "summary"]
CAFE_OPTIONAL = ["name_en", "exit", "price", "tags", "photos"]
CAFE_FORBIDDEN = ["stars", "score", "rating"]
CAFE_SECTIONS = ["我的心得", "推薦點什麼", "讀書小提醒"]
GUIDE_CRITERIA = ["min_stars", "closes_after", "outlets_in", "quiet_in",
                  "tags_any", "no_time_limit"]
BANNED_WORDS = ["必去", "最好", "第一", "神店", "天花板", "CP 值爆表", "CP值爆表"]
SUMMARY_MAX = 30
RESERVED_SLUGS = {"cafes", "stations", "guides", "assets"}

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")
TIME_RE = re.compile(r"^(?:(?:[01]\d|2[0-3]):[0-5]\d|24:00)$")
NOTE_RE = re.compile(r"^(\d{4}-\d{2}(?:-\d{2})?)-([a-z0-9-]+)\.md$")
UPDATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9]+(?:-[a-z0-9]+)*)(?:--(\d+))?\.md$")  # 同一天第二份加 --2
WIKI_RE = re.compile(r"\[\[([a-z]+):([^\]]+)\]\]")


# ════════════════════════ 小工具 ════════════════════════

class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.pending: list[str] = []

    def err(self, where: str, msg: str) -> None:
        line = f"{where}：{msg}"
        if line not in self.errors:
            self.errors.append(line)

    def warn(self, where: str, msg: str) -> None:
        line = f"{where}：{msg}"
        if line not in self.warnings:
            self.warnings.append(line)


R = Report()


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def parse_date(s: str) -> dt.date | None:
    m = DATE_RE.match(str(s))
    if not m:
        return None
    try:
        return dt.date(int(m[1]), int(m[2]), int(m[3] or 1))
    except ValueError:
        return None


def minutes(t: str) -> int:
    """HH:MM → 分鐘數；00:00–05:59 視為跨夜（加 24 小時）。"""
    h, m = map(int, t.split(":"))
    total = h * 60 + m
    return total + 24 * 60 if h < 6 else total


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


# ════════════════════════ 讀檔與解析 ════════════════════════

def parse_value(val: str):
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        return [unquote(x.strip()) for x in inner.split(",") if x.strip()] if inner else []
    if val in ("true", "false"):
        return val == "true"
    return unquote(val)


def unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def parse_doc(path: Path) -> tuple[dict, str]:
    lines = path.read_text(encoding="utf-8").lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("缺少 front matter（第一行必須是 ---）")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("front matter 沒有用 --- 結尾")
    meta: dict = {}
    for no in range(1, end):
        line = lines[no]
        if not line.strip():
            continue
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if not m:
            raise ValueError(f"第 {no + 1} 行看不懂：「{line}」（只支援 key: 值）")
        key, val = m[1], m[2].strip()
        if key in meta:
            raise ValueError(f"第 {no + 1} 行：欄位 {key} 重複")
        if val != "":
            meta[key] = parse_value(val)
    return meta, "\n".join(lines[end + 1:]).strip()


def load(kind: str) -> dict[str, dict]:
    items: dict[str, dict] = {}
    for path in sorted((CONTENT / kind).glob("*.md")):
        where = rel(path)
        try:
            meta, body = parse_doc(path)
        except ValueError as e:
            R.err(where, str(e))
            continue
        slug = meta.get("slug")
        if slug != path.stem:
            R.err(where, f"slug「{slug}」必須等於檔名「{path.stem}」")
        if not SLUG_RE.match(path.stem):
            R.err(where, "檔名必須是英文小寫加連字號（slug 規則）")
        items[path.stem] = {"meta": meta, "body": body, "where": where}
    return items


# ════════════════════════ 檢查規則 ════════════════════════

def check_cafe(slug: str, c: dict, site: dict) -> None:
    m, w = c["meta"], c["where"]
    for key in CAFE_FORBIDDEN:
        if key in m:
            R.err(w, f"不准有 {key} 欄位：星數只能由 build.py 計算")
    for key in m:
        if key not in CAFE_REQUIRED + CAFE_OPTIONAL + CAFE_FORBIDDEN:
            R.err(w, f"不認得的欄位「{key}」")
    missing = [k for k in CAFE_REQUIRED if k not in m]
    if missing:
        R.err(w, "缺少必填欄位：" + "、".join(missing))
        c["ok"] = False
        return
    for key, label, table in SCORE_ITEMS:
        if m[key] not in table:
            R.err(w, f"{key} 只能是 {'／'.join(table)}，目前是「{m[key]}」")
            c["ok"] = False
    if m["station"] not in site["stations"]:
        R.err(w, f"station「{m['station']}」在 content/stations/ 找不到")
    if not (m["walk_min"] == UNKNOWN or str(m["walk_min"]).isdigit()):
        R.err(w, "walk_min 必須是整數或「未確認」")
    if not (m["closes_weekday"] == UNKNOWN or TIME_RE.match(m["closes_weekday"])):
        R.err(w, "closes_weekday 必須是 HH:MM 或「未確認」")
        c["ok"] = False
    if not (m["time_limit"] in ("不限時", UNKNOWN) or str(m["time_limit"]).isdigit()):
        R.err(w, "time_limit 必須是「不限時」、整數分鐘或「未確認」")
        c["ok"] = False
    for key in ("visited", "verified"):
        d = parse_date(m[key])
        if d is None:
            R.err(w, f"{key} 必須是 YYYY-MM-DD 或 YYYY-MM")
        elif d > TODAY:
            R.err(w, f"{key} 不能是未來的日期")
    if not isinstance(m["sources"], list) or not m["sources"]:
        R.err(w, "sources 必須是非空的清單，例如 [造訪筆記 2026-11-08]")
    if len(m["summary"]) > SUMMARY_MAX:
        R.err(w, f"summary 最多 {SUMMARY_MAX} 字，目前 {len(m['summary'])} 字")
    for tag in m.get("tags", []):
        if tag not in site["meta"]["tags"]:
            R.err(w, f"標籤「{tag}」不在 content/meta.json 裡；新增標籤要先問使用者")
    # 每一頁都要追溯得到原始筆記
    note = RAW / "notes" / f"{m['visited']}-{slug}.md"
    if not note.exists():
        R.err(w, f"找不到對應的造訪筆記 {rel(note)}（visited 要等於筆記檔名的日期）")
    for photo in m.get("photos", []):
        if not (ASSETS / "photos" / slug / photo).exists():
            R.err(w, f"照片 assets/photos/{slug}/{photo} 不存在")
    # 內文三段
    headings = re.findall(r"^## (.+)$", c["body"], flags=re.M)
    if headings != CAFE_SECTIONS:
        R.err(w, "內文必須剛好是三段 ## 標題，依序為：" + "、".join(CAFE_SECTIONS)
              + f"（目前是：{'、'.join(headings) or '沒有'}）")
    if "★" in c["body"] or "☆" in c["body"]:
        R.err(w, "內文不准手寫星數")
    # 補充資訊有沒有反映進來
    for upd in sorted((RAW / "updates").glob("*.md")):
        um = UPDATE_RE.match(upd.name)
        if um and um[2] == slug and not any(um[1] in s for s in m["sources"]):
            R.warn(w, f"{rel(upd)} 的補充資訊可能還沒反映：sources 裡沒有 {um[1]}")
    # 過時與待確認
    vd = parse_date(m["verified"])
    if vd and (TODAY - vd).days > STALE_DAYS:
        R.warn(w, f"最後確認是 {m['verified']}，已超過 {STALE_DAYS} 天，建議重新查證")
    unknown = [k for k in CAFE_REQUIRED if m.get(k) == UNKNOWN]
    if unknown:
        R.pending.append(f"{slug}：" + "、".join(unknown))


def check_station(slug: str, s: dict, site: dict) -> None:
    m, w = s["meta"], s["where"]
    for key in ("name", "slug", "lines"):
        if key not in m:
            R.err(w, f"缺少必填欄位 {key}")
    for line in m.get("lines", []):
        if line not in site["meta"]["lines"]:
            R.err(w, f"路線「{line}」不在 content/meta.json 裡")
    if not any(c["meta"].get("station") == slug for c in site["cafes"].values()):
        R.err(w, "這個站還沒有任何已收錄的咖啡廳，不應建立站頁")


def check_guide(slug: str, g: dict, site: dict) -> None:
    m, w = g["meta"], g["where"]
    for key in ("title", "slug", "summary"):
        if key not in m:
            R.err(w, f"缺少必填欄位 {key}")
    if len(m.get("summary", "")) > SUMMARY_MAX:
        R.err(w, f"summary 最多 {SUMMARY_MAX} 字")
    if not any(k in m for k in GUIDE_CRITERIA):
        R.err(w, "至少要有一項篩選條件：" + "、".join(GUIDE_CRITERIA))
    for key in m:
        if key not in ("title", "slug", "summary", *GUIDE_CRITERIA):
            R.err(w, f"不認得的欄位「{key}」")
    if "closes_after" in m and not TIME_RE.match(m["closes_after"]):
        R.err(w, "closes_after 必須是 HH:MM")
    if "min_stars" in m and m["min_stars"] not in list("12345"):
        R.err(w, "min_stars 必須是 1～5")
    for tag in m.get("tags_any", []):
        if tag not in site["meta"]["tags"]:
            R.err(w, f"標籤「{tag}」不在 content/meta.json 裡")


def check_page(slug: str, p: dict) -> None:
    m, w = p["meta"], p["where"]
    for key in ("title", "slug", "nav_order"):
        if key not in m:
            R.err(w, f"缺少必填欄位 {key}")
    if not str(m.get("nav_order", "")).isdigit():
        R.err(w, "nav_order 必須是整數")
    if slug in RESERVED_SLUGS:
        R.err(w, f"slug「{slug}」是保留字，請換一個")


def check_raw(site: dict) -> None:
    for note in sorted((RAW / "notes").glob("*.md")):
        nm = NOTE_RE.match(note.name)
        if not nm:
            R.warn(rel(note), "檔名不符合 YYYY-MM-DD-<slug>.md 或 YYYY-MM-<slug>.md")
        elif nm[2] not in site["cafes"]:
            R.warn(rel(note), f"待收錄：還沒有 content/cafes/{nm[2]}.md")
    for upd in sorted((RAW / "updates").glob("*.md")):
        um = UPDATE_RE.match(upd.name)
        if not um:
            R.warn(rel(upd), "檔名不符合 YYYY-MM-DD-<slug>.md（同一天第二份用 YYYY-MM-DD-<slug>--2.md）")
        elif um[2] not in site["cafes"]:
            R.warn(rel(upd), f"補充的咖啡廳 {um[2]} 還沒有頁面")


def check_text(item: dict) -> None:
    for word in BANNED_WORDS:
        if word in item["body"] or word in str(item["meta"].get("summary", "")):
            R.warn(item["where"], f"用了誇大字眼「{word}」")


# ════════════════════════ 計分 ════════════════════════

def score(m: dict) -> dict:
    rows, total, unknown = [], 0, []
    for key, label, table in SCORE_ITEMS:
        pts = table.get(m[key], 0)
        total += pts
        rows.append((label, m[key], pts, max(table.values())))
        if m[key] == UNKNOWN:
            unknown.append(label)
    closes = m["closes_weekday"]
    if closes == UNKNOWN:
        late, shown = 0, UNKNOWN
        unknown.append("營業時間")
    else:
        late = LATE_POINTS if minutes(closes) >= minutes(LATE_AFTER) else 0
        shown = f"平日到 {closes}"
    total += late
    rows.append(("晚上", shown, late, LATE_POINTS))
    stars = STARS_BY_TOTAL[total]
    capped = False
    tl = str(m["time_limit"])
    if tl.isdigit() and int(tl) <= CAP_MINUTES and stars > CAP_STARS:
        stars, capped = CAP_STARS, True
    return {"rows": rows, "total": total, "stars": stars, "capped": capped, "unknown": unknown}


def guide_matches(g: dict, c: dict) -> bool:
    m, s = c["meta"], c["score"]
    if "min_stars" in g and s["stars"] < int(g["min_stars"]):
        return False
    if "closes_after" in g:
        cl = m["closes_weekday"]
        if cl == UNKNOWN or minutes(cl) < minutes(g["closes_after"]):
            return False
    if "outlets_in" in g and m["outlets"] not in g["outlets_in"]:
        return False
    if "quiet_in" in g and m["quiet"] not in g["quiet_in"]:
        return False
    if "tags_any" in g and not set(m.get("tags", [])) & set(g["tags_any"]):
        return False
    if g.get("no_time_limit") is True and m["time_limit"] != "不限時":
        return False
    return True


def guide_criteria_text(g: dict) -> str:
    parts = []
    if "min_stars" in g:
        parts.append(f"讀書友善度至少 {g['min_stars']} 星")
    if "closes_after" in g:
        parts.append(f"平日營業到 {g['closes_after']}（含）以後")
    if "outlets_in" in g:
        parts.append("插座：" + "或".join(g["outlets_in"]))
    if "quiet_in" in g:
        parts.append("安靜程度：" + "或".join(g["quiet_in"]))
    if "tags_any" in g:
        parts.append("標籤含「" + "」或「".join(g["tags_any"]) + "」")
    if g.get("no_time_limit") is True:
        parts.append("不限時")
    return "；".join(parts)


# ════════════════════════ Markdown（簡化版） ════════════════════════

class Renderer:
    def __init__(self, site: dict) -> None:
        self.site = site

    def resolve(self, kind: str, target: str, prefix: str, where: str) -> str:
        site = self.site
        table = {"cafe": ("cafes", "name"), "station": ("stations", "name"),
                 "guide": ("guides", "title")}
        if kind in table:
            folder, title_key = table[kind]
            item = site[folder].get(target)
            if item:
                title = item["meta"].get(title_key, target)
                if kind == "station":
                    title += "站"
                return f'<a href="{prefix}{folder}/{target}/">{esc(title)}</a>'
        elif kind == "page":
            item = site["pages"].get(target)
            if item:
                return f'<a href="{prefix}{target}/">{esc(item["meta"].get("title", target))}</a>'
        elif kind == "repo":
            if (ROOT / target).exists():
                url = site["meta"].get("repo_url", "").rstrip("/")
                if url:
                    return f'<a href="{esc(url)}/blob/main/{esc(target)}"><code>{esc(target)}</code></a>'
                return f"<code>{esc(target)}</code>"
        R.err(where, f"站內連結 [[{kind}:{target}]] 指向不存在的頁面或檔案")
        return esc(target)

    def inline(self, text: str, prefix: str, where: str) -> str:
        out, last = [], 0
        for m in WIKI_RE.finditer(text):
            out.append(self._fmt(text[last:m.start()]))
            out.append(self.resolve(m[1], m[2].strip(), prefix, where))
            last = m.end()
        out.append(self._fmt(text[last:]))
        return "".join(out)

    @staticmethod
    def _fmt(s: str) -> str:
        s = esc(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', s)
        return s

    def render(self, body: str, prefix: str, where: str) -> str:
        out: list[str] = []
        para: list[str] = []
        items: list[str] = []

        def flush() -> None:
            if para:
                joined = ""
                for piece in para:
                    if joined and joined[-1].isascii() and piece[0].isascii():
                        joined += " "
                    joined += piece
                out.append(f"<p>{self.inline(joined, prefix, where)}</p>")
                para.clear()
            if items:
                lis = "".join(f"<li>{self.inline(i, prefix, where)}</li>" for i in items)
                out.append(f"<ul>{lis}</ul>")
                items.clear()

        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                flush()
                continue
            if line.startswith("|"):
                R.err(where, "不支援表格語法")
            if "![" in line:
                R.err(where, "不支援圖片語法，照片請放 front matter 的 photos")
            if re.search(r"<[a-zA-Z/]", line):
                R.err(where, "不支援 HTML 標籤")
            if line.startswith("# "):
                R.err(where, "內文不能用 # 一級標題（頁面標題來自 front matter）")
            if line.startswith("### "):
                flush()
                out.append(f"<h3>{self.inline(line[4:], prefix, where)}</h3>")
            elif line.startswith("## "):
                flush()
                out.append(f"<h2>{self.inline(line[3:], prefix, where)}</h2>")
            elif line.startswith("- "):
                if para:
                    flush()
                items.append(line[2:])
            else:
                if items:
                    flush()
                para.append(line)
        flush()
        return "\n".join(out)


# ════════════════════════ HTML 元件 ════════════════════════

ICONS = {
    "plug": '<path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0V8zM12 16v5"/>',
    "wifi": '<path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.8 16a5 5 0 0 1 6.4 0"/><path d="M12 19.5h.01"/>',
    "quiet": '<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 10a3 3 0 0 1 0 4"/>',
    "moon": '<path d="M19.5 14.5A8 8 0 1 1 9.5 4.5a6.5 6.5 0 0 0 10 10z"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "coin": '<circle cx="12" cy="12" r="8.5"/><path d="M14.5 9.3c-.5-.8-1.4-1.3-2.5-1.3-1.5 0-2.6.8-2.6 1.9 0 2.6 5.2 1.4 5.2 4.2 0 1.1-1.1 1.9-2.6 1.9-1.1 0-2.1-.5-2.6-1.3M12 6.5v1.5M12 16v1.5"/>',
    "cup": '<path d="M4.5 9h12v4a6 6 0 0 1-6 6h0a6 6 0 0 1-6-6V9zM16.5 10.5h1a2.5 2.5 0 0 1 0 5h-1.3M8 3.5v2.5M11.5 3.5v2.5"/>',
    "pin": '<path d="M12 21s6.5-5.8 6.5-11.5a6.5 6.5 0 0 0-13 0C5.5 15.2 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.3"/>',
    "train": '<rect x="5.5" y="3.5" width="13" height="13" rx="3"/><path d="M5.5 10.5h13M8.5 20.5l2-4M15.5 20.5l-2-4M9 13.5h.01M15 13.5h.01"/>',
    "logo": '<path d="M4.5 10h12v3.5a6 6 0 0 1-6 6h0a6 6 0 0 1-6-6V10zM16.5 11.5h1a2.5 2.5 0 0 1 0 5h-1.3M3 21h16"/><path d="M8.5 7c0-1.2 1-1.5 1-2.7M12 7c0-1.2 1-1.5 1-2.7"/>',
}


def icon(name: str) -> str:
    return (f'<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" '
            f'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round">{ICONS[name]}</svg>')


def stars_html(n: int, big: bool = False) -> str:
    cls = "stars big" if big else "stars"
    return (f'<span class="{cls}" role="img" aria-label="讀書友善度 {n} 顆星（滿分 5 顆）">'
            f'<span class="on">{"★" * n}</span><span class="off">{"★" * (5 - n)}</span></span>')


def station_badge(site: dict, slug: str, prefix: str = "", link: bool = False) -> str:
    st = site["stations"].get(slug)
    if not st:
        return ""
    dots = "".join(
        f'<i class="line-dot" style="--line-color:{site["meta"]["lines"].get(line, "")}" title="{esc(line)}"></i>'
        for line in st["meta"].get("lines", []))
    inner = f'<span class="line-dots">{dots}</span>{esc(st["meta"]["name"])}站'
    if link:
        return f'<a class="station-badge" href="{prefix}stations/{slug}/">{inner}</a>'
    return f'<span class="station-badge">{inner}</span>'


def photo_html(c: dict, prefix: str, first_only: bool = True) -> str:
    m = c["meta"]
    photos = m.get("photos", [])
    if photos:
        shown = photos[:1] if first_only else photos
        return "".join(
            f'<div class="photo"><img src="{prefix}assets/photos/{m["slug"]}/{esc(p)}" '
            f'alt="{esc(m["name"])}的照片" loading="lazy"></div>' for p in shown)
    return (f'<div class="photo placeholder" aria-hidden="true"><span>{esc(m["name"][0])}</span>'
            f'<small>還沒有照片</small></div>')


def chips(tags: list[str]) -> str:
    if not tags:
        return ""
    return '<ul class="chips">' + "".join(f'<li class="chip">{esc(t)}</li>' for t in tags) + "</ul>"


def warn_badges(c: dict) -> str:
    out = []
    if c["score"]["unknown"]:
        out.append(f'<span class="badge-warn">含待確認項目：{esc("、".join(c["score"]["unknown"]))}</span>')
    if c["stale"]:
        out.append(f'<span class="badge-warn">資訊可能過時（最後確認 {esc(c["meta"]["verified"])}）</span>')
    return "".join(out)


def cafe_card(site: dict, c: dict, prefix: str) -> str:
    m, s = c["meta"], c["score"]
    late = "1" if m["closes_weekday"] != UNKNOWN and minutes(m["closes_weekday"]) >= minutes(LATE_AFTER) else "0"
    walk = f"步行 {m['walk_min']} 分鐘" if str(m["walk_min"]).isdigit() else ""
    data = (f'data-cafe data-station="{esc(m["station"])}" data-outlets="{esc(m["outlets"])}" '
            f'data-wifi="{esc(m["wifi"])}" data-quiet="{esc(m["quiet"])}" data-late="{late}" '
            f'data-nolimit="{"1" if m["time_limit"] == "不限時" else "0"}" '
            f'data-tags="{esc(",".join(m.get("tags", [])))}" data-stars="{s["stars"]}"')
    return f"""<article class="card" {data}>
  <a href="{prefix}cafes/{m['slug']}/">
    {photo_html(c, prefix)}
    <div class="card-body">
      <h3>{esc(m['name'])}</h3>
      <div class="card-meta">{station_badge(site, m['station'])}<span>{esc(walk)}</span></div>
      {stars_html(s['stars'])}
      <p class="summary">{esc(m['summary'])}</p>
      {chips(m.get('tags', []))}
    </div>
  </a>
</article>"""


def sorted_cafes(cafes: list[dict]) -> list[dict]:
    return sorted(cafes, key=lambda c: (-c["score"]["stars"], -c["score"]["total"], c["meta"]["name"]))


# ════════════════════════ 版型 ════════════════════════

def layout(site: dict, title: str, body: str, depth: int, active: str = "",
           description: str = "") -> str:
    p = "../" * depth or "./"
    nav = [("首頁", "", "home"), ("捷運站", "stations/", "stations"), ("主題清單", "guides/", "guides")]
    for slug, pg in sorted(site["pages"].items(), key=lambda kv: int(kv[1]["meta"].get("nav_order", 99))):
        nav.append((pg["meta"]["title"], f"{slug}/", slug))
    current = ' aria-current="page"'
    nav_html = "".join(
        f'<a href="{p}{href}"{current if key == active else ""}>{esc(label)}</a>'
        for label, href, key in nav)
    name = site["meta"]["site_name"]
    full_title = name if active == "home" else f"{title}｜{name}"
    repo = site["meta"].get("repo_url", "")
    repo_link = f'｜<a href="{esc(repo)}">GitHub</a>' if repo else ""
    return f"""<!doctype html>
<html lang="zh-Hant-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(description or site['meta']['tagline'])}">
<link rel="icon" href="{p}assets/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=LXGW+WenKai+TC:wght@400;700&amp;family=Noto+Sans+TC:wght@400;500;700&amp;display=swap">
<link rel="stylesheet" href="{p}assets/css/style.css">
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="{p}">{icon('logo')}<span>{esc(name)}</span></a>
    <nav class="nav" aria-label="主要導覽">{nav_html}</nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p>{esc(name)}｜所有資訊來自作者的親身造訪，出發前請以店家公告為準。</p>
    <p>本網站由 AI 依照 <code>AGENTS.md</code> {esc(site['version'])} 的規則維護｜最後建置 {TODAY.isoformat()}{repo_link}</p>
  </div>
</footer>
<script src="{p}assets/js/filter.js" defer></script>
</body>
</html>
"""


# ════════════════════════ 各種頁面 ════════════════════════

def page_home(site: dict) -> str:
    cafes = sorted_cafes(list(site["cafes"].values()))
    station_links = "".join(station_badge(site, s, "", link=True) for s in site["stations"])
    station_opts = "".join(f'<option value="{s}">{esc(st["meta"]["name"])}站</option>'
                           for s, st in site["stations"].items())
    tag_opts = "".join(f'<option value="{esc(t)}">{esc(t)}</option>' for t in site["meta"]["tags"])
    guides = "".join(
        f'<a class="tile" href="guides/{g}/"><h3>{esc(x["meta"]["title"])}</h3>'
        f'<p>{esc(x["meta"]["summary"])}・{len(x["matches"])} 間</p></a>'
        for g, x in site["guides"].items())
    cards = "\n".join(cafe_card(site, c, "") for c in cafes)
    how = site["pages"].get("how-it-works")
    how_link = '<a href="how-it-works/">看看這個網站怎麼做的 →</a>' if how else ""
    body = f"""
<section class="hero">
  <div class="ring" aria-hidden="true"></div>
  <h1>{esc(site['meta']['site_name'])}</h1>
  <p>{esc(site['meta']['tagline'])}</p>
  <div class="station-row"><span class="row-label">依捷運站找</span>{station_links}</div>
</section>

<form class="filters" data-filter>
  <fieldset>
    <legend>我想要……</legend>
    <label class="check"><input type="checkbox" name="outlets" value="1">有插座</label>
    <label class="check"><input type="checkbox" name="wifi" value="1">Wi-Fi 穩定</label>
    <label class="check"><input type="checkbox" name="quiet" value="1">安靜</label>
    <label class="check"><input type="checkbox" name="late" value="1">營業到 21:00 以後</label>
    <label class="check"><input type="checkbox" name="nolimit" value="1">不限時</label>
    <select name="station" aria-label="捷運站"><option value="">所有捷運站</option>{station_opts}</select>
    <select name="tag" aria-label="情境標籤"><option value="">所有情境</option>{tag_opts}</select>
  </fieldset>
  <div class="filter-foot"><span>符合 <b data-count>{len(cafes)}</b> 間・依讀書友善度排序</span><button type="reset">清除條件</button></div>
</form>

<div class="grid">
{cards}
</div>
<p class="empty" data-empty hidden>沒有符合條件的店，試著拿掉一兩個條件。</p>

<section>
  <h2 class="section-title">主題清單</h2>
  <p class="section-sub">把常被問的問題，整理成可以直接用的清單。</p>
  <div class="tiles">{guides}</div>
</section>

<section class="notice">
  <p>這個網站的每一頁，都是 AI 照著同一本工作手冊做出來的：星數由公式計算、資訊查不到就標「未確認」、每一頁都追溯得到我的造訪筆記。{how_link}</p>
</section>
"""
    return layout(site, "首頁", body, 0, "home")


def page_cafe(site: dict, c: dict) -> str:
    m, s, p = c["meta"], c["score"], "../../"
    st = site["stations"].get(m["station"], {"meta": {"name": m["station"]}})

    def spec(ic: str, label: str, value: object, extra: str = "") -> str:
        value = str(value)
        cls = "spec-item unknown" if UNKNOWN in value else "spec-item"
        return (f'<div class="{cls}">{icon(ic)}<div><dt>{label}</dt>'
                f'<dd>{esc(value)}{extra}</dd></div></div>')

    walk = f"步行 {m['walk_min']} 分鐘" if str(m["walk_min"]).isdigit() else f"步行時間{UNKNOWN}"
    exit_txt = f"，{m['exit']}" if m.get("exit") else ""
    tl = m["time_limit"]
    tl_txt = tl if tl in ("不限時", UNKNOWN) else f"{tl} 分鐘"
    specs = "".join([
        spec("plug", "插座", m["outlets"]),
        spec("wifi", "Wi-Fi", m["wifi"]),
        spec("quiet", "安靜程度", m["quiet"]),
        spec("moon", "營業時間", m["hours"]),
        spec("clock", "限時（平日）", tl_txt),
        spec("coin", "低消", m["min_charge"]),
        spec("cup", "價位", m.get("price", UNKNOWN)),
        spec("train", "交通", f"{st['meta']['name']}站，{walk}{exit_txt}"),
        spec("pin", "地址", m["address"]),
    ])
    rows = "".join(f"<tr><td>{esc(label)}</td><td>{esc(val)}</td><td>+{pts}／{full}</td></tr>"
                   for label, val, pts, full in s["rows"])
    cap = (f'<p class="cap-note">限時 {CAP_MINUTES} 分鐘以內，最多 {CAP_STARS} 顆星。</p>'
           if s["capped"] else "")
    gallery = photo_html(c, p, first_only=False)
    repo = site["meta"].get("repo_url", "").rstrip("/")
    notes = [f for f in sorted((RAW / "notes").glob(f"*-{m['slug']}.md"))
             if (nm := NOTE_RE.match(f.name)) and nm[2] == m["slug"]]
    updates = [f for f in (RAW / "updates").glob("*.md")
               if (um := UPDATE_RE.match(f.name)) and um[2] == m["slug"]]
    updates.sort(key=lambda f: (UPDATE_RE.match(f.name)[1], int(UPDATE_RE.match(f.name)[3] or 1)))

    def raw_link(f: Path) -> str:
        path = rel(f)
        return f'<a href="{esc(repo)}/blob/main/{esc(path)}"><code>{esc(path)}</code></a>' if repo else f"<code>{esc(path)}</code>"

    raw_links = "、".join(raw_link(f) for f in notes + updates)
    body_html = site["renderer"].render(c["body"], p, c["where"])
    name_en = f'<p class="name-en">{esc(m["name_en"])}</p>' if m.get("name_en") else ""
    body = f"""
<nav class="crumbs" aria-label="麵包屑"><a href="{p}">首頁</a> › <a href="{p}stations/{m['station']}/">{esc(st['meta']['name'])}站</a> › {esc(m['name'])}</nav>

<div class="cafe-head">
  <div class="cafe-intro">
    <h1>{esc(m['name'])}</h1>
    {name_en}
    <div class="card-meta">{station_badge(site, m['station'], p, link=True)}<span>{esc(walk)}</span></div>
    <p class="lead">{esc(m['summary'])}</p>
    {chips(m.get('tags', []))}
    <div class="warn-row">{warn_badges(c)}</div>
  </div>
  <aside class="score-box" aria-label="讀書友善度">
    <p class="score-label">讀書友善度</p>
    {stars_html(s['stars'], big=True)}
    <table class="score-table">
      <thead><tr><th>項目</th><th>狀態</th><th>分數</th></tr></thead>
      <tbody>{rows}</tbody>
      <tfoot><tr><td colspan="2">總分</td><td>{s['total']}／{MAX_TOTAL}</td></tr></tfoot>
    </table>
    {cap}
    <p class="score-foot">星數由公式自動計算，<a href="{p}rating/">看評分方法</a></p>
  </aside>
</div>

<div class="gallery">{gallery}</div>

<section>
  <h2 class="section-title">讀書規格</h2>
  <dl class="spec-grid">{specs}</dl>
</section>

<article class="prose">
{body_html}
</article>

<aside class="source-box">
  <p><strong>資訊來源</strong>：{esc('、'.join(m['sources']))}</p>
  <p><strong>最近造訪</strong>：{esc(m['visited'])}｜<strong>最後確認</strong>：{esc(m['verified'])}</p>
  <p><strong>原始筆記</strong>：{raw_links}</p>
</aside>
"""
    return layout(site, m["name"], body, 2, "", m["summary"])


def page_stations_index(site: dict) -> str:
    groups = []
    for line, color in site["meta"]["lines"].items():
        members = [(s, st) for s, st in site["stations"].items() if line in st["meta"].get("lines", [])]
        if not members:
            continue
        tiles = "".join(
            f'<a class="tile" href="{s}/"><h3>{esc(st["meta"]["name"])}站</h3>'
            f'<p>{len(st["cafes"])} 間・最高 {max(c["score"]["stars"] for c in st["cafes"])} 星</p></a>'
            for s, st in members)
        groups.append(f'<section class="line-group"><h2 class="section-title">'
                      f'<i class="line-dot big" style="--line-color:{color}"></i>{esc(line)}</h2>'
                      f'<div class="tiles">{tiles}</div></section>')
    body = f"""
<nav class="crumbs" aria-label="麵包屑"><a href="../">首頁</a> › 捷運站</nav>
<h1 class="page-title">依捷運站找</h1>
<p class="section-sub">轉乘站會同時出現在兩條路線底下。</p>
{''.join(groups)}
"""
    return layout(site, "捷運站", body, 1, "stations")


def page_station(site: dict, slug: str, st: dict) -> str:
    m, p = st["meta"], "../../"
    lines = "、".join(m.get("lines", []))
    cards = "\n".join(cafe_card(site, c, p) for c in sorted_cafes(st["cafes"]))
    body = f"""
<nav class="crumbs" aria-label="麵包屑"><a href="{p}">首頁</a> › <a href="{p}stations/">捷運站</a> › {esc(m['name'])}站</nav>
<h1 class="page-title">{station_badge(site, slug)} </h1>
<p class="section-sub">{esc(lines)}</p>
<article class="prose">{site['renderer'].render(st['body'], p, st['where'])}</article>
<h2 class="section-title">這站的咖啡廳（{len(st['cafes'])} 間）</h2>
<div class="grid">{cards}</div>
"""
    return layout(site, f"{m['name']}站", body, 2, "stations")


def page_guides_index(site: dict) -> str:
    tiles = "".join(
        f'<a class="tile" href="{g}/"><h3>{esc(x["meta"]["title"])}</h3>'
        f'<p>{esc(x["meta"]["summary"])}・{len(x["matches"])} 間</p></a>'
        for g, x in site["guides"].items())
    body = f"""
<nav class="crumbs" aria-label="麵包屑"><a href="../">首頁</a> › 主題清單</nav>
<h1 class="page-title">主題清單</h1>
<p class="section-sub">每份清單的成員都由條件自動篩選，新收錄的店只要符合條件就會自動出現。</p>
<div class="tiles">{tiles}</div>
"""
    return layout(site, "主題清單", body, 1, "guides")


def page_guide(site: dict, slug: str, g: dict) -> str:
    m, p = g["meta"], "../../"
    cards = "\n".join(cafe_card(site, c, p) for c in sorted_cafes(g["matches"]))
    empty = '<p class="empty">目前沒有符合條件的店。</p>' if not g["matches"] else ""
    body = f"""
<nav class="crumbs" aria-label="麵包屑"><a href="{p}">首頁</a> › <a href="{p}guides/">主題清單</a> › {esc(m['title'])}</nav>
<h1 class="page-title">{esc(m['title'])}</h1>
<p class="lead">{esc(m['summary'])}</p>
<p class="notice">篩選條件：{esc(guide_criteria_text(m))}（由建置程式自動篩選）</p>
<article class="prose">{site['renderer'].render(g['body'], p, g['where'])}</article>
<h2 class="section-title">符合的店（{len(g['matches'])} 間）</h2>
<div class="grid">{cards}</div>
{empty}
"""
    return layout(site, m["title"], body, 2, "guides", m["summary"])


def rating_tables() -> str:
    rows = []
    for key, label, table in SCORE_ITEMS:
        by_pts: dict[int, list[str]] = {}
        for val, pts in table.items():
            by_pts.setdefault(pts, []).append(val)
        cells = "".join(f"<td>{esc('／'.join(by_pts.get(pts, [])) or '—')}</td>" for pts in (2, 1, 0))
        rows.append(f"<tr><th>{esc(label)}（{key}）</th>{cells}</tr>")
    rows.append(f"<tr><th>晚上（closes_weekday）</th><td>—</td><td>平日營業到 {LATE_AFTER}（含）以後，跨夜也算</td><td>{LATE_AFTER} 以前／{UNKNOWN}</td></tr>")
    star_rows = []
    for stars in range(5, 0, -1):
        totals = sorted(t for t, s in STARS_BY_TOTAL.items() if s == stars)
        rng = str(totals[0]) if len(totals) == 1 else f"{totals[0]}～{totals[-1]}"
        star_rows.append(f"<tr><td>{rng}</td><td>{stars_html(stars)}</td></tr>")
    return f"""
<h2>目前使用的公式</h2>
<p class="section-sub">以下兩張表由建置程式直接從計分程式碼產生，跟實際計分保證一致。</p>
<div class="table-scroll"><table class="data-table">
  <thead><tr><th>項目</th><th>2 分</th><th>1 分</th><th>0 分</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table></div>
<div class="table-scroll"><table class="data-table narrow">
  <thead><tr><th>總分（滿分 {MAX_TOTAL}）</th><th>星數</th></tr></thead>
  <tbody>{''.join(star_rows)}</tbody>
</table></div>
<ul>
  <li>平日限時 {CAP_MINUTES} 分鐘以內：最多 {CAP_STARS} 顆星。</li>
  <li>任何計分項目是「{UNKNOWN}」：以 0 分計算，頁面標示「含待確認項目」。</li>
  <li>最後確認超過 {STALE_DAYS} 天：頁面標示「資訊可能過時」。</li>
</ul>
"""


def page_static(site: dict, slug: str, pg: dict) -> str:
    m, p = pg["meta"], "../"
    extra = rating_tables() if slug == "rating" else ""
    body = f"""
<nav class="crumbs" aria-label="麵包屑"><a href="{p}">首頁</a> › {esc(m['title'])}</nav>
<h1 class="page-title">{esc(m['title'])}</h1>
<article class="prose">
{site['renderer'].render(pg['body'], p, pg['where'])}
{extra}
</article>
"""
    return layout(site, m["title"], body, 1, slug)


# ════════════════════════ 主流程 ════════════════════════

def write(path: Path, text: str) -> None:
    """寫檔。OneDrive／Dropbox 同步中可能短暫鎖住檔案，所以失敗時稍等重試，最後改用暫存檔替換。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
        return  # 內容沒變就不寫，也避免觸發不必要的同步
    for attempt in range(5):
        try:
            path.write_text(text, encoding="utf-8", newline="\n")
            return
        except OSError:
            time.sleep(0.4 * (attempt + 1))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    check_only = "--check" in sys.argv[1:]
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    vm = re.search(r"版本：(v[\d.]+)", agents)
    site: dict = {
        "meta": json.loads((CONTENT / "meta.json").read_text(encoding="utf-8")),
        "version": vm[1] if vm else "（未標版本）",
    }
    site["stations"] = load("stations")
    site["cafes"] = load("cafes")
    site["guides"] = load("guides")
    site["pages"] = load("pages")
    site["renderer"] = Renderer(site)

    for slug, c in site["cafes"].items():
        c["ok"] = True
        check_cafe(slug, c, site)
        check_text(c)
    for slug, s in site["stations"].items():
        check_station(slug, s, site)
        check_text(s)
    for slug, g in site["guides"].items():
        check_guide(slug, g, site)
    for slug, pg in site["pages"].items():
        check_page(slug, pg)
    check_raw(site)
    # 站內連結與 Markdown 語法：不管有沒有其他錯誤都先掃一遍，一次回報所有問題
    for kind in ("cafes", "stations", "guides", "pages"):
        for item in site[kind].values():
            site["renderer"].render(item["body"], "", item["where"])

    # 計分與關聯（只對欄位正確的咖啡廳）
    for c in site["cafes"].values():
        if c.get("ok"):
            c["score"] = score(c["meta"])
            vd = parse_date(c["meta"]["verified"])
            c["stale"] = bool(vd and (TODAY - vd).days > STALE_DAYS)
    valid = [c for c in site["cafes"].values() if "score" in c]
    for slug, st in site["stations"].items():
        st["cafes"] = [c for c in valid if c["meta"]["station"] == slug]
    for g in site["guides"].values():
        g["matches"] = [c for c in valid if guide_matches(g["meta"], c)]
        if not g["matches"]:
            R.warn(g["where"], "目前沒有任何店符合這份清單的條件")

    # 先把所有頁面算出來（站內連結的錯誤會在這一步被抓到）
    pages: dict[str, str] = {}
    if not R.errors:
        pages["index.html"] = page_home(site)
        pages["stations/index.html"] = page_stations_index(site)
        pages["guides/index.html"] = page_guides_index(site)
        for c in valid:
            pages[f"cafes/{c['meta']['slug']}/index.html"] = page_cafe(site, c)
        for slug, st in site["stations"].items():
            pages[f"stations/{slug}/index.html"] = page_station(site, slug, st)
        for slug, g in site["guides"].items():
            pages[f"guides/{slug}/index.html"] = page_guide(site, slug, g)
        for slug, pg in site["pages"].items():
            pages[f"{slug}/index.html"] = page_static(site, slug, pg)

    # 報告
    print(f"台北讀書咖啡誌｜建置報告（AGENTS.md {site['version']}，{TODAY.isoformat()}）")
    print(f"  咖啡廳 {len(site['cafes'])}｜捷運站 {len(site['stations'])}｜"
          f"主題清單 {len(site['guides'])}｜固定頁 {len(site['pages'])}")
    print(f"\n錯誤 {len(R.errors)} 個")
    for e in R.errors:
        print(f"  ✗ {e}")
    print(f"\n警告 {len(R.warnings)} 個")
    for w in R.warnings:
        print(f"  ! {w}")
    if R.pending:
        print("\n待確認欄位（請使用者有空時查證）")
        for line in R.pending:
            print(f"  ? {line}")

    if R.errors:
        print("\n✗ 有錯誤，沒有產生網站。請依上面的訊息修正 content/ 後再建置。")
        return 1
    if check_only:
        print("\n✓ 檢查通過（--check 模式，沒有產生檔案）")
        return 0

    # 寫出網站。不整個刪掉 docs/ 再重建：OneDrive／Dropbox 同步或預覽伺服器
    # 常會鎖住資料夾，所以改成「覆寫檔案＋只刪除過時的檔案」。
    expected: set[Path] = set()
    for path, text in pages.items():
        write(OUT / path, text)
        expected.add(OUT / path)
    for src in ASSETS.rglob("*"):
        if src.is_file():
            dst = OUT / "assets" / src.relative_to(ASSETS)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            expected.add(dst)
    write(OUT / ".nojekyll", "")
    expected.add(OUT / ".nojekyll")
    stale = [f for f in OUT.rglob("*") if f.is_file() and f not in expected]
    for f in stale:
        try:
            f.unlink()
        except OSError:
            print(f"  ! 無法刪除過時檔案 {rel(f)}（可能被同步程式鎖住），請稍後再建置一次")
    for d in sorted((d for d in OUT.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
        try:
            d.rmdir()  # 只會刪掉空資料夾
        except OSError:
            pass
    print(f"\n✓ 已產生 {len(pages)} 個頁面到 docs/（移除 {len(stale)} 個過時檔案）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
