# News // Studer — news.studer.website

Deine persönliche Google-News-Alternative: Nachrichten und Videos zu deinen Interessen, jeden Tag neu, plus ein **Radio-Briefing**, das dir die News vorliest. Komplett gratis auf GitHub Pages, erreichbar unter **https://news.studer.website**.

```
index.html                        ← die ganze Website (eine Datei)
config.json                       ← Themen, Quellen, YouTube-Kanäle, Branding, Stimme
scripts/fetch_news.py             ← sammelt alles und schreibt news.json
scripts/make_audio.py             ← spricht das Radio mit Neural-Stimme ein (MP3)
.github/workflows/update-news.yml ← läuft 3× täglich automatisch
```

## Themen (47, in 8 Gruppen)

| Gruppe | Themen |
|---|---|
| Welt & Politik | Schweiz · Zürich · Welt · UNO & Diplomatie · Europa & EU · USA · Brasilien (Politik & Wirtschaft) · Klima & Umwelt |
| Krieg & Sicherheit | Krieg in der Ukraine · Nahost · Kriege & Krisen weltweit · Militär & Verteidigung |
| Regionen | Deutschland & Österreich · Asien & China · Afrika · Lateinamerika |
| Wirtschaft & Finanzen | Wirtschaft Schweiz · Globale Wirtschaft · Börse & Märkte · Startups & Unternehmen · Krypto |
| Sport | Fussball · Formel 1 · MotoGP · Eishockey · Tennis · Ski & Wintersport · Radsport · Basketball & NBA · NFL & US-Sport · Sport allgemein |
| Tech & Games | Gaming · E-Sport · Cybersecurity · Tech · Künstliche Intelligenz · Weltraum |
| Kultur & Gesellschaft | Kultur & Kunst · Bücher & Literatur · Film, Serien & Musik · Gesellschaft · Geschichte |
| Wissen & Leben | Wissenschaft · Gesundheit · Reisen · Auto & Mobilität · Essen & Genuss |

Beim ersten Besuch ist **nichts vorausgewählt**. Solange keine Interessen gewählt sind, zeigt „Für dich“ die grössten Meldungen des Tages.
Unter **Interessen** wählst du einzelne Themen oder ganze Gruppen („+ ALLE“), kannst die Liste filtern (z.B. „Krieg“) und eigene Stichworte ergänzen (z.B. „Lula“, „Ferrari“, „Swisscom“).

## So funktioniert's

1. **GitHub Actions** startet 3× täglich `fetch_news.py`.
2. Das Script holt RSS-Feeds, Google-News-Suchen (CH, US, GB, BR …) und YouTube-Kanäle, entfernt Duplikate, bündelt gleiche Meldungen („+3 Quellen“) und bewertet nach Aktualität und Abdeckung.
3. Eine **gratis KI (GitHub Models)** übersetzt fremdsprachige Titel auf Deutsch und schreibt die Radio-Moderation.
4. `index.html` + `news.json` werden auf GitHub Pages veröffentlicht.
5. Im Browser wählst du deine Interessen. Das Radio liest die Sprachausgabe deines Browsers vor.

## Einrichten

### 1. Repository & Pages

1. Auf GitHub ein **neues, öffentliches Repository** erstellen (z.B. `news`).
2. Alle Dateien hochladen – inklusive `.github/workflows/update-news.yml`.
   Im Web-Upload werden versteckte Ordner manchmal ignoriert. Dann: *Add file → Create new file*, als Namen `.github/workflows/update-news.yml` eintippen, Inhalt hineinkopieren.
3. **Settings → Pages → Build and deployment → Source: „GitHub Actions“**.
4. **Actions → Update News → Run workflow**. Nach ein paar Minuten läuft die Seite unter `https://<dein-name>.github.io/<repo>/`.

### 2. Eigene Domain news.studer.website

1. **DNS** beim Anbieter von `studer.website` – einen neuen Eintrag anlegen:

   | Typ | Name | Wert |
   |---|---|---|
   | CNAME | `news` | `<dein-github-name>.github.io` |

   (Nur den Benutzernamen + `.github.io`, **ohne** Repo-Namen.)
2. **GitHub → Repo → Settings → Pages → Custom domain**: `news.studer.website` eintragen → *Save*.
   Bei Deployment über GitHub Actions braucht es **keine** `CNAME`-Datei im Repo.
3. Warten bis der DNS-Check grün ist (Minuten bis wenige Stunden), dann **Enforce HTTPS** aktivieren.
4. Empfehlung: Unter **GitHub → Profil-Settings → Pages → Add a domain** die Domain `studer.website` **verifizieren**. Dann kann niemand sonst Subdomains davon auf GitHub Pages übernehmen (Schutz vor Subdomain-Takeover).

Danach aktualisiert sich alles von selbst (ca. 05:23, 12:23 und 17:23 Uhr Schweizer Winterzeit). Zeiten ändern: `cron` in `update-news.yml` (Zeiten in UTC).

## Anpassen

Alles in `config.json`:

- **Branding**: `site.name`, `site.logo`, `site.domain`, `site.radioName` (Name der Radio-Sendung).
- **Neues Thema**: einen Block in `topics` kopieren, `id`, `group`, `label`, `icon` ändern. Neue Gruppen entstehen automatisch.
- **RSS-Feed**: `{ "name": "Quelle", "url": "https://…/feed", "lang": "de" }`
- **Google-News-Suche**: `{ "q": "Begriff OR \"genaue Phrase\"", "region": "CH" }` – Regionen: `CH`, `DE`, `AT`, `US`, `GB`, `BR`, `PT`.
  Rubriken: `{ "section": "WORLD|NATION|BUSINESS|TECHNOLOGY|SCIENCE|HEALTH|SPORTS|ENTERTAINMENT", "region": "CH" }`, Schlagzeilen: `{ "top": true, "region": "CH" }`
- **YouTube-Kanal**: `{ "name": "Kanal", "handle": "@kanalname" }` (oder `"id": "UC…"`). Shorts werden ignoriert (`includeShorts`).
- `defaultInterests`: bleibt leer (`[]`), damit beim ersten Besuch nichts vorausgewählt ist.

Unter **[ QUELLEN-STATUS ]** im Footer siehst du nach jedem Lauf, welche Quelle funktioniert hat. Zeigt eine Quelle dauerhaft „FEHLER“, URL anpassen oder entfernen.

## KI (gratis, optional)

Der Workflow nutzt **GitHub Models** mit dem eingebauten `GITHUB_TOKEN` (Permission `models: read`). Kein Key nötig.

- Das Gratis-Kontingent ist begrenzt (ca. 15 Anfragen/Minute, ~150/Tag). Darum macht das Script höchstens **45 Anfragen pro Lauf** (`ai.maxCallsPerRun`) mit 4,5 s Abstand und moderiert jeweils 5 Rubriken pro Anfrage (`ai.radioBatch`). Ein Lauf braucht so typischerweise 15–25 Anfragen. Nach 3 Fehlern in Folge läuft der Rest ohne KI weiter.
- Fällt die KI aus, läuft alles weiter: Titel bleiben im Original, das Radio baut die Moderation aus Vorlagen und liest Fremdsprachiges mit passender Stimme.
- Anderes Modell: Repository-Variable `AI_MODEL` (Settings → Secrets and variables → Actions → Variables).
- KI aus: `"enabled": false` in `config.json`.

## Radio / Podcast

**Natürliche Stimme (Standard):** Beim Update spricht `scripts/make_audio.py` das Radio mit einer Microsoft-Neural-Stimme ein (Paket `edge-tts`, gratis, kein Konto). Die MP3s liegen in `audio/`, die Website spielt sie ab. Das klingt deutlich menschlicher als Browser-Stimmen und läuft auch bei gesperrtem Handy weiter.

- Stimme ändern: `tts.voice` in `config.json` oder Repository-Variable `TTS_VOICE`. Gute deutsche Stimmen:
  `de-CH-LeniNeural` (Standard, Schweizer Färbung) · `de-CH-JanNeural` (männlich, CH) · `de-DE-SeraphinaMultilingualNeural` (sehr natürlich, spricht auch englische/portugiesische Namen gut) · `de-DE-FlorianMultilingualNeural` (männlich) · `de-DE-ConradNeural` · `de-AT-IngridNeural`
- Tempo: `tts.rate` (z.B. `"+5%"`), Tonhöhe: `tts.pitch`.
- Eingesprochen werden Intro, Überblick, eine Rubrik pro Thema (ab 2 Artikeln) und Outro. Die Seite stellt daraus dein persönliches Briefing zusammen.
- `edge-tts` nutzt den Vorlese-Dienst von Microsoft Edge – gratis, aber kein offizielles API. Fällt er aus, schlägt nur dieser Schritt fehl (der Workflow läuft weiter) und die Seite nimmt automatisch die Browser-Stimme.

**Browser-Stimme (Fallback / Auswahl unter „Stimme“):** läuft komplett im Browser (Web Speech API). Am besten in **Microsoft Edge** („Online (Natural)“-Stimmen). Nur hier werden deine eigenen Stichworte mitgelesen.

- Länge Kurz/Normal/Lang, Tempo, Stimme und Jingle einstellbar.
- Transkript kopieren oder als `.txt` herunterladen. Klick auf einen Satz springt dorthin, Leertaste = Play/Pause.
- ♪ bei einem Artikel liest nur diese Meldung (Browser-Stimme).

## Mit dem Heimserver

Das Script braucht nur Python 3:

```bash
python3 scripts/fetch_news.py --config config.json --out news.json
python3 -m http.server 8000      # → http://localhost:8000

# lokale KI (z.B. Ollama) statt GitHub Models
AI_BASE_URL=http://localhost:11434/v1 AI_MODEL=llama3.1 \
  python3 scripts/fetch_news.py --out /var/www/news/news.json
```

Per `cron` ausführen und `index.html` + `news.json` mit nginx/Caddy ausliefern – dann läuft alles zuhause. Mit eigener KI kannst du `maxCallsPerRun` in `config.json` hochsetzen.

## Gut zu wissen

- `index.html` per Doppelklick öffnen geht nicht (`file://` blockiert `news.json`). Lokal immer `python3 -m http.server`.
- GitHub pausiert geplante Workflows nach ca. 60 Tagen ohne Repo-Aktivität (du bekommst vorher eine E-Mail). Dann unter *Actions* „Enable workflow“ klicken.
- Interessen, gelesene und gemerkte Artikel bleiben nur in deinem Browser (localStorage).
