# backtest.py - בדיקה היסטורית של רכיבי המחיר של MarketPulse (מחיר ונפח, טכני, ויקוף).
#
# מה נבדק: בכל נקודת זמן בעבר (כל STEP ימי מסחר), לכל נייר ערך ביקום, מחשבים את אותם כללים
# שהמערכת החיה משתמשת בהם, על היסטוריית השנה שהסתיימה באותו יום, ובודקים מה קרה אחר כך.
# מה לא נבדק: אנליסטים, חדשות ותמות עולם. אין לנו נתוני עבר נקיים עבורם.
#
# מגבלות שחשוב להכיר (נכתבות גם בדוח):
#  - יקום הניירות הוא הרשימה של היום (הטיית הישרדות: חברות שנכשלו ונעלמו לא נכללות).
#  - נתוני Yahoo לא רשמיים, מתואמים לדיבידנדים ופיצולים.
#  - הדגימות חופפות בזמן ומתואמות בין ניירות, ולכן מחשבים מובהקות לפי ממוצע לכל תאריך.
#  - בלי עמלות, החלקה ופערי פתיחה. הכניסה בסגירה של יום האיתות.
#
# הרצה: python backtest.py   (משתני סביבה אופציונליים: BT_STEP, BT_PERIOD, BT_MAX_TICKERS)

import gzip
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import fetch_data as fd

OUT_DIR = "backtest"
STEP = int(os.environ.get("BT_STEP", "5"))               # כל כמה ימי מסחר דוגמים נקודת זמן
HISTORY_PERIOD = os.environ.get("BT_PERIOD", "10y")
MAX_TICKERS = int(os.environ.get("BT_MAX_TICKERS", "0"))  # 0 = כל היקום; מספר קטן = בדיקה מהירה
WINDOW = 252                                              # שנת מסחר, כמו הניתוח החי
HORIZONS = (5, 10, 20)                                    # אופקי בדיקה בימי מסחר
EXPIRY = fd.TRACK_HORIZON_DAYS                            # אורך סימולציית העסקה (30), כמו המעקב החי
BANDS = [("0-19", 0, 20), ("20-39", 20, 40), ("40-59", 40, 60), ("60+", 60, 1000)]


# ------------------------------------------------------------------
# נתונים
# ------------------------------------------------------------------
def load_history(ticker):
    try:
        h = yf.Ticker(ticker).history(period=HISTORY_PERIOD, auto_adjust=True)
    except Exception as e:
        print(f"דילוג על {ticker}: {e}")
        return None
    if h is None or h.empty or "Close" not in h.columns:
        return None
    h = h.dropna(subset=["Close"])
    h.index = pd.to_datetime(h.index.date)          # תאריכים בלבד, בלי אזור זמן
    h = h[~h.index.duplicated(keep="last")]
    if len(h) < WINDOW + EXPIRY + 30:
        return None
    return h


def simulate_trade(entry, stop, target, direction, highs, lows, closes):
    # אותם כללים כמו המעקב החי: סטופ לפני יעד באותו יום, פקיעה אחרי EXPIRY ימים בסגירה
    buy = direction == "buy"
    for k in range(len(highs)):
        hit_stop = lows[k] <= stop if buy else highs[k] >= stop
        hit_tgt = highs[k] >= target if buy else lows[k] <= target
        if hit_stop:
            return "stop", stop, k + 1
        if hit_tgt:
            return "target", target, k + 1
    return "expired", float(closes[-1]), len(highs)


def trade_return(entry, exit_px, direction):
    return (exit_px - entry) / entry * 100 * (1 if direction == "buy" else -1)


# ------------------------------------------------------------------
# יצירת דגימות
# ------------------------------------------------------------------
def build_samples(universe, spy):
    spy_sma200 = spy.rolling(200).mean()
    samples = []
    n_done = 0
    started = time.time()
    for asset_class, tickers in universe.items():
        for ticker in dict.fromkeys(tickers):
            h = load_history(ticker)
            if h is None:
                print(f"אין היסטוריה מספקת: {ticker}")
                continue
            n_done += 1
            closes = h["Close"].to_numpy(float)
            highs = h["High"].to_numpy(float)
            lows = h["Low"].to_numpy(float)
            dates = h.index
            count = 0
            for idx in range(WINDOW - 1, len(h) - EXPIRY - 1, STEP):
                window = h.iloc[idx - WINDOW + 1: idx + 1]
                try:
                    p = fd.analyze_hist(window, ticker, asset_class)
                    if not p:
                        continue
                    comps = fd.available_components(p)
                    cw = sum(fd.CLUSTER_W[k] for k in fd.CLUSTER_KEYS if k in comps)
                    cluster = sum(fd.CLUSTER_W[k] * comps[k] for k in fd.CLUSTER_KEYS if k in comps) / cw
                    direction = "buy" if cluster >= 0 else "sell"
                    entry = p["last"]
                    day = dates[idx]
                    row = {
                        "ticker": ticker, "asset_class": asset_class, "date": day.strftime("%Y-%m-%d"),
                        "year": int(day.year), "direction": direction,
                        "cluster": round(cluster, 2), "strength": round(abs(cluster), 2),
                        "price_dir": round(p["price_dir"], 2), "tech_dir": round(p["tech_dir"], 2),
                        "wy_dir": round(p["wy_dir"], 2) if p.get("wy_dir") is not None else None,
                        "rsi": round(p["rsi"], 1), "ext_atr": round(p["ext_atr"], 2),
                        "spy_below200": bool(spy.asof(day) < spy_sma200.asof(day)) if not math.isnan(spy_sma200.asof(day)) else False,
                    }
                    for hz in HORIZONS:
                        fut = dates[idx + hz]
                        row[f"fwd{hz}"] = round((closes[idx + hz] / closes[idx] - 1) * 100, 3)
                        row[f"spy{hz}"] = round((spy.asof(fut) / spy.asof(day) - 1) * 100, 3)
                    fh, fl, fc = highs[idx + 1: idx + 1 + EXPIRY], lows[idx + 1: idx + 1 + EXPIRY], closes[idx + 1: idx + 1 + EXPIRY]
                    for d, short in (("buy", "b"), ("sell", "s")):
                        stop, target, rr, _note = fd.compute_levels(p, d)
                        status, exit_px, days = simulate_trade(entry, stop, target, d, fh, fl, fc)
                        row[f"t{short}_status"] = status
                        row[f"t{short}_ret"] = round(trade_return(entry, exit_px, d), 3)
                        row[f"t{short}_days"] = days
                        row[f"t{short}_stopdist"] = round(abs(stop - entry) / entry * 100, 2)
                    samples.append(row)
                    count += 1
                except Exception as e:   # דגימה בעייתית אחת לא עוצרת את הבדיקה
                    print(f"שגיאה בדגימה {ticker} {dates[idx].date()}: {e}")
            elapsed = time.time() - started
            print(f"{ticker}: {count} דגימות | סה\"כ {len(samples)} | {n_done} ניירות | {elapsed / 60:.1f} דקות")
            if MAX_TICKERS and n_done >= MAX_TICKERS:
                return pd.DataFrame(samples)
    return pd.DataFrame(samples)


# ------------------------------------------------------------------
# ניתוח
# ------------------------------------------------------------------
def tstat_by_date(df, col, horizon):
    # הדגימות חופפות בזמן (אופק ארוך מהצעד) ומתואמות בין ניירות באותו תאריך.
    # לכן: ממוצע לכל תאריך, ואז t על סדרת התאריכים עם התאמה לחפיפה. שמרני במכוון.
    s = df.groupby("date")[col].mean().dropna()
    if len(s) < 5:
        return None
    n_eff = max(1.0, len(s) / max(1.0, horizon / STEP))
    sd = float(s.std())
    return round(float(s.mean()) / (sd / math.sqrt(n_eff)), 2) if sd > 0 else None


def add_edges(df):
    df = df.copy()
    df["sign"] = np.where(df["direction"] == "buy", 1, -1)
    for hz in HORIZONS:
        uncond = df[f"fwd{hz}"].mean()          # ממוצע כל הדגימות = סחף השוק, בלי יכולת ניבוי
        df[f"edge{hz}"] = df["sign"] * (df[f"fwd{hz}"] - uncond)   # יתרון על "בלי כישרון"
        df[f"dret{hz}"] = df["sign"] * df[f"fwd{hz}"]              # תשואה בכיוון האיתות
    df["t_status"] = np.where(df["direction"] == "buy", df["tb_status"], df["ts_status"])
    df["t_ret"] = np.where(df["direction"] == "buy", df["tb_ret"], df["ts_ret"])
    df["c_status"] = np.where(df["direction"] == "buy", df["ts_status"], df["tb_status"])   # הכיוון ההפוך
    df["c_ret"] = np.where(df["direction"] == "buy", df["ts_ret"], df["tb_ret"])
    return df


def edge_row(name, g):
    if len(g) == 0:
        return None
    return {
        "קבוצה": name, "דגימות": int(len(g)),
        "יתרון 5 ימים %": round(float(g["edge5"].mean()), 3),
        "יתרון 10 ימים %": round(float(g["edge10"].mean()), 3),
        "יתרון 20 ימים %": round(float(g["edge20"].mean()), 3),
        "t (10 ימים)": tstat_by_date(g, "edge10", 10),
        "אחוז עם יתרון חיובי (10 ימים)": round(float((g["edge10"] > 0).mean() * 100), 1),
    }


def trade_row(name, status, ret):
    n = len(status)
    if n == 0:
        return None
    return {
        "קבוצה": name, "עסקאות": int(n),
        "ברווח %": round(float((ret > 0).mean() * 100), 1),
        "יעד %": round(float((status == "target").mean() * 100), 1),
        "סטופ %": round(float((status == "stop").mean() * 100), 1),
        "פקיעה %": round(float((status == "expired").mean() * 100), 1),
        "תשואה ממוצעת %": round(float(ret.mean()), 2),
        "תשואה חציונית %": round(float(ret.median()), 2),
    }


def per_date_ic(df, col, horizon):
    # מתאם דרגות (Spearman) בין הרכיב לבין התשואה העתידית, לכל תאריך בנפרד, ואז ממוצע על התאריכים
    vals = []
    for _day, g in df.groupby("date"):
        g = g[[col, f"fwd{horizon}"]].dropna()
        if len(g) >= 20 and g[col].nunique() > 3:
            vals.append(g[col].rank().corr(g[f"fwd{horizon}"].rank()))
    vals = pd.Series(vals).dropna()
    if len(vals) < 10:
        return None, None
    n_eff = max(1.0, len(vals) / max(1.0, horizon / STEP))
    t = float(vals.mean()) / (float(vals.std()) / math.sqrt(n_eff)) if vals.std() > 0 else None
    return round(float(vals.mean()), 4), (round(t, 2) if t is not None else None)


def analyze(df):
    df = add_edges(df)
    res = {}

    res["כיוון"] = [r for r in (edge_row(d, df[df.direction == k]) for d, k in (("קנייה", "buy"), ("מכירה", "sell"))) if r]

    rows = []
    for dname, dkey in (("קנייה", "buy"), ("מכירה", "sell")):
        for bname, lo, hi in BANDS:
            g = df[(df.direction == dkey) & (df.strength >= lo) & (df.strength < hi)]
            r = edge_row(f"{dname}, חוזק {bname}", g)
            if r:
                rows.append(r)
    res["לפי חוזק האיתות"] = rows

    ic = []
    for label, col in (("מחיר ונפח", "price_dir"), ("טכני", "tech_dir"), ("ויקוף", "wy_dir"), ("אשכול המחיר המשולב", "cluster")):
        sub = df[df[col].notna()]
        row = {"רכיב": label, "דגימות": int(len(sub))}
        for hz in HORIZONS:
            m, t = per_date_ic(sub, col, hz)
            row[f"IC {hz} ימים"] = m
            row[f"t {hz} ימים"] = t
        ic.append(row)
    res["מתאם הרכיב לתשואה העתידית (IC)"] = ic

    quint = []
    for label, col in (("מחיר ונפח", "price_dir"), ("טכני", "tech_dir"), ("ויקוף", "wy_dir")):
        sub = df[df[col].notna()].copy()
        try:
            sub["q"] = pd.qcut(sub[col], 5, labels=False, duplicates="drop")
        except ValueError:
            continue
        for q, g in sub.groupby("q"):
            quint.append({"רכיב": label, "חמישון (0 = הכי דובי)": int(q), "דגימות": int(len(g)),
                          "תשואה גולמית 10 ימים %": round(float(g["fwd10"].mean()), 3),
                          "אחוז עליות": round(float((g["fwd10"] > 0).mean() * 100), 1)})
    res["חמישונים: האם רכיב גבוה מנבא תשואה גבוהה"] = quint

    buys, sells = df[df.direction == "buy"], df[df.direction == "sell"]
    ext_b = (buys.rsi >= fd.RSI_BUY_MAX) | (buys.ext_atr >= fd.EXT_ATR_MAX)
    ext_s = (sells.rsi <= fd.RSI_SELL_MIN) | (sells.ext_atr <= -fd.EXT_ATR_MAX)
    filt = [edge_row("קנייה מתוחה (תיפסל במערכת)", buys[ext_b]), edge_row("קנייה רגילה", buys[~ext_b]),
            edge_row("מכירה מתוחה (תיפסל במערכת)", sells[ext_s]), edge_row("מכירה רגילה", sells[~ext_s]),
            edge_row("קנייה כש-S&P 500 מתחת לממוצע 200", buys[buys.spy_below200]),
            edge_row("קנייה כש-S&P 500 מעל ממוצע 200", buys[~buys.spy_below200])]
    res["מסננים"] = [r for r in filt if r]

    res["לפי שנה"] = [r for r in (edge_row(f"{y} {dn}", df[(df.year == y) & (df.direction == dk)])
                                  for y in sorted(df.year.unique()) for dn, dk in (("קנייה", "buy"), ("מכירה", "sell"))) if r]
    res["לפי סוג נכס"] = [r for r in (edge_row(f"{ac}", df[df.asset_class == ac]) for ac in df.asset_class.unique()) if r]

    trades = [
        trade_row("בכיוון האיתות", df.t_status, df.t_ret),
        trade_row("בכיוון ההפוך לאיתות", df.c_status, df.c_ret),
        trade_row("תמיד קנייה", df.tb_status, df.tb_ret),
        trade_row("תמיד מכירה", df.ts_status, df.ts_ret),
    ]
    for bname, lo, hi in BANDS:
        g = df[(df.strength >= lo) & (df.strength < hi)]
        trades.append(trade_row(f"בכיוון האיתות, חוזק {bname}", g.t_status, g.t_ret))
    res["סימולציית עסקאות (סטופ ויעד כמו במערכת, עד 30 ימים)"] = [r for r in trades if r]

    sd = np.where(df.direction == "buy", df.tb_stopdist, df.ts_stopdist)
    res["_meta"] = {
        "דגימות": int(len(df)), "ניירות": int(df.ticker.nunique()),
        "תקופה": f"{df.date.min()} עד {df.date.max()}", "צעד דגימה (ימי מסחר)": STEP,
        "מרחק סטופ חציוני %": round(float(np.median(sd)), 2),
        "ריצה": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    return res


# ------------------------------------------------------------------
# דוח
# ------------------------------------------------------------------
def md_table(rows):
    if not rows:
        return "(אין נתונים)\n"
    cols = list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |")
    return "\n".join(out) + "\n"


def write_report(res, path):
    meta = res["_meta"]
    lines = ["# דוח בדיקה היסטורית של MarketPulse", "",
             f"נוצר: {meta['ריצה']}. {meta['דגימות']:,} דגימות, {meta['ניירות']} ניירות ערך, {meta['תקופה']}, "
             f"דגימה כל {meta['צעד דגימה (ימי מסחר)']} ימי מסחר. מרחק סטופ חציוני: {meta['מרחק סטופ חציוני %']}%.", "",
             "נבדקו רק רכיבי המחיר (מחיר ונפח, טכני, ויקוף). אנליסטים, חדשות ותמות לא ניתנים לבדיקה היסטורית.", "",
             "**איך לקרוא:** \"יתרון\" = תשואה בכיוון האיתות פחות התשואה הממוצעת של כל הדגימות (כלומר בלי סחף השוק). "
             "יתרון קרוב ל-0 = אין יכולת ניבוי. t מעל 2 (בערך) מצביע על מובהקות, והוא מחושב שמרנית לפי ממוצע לכל תאריך.", "",
             "**מגבלות:** יקום הניירות הוא הרשימה של היום (הטיית הישרדות), נתונים לא רשמיים, בלי עמלות והחלקה, "
             "וכניסה בסגירה של יום האיתות.", ""]
    for key, rows in res.items():
        if key.startswith("_"):
            continue
        lines += [f"## {key}", "", md_table(rows)]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    import warnings
    warnings.simplefilter("ignore")
    os.makedirs(OUT_DIR, exist_ok=True)
    universe = dict(fd.UNIVERSE)
    spy_h = load_history("SPY")
    if spy_h is None:
        print("לא ניתן לטעון את SPY. עוצר.")
        sys.exit(1)
    spy = spy_h["Close"]
    df = build_samples(universe, spy)
    if df.empty or len(df) < 200:
        print(f"מעט מדי דגימות ({len(df)}). עוצר.")
        sys.exit(1)
    print(f"נאספו {len(df)} דגימות. מנתח...")
    res = analyze(df)
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    write_report(res, os.path.join(OUT_DIR, "report.md"))
    keep = ["ticker", "asset_class", "date", "direction", "cluster", "price_dir", "tech_dir", "wy_dir", "rsi", "ext_atr",
            "spy_below200", "fwd5", "fwd10", "fwd20", "spy5", "spy10", "spy20", "tb_status", "tb_ret", "ts_status", "ts_ret"]
    with gzip.open(os.path.join(OUT_DIR, "samples.csv.gz"), "wt", encoding="utf-8") as f:
        df[keep].to_csv(f, index=False)
    print("הבדיקה הושלמה. הדוח נשמר ב-backtest/report.md")


if __name__ == "__main__":
    main()
