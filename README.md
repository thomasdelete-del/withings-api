# Private Withings REST API

Eine kleine, selbst betriebene REST-API für persönliche Withings-Daten. Sie läuft als Docker-Dienst auf Railway, synchronisiert Messwerte über OAuth 2.0 und kann vorhandene Withings-CSV-Dateien oder ein vollständiges Export-ZIP einlesen.

## Funktionen

- Withings OAuth 2.0 mit Prüfung des `state`-Parameters
- automatische Erneuerung und verschlüsselte Speicherung der Tokens
- Erstimport aller über `Measure/Getmeas` erreichbaren historischen Messwerte
- anschließende inkrementelle Synchronisation alle 30 Minuten
- persistente SQLite-Datenbank auf einem Railway-Volume
- Import von Withings-CSV- und ZIP-Exporten
- Zusammenführung und Deduplizierung von API- und Exportdaten
- REST-Zugriff mit `X-API-Key`
- separate Browser-Anmeldung für Einrichtung und Status
- OpenAPI/Swagger unter `/docs`
- CSV-Gesamtexport der zusammengeführten Daten

Die API-Synchronisation erfasst unter anderem Gewicht, Körperfett, Fettmasse, Muskelmasse, Knochenmasse, Körperwasser, Blutdruck, Puls, Temperatur, SpO₂ und weitere Withings-Messarten. Der Exportimport erkennt zusätzlich Aktivitäts- und Schlafspalten. Unbekannte Withings-API-Messarten bleiben als `withings_type_<Nummer>` erhalten und gehen nicht verloren.

## Projektstruktur

```text
app/
  config.py       Umgebungsvariablen
  db.py           SQLite-Schema und Abfragen
  importer.py     CSV-/ZIP-Import und Einheitenumrechnung
  main.py         FastAPI-Endpunkte und Hintergrundsynchronisation
  models.py       kanonisches Messwertmodell
  security.py     API-Key, Basic Auth und Tokenverschlüsselung
  withings.py     OAuth, Token-Refresh und Withings-Synchronisation
tests/            Import- und Parsertests
Dockerfile        Railway-/Docker-Image
```

## 1. Projekt in GitHub anlegen

1. Dieses Paket entpacken.
2. Auf GitHub ein **privates** Repository anlegen, beispielsweise `withings-api`.
3. Den Inhalt des entpackten Ordners in das Repository hochladen. `.env` niemals hochladen; sie wird durch `.gitignore` ausgeschlossen.

Alternativ im Terminal:

```bash
git init
git add .
git commit -m "Initiale private Withings REST API"
git branch -M main
git remote add origin https://github.com/DEIN-NAME/withings-api.git
git push -u origin main
```

## 2. Railway-Dienst anlegen

1. Bei [Railway](https://railway.com/) anmelden.
2. **New Project → Deploy from GitHub repo** wählen und das private Repository auswählen.
3. Railway erkennt den `Dockerfile` im Hauptverzeichnis automatisch.
4. Unter **Settings → Networking** eine öffentliche Railway-Domain erzeugen.
5. Unter **Settings → Region** `EU West (Amsterdam)` auswählen.
6. Ein Volume an den Dienst anhängen und als Mount-Pfad exakt `/data` eintragen.
7. Unter **Settings → Healthcheck** den Pfad `/health` eintragen.

Die öffentliche Adresse sieht anschließend ungefähr so aus:

```text
https://withings-api-production-1234.up.railway.app
```

## 3. Geheimnisse erzeugen

Auf einem Rechner mit Python und `cryptography`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Die drei Ausgaben werden für `API_KEY`, `ADMIN_PASSWORD` und `TOKEN_ENCRYPTION_KEY` verwendet. Alle Werte nur in Railway als Variablen eintragen, niemals in GitHub.

## 4. Erste Railway-Variablen setzen

Unter **Variables** eintragen:

| Variable | Wert |
| --- | --- |
| `BASE_URL` | vollständige Railway-Adresse ohne `/` am Ende |
| `DATABASE_PATH` | `/data/withings.db` |
| `API_KEY` | erster erzeugter Zufallswert |
| `ADMIN_USERNAME` | beispielsweise `admin` |
| `ADMIN_PASSWORD` | zweiter erzeugter Zufallswert |
| `TOKEN_ENCRYPTION_KEY` | erzeugter Fernet-Schlüssel |
| `SYNC_INTERVAL_MINUTES` | `30` |
| `IMPORT_TIMEZONE` | `Europe/Berlin` |

Die Withings-Zugangsdaten kommen im nächsten Schritt hinzu.

## 5. Withings-Anwendung erstellen

1. Den [Withings Partner Hub](https://developer.withings.com/) öffnen und für die kostenlose Public API eine Anwendung erstellen.
2. Als Redirect-/Callback-URL exakt folgende Adresse eintragen:

   ```text
   https://DEINE-RAILWAY-DOMAIN/oauth/callback
   ```

3. `client_id` und `client_secret` aus dem Withings-Dashboard kopieren.
4. In Railway ergänzen:

| Variable | Wert |
| --- | --- |
| `WITHINGS_CLIENT_ID` | Withings Client-ID |
| `WITHINGS_CLIENT_SECRET` | Withings Client-Secret |
| `WITHINGS_SCOPES` | `user.metrics` |

5. Railway stellt den Dienst nach der Variablenänderung neu bereit.

Der Client-Secret darf nicht im Repository stehen. Der Redirect muss Zeichen für Zeichen mit der bei Withings registrierten URL übereinstimmen.

## 6. Persönliches Withings-Konto verbinden

1. Die Railway-Adresse im Browser öffnen.
2. **Withings-Konto verbinden** wählen.
3. Bei der Browserabfrage `ADMIN_USERNAME` und `ADMIN_PASSWORD` eingeben.
4. Bei Withings anmelden und den Zugriff bestätigen.
5. Nach der Rückleitung beginnt automatisch die erste Synchronisation.
6. Unter `/admin/status` prüfen, ob `withings_connected` auf `true` steht und Messwerte vorhanden sind.

Der von Withings zurückgegebene Autorisierungscode ist nur sehr kurz gültig; die Callback-Route tauscht ihn deshalb unmittelbar gegen Tokens ein.

## 7. Exportierte Withings-Daten importieren

Withings kann ein ZIP-Archiv mit mehreren CSV-Dateien bereitstellen. Das ZIP muss nicht vorher entpackt werden.

1. `/docs` öffnen.
2. Oben **Authorize** wählen und den Wert von `API_KEY` eintragen.
3. `POST /api/v1/import` öffnen.
4. **Try it out** wählen und die Withings-CSV oder das vollständige ZIP auswählen.
5. **Execute** wählen.

Unterstützt werden deutsch- und englischsprachige Standardspalten, Komma/Semikolon/Tabulator als Trennzeichen, Punkt oder Komma als Dezimaltrennzeichen und typische Withings-Datumsformate. Pfund werden in Kilogramm, Meilen in Kilometer sowie Minuten/Stunden in Sekunden umgerechnet. Nicht erkannte Spalten werden im Importbericht genannt und nicht stillschweigend als falsche Messwerte übernommen.

Per `curl`:

```bash
curl -X POST \
  -H "X-API-Key: DEIN_API_KEY" \
  -F "file=@withings-export.zip" \
  https://DEINE-RAILWAY-DOMAIN/api/v1/import
```

## REST-Endpunkte

| Methode | Pfad | Zweck |
| --- | --- | --- |
| `GET` | `/health` | Betriebsstatus, ohne private Messwerte |
| `GET` | `/admin/status` | Verbindung, Zähler und letzter Sync; Basic Auth |
| `GET` | `/oauth/start` | Withings-Konto verbinden; Basic Auth |
| `POST` | `/api/v1/sync` | Synchronisation sofort starten |
| `GET` | `/api/v1/measurements` | Messwerte filtern und paginieren |
| `GET` | `/api/v1/latest` | jüngster Wert jeder Messgröße |
| `GET` | `/api/v1/weight` | Gewichtswerte |
| `GET` | `/api/v1/measure-types` | Withings-Typnummern und Einheiten |
| `POST` | `/api/v1/import` | CSV oder ZIP importieren |
| `GET` | `/api/v1/export.csv` | zusammengeführte Daten als CSV |
| `GET` | `/docs` | interaktive OpenAPI-Dokumentation |

Alle `/api/v1/...`-Endpunkte verlangen den Header `X-API-Key`.

### Beispiele

Jüngste Messwerte:

```bash
curl -H "X-API-Key: DEIN_API_KEY" \
  https://DEINE-RAILWAY-DOMAIN/api/v1/latest
```

Gewicht seit dem 1. Januar 2026:

```bash
curl -G \
  -H "X-API-Key: DEIN_API_KEY" \
  --data-urlencode "from=2026-01-01T00:00:00+01:00" \
  https://DEINE-RAILWAY-DOMAIN/api/v1/weight
```

Nur Körperfett, maximal 100 Werte:

```bash
curl -G \
  -H "X-API-Key: DEIN_API_KEY" \
  --data-urlencode "metric=fat_ratio" \
  --data-urlencode "limit=100" \
  https://DEINE-RAILWAY-DOMAIN/api/v1/measurements
```

## Datensicherheit

- Das GitHub-Repository sollte privat bleiben.
- `client_secret`, API-Key, Admin-Passwort und Fernet-Schlüssel gehören ausschließlich in Railway-Variablen.
- Access- und Refresh-Tokens werden verschlüsselt in SQLite gespeichert.
- Der REST-Zugriff ist durch einen langen, zufälligen API-Key geschützt.
- OAuth-Einrichtung und Status besitzen eine getrennte Browser-Anmeldung.
- Railway stellt die öffentliche Verbindung per HTTPS bereit.
- Das Volume sollte regelmäßig über Railways Volume-Backups gesichert werden.
- `TOKEN_ENCRYPTION_KEY` separat sicher aufbewahren. Geht er verloren, müssen die gespeicherten Tokens durch erneute Withings-Autorisierung ersetzt werden.

Die Datenbank enthält Gesundheitsdaten. Die API sollte nicht ohne Zugriffsschutz veröffentlicht und der Schlüssel nicht in URLs, Screenshots oder öffentliche Repositories kopiert werden.

## Lokaler Test

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# lokale Werte in .env eintragen
pytest
uvicorn app.main:app --reload
```

Oder mit Docker:

```bash
cp .env.example .env
# lokale Werte in .env eintragen
docker compose up --build
```

Danach: `http://localhost:8000/docs`.

## Technische Hinweise

- Bei der ersten API-Synchronisation wird ab Unix-Zeitpunkt 0 abgefragt. Folgesynchronisationen verwenden `lastupdate` mit einem Überlappungsfenster von 24 Stunden, damit nachträglich geänderte Messungen nicht übersehen werden.
- Die Deduplizierung verwendet Zeitpunkt, Messgröße, Wert, Einheit und gegebenenfalls Periodenende. Wiederholte Importe desselben Archivs erzeugen daher keine Kopien.
- Die App ist für genau eine Railway-Instanz mit einem SQLite-Volume ausgelegt. Nicht horizontal auf mehrere Replikate skalieren.
- Die automatische Synchronisation läuft im Webprozess. Wird ein kostenloser Dienst angehalten, erfolgt der nächste Lauf erst nach dem Neustart; ein manueller Lauf ist jederzeit über `POST /api/v1/sync` möglich.
- Die API ruft aktuell die Withings-Messwertschnittstelle `Measure/Getmeas` ab. Aktivitäten und Schlaf werden aus Withings-Exporten importiert; eine spätere Erweiterung kann `Getactivity` und `Getsummary` ergänzen.

## Quellen

- [Withings Public API](https://developer.withings.com/developer-guide/v3/withings-solutions/app-to-app-solution/)
- [Withings API-Referenz: OAuth und Messwerte](https://developer.withings.com/api-reference/)
- [Withings OAuth-Webablauf](https://developer.withings.com/developer-guide/v3/integration-guide/public-health-data-api/get-access/oauth-web-flow/)
- [Withings Access- und Refresh-Tokens](https://developer.withings.com/developer-guide/v3/integration-guide/public-health-data-api/get-access/access-and-refresh-tokens-no-recover/)
- [Withings: Gesundheitsdaten exportieren](https://support.withings.com/hc/de/articles/31647944317201-Withings-App-Android-Exportieren-Ihrer-Daten)
- [Railway: Dockerfiles](https://docs.railway.com/builds/dockerfiles)
- [Railway: persistente Volumes](https://docs.railway.com/volumes)
- [Railway: öffentliche HTTPS-Domains](https://docs.railway.com/networking/public-networking)
- [Railway: Volume-Backups](https://docs.railway.com/volumes/backups)

## Lizenz

MIT – Nutzung auf eigenes Risiko. Dieses Projekt ist keine medizinische Software und nimmt keine medizinische Bewertung der Messwerte vor.
