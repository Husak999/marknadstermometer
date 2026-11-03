"""
Teman (industries) till Marknadstermometern
-------------------------------------------
Rankar ~100+ industries bland amerikanska aktier (NYSE, Nasdaq, NYSE American)
med börsvärde >= 1 miljard $ och kurs >= 5 $. Temats avkastning = medianaktiens
avkastning, så en enskild jätte inte styr hela temat.

För de 5 starkaste temana per period (7d / 30d / 6m) tas de 10 aktier med
högst RS fram. RS = IBD-modellen: viktad avkastning 3/6/9/12 mån (40/20/20/20),
rankad 1–99 mot hela urvalet.

Körs EN gång per handelsdag, efter amerikansk stängning. Bara färdiga
dagsstängningar används, så listan står still under dagen och roterar
automatiskt nästa kväll.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
UNIVERSE_CACHE = HERE / "teman_universum.json"

# Dagligen uppdaterade listor (Nasdaqs screener-data) i ett publikt GitHub-repo
UNIVERSE_URLS = [
    "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nasdaq/nasdaq_full_tickers.json",
    "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/nyse/nyse_full_tickers.json",
    "https://raw.githubusercontent.com/rreichel3/US-Stock-Symbols/main/amex/amex_full_tickers.json",
]

MIN_CAP = 1e9          # börsvärde, $
MIN_PRICE = 5.0        # kurs, $
MIN_MEMBERS = 5        # minsta antal aktier för att räknas som tema
TOP_THEMES = 5         # teman per period som får en topp 10-lista
TOP_STOCKS = 10
PERIODS = {"7d": 7, "30d": 30, "6m": 182}
SKIP_INDUSTRIES = {"Blank Checks", "Trusts Except Educational Religious and Charitable"}

NY = ZoneInfo("America/New_York")

# Snyggare namn på Nasdaqs klumpigaste industry-namn. Saknas ett namn här
# visas originalet. Fyll på fritt.
PRETTY = {
    "EDP Services": "IT Services",
    "Computer Software: Prepackaged Software": "Software",
    "Computer Software: Programming Data Processing": "Software: Data & Cloud",
    "Biotechnology: Pharmaceutical Preparations": "Pharma & Biotech",
    "Biotechnology: Biological Products (No Diagnostic Substances)": "Biotech: Biologics",
    "Biotechnology: In Vitro & In Vivo Diagnostic Substances": "Biotech: Diagnostics",
    "Biotechnology: Laboratory Analytical Instruments": "Lab Instruments",
    "Biotechnology: Electromedical & Electrotherapeutic Apparatus": "Medical Devices: Electromedical",
    "Biotechnology: Commercial Physical & Biological Resarch": "Biotech: Contract Research",
    "Medicinal Chemicals and Botanical Products": "Medicinal Chemicals",
    "Radio And Television Broadcasting And Communications Equipment": "Communications Equipment",
    "Water Sewer Pipeline Comm & Power Line Construction": "Infrastructure Construction",
    "Mining & Quarrying of Nonmetallic Minerals (No Fuels)": "Nonmetallic Mining",
    "Military/Government/Technical": "Defense & Government Tech",
    "Electric Utilities: Central": "Electric Utilities",
    "Real Estate Investment Trusts": "REITs",
    "Industrial Machinery/Components": "Industrial Machinery",
    "Integrated oil Companies": "Integrated Oil",
    "Oil & Gas Production": "Oil & Gas E&P",
    "Auto Parts:O.E.M.": "Auto Parts",
    "Accident &Health Insurance": "Accident & Health Insurance",
    "Services-Misc. Amusement & Recreation": "Leisure & Recreation",
    "Beverages (Production/Distribution)": "Beverages",
    "Retail: Computer Software & Peripheral Equipment": "Retail: Software & Hardware",
    "RETAIL: Building Materials": "Retail: Building Materials",
    "Retail-Auto Dealers and Gas Stations": "Auto Dealers",
    "Building operators": "Building Operators",
    "Finance: Consumer Services": "Consumer Finance",
    "Investment Bankers/Brokers/Service": "Brokers & Investment Banks",
    "Cable & Other Pay Television Services": "Cable & Pay TV",
    "Precious Metals": "Precious Metals (guld/silver)",
}

_NAME_CUT = re.compile(
    r"\s+(Class [A-Z]\b.*|Common Stock.*|Ordinary Shares.*|American Depositary.*|Capital Stock.*|"
    r"Common Shares.*|Units.*|Depositary Shares.*|Subordinate Voting.*|Inc\.?\s*$)",
    re.I,
)


def log(msg: str) -> None:
    print(f"[teman] {msg}", flush=True)


def pretty(ind: str) -> str:
    ind = ind.strip()
    return PRETTY.get(ind, ind)


def short_name(name: str) -> str:
    n = _NAME_CUT.sub("", name or "").strip(" ,.-")
    return n[:40] if n else (name or "")[:40]


def _num(x) -> float:
    try:
        return float(str(x).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


# ----------------------------------------------------------------------------
# Urval
# ----------------------------------------------------------------------------
def load_universe() -> list[dict]:
    rows: list[dict] = []
    try:
        for url in UNIVERSE_URLS:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            rows += r.json()
    except Exception as exc:  # noqa: BLE001
        log(f"Kunde inte hämta aktielistan ({exc}), använder sparad lista")
        rows = []

    out: dict[str, dict] = {}
    for r in rows:
        sym = str(r.get("symbol", "")).strip().upper()
        ind = str(r.get("industry") or "").strip()
        if not sym or not ind or ind in SKIP_INDUSTRIES:
            continue
        if any(c in sym for c in "^$ ") or len(sym) > 6:
            continue
        cap, px = _num(r.get("marketCap")), _num(r.get("lastsale"))
        if cap < MIN_CAP or px < MIN_PRICE:
            continue
        name = short_name(str(r.get("name", "")))
        key = name.lower()  # samma bolag med flera aktieslag (GOOG/GOOGL) → behåll största
        cand = {"symbol": sym.replace("/", "-").replace(".", "-"), "name": name,
                "industry": ind, "sector": str(r.get("sector") or "").strip(), "cap": round(cap)}
        if key in out and out[key]["cap"] >= cand["cap"]:
            continue
        out[key] = cand

    uni = sorted(out.values(), key=lambda x: -x["cap"])
    if len(uni) >= 1000:
        log(f"Urval: {len(uni)} aktier >= {MIN_CAP/1e9:.0f} mdr $")
        UNIVERSE_CACHE.write_text(json.dumps(uni, ensure_ascii=False, indent=0))
        return uni
    if UNIVERSE_CACHE.exists():
        log(f"Bara {len(uni)} aktier i ny lista, använder sparad")
        return json.loads(UNIVERSE_CACHE.read_text())
    return uni


# ----------------------------------------------------------------------------
# Tid: vilken handelsdag är den senast färdiga?
# ----------------------------------------------------------------------------
def last_completed_session(now: datetime | None = None) -> str:
    """Senaste vardag vars stängning är klar (efter 16:30 NY-tid). Helgdagar
    hanteras av att datan helt enkelt inte får en ny rad."""
    t = (now or datetime.now(NY)).astimezone(NY)
    d = t.date()
    if t.hour * 60 + t.minute < 16 * 60 + 30:
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def needs_update(old: dict | None, force: bool = False) -> tuple[bool, str]:
    target = last_completed_session()
    if force:
        return True, target
    if not old or not old.get("periods"):
        return True, target
    return old.get("checked_for") != target, target


# ----------------------------------------------------------------------------
# Beräkning
# ----------------------------------------------------------------------------
def _ret_calendar(closes: pd.DataFrame, days: int) -> pd.Series:
    end = closes.index[-1]
    past = closes[closes.index <= end - pd.Timedelta(days=days)]
    if past.empty:
        return pd.Series(np.nan, index=closes.columns)
    return (closes.iloc[-1] / past.iloc[-1] - 1.0) * 100.0


def _ret_bars(closes: pd.DataFrame, bars: int) -> pd.Series:
    if len(closes) <= bars:
        return pd.Series(np.nan, index=closes.columns)
    return (closes.iloc[-1] / closes.iloc[-1 - bars] - 1.0) * 100.0


def rs_scores(closes: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """IBD-RS: 40 % senaste kvartalet + 20 % vardera för kvartal 2–4.
    Returnerar (råpoäng, rating 1–99). Aktier med < 6 mån historik räknas med
    de kvartal som finns."""
    q = [_ret_bars(closes, n) / 100.0 + 1.0 for n in (63, 126, 189, 252)]
    w = [0.4, 0.2, 0.2, 0.2]
    num = sum(wi * qi.fillna(0) for wi, qi in zip(w, q))
    den = sum(wi * qi.notna() for wi, qi in zip(w, q))
    raw = (num / den.replace(0, np.nan)).where(q[0].notna() & q[1].notna())
    rating = (raw.rank(pct=True) * 98 + 1).round()
    return raw, rating


def build_themes(closes: pd.DataFrame, universe: list[dict], spy: pd.Series | None) -> dict:
    """closes: dagsstängningar (bara färdiga dagar) för aktierna i urvalet."""
    meta = {u["symbol"]: u for u in universe if u["symbol"] in closes.columns}
    closes = closes[list(meta)]
    closes = closes.dropna(thresh=max(1, closes.shape[1] // 2)).ffill(limit=3)
    last = closes.iloc[-1]
    ok = last.notna()
    closes = closes.loc[:, ok]
    syms = list(closes.columns)
    ind_of = pd.Series({s: meta[s]["industry"] for s in syms})

    raw_rs, rs = rs_scores(closes)
    r3m = _ret_bars(closes, 63)
    r1m = _ret_calendar(closes, 30)
    hi52 = closes.tail(252).max()
    from_hi = (closes.iloc[-1] / hi52 - 1.0) * 100.0
    ma50 = closes.rolling(50, min_periods=50).mean().iloc[-1]

    spy_ret = {}
    spy_3m = None
    if spy is not None:
        s = spy.dropna()
        s = s[s.index <= closes.index[-1]]
        if len(s) > 63:
            spy_3m = float((s.iloc[-1] / s.iloc[-64] - 1.0) * 100.0)
        for key, days in PERIODS.items():
            past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
            spy_ret[key] = float((s.iloc[-1] / past.iloc[-1] - 1.0) * 100.0) if not past.empty else None

    def r2(x):
        return None if x is None or pd.isna(x) else round(float(x), 2)

    out: dict = {"periods": {}, "leaders": {}}
    leader_inds: set[str] = set()
    for key, days in PERIODS.items():
        ret = _ret_calendar(closes, days)
        df = pd.DataFrame({"ind": ind_of, "ret": ret}).dropna()
        g = df.groupby("ind")["ret"]
        tab = pd.DataFrame({"med": g.median(), "n": g.size(), "up": g.apply(lambda x: (x > 0).mean() * 100)})
        tab = tab[tab["n"] >= MIN_MEMBERS].sort_values("med", ascending=False)
        rows = []
        for i, (ind, r) in enumerate(tab.iterrows()):
            top = i < TOP_THEMES
            if top:
                leader_inds.add(ind)
            rows.append({
                "id": ind,
                "name": pretty(ind),
                "ret": r2(r["med"]),
                "vs_spx": r2(r["med"] - spy_ret[key]) if spy_ret.get(key) is not None else None,
                "n": int(r["n"]),
                "up_pct": round(float(r["up"])),
                "top10": top,
            })
        out["periods"][key] = rows
        out["spx"] = out.get("spx", {})
        out["spx"][key] = r2(spy_ret.get(key))

    for ind in sorted(leader_inds):
        members = [s for s in syms if ind_of[s] == ind and pd.notna(raw_rs.get(s))]
        members.sort(key=lambda s: raw_rs[s], reverse=True)
        lst = []
        for s in members[:TOP_STOCKS]:
            lst.append({
                "t": s,
                "name": meta[s]["name"],
                "rs": int(rs[s]),
                "vs_spx_3m": r2(r3m[s] - spy_3m) if spy_3m is not None and pd.notna(r3m[s]) else None,
                "ret_1m": r2(r1m[s]),
                "from_high": r2(from_hi[s]),
                "over_ma50": bool(last[s] > ma50[s]) if pd.notna(ma50[s]) else None,
                "cap_b": round(meta[s]["cap"] / 1e9, 1),
            })
        out["leaders"][ind] = {"name": pretty(ind), "n": int((ind_of == ind).sum()), "stocks": lst}

    out["asof"] = closes.index[-1].strftime("%Y-%m-%d")
    out["n_stocks"] = len(syms)
    out["n_themes"] = len(out["periods"].get("30d", []))
    out["universe"] = f"USA-aktier >= {MIN_CAP/1e9:.0f} mdr $ (NYSE, Nasdaq, NYSE American)"
    out["method"] = ("Temats avkastning = medianaktien i temat. RS = IBD-modellen, viktad avkastning "
                     "3/6/9/12 mån (40/20/20/20) rankad 1–99 mot hela urvalet.")
    return out


def drop_unfinished_bar(closes: pd.DataFrame) -> pd.DataFrame:
    """Tar bort dagens rad om amerikansk handel inte stängt än."""
    if closes.empty:
        return closes
    t = datetime.now(NY)
    if closes.index[-1].date() == t.date() and t.hour * 60 + t.minute < 16 * 60 + 30:
        return closes.iloc[:-1]
    return closes
