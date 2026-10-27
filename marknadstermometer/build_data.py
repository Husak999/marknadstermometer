#!/usr/bin/env python3
"""
Daglig Invest – Marknadstermometer
----------------------------------
Hämtar kursdata gratis via Yahoo Finance (yfinance), räknar ut
marknadsbredd, nya highs/lows, sektorstyrka, VIX och S&P 500-trend,
och skriver resultatet till data.json som HTML-widgeten läser.

Körs av GitHub Actions (se .github/workflows/marknadstermometer.yml).
Kostar ingenting att driva.
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

HERE = Path(__file__).resolve().parent
OUT_FILE = HERE / "data.json"
FALLBACK_TICKERS = HERE / "sp500_fallback.json"

CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
)

# Sektor-ETF:er (SPDR) som representerar S&P 500:s elva sektorer
SECTOR_ETFS = {
    "XLK": "Teknik",
    "XLC": "Kommunikation",
    "XLY": "Sällanköpsvaror",
    "XLF": "Finans",
    "XLV": "Hälsovård",
    "XLI": "Industri",
    "XLE": "Energi",
    "XLB": "Material",
    "XLP": "Dagligvaror",
    "XLU": "Kraftförsörjning",
    "XLRE": "Fastigheter",
}
SECTOR_PERIODS = {"7d": 7, "30d": 30, "6m": 182}  # kalenderdagar

INDEX_TICKERS = ["^GSPC", "^VIX", "^VIX3M", "SPY"]

STOCKHOLM = ZoneInfo("Europe/Stockholm")


# ----------------------------------------------------------------------------
# Hjälpfunktioner
# ----------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[termometer] {msg}", flush=True)


def to_yahoo(symbol: str) -> str:
    """BRK.B -> BRK-B (Yahoo-notation)."""
    return symbol.replace(".", "-").strip()


def load_constituents() -> list[dict]:
    """Aktuell S&P 500-lista. Faller tillbaka på sparad lista om nätet strular."""
    try:
        r = requests.get(CONSTITUENTS_URL, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        out = [
            {"symbol": str(sym).strip(), "sector": str(sec).strip()}
            for sym, sec in zip(df["Symbol"], df["GICS Sector"])
            if str(sym).strip() and str(sym) != "nan"
        ]
        if len(out) >= 450:
            log(f"Hämtade {len(out)} S&P 500-bolag från datasets/s-and-p-500-companies")
            # Uppdatera fallback-listan så den aldrig blir gammal
            FALLBACK_TICKERS.write_text(json.dumps(out, ensure_ascii=False, indent=0))
            return out
        log(f"Varning: bara {len(out)} bolag i listan, använder fallback")
    except Exception as exc:  # noqa: BLE001
        log(f"Kunde inte hämta constituents ({exc}), använder fallback")
    return json.loads(FALLBACK_TICKERS.read_text())


def download_closes(tickers: list[str], period: str = "2y", attempts: int = 3) -> pd.DataFrame:
    """Hämtar stängningskurser för många tickers, med omförsök för de som saknas."""
    frames: list[pd.DataFrame] = []
    remaining = list(dict.fromkeys(tickers))
    for attempt in range(1, attempts + 1):
        if not remaining:
            break
        log(f"Nedladdning försök {attempt}: {len(remaining)} tickers")
        try:
            raw = yf.download(
                remaining,
                period=period,
                interval="1d",
                auto_adjust=False,
                group_by="column",
                threads=True,
                progress=False,
            )
        except Exception as exc:  # noqa: BLE001
            log(f"yf.download misslyckades: {exc}")
            time.sleep(10 * attempt)
            continue
        if raw is None or raw.empty:
            time.sleep(10 * attempt)
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"].copy()
        else:  # bara en ticker
            close = raw[["Close"]].copy()
            close.columns = remaining[:1]
        close = close.dropna(axis=1, how="all")
        frames.append(close)
        got = set(close.columns)
        remaining = [t for t in remaining if t not in got]
        if remaining:
            log(f"Saknar fortfarande {len(remaining)} tickers, t.ex. {remaining[:8]}")
            time.sleep(5 * attempt)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, axis=1)
    df = df.loc[:, ~df.columns.duplicated()]
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.sort_index()


def pct(a: float, b: float) -> float | None:
    if a is None or b is None or b == 0 or any(map(lambda v: v is None or (isinstance(v, float) and math.isnan(v)), (a, b))):
        return None
    return round((a / b - 1.0) * 100.0, 2)


def ret_calendar(series: pd.Series, days: int) -> float | None:
    """Avkastning från senaste stängning tillbaka `days` kalenderdagar."""
    s = series.dropna()
    if len(s) < 2:
        return None
    end_date = s.index[-1]
    target = end_date - pd.Timedelta(days=days)
    past = s[s.index <= target]
    if past.empty:
        return None
    return pct(float(s.iloc[-1]), float(past.iloc[-1]))


def ret_bars(series: pd.Series, bars: int) -> float | None:
    s = series.dropna()
    if len(s) <= bars:
        return None
    return pct(float(s.iloc[-1]), float(s.iloc[-1 - bars]))


def ytd(series: pd.Series) -> float | None:
    s = series.dropna()
    if s.empty:
        return None
    year = s.index[-1].year
    prev = s[s.index.year < year]
    if prev.empty:
        return None
    return pct(float(s.iloc[-1]), float(prev.iloc[-1]))


def clean(v):
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        if math.isnan(v) or math.isinf(v):
            return None
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


# ----------------------------------------------------------------------------
# Beräkningar
# ----------------------------------------------------------------------------
def breadth_and_highlows(closes: pd.DataFrame) -> dict:
    """Andel aktier över MA20/50/200 samt nya 52-veckors- och 20-dagars-highs/lows."""
    res = {}
    last = closes.iloc[-1]
    counts = closes.notna().sum()

    for n in (20, 50, 200):
        ma = closes.rolling(n, min_periods=n).mean().iloc[-1]
        valid = (counts >= n) & last.notna() & ma.notna()
        above = (last[valid] > ma[valid]).sum()
        total = int(valid.sum())
        res[f"ma{n}"] = round(100.0 * above / total, 1) if total else None
        res[f"ma{n}_n"] = total

    def highs_lows(window: int) -> tuple[int, int, int]:
        sub = closes.tail(window)
        valid = sub.notna().sum() >= min(window, 60)
        sub = sub.loc[:, valid]
        hi = sub.max()
        lo = sub.min()
        cur = sub.iloc[-1]
        n_hi = int((cur >= hi * 0.999).sum())  # inom 0,1 % av toppen räknas som ny high
        n_lo = int((cur <= lo * 1.001).sum())
        return n_hi, n_lo, int(valid.sum())

    h52, l52, n52 = highs_lows(252)
    h20, l20, n20 = highs_lows(20)
    res["highs_52w"], res["lows_52w"], res["n_52w"] = h52, l52, n52
    res["highs_20d"], res["lows_20d"], res["n_20d"] = h20, l20, n20

    # Andel aktier som stigit senaste 5 handelsdagarna (kompletterande breddmått)
    if len(closes) > 5:
        chg5 = closes.iloc[-1] / closes.iloc[-6] - 1
        n5 = int(chg5.notna().sum())
        res["up_5d_pct"] = round(100.0 * (chg5 > 0).sum() / n5, 1) if n5 else None
    res["n"] = int(last.notna().sum())
    res["date"] = closes.index[-1].strftime("%Y-%m-%d")
    return res


def sector_table(closes: pd.DataFrame) -> dict:
    out: dict[str, list] = {}
    spy = closes["SPY"] if "SPY" in closes else None
    for key, days in SECTOR_PERIODS.items():
        rows = []
        spy_ret = ret_calendar(spy, days) if spy is not None else None
        for etf, name in SECTOR_ETFS.items():
            if etf not in closes:
                continue
            r = ret_calendar(closes[etf], days)
            if r is None:
                continue
            rows.append(
                {
                    "etf": etf,
                    "name": name,
                    "ret": r,
                    "vs_spx": round(r - spy_ret, 2) if spy_ret is not None else None,
                }
            )
        rows.sort(key=lambda x: x["ret"], reverse=True)
        out[key] = rows
        out[f"{key}_spx"] = spy_ret
    return out


def vix_block(closes: pd.DataFrame) -> dict:
    if "^VIX" not in closes:
        return {}
    v = closes["^VIX"].dropna()
    last = float(v.iloc[-1])
    prev = float(v.iloc[-2]) if len(v) > 1 else None
    year = v.tail(252)
    out = {
        "last": round(last, 2),
        "prev": round(prev, 2) if prev else None,
        "chg_pct": pct(last, prev),
        "avg20": round(float(v.tail(20).mean()), 2),
        "hi_52w": round(float(year.max()), 2),
        "lo_52w": round(float(year.min()), 2),
        "pct_rank_1y": round(float((year < last).mean() * 100), 0),
    }
    if "^VIX3M" in closes:
        v3 = closes["^VIX3M"].dropna()
        if not v3.empty:
            out["vix3m"] = round(float(v3.iloc[-1]), 2)
            out["term_ratio"] = round(last / float(v3.iloc[-1]), 3)
    return out


def spx_block(closes: pd.DataFrame) -> dict:
    if "^GSPC" not in closes:
        return {}
    s = closes["^GSPC"].dropna()
    last = float(s.iloc[-1])
    out = {
        "last": round(last, 2),
        "chg_1d": ret_bars(s, 1),
        "chg_5d": ret_bars(s, 5),
        "chg_1m": ret_calendar(s, 30),
        "chg_ytd": ytd(s),
        "from_52w_high": pct(last, float(s.tail(252).max())),
        "date": s.index[-1].strftime("%Y-%m-%d"),
    }
    for n in (20, 50, 200):
        ma = float(s.rolling(n).mean().iloc[-1]) if len(s) >= n else None
        out[f"ma{n}"] = round(ma, 2) if ma else None
        out[f"vs_ma{n}"] = pct(last, ma) if ma else None
    # Lutning på MA50: stiger den jämfört med för 10 handelsdagar sedan?
    if len(s) >= 60:
        ma50 = s.rolling(50).mean()
        out["ma50_rising"] = bool(ma50.iloc[-1] > ma50.iloc[-11])
    return out


def composite(breadth: dict, vix: dict, spx: dict) -> dict:
    """Sammanvägt klimat 0–100. 0 = extremt bearish, 100 = extremt bullish."""

    def clamp(x, lo=0.0, hi=100.0):
        return max(lo, min(hi, x))

    c = {}
    c["ma50"] = breadth.get("ma50")
    c["ma20"] = breadth.get("ma20")
    c["ma200"] = breadth.get("ma200")

    h, l = breadth.get("highs_52w", 0), breadth.get("lows_52w", 0)
    c["highlow"] = 50.0 if (h + l) == 0 else clamp(50.0 + 50.0 * (h - l) / (h + l))

    v = vix.get("last")
    if v is None:
        c["vix"] = 50.0
    elif v <= 12:
        c["vix"] = 100.0
    elif v <= 20:
        c["vix"] = 100.0 - (v - 12) / 8 * 40.0  # 12→100, 20→60
    elif v <= 30:
        c["vix"] = 60.0 - (v - 20) / 10 * 35.0  # 20→60, 30→25
    elif v <= 45:
        c["vix"] = 25.0 - (v - 30) / 15 * 25.0  # 30→25, 45→0
    else:
        c["vix"] = 0.0

    a50 = (spx.get("vs_ma50") or 0) > 0
    a200 = (spx.get("vs_ma200") or 0) > 0
    rising = spx.get("ma50_rising", True)
    if a50 and a200:
        c["trend"] = 100.0 if rising else 80.0
    elif a200:
        c["trend"] = 45.0
    elif a50:
        c["trend"] = 35.0
    else:
        c["trend"] = 10.0

    weights = {"ma50": 0.25, "ma200": 0.20, "ma20": 0.15, "highlow": 0.15, "vix": 0.15, "trend": 0.10}
    total_w = sum(w for k, w in weights.items() if c.get(k) is not None)
    score = sum(c[k] * w for k, w in weights.items() if c.get(k) is not None) / total_w
    score = round(clamp(score))

    if score < 25:
        regime = "stark_bearish"
    elif score < 40:
        regime = "bearish"
    elif score < 60:
        regime = "neutral"
    elif score < 75:
        regime = "bullish"
    else:
        regime = "stark_bullish"

    return {"score": int(score), "regime": regime, "components": {k: round(v, 1) for k, v in c.items() if v is not None}, "weights": weights}


# ----------------------------------------------------------------------------
def main() -> int:
    t0 = time.time()
    constituents = load_constituents()
    stock_tickers = [to_yahoo(c["symbol"]) for c in constituents]
    all_tickers = stock_tickers + list(SECTOR_ETFS) + INDEX_TICKERS

    closes = download_closes(all_tickers)
    if closes.empty:
        log("FEL: ingen kursdata alls – behåller gammal data.json")
        return 1

    stocks = closes[[t for t in stock_tickers if t in closes]]
    # Behåll bara handelsdagar där minst hälften av aktierna har en stängningskurs
    # (index/VIX kan ha en extra, nyare rad som aktierna saknar). Fyll sedan
    # enstaka luckor framåt så glidande medelvärden inte spricker.
    stocks = stocks.dropna(thresh=max(1, stocks.shape[1] // 2)).ffill(limit=3)
    log(f"Kursdata för {stocks.shape[1]} av {len(stock_tickers)} aktier, {len(stocks)} handelsdagar, "
        f"senaste aktiedag {stocks.index[-1].date()}, index senaste rad {closes.index[-1].date()}")
    if stocks.shape[1] < 350:
        log("FEL: för få aktier hämtade – behåller gammal data.json")
        return 1

    breadth = breadth_and_highlows(stocks)
    sectors = sector_table(closes)
    vix = vix_block(closes)
    spx = spx_block(closes)
    comp = composite(breadth, vix, spx)

    now_utc = datetime.now(timezone.utc)
    data = {
        "updated_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_se": now_utc.astimezone(STOCKHOLM).strftime("%Y-%m-%d %H:%M"),
        "market_date": breadth.get("date") or spx.get("date"),
        "score": comp["score"],
        "regime": comp["regime"],
        "components": comp["components"],
        "weights": comp["weights"],
        "breadth": breadth,
        "sectors": sectors,
        "vix": vix,
        "spx": spx,
        "universe": "S&P 500",
        "source": "Yahoo Finance via yfinance",
    }

    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        return clean(obj)

    OUT_FILE.write_text(json.dumps(_clean(data), ensure_ascii=False, indent=1))
    log(f"Skrev {OUT_FILE.name}: score={comp['score']} regime={comp['regime']} "
        f"MA20={breadth.get('ma20')}% MA50={breadth.get('ma50')}% MA200={breadth.get('ma200')}% "
        f"VIX={vix.get('last')} ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
