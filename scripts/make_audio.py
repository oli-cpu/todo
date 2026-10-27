#!/usr/bin/env python3
"""
news.studer.website – Radio-Audio mit natürlicher Stimme
========================================================

Liest news.json, baut für jede Rubrik den Sprechtext (KI-Moderation oder
Vorlage) und lässt ihn mit einer Microsoft-Neural-Stimme (edge-tts) als MP3
einsprechen. Die Dateien landen in <out>/audio/, die Infos dazu in
news.json → radio.audio. Die Website spielt diese MP3s ab; fehlt etwas,
nimmt sie automatisch die Stimme des Browsers.

Benötigt:  pip install edge-tts
Aufruf:    python3 scripts/make_audio.py --config config.json --news _site/news.json --out _site

Hinweis: edge-tts nutzt den Vorlese-Dienst von Microsoft Edge. Gratis und ohne
Konto, aber kein offizielles API – falls er einmal nicht geht, läuft die Seite
einfach mit der Browser-Stimme weiter.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Zurich")
except Exception:  # noqa: BLE001
    TZ = timezone(timedelta(hours=1))

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
          "Oktober", "November", "Dezember"]
LANG_NAMES = {"en": "Englisch", "pt": "Portugiesisch", "fr": "Französisch", "it": "Italienisch", "es": "Spanisch"}

# Abkürzungen & Zeichen so umschreiben, dass sie sauber gesprochen werden
SPEECH = [
    (r"\bz\.\s?B\.", "zum Beispiel"), (r"\bu\.\s?a\.", "unter anderem"), (r"\bd\.\s?h\.", "das heisst"),
    (r"\bbzw\.", "beziehungsweise"), (r"\bca\.", "circa"), (r"\binkl\.", "inklusive"),
    (r"\bMio\.", "Millionen"), (r"\bMrd\.", "Milliarden"), (r"\bNr\.", "Nummer"), (r"\bSt\.", "Sankt"),
    (r"(\d)\s?%", r"\1 Prozent"), (r"\s&\s", " und "), (r"\bCHF\b", "Franken"), (r"\bUSD\b", "Dollar"),
    (r"\bEUR\b", "Euro"), (r"[–—]", ", "), (r"[«»„“”\"]", ""), (r"\s+,", ","), (r"\s{2,}", " "),
]


def speech_text(t: str) -> str:
    for rx, rep in SPEECH:
        t = re.sub(rx, rep, t)
    return t.strip()


def first_sentences(t: str, n: int = 1) -> str:
    t = re.sub(r"\s*…$", ".", (t or "").strip())
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9„«\"])", t)
    return " ".join(parts[:n]).strip()


def spoken_domain(d: str) -> str:
    return " punkt ".join(d.split("."))


def is_german(a: dict) -> bool:
    return bool(a.get("titleDe")) or a.get("lang") == "de"


def story(a: dict, k: int) -> str:
    src = a.get("source") or "eine Quelle"
    intro = ["{s} berichtet: ", "Laut {s}: ", "Bei {s} heisst es: ", "Ausserdem meldet {s}: ", "{s} schreibt: "][k % 5]
    intro = intro.replace("{s}", src)
    if is_german(a):
        txt = intro + (a.get("titleDe") or a["title"]).rstrip(".!? ") + "."
        summ = a.get("summaryDe") or (a.get("summary") if a.get("lang") == "de" else "")
        if summ:
            txt += " " + first_sentences(summ)
        return txt
    lang = LANG_NAMES.get(a.get("lang", ""), "in einer anderen Sprache")
    return f"{intro.rstrip(': ')} auf {lang}: {a['title'].rstrip('.!? ')}."


def template_segment(topic: dict, arts: list[dict], n: int) -> str:
    label = topic["label"].replace(" · ", ", ")
    opener = [f"Jetzt zu {label}.", f"Weiter geht's mit {label}.", f"Kommen wir zu {label}.", f"Und nun: {label}."][n % 4]
    pool = sorted(arts[:8], key=lambda a: (not is_german(a), -a.get("score", 0)))
    picks = [a for a in pool if is_german(a)][:3] or pool[:2]
    return " ".join([opener] + [story(a, n + i) for i, a in enumerate(picks)])


def build_parts(news: dict, cfg: dict) -> list[dict]:
    site = news.get("site") or cfg.get("site", {})
    show = site.get("radioName", "News Briefing")
    gen = datetime.fromisoformat(news["generated"]).astimezone(TZ)
    clock = f"{gen.hour} Uhr" + (f" {gen.minute}" if gen.minute else "")
    arts = news.get("articles", [])
    by_topic: dict[str, list[dict]] = {}
    for a in arts:
        by_topic.setdefault(a["topic"], []).append(a)
    for lst in by_topic.values():
        lst.sort(key=lambda a: a.get("score", 0), reverse=True)
    radio = news.get("radio") or {}

    parts = [{"id": "intro", "label": "Intro", "ai": False,
              "text": f"Willkommen beim {show}. Hier sind die Nachrichten von {WEEKDAYS[gen.weekday()]}, "
                      f"dem {gen.day}. {MONTHS[gen.month - 1]}. Stand: {clock}."}]

    by_id = {a["id"]: a for a in arts}
    if radio.get("overview"):
        ov, ov_ai = radio["overview"], True
    else:
        top = [by_id[i] for i in news.get("top", []) if i in by_id]
        top = [a for a in top if is_german(a)][:3] or top[:3]
        ov, ov_ai = " ".join(story(a, i) for i, a in enumerate(top)), False
    if ov:
        parts.append({"id": "overview", "label": "Überblick", "ai": ov_ai,
                      "text": "Zuerst die wichtigsten Schlagzeilen im Überblick. " + ov})

    for n, t in enumerate(news.get("topics", [])):
        lst = by_topic.get(t["id"], [])
        if len(lst) < 2:
            continue
        ai_txt = (radio.get("segments") or {}).get(t["id"])
        parts.append({"id": t["id"], "label": t["label"].split(" · ")[0], "ai": bool(ai_txt),
                      "text": ai_txt or template_segment(t, lst, n)})

    dom = site.get("domain")
    parts.append({"id": "outro", "label": "Outro", "ai": False,
                  "text": f"Das war das {show}. "
                          + (f"Alle Artikel und Videos findest du auf {spoken_domain(dom)}. " if dom else "")
                          + "Bis zum nächsten Mal!"})
    return parts


async def synth(edge_tts, part: dict, path: str, voice: str, rate: str, pitch: str, sem: asyncio.Semaphore) -> bool:
    async with sem:
        for attempt in range(3):
            try:
                comm = edge_tts.Communicate(speech_text(part["text"]), voice, rate=rate, pitch=pitch)
                await asyncio.wait_for(comm.save(path), timeout=120)
                if os.path.exists(path) and os.path.getsize(path) > 2000:
                    return True
            except Exception as e:  # noqa: BLE001
                print(f"  ! {part['id']}: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(2 + attempt * 4)
        if os.path.exists(path):
            os.remove(path)
        return False


async def run(args) -> int:
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)
    tts = cfg.get("tts", {})
    if not tts.get("enabled", True):
        print("TTS: in config.json deaktiviert")
        return 0
    try:
        import edge_tts  # type: ignore
    except ImportError:
        print("TTS: edge-tts nicht installiert (pip install edge-tts) – Website nutzt die Browser-Stimme")
        return 0

    with open(args.news, encoding="utf-8") as f:
        news = json.load(f)
    voice = os.environ.get("TTS_VOICE") or tts.get("voice", "de-CH-LeniNeural")
    rate, pitch = tts.get("rate", "+0%"), tts.get("pitch", "+0Hz")
    parts = build_parts(news, cfg)
    os.makedirs(os.path.join(args.out, "audio"), exist_ok=True)
    print(f"TTS: {len(parts)} Teile mit {voice} …", flush=True)

    sem = asyncio.Semaphore(4)
    jobs = []
    for p in parts:
        h = hashlib.sha1(f"{voice}|{rate}|{pitch}|{p['text']}".encode()).hexdigest()[:10]
        p["file"] = f"audio/{p['id']}-{h}.mp3"
        jobs.append(synth(edge_tts, p, os.path.join(args.out, p["file"]), voice, rate, pitch, sem))
    results = await asyncio.gather(*jobs)

    done = {}
    for p, ok in zip(parts, results):
        if not ok:
            continue
        size = os.path.getsize(os.path.join(args.out, p["file"]))
        done[p["id"]] = {"label": p["label"], "file": p["file"], "text": p["text"], "ai": p["ai"],
                         "dur": round(size * 8 / 48000, 1)}  # Standardformat: 48 kbit/s Mono
    print(f"TTS: {len(done)}/{len(parts)} Teile erzeugt", flush=True)
    if "intro" not in done or len(done) < 3:
        print("TTS: zu wenig erzeugt – Website bleibt bei der Browser-Stimme")
        return 0

    news.setdefault("radio", {})["audio"] = {
        "voice": voice, "label": tts.get("label") or voice, "order": [p["id"] for p in parts if p["id"] in done],
        "parts": done,
    }
    tmp = args.news + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(news, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, args.news)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--news", default="news.json")
    ap.add_argument("--out", default=".", help="Ordner der Website (audio/ wird darin angelegt)")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
