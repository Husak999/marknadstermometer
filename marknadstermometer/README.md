# Daglig Invest – Marknadstermometer

Gratis marknadsbarometer för S&P 500 som widget till Wix. Visar klimat (0–100),
marknadsbredd (andel aktier över MA20/MA50/MA200), hetaste sektorer (7 dagar /
30 dagar / 6 månader), VIX med kommentar, nya 52-veckors highs & lows, S&P 500-trend
samt summering och guidelines för momentum-/breakout- och swingtraders.

## Hur det fungerar (0 kr i drift)

```
GitHub Actions (schema)  →  build_data.py (yfinance, gratis)  →  data.json i repot
                                                                      ↓
Wix HTML-widget (index.html)  ←  hämtar data.json via raw.githubusercontent.com
```

- `build_data.py` hämtar kurser för alla ~500 bolag i S&P 500, elva sektor-ETF:er,
  S&P 500, VIX och VIX3M via Yahoo Finance och räknar ut alla nyckeltal.
- `.github/workflows/marknadstermometer.yml` kör skriptet varje timme under
  amerikansk handel (mån–fre), efter stängning och en gång på morgonen svensk tid.
  Den committar `data.json` tillbaka till repot.
- `index.html` är hela widgeten. Den läser `data.json` (adressen står i `DATA_URL`
  högst upp i `<script>`-delen) och ritar allt. Inga externa bibliotek.

**Obs:** GitHub kör schemalagda jobb bara på repots *default-branch* (oftast `main`).
Ligger filerna på en annan branch uppdateras datan bara vid push eller manuell körning
(fliken *Actions* → *Run workflow*).

## Lägga in på Wix

1. I Wix-editorn: **Lägg till (+) → Inbäddning → Bädda in HTML / Embed Code**
   (i Wix Studio: *Embed & Social → Embed HTML*).
2. Välj **Kod**, klistra in hela innehållet i `index.html`, klicka *Uppdatera*.
3. Dra ut elementet till full bredd och sätt höjden till ca **2 300 px** (desktop)
   respektive **4 500 px** (mobil-vy). Widgeten har egen mörkblå bakgrund.
4. Placera elementet på medlemssidan så att bara inloggade ser det.

Alternativ: aktivera GitHub Pages i repot (Settings → Pages → branch `main`, mapp `/`)
och använd **Bädda in en webbplats** med adressen till `index.html`. Då kan du
uppdatera widgeten utan att röra Wix.

## Flytta till eget repo

1. Skapa ett nytt publikt repo på GitHub, t.ex. `marknadstermometer`.
2. Kopiera in mappen `marknadstermometer/` och `.github/workflows/marknadstermometer.yml`.
3. Byt `DATA_URL` i `index.html` till
   `https://raw.githubusercontent.com/<användare>/<repo>/main/marknadstermometer/data.json`.
4. Kör workflow:en manuellt en gång (*Actions → Run workflow*). Klart.

## Justera texter och vikter

- Alla guidelines, kommentarer och förklaringar ligger i `index.html`
  (objekten `GUIDE`, `vixNote`, `observations`). Redigera fritt, ingen omkörning behövs.
- Vikterna i klimatsiffran ligger i `composite()` i `build_data.py`:
  MA50 25 %, MA200 20 %, MA20 15 %, highs/lows 15 %, VIX 15 %, indextrend 10 %.
  Gränser: < 25 stark bearish, 25–40 bearish, 40–60 neutral, 60–75 bullish, > 75 stark bullish.

Utbildning, inte finansiell rådgivning.
