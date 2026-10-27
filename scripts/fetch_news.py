#!/usr/bin/env python3
"""
news.studer.website – Nachrichten-Sammler
=========================================

Holt RSS/Atom-Feeds, Google-News-Suchen und YouTube-Kanäle aus config.json,
entfernt Duplikate, bündelt gleiche Meldungen, bewertet sie und schreibt alles
in eine news.json, die von index.html gelesen wird.

Optional (wenn eine KI erreichbar ist):
  * übersetzt fremdsprachige Titel/Teaser auf Deutsch
  * schreibt Moderationstexte für den Radio-/Podcast-Modus

Nur Python-Standardbibliothek – keine Installation nötig.

KI-Anbieter (in dieser Reihenfolge):
  1. AI_BASE_URL (+ AI_API_KEY, AI_MODEL) – z.B. Ollama auf deinem Heimserver:
       AI_BASE_URL=http://localhost:11434/v1  AI_MODEL=llama3.1
  2. GITHUB_TOKEN – gratis GitHub Models (in GitHub Actions automatisch da)
  3. keine KI – alles funktioniert trotzdem, nur ohne Übersetzung/Moderation

Aufruf:
  python3 scripts/fetch_news.py --config config.json --out news.json
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import email.utils
import hashlib
import html
import html.entities
import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Allgemein
# --------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 StuderNews/1.0 (+https://news.studer.website)")
NOW = datetime.now(timezone.utc)

REGIONS = {
    # region: (hl, gl, ceid, sprache)
    "CH": ("de", "CH", "CH:de", "de"),
    "DE": ("de", "DE", "DE:de", "de"),
    "AT": ("de", "AT", "AT:de", "de"),
    "US": ("en-US", "US", "US:en", "en"),
    "GB": ("en-GB", "GB", "GB:en", "en"),
    "BR": ("pt-BR", "BR", "BR:pt-419", "pt"),
    "PT": ("pt-PT", "PT", "PT:pt-150", "pt"),
}

LOG_LINES: list[str] = []


def log(msg: str) -> None:
    LOG_LINES.append(msg)
    print(msg, flush=True)


_HOST_LOCK = threading.Lock()
_HOST_SEM: dict[str, threading.Semaphore] = {}


def _host_slot(url: str) -> threading.Semaphore:
    """Max. 4 gleichzeitige Anfragen pro Server (Google News & Co. nicht überlasten)."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    with _HOST_LOCK:
        return _HOST_SEM.setdefault(host, threading.Semaphore(4))


def http_get(url: str, timeout: int = 20, extra_headers: dict | None = None) -> tuple[bytes, str]:
    with _host_slot(url):
        return _http_get(url, timeout, extra_headers)


def _http_get(url: str, timeout: int = 20, extra_headers: dict | None = None) -> tuple[bytes, str]:
    headers = {
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8,pt;q=0.7",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(8_000_000)
        ctype = resp.headers.get("Content-Type", "")
    return data, ctype


# --------------------------------------------------------------------------
# Text-Helfer
# --------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def clean_text(raw: str | None, limit: int = 0) -> str:
    if not raw:
        return ""
    txt = html.unescape(raw)
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", txt)
    txt = TAG_RE.sub(" ", txt)
    txt = html.unescape(txt)  # doppelt kodierte Entities
    txt = WS_RE.sub(" ", txt).strip()
    if limit and len(txt) > limit:
        cut = txt[:limit].rsplit(" ", 1)[0]
        txt = cut.rstrip(",.;:–- ") + " …"
    return txt


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


STOP = set("""
der die das und oder aber ein eine einer eines einem einen ist sind war wird werden wurde mit
von vom zum zur fur fuer auf aus bei nach uber unter vor als auch nicht noch nur sich sie ihr
er es wir man im in an am zu den dem des so wie was wer wo dass beim mehr neue neuen neuer
the a an and or but of to in on at for with from by as is are was were be been has have had
it its this that these those after over into about new says say will would can could not
o os as um uma uns umas de do da dos das em no na nos nas por para com que e se ao aos sobre
apos diz mais como seu sua ser foi sao esta este pelo pela pelos pelas entre ate
""".split())


def tokens(title: str) -> set[str]:
    t = strip_accents(title.lower())
    words = re.findall(r"[a-z0-9]+", t)
    return {w for w in words if len(w) >= 3 and w not in STOP}


def safe_url(u: str | None) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    return u if re.match(r"^https?://", u, re.I) else ""


def canonical_url(u: str) -> str:
    try:
        p = urllib.parse.urlsplit(u)
        q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
             if not k.lower().startswith(("utm_", "fbclid", "gclid", "at_", "ref"))]
        path = p.path.rstrip("/")
        return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower().replace("www.", ""), path,
                                        urllib.parse.urlencode(q), ""))
    except Exception:
        return u


def parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d is not None:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        iso = s.replace("Z", "+00:00")
        iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
        d = datetime.fromisoformat(iso)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Robuster RSS/Atom-Parser (nur Standardbibliothek)
# --------------------------------------------------------------------------

XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}


def _fix_entities(text: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in XML_ENTITIES:
            return m.group(0)
        cp = html.entities.name2codepoint.get(name)
        return f"&#{cp};" if cp else ""
    text = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", repl, text)
    # alleinstehende & maskieren
    text = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)", "&amp;", text)
    return text


def decode_bytes(data: bytes, ctype: str) -> str:
    enc = None
    m = re.search(rb"<\?xml[^>]*encoding=[\"']([A-Za-z0-9_\-]+)[\"']", data[:300])
    if m:
        enc = m.group(1).decode("ascii", "ignore")
    if not enc:
        m2 = re.search(r"charset=([A-Za-z0-9_\-]+)", ctype or "")
        if m2:
            enc = m2.group(1)
    for e in (enc, "utf-8", "cp1252"):
        if not e:
            continue
        try:
            return data.decode(e)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def parse_xml(data: bytes, ctype: str = "") -> ET.Element:
    text = decode_bytes(data, ctype).lstrip("﻿ \t\r\n")
    text = re.sub(r"^<\?xml[^>]*\?>", "", text).lstrip()
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        return ET.fromstring(_fix_entities(text))


def lname(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""


def child(el: ET.Element, *names: str) -> ET.Element | None:
    for c in el:
        if lname(c) in names:
            return c
    return None


def child_text(el: ET.Element, *names: str) -> str:
    for n in names:  # Reihenfolge der Namen = Priorität
        c = child(el, n)
        if c is not None:
            t = "".join(c.itertext()).strip()
            if t:
                return t
    return ""


def entry_link(el: ET.Element) -> str:
    best = ""
    for c in el:
        if lname(c) != "link":
            continue
        href = c.attrib.get("href")
        if href:  # Atom
            rel = c.attrib.get("rel", "alternate")
            if rel == "alternate":
                return href
            best = best or href
        elif (c.text or "").strip():  # RSS
            return c.text.strip()
    if not best:
        g = child(el, "guid")
        if g is not None and (g.text or "").startswith("http"):
            best = g.text.strip()
    return best


def entry_image(el: ET.Element, html_blobs: list[str]) -> str:
    for c in el.iter():
        n = lname(c)
        url = c.attrib.get("url") or ""
        if n == "thumbnail" and url:
            return url
        if n == "content" and url and (c.attrib.get("medium") == "image"
                                       or c.attrib.get("type", "").startswith("image")
                                       or re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I)):
            return url
        if n == "enclosure" and url and c.attrib.get("type", "").startswith("image"):
            return url
        if n == "image" and child(c, "url") is None and (c.attrib.get("href") or url):
            return c.attrib.get("href") or url
    for blob in html_blobs:
        m = IMG_RE.search(html.unescape(blob or ""))
        if m:
            return m.group(1)
    return ""


def author_name(el: ET.Element) -> str:
    a = child(el, "author", "creator")
    if a is None:
        return ""
    n = child(a, "name")
    return clean_text("".join((n if n is not None else a).itertext()))


def parse_feed(data: bytes, ctype: str = "") -> list[dict]:
    root = parse_xml(data, ctype)
    entries = [e for e in root.iter() if lname(e) in ("item", "entry")]
    out = []
    for e in entries:
        title = clean_text(child_text(e, "title"))
        if not title:
            continue
        desc_raw = child_text(e, "description", "summary")
        content_raw = child_text(e, "encoded")
        if not desc_raw:
            c = child(e, "content")
            if c is not None and not c.attrib.get("url"):
                content_raw = content_raw or "".join(c.itertext())
        media_desc = ""
        for c in e.iter():
            if lname(c) == "description" and c is not e and c.text and lname(c) != lname(e):
                media_desc = media_desc or c.text
        date = parse_date(child_text(e, "pubdate", "published", "updated", "date", "issued"))
        src_el = child(e, "source")
        out.append({
            "title": title,
            "link": entry_link(e),
            "summary_raw": desc_raw or content_raw or media_desc,
            "image": entry_image(e, [desc_raw, content_raw]),
            "date": date,
            "source": clean_text("".join(src_el.itertext())) if src_el is not None else "",
            "videoId": child_text(e, "videoid"),
            "author": author_name(e),
        })
    return out


# --------------------------------------------------------------------------
# Quellen auflösen
# --------------------------------------------------------------------------

def google_url(spec: dict) -> tuple[str, str, str]:
    region = spec.get("region", "CH").upper()
    hl, gl, ceid, lang = REGIONS.get(region, REGIONS["CH"])
    params = f"hl={hl}&gl={gl}&ceid={ceid}"
    if spec.get("top"):
        return f"https://news.google.com/rss?{params}", lang, f"Google News {region}"
    if spec.get("section"):
        sec = spec["section"].upper()
        return (f"https://news.google.com/rss/headlines/section/topic/{sec}?{params}",
                lang, f"Google News {sec.title()} {region}")
    q = spec["q"]
    if "when:" not in q:
        q += " when:2d"
    return (f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&{params}",
            lang, f"Google News „{spec['q'][:30]}“ {region}")


CHANNEL_RE = [
    re.compile(r'"externalId":"(UC[\w-]{22})"'),
    re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'),
    re.compile(r'"browseId":"(UC[\w-]{22})"'),
    re.compile(r'"channelId":"(UC[\w-]{22})"'),
]


def resolve_handle(handle: str) -> str:
    h = handle if handle.startswith("@") else "@" + handle
    data, _ = http_get(f"https://www.youtube.com/{urllib.parse.quote(h, safe='@')}",
                       extra_headers={"Cookie": "CONSENT=YES+cb; SOCS=CAI", "Accept": "text/html"})
    page = data.decode("utf-8", "ignore")
    for rx in CHANNEL_RE:
        m = rx.search(page)
        if m:
            return m.group(1)
    raise ValueError(f"Kanal-ID für {h} nicht gefunden")


def build_jobs(cfg: dict) -> list[dict]:
    jobs = []
    for t in cfg["topics"]:
        for f in t.get("feeds", []):
            jobs.append({"kind": "feed", "topic": t["id"], "url": f["url"], "name": f.get("name", ""),
                         "lang": f.get("lang", "de"), "weight": f.get("weight", 1.0)})
        for g in t.get("google", []):
            url, lang, name = google_url(g)
            jobs.append({"kind": "google", "topic": t["id"], "url": url, "name": name, "lang": lang,
                         "weight": g.get("weight", 1.15 if (g.get("top") or g.get("section")) else 1.0)})
        for y in t.get("youtube", []):
            jobs.append({"kind": "youtube", "topic": t["id"], "id": y.get("id", ""),
                         "handle": y.get("handle", ""), "name": y.get("name", ""),
                         "lang": y.get("lang", "")})
    return jobs


def run_job(job: dict) -> tuple[dict, list[dict], str | None]:
    try:
        if job["kind"] == "youtube":
            cid = job["id"] or resolve_handle(job["handle"])
            job["id"] = cid
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
        else:
            url = job["url"]
        last_err = None
        for attempt in range(2):
            try:
                data, ctype = http_get(url)
                return job, parse_feed(data, ctype), None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(2 + attempt * 3)
        raise last_err  # type: ignore[misc]
    except Exception as e:  # noqa: BLE001
        return job, [], f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Verarbeitung
# --------------------------------------------------------------------------

def make_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def to_article(job: dict, raw: dict, max_age_h: float) -> dict | None:
    date = raw["date"] or NOW
    if date > NOW:
        date = NOW
    age_h = (NOW - date).total_seconds() / 3600
    if age_h > max_age_h:
        return None
    url = safe_url(raw["link"])
    if not url:
        return None
    title = raw["title"]
    source = raw["source"] or job["name"]
    if job["kind"] == "google":
        if raw["source"] and title.endswith(" - " + raw["source"]):
            title = title[: -len(raw["source"]) - 3].strip()
        else:
            title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip()
    summary = clean_text(raw["summary_raw"], 360)
    # Google News & Co. liefern oft nur Titel + Quelle als Beschreibung
    norm_s = strip_accents(summary.lower())
    norm_t = strip_accents(title.lower())
    if not summary or norm_s.startswith(norm_t[:40]) or len(summary) < 25:
        summary = ""
    return {
        "id": make_id(job["topic"], canonical_url(url), title),
        "topic": job["topic"],
        "title": title,
        "summary": summary,
        "url": url,
        "source": source,
        "via": "google" if job["kind"] == "google" else "feed",
        "lang": job["lang"],
        "published": date.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "image": safe_url(raw["image"]),
        "_age": age_h,
        "_weight": job.get("weight", 1.0),
        "_tok": tokens(title),
    }


def similar(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    shared = len(a & b)
    if shared < 2:
        return False
    overlap = shared / min(len(a), len(b))
    jacc = shared / len(a | b)
    return (shared >= 3 and overlap >= 0.6) or jacc >= 0.5


def cluster_and_score(items: list[dict]) -> list[dict]:
    """Bündelt gleiche Meldungen pro Thema, bewertet nach Aktualität und Abdeckung."""
    # exakte Duplikate (gleiche URL oder gleicher Titel) entfernen
    seen, uniq = set(), []
    for a in sorted(items, key=lambda x: (x["via"] != "feed", x["_age"])):
        k1 = canonical_url(a["url"])
        k2 = strip_accents(a["title"].lower())
        if k1 in seen or k2 in seen:
            continue
        seen.update((k1, k2))
        uniq.append(a)

    clusters: list[list[dict]] = []
    for a in uniq:
        for c in clusters:
            if similar(a["_tok"], c[0]["_tok"]):
                c.append(a)
                break
        else:
            clusters.append([a])

    out = []
    for c in clusters:
        def quality(x: dict) -> tuple:
            return (x["via"] == "feed", bool(x["image"]), len(x["summary"]) > 40, -x["_age"])
        rep = max(c, key=quality)
        sources = {x["source"].lower() for x in c}
        newest = min(x["_age"] for x in c)
        recency = 0.5 ** (newest / 14.0)
        coverage = 1 + 0.75 * math.log2(len(sources))
        weight = max(x["_weight"] for x in c)
        art = dict(rep)
        if not art["summary"]:
            art["summary"] = next((x["summary"] for x in c if x["summary"]), "")
        if not art["image"]:
            art["image"] = next((x["image"] for x in c if x["image"]), "")
        art["score"] = round(100 * recency * coverage * weight, 2)
        art["also"] = [{"source": x["source"], "url": x["url"]} for x in c if x is not rep][:5]
        out.append(art)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def to_video(job: dict, raw: dict, max_age_h: float, include_shorts: bool) -> dict | None:
    vid = raw.get("videoId") or ""
    link = raw["link"] or ""
    if not vid:
        m = re.search(r"(?:v=|/shorts/|youtu\.be/)([\w-]{11})", link)
        vid = m.group(1) if m else ""
    if not re.fullmatch(r"[\w-]{11}", vid):
        return None
    if "/shorts/" in link and not include_shorts:
        return None
    date = raw["date"] or NOW
    age_h = (NOW - date).total_seconds() / 3600
    if age_h > max_age_h:
        return None
    return {
        "id": make_id(job["topic"], vid),
        "topic": job["topic"],
        "videoId": vid,
        "title": raw["title"],
        "channel": raw["author"] or job["name"],
        "summary": clean_text(raw["summary_raw"], 220),
        "lang": job.get("lang", ""),
        "published": date.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "url": f"https://www.youtube.com/watch?v={vid}",
    }


# --------------------------------------------------------------------------
# KI (OpenAI-kompatibel: GitHub Models, Ollama, LM Studio, OpenAI …)
# --------------------------------------------------------------------------

class AI:
    def __init__(self, cfg: dict):
        self.ok = False
        self.calls = 0
        self.errors = 0
        self.last = 0.0
        self.fails = 0
        ai_cfg = cfg.get("ai", {})
        self.budget = int(ai_cfg.get("maxCallsPerRun", 45))
        self.gap = 0.0
        if not ai_cfg.get("enabled", False):
            log("KI: in config.json deaktiviert")
            return
        base = os.environ.get("AI_BASE_URL", "").strip()
        if base:
            self.url = base.rstrip("/") + "/chat/completions"
            self.key = os.environ.get("AI_API_KEY", "").strip() or "none"
            self.model = os.environ.get("AI_MODEL", "").strip() or ai_cfg.get("model", "llama3.1")
            self.provider = "custom"
        elif os.environ.get("GITHUB_TOKEN"):
            self.url = "https://models.github.ai/inference/chat/completions"
            self.key = os.environ["GITHUB_TOKEN"]
            self.model = os.environ.get("AI_MODEL", "").strip() or ai_cfg.get("model", "openai/gpt-4o-mini")
            self.provider = "github-models"
            # Gratis-Kontingent: ~15 Anfragen/Minute, ~150/Tag → Abstand halten
            self.gap = float(ai_cfg.get("secondsBetweenCalls", 4.5))
        else:
            log("KI: kein Anbieter gefunden (GITHUB_TOKEN oder AI_BASE_URL) – weiter ohne KI")
            return
        self.ok = True
        log(f"KI: {self.provider} · Modell {self.model}")

    @property
    def has_budget(self) -> bool:
        return self.ok and 0 <= self.calls < self.budget

    def chat(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.4) -> str | None:
        if not self.ok:
            return None
        res = self._chat(system, user, max_tokens, temperature)
        if res is None:
            self.fails += 1
            if self.fails >= 3 and self.ok:
                log("KI: 3 Fehler hintereinander – weiter ohne KI")
                self.ok = False
        else:
            self.fails = 0
        return res

    def _chat(self, system: str, user: str, max_tokens: int, temperature: float) -> str | None:
        if self.calls >= self.budget:
            if self.budget >= 0:
                log(f"KI: Budget von {self.budget} Anfragen pro Lauf erreicht – Rest ohne KI")
                self.budget = -1
            return None
        wait = self.gap - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode()
        for attempt in range(3):
            req = urllib.request.Request(self.url, data=body, method="POST", headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
                "User-Agent": UA,
            })
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self.calls += 1
                return (data["choices"][0]["message"]["content"] or "").strip()
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode("utf-8", "ignore")
                if e.code == 429 and attempt < 2:
                    wait = min(int(e.headers.get("Retry-After", "20") or 20), 60)
                    log(f"KI: Rate-Limit, warte {wait}s …")
                    time.sleep(wait)
                    continue
                log(f"KI-Fehler HTTP {e.code}: {detail}")
                self.errors += 1
                if e.code in (401, 403, 404):
                    log("KI: kein Zugriff – deaktiviere KI für diesen Lauf")
                    self.ok = False
                return None
            except Exception as e:  # noqa: BLE001
                log(f"KI-Fehler: {type(e).__name__}: {e}")
                self.errors += 1
                if attempt < 2:
                    time.sleep(5)
                    continue
                return None
        return None


def extract_json(text: str | None):
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", t, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None


TRANSLATE_SYSTEM = (
    "Du übersetzt Nachrichten-Schlagzeilen und Teaser präzise ins Deutsche (Schweizer Rechtschreibung: "
    "immer 'ss' statt 'ß'). Behalte Namen, Firmen, Produkt- und Spieltitel im Original. "
    "Schreibe aus dem Teaser eine sachliche Zusammenfassung in 1–2 kurzen Sätzen; wenn kein Teaser da ist, "
    "lass 's' leer. Erfinde nichts. Antworte ausschliesslich mit JSON im Format "
    '{"items":[{"i":0,"t":"Titel","s":"Zusammenfassung"}]}.'
)


def translate(ai: AI, items: list[dict], batch: int = 24) -> int:
    done = 0
    for start in range(0, len(items), batch):
        chunk = items[start:start + batch]
        payload = [{"i": k, "t": it["title"], "s": it.get("summary", "")[:300]} for k, it in enumerate(chunk)]
        res = extract_json(ai.chat(TRANSLATE_SYSTEM, json.dumps({"items": payload}, ensure_ascii=False),
                                   max_tokens=2500, temperature=0.2))
        if not ai.has_budget and res is None:
            break
        rows = res.get("items", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
        for row in rows:
            try:
                it = chunk[int(row["i"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            t = clean_text(str(row.get("t", "")), 220)
            s = clean_text(str(row.get("s", "")), 360)
            if t:
                it["titleDe"] = t.replace("ß", "ss")
                done += 1
            if s:
                it["summaryDe"] = s.replace("ß", "ss")
    return done


RADIO_SYSTEM = (
    "Du bist Moderator:in von '{show}', einem kurzen deutschsprachigen Nachrichten-Podcast im "
    "Radio-Stil für ein Schweizer Publikum (Schweizer Rechtschreibung: 'ss' statt 'ß'). "
    "Schreibe ausschliesslich gesprochenen Fliesstext: keine Überschriften, kein Markdown, keine Listen, "
    "keine Emojis, keine Regieanweisungen. Verwende nur Fakten aus den gelieferten Meldungen und erfinde "
    "nichts dazu. Nenne bei jeder Meldung kurz die Quelle. Schreibe Zahlen, Währungen und Abkürzungen so, "
    "dass sie sich gut vorlesen lassen. Kurze Sätze, lebendig, aber sachlich."
)


def hours_label(iso: str) -> str:
    d = parse_date(iso) or NOW
    h = int((NOW - d).total_seconds() // 3600)
    return "gerade eben" if h < 1 else f"vor {h} Std."


def story_lines(arts: list[dict]) -> str:
    lines = []
    for n, a in enumerate(arts, 1):
        t = a.get("titleDe") or a["title"]
        s = a.get("summaryDe") or a.get("summary") or ""
        lines.append(f"{n}. [{a['source']}, {hours_label(a['published'])}] {t}" + (f" — {s}" if s else ""))
    return "\n".join(lines)


def topic_strength(arts: list[dict]) -> float:
    return sum(a.get("score", 0) for a in arts[:4])


def radio_segments(ai: AI, cfg: dict, by_topic: dict[str, list[dict]], top: list[dict]) -> dict:
    """Moderationstexte: ein Überblick + ein Beitrag pro Rubrik (mehrere Rubriken pro KI-Anfrage)."""
    ai_cfg = cfg.get("ai", {})
    words = ai_cfg.get("segmentWords", 130)
    batch = max(1, int(ai_cfg.get("radioBatch", 5)))
    system = RADIO_SYSTEM.replace("{show}", cfg.get("site", {}).get("radioName", "News Briefing"))
    out: dict = {"segments": {}, "overview": ""}
    if top:
        txt = ai.chat(system,
                      f"Schreibe den Block 'Die wichtigsten Meldungen im Überblick' mit etwa {int(words * 0.9)} "
                      f"Wörtern. Beginne direkt mit der ersten Meldung (keine Begrüssung, keine Verabschiedung).\n\n"
                      f"Meldungen:\n{story_lines(top[:5])}", max_tokens=900, temperature=0.6)
        if txt:
            out["overview"] = clean_text(txt).replace("ß", "ss")
    # stärkste Rubriken zuerst, damit bei knappem Budget die wichtigsten moderiert sind
    todo = [t for t in cfg["topics"] if len(by_topic.get(t["id"], [])) >= 3]
    todo.sort(key=lambda t: topic_strength(by_topic[t["id"]]), reverse=True)
    for i in range(0, len(todo), batch):
        if not ai.has_budget:
            break
        chunk = todo[i:i + batch]
        blocks = []
        for t in chunk:
            blocks.append(f"### id={t['id']} · Rubrik: {t['label'].replace(' · ', ', ')}\n"
                          f"{story_lines(by_topic[t['id']][:6])}")
        prompt = (
            f"Schreibe für JEDE der folgenden {len(chunk)} Rubriken einen eigenen Radio-Beitrag mit etwa {words} "
            f"Wörtern. Jeder Beitrag beginnt mit einer kurzen, natürlichen Überleitung zur Rubrik "
            f"(abwechslungsreich, z.B. 'Weiter geht's mit …', 'Jetzt zu …', 'Und im …'), behandelt die wichtigsten "
            f"3–5 Meldungen und endet ohne Verabschiedung. Jeder Beitrag muss für sich allein verständlich sein.\n"
            f"Antworte ausschliesslich mit JSON im Format "
            f'{{"segments": {{"<id>": "Text", ...}}}} und verwende genau die angegebenen ids.\n\n'
            + "\n\n".join(blocks))
        res = extract_json(ai.chat(system, prompt, max_tokens=min(4000, 420 * len(chunk) + 200), temperature=0.6))
        segs = res.get("segments", res) if isinstance(res, dict) else {}
        for t in chunk:
            txt = segs.get(t["id"]) if isinstance(segs, dict) else None
            if isinstance(txt, str) and len(txt) > 40:
                out["segments"][t["id"]] = clean_text(txt).replace("ß", "ss")
    return out


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default="news.json")
    ap.add_argument("--no-ai", action="store_true", help="KI-Schritt überspringen")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    st = cfg.get("settings", {})
    max_age = float(st.get("maxAgeHours", 48))
    vid_age = float(st.get("videoMaxAgeHours", 96))
    max_per_topic = int(st.get("maxArticlesPerTopic", 50))
    max_vids = int(st.get("maxVideosPerTopic", 10))
    shorts = bool(st.get("includeShorts", False))

    jobs = build_jobs(cfg)
    log(f"Hole {len(jobs)} Quellen …")
    t0 = time.time()
    raw_articles: dict[str, list[dict]] = {t["id"]: [] for t in cfg["topics"]}
    raw_videos: dict[str, list[dict]] = {t["id"]: [] for t in cfg["topics"]}
    source_status = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for job, entries, err in ex.map(run_job, jobs):
            label = job["name"] or job.get("handle") or job.get("url")
            if err:
                log(f"  ✗ [{job['topic']}] {label}: {err}")
                source_status.append({"name": label, "topic": job["topic"], "ok": False, "error": err[:160]})
                continue
            n = 0
            for raw in entries:
                if job["kind"] == "youtube":
                    v = to_video(job, raw, vid_age, shorts)
                    if v:
                        raw_videos[job["topic"]].append(v)
                        n += 1
                else:
                    a = to_article(job, raw, max_age)
                    if a:
                        raw_articles[job["topic"]].append(a)
                        n += 1
            log(f"  ✓ [{job['topic']}] {label}: {n}/{len(entries)}")
            source_status.append({"name": label, "topic": job["topic"], "ok": True, "items": n})
    log(f"Abruf fertig in {time.time() - t0:.1f}s")

    by_topic: dict[str, list[dict]] = {}
    for tid, items in raw_articles.items():
        by_topic[tid] = cluster_and_score(items)[:max_per_topic]
    videos: dict[str, list[dict]] = {}
    for tid, vids in raw_videos.items():
        seen, uniq = set(), []
        for v in sorted(vids, key=lambda x: x["published"], reverse=True):
            if v["videoId"] not in seen:
                seen.add(v["videoId"])
                uniq.append(v)
        videos[tid] = uniq[:max_vids]

    # Top-Meldungen über alle Themen (themenübergreifend dedupliziert)
    pool, seen_urls = [], set()
    for items in by_topic.values():
        for a in items[:15]:
            k = canonical_url(a["url"])
            if k not in seen_urls:
                seen_urls.add(k)
                pool.append(a)
    top = sorted(pool, key=lambda x: x["score"], reverse=True)[:12]

    # KI
    radio = {"segments": {}, "overview": ""}
    ai = AI(cfg) if not args.no_ai else None
    if ai and ai.ok:
        ai_cfg = cfg.get("ai", {})
        if ai_cfg.get("translate", True):
            n_per = int(ai_cfg.get("maxTranslatePerTopic", 20))
            todo, ids = [], set()
            for items in [top] + list(by_topic.values()):
                for a in items[:n_per]:
                    if a["lang"] != "de" and a["id"] not in ids:
                        ids.add(a["id"])
                        todo.append(a)
            for vs in videos.values():
                for v in vs[:4]:
                    if v.get("lang") and v["lang"] != "de" and v["id"] not in ids:
                        ids.add(v["id"])
                        todo.append(v)
            n_radio = sum(1 for t in cfg["topics"] if len(by_topic.get(t["id"], [])) >= 3)
            reserve = (math.ceil(n_radio / max(1, int(ai_cfg.get("radioBatch", 5)))) + 1) if ai_cfg.get("radio", True) else 0
            max_items = max(0, (ai.budget - reserve)) * 24
            if len(todo) > max_items:
                todo = todo[:max_items]
            log(f"KI: übersetze {len(todo)} Einträge …")
            n = translate(ai, todo)
            log(f"KI: {n} Übersetzungen")
        if ai.has_budget and ai_cfg.get("radio", True):
            log("KI: schreibe Radio-Moderation …")
            radio = radio_segments(ai, cfg, by_topic, top)
            log(f"KI: {len(radio['segments'])} Rubriken moderiert")

    def public(a: dict) -> dict:
        return {k: v for k, v in a.items() if not k.startswith("_") and v not in ("", None, [])}

    articles = [public(a) for items in by_topic.values() for a in items]
    out = {
        "version": 1,
        "generated": NOW.isoformat(timespec="seconds"),
        "site": cfg.get("site", {}),
        "defaultInterests": cfg.get("defaultInterests", []),
        "topics": [{"id": t["id"], "label": t["label"], "icon": t.get("icon", "◇"),
                    "group": t.get("group", "Weitere"),
                    "description": t.get("description", ""),
                    "count": len(by_topic.get(t["id"], [])),
                    "videos": len(videos.get(t["id"], []))} for t in cfg["topics"]],
        "top": [a["id"] for a in top],
        "articles": articles,
        "videos": [public(v) for vs in videos.values() for v in vs],
        "radio": radio,
        "ai": {"used": bool(ai and ai.calls), "model": getattr(ai, "model", None) if ai and ai.calls else None,
               "calls": ai.calls if ai else 0},
        "sources": source_status,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    ok = sum(1 for s in source_status if s["ok"])
    log(f"Fertig: {len(articles)} Artikel, {len(out['videos'])} Videos, "
        f"{ok}/{len(source_status)} Quellen ok → {args.out}")
    return 0 if articles else 1


if __name__ == "__main__":
    sys.exit(main())
