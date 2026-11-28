# tech_detail.py - פירוק טכני מפורט לנייר ערך אחד: רמות תמיכה והתנגדות, קווי מגמה,
# ממוצעים נעים ואינדיקטורי מומנטום. כל הפונקציות כאן מחשבות מספרים בלבד (בלי תלות בשאר המערכת).
# הפלט משמש להצגה בדשבורד ובטלגרם, ולבדיקה אם היעד והסטופ יושבים במקומות הגיוניים.
import math

import numpy as np
import pandas as pd


def _pivots(series, w, kind):
    # נקודות קיצון מקומיות: שיא (kind="high") או שפל (kind="low") מול w ימים לכל צד
    vals = series.to_numpy(float)
    out = []
    for i in range(w, len(vals) - w):
        seg = vals[i - w: i + w + 1]
        if (kind == "high" and vals[i] == seg.max()) or (kind == "low" and vals[i] == seg.min()):
            out.append((i, float(vals[i])))
    return out


def sr_levels(hist, last, atr, n_each=3):
    # רמות תמיכה/התנגדות: מקבצים שיאים ושפלים מקומיים שנמצאים קרוב זה לזה לרמה אחת.
    # ככל שיותר נגיעות ונגיעות עדכניות יותר, הרמה חזקה יותר.
    h = hist.iloc[-252:]
    pts = _pivots(h["High"], 4, "high") + _pivots(h["Low"], 4, "low")
    tol = max(0.6 * atr, last * 0.005)
    clusters = []
    for idx, price in sorted(pts, key=lambda x: x[1]):
        if clusters and price - clusters[-1]["max"] <= tol:
            c = clusters[-1]
            c["prices"].append(price); c["idx"].append(idx); c["max"] = price
        else:
            clusters.append({"prices": [price], "idx": [idx], "max": price})
    n = len(h)
    levels = []
    for c in clusters:
        recent = sum(1 for i in c["idx"] if i >= n - 60)
        levels.append({"price": float(np.mean(c["prices"])), "touches": len(c["prices"]), "recent_touches": recent})
    sup = sorted([l for l in levels if l["price"] < last * 0.998], key=lambda l: -l["price"])[:n_each]
    res = sorted([l for l in levels if l["price"] > last * 1.002], key=lambda l: l["price"])[:n_each]
    for l in sup + res:
        l["dist_pct"] = round((l["price"] / last - 1) * 100, 2)
    return {"support": sup, "resistance": res,
            "hi52": float(h["High"].max()), "lo52": float(h["Low"].min())}


def _line_through(p1, p2, n_now):
    (x1, y1), (x2, y2) = p1, p2
    slope = (y2 - y1) / (x2 - x1)
    return slope, y2 + slope * (n_now - x2)


def trend_info(hist, last):
    closes = hist["Close"].iloc[-60:]
    y = np.log(closes.to_numpy(float))
    x = np.arange(len(y))
    slope, inter = np.polyfit(x, y, 1)
    fit = slope * x + inter
    ss_res = float(((y - fit) ** 2).sum()); ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    std = float(np.std(y - fit)) or 1e-9
    pos = float((y[-1] - fit[-1]) / (2 * std))        # -1 = תחתית הערוץ, +1 = ראש הערוץ
    out = {"slope_pct_week": round((math.exp(slope * 5) - 1) * 100, 2), "r2": round(r2, 2),
           "channel_pos": round(max(-1.5, min(1.5, pos)), 2)}
    # קווי מגמה דרך שני השפלים/השיאים המקומיים האחרונים (120 ימים אחרונים)
    h = hist.iloc[-120:]
    n_now = len(h) - 1
    lows = _pivots(h["Low"], 4, "low")[-2:]
    highs = _pivots(h["High"], 4, "high")[-2:]
    if len(lows) == 2 and lows[1][0] - lows[0][0] >= 5:
        s, v = _line_through(lows[0], lows[1], n_now)
        out["support_line"] = {"value": float(v), "slope_pct_week": round(s * 5 / last * 100, 2),
                               "broken": bool(last < v * 0.995)}
    if len(highs) == 2 and highs[1][0] - highs[0][0] >= 5:
        s, v = _line_through(highs[0], highs[1], n_now)
        out["resistance_line"] = {"value": float(v), "slope_pct_week": round(s * 5 / last * 100, 2),
                                  "broken": bool(last > v * 1.005)}
    return out


def ma_info(closes, last):
    out = {}
    for n in (20, 50, 100, 200):
        if len(closes) >= n + 10:
            s = closes.rolling(n).mean()
            v = float(s.iloc[-1])
            out[f"sma{n}"] = {"value": v, "dist_pct": round((last / v - 1) * 100, 2),
                              "slope_pct_10d": round((v / float(s.iloc[-11]) - 1) * 100, 2)}
    vals = [out.get(f"sma{n}", {}).get("value") for n in (20, 50, 200)]
    if all(v is not None for v in vals):
        if last > vals[0] > vals[1] > vals[2]:
            out["alignment"] = "bull"       # מחיר מעל 20 מעל 50 מעל 200: מגמה עולה מסודרת
        elif last < vals[0] < vals[1] < vals[2]:
            out["alignment"] = "bear"
        else:
            out["alignment"] = "mixed"
    return out


def momentum_info(hist):
    c, hi, lo = hist["Close"], hist["High"], hist["Low"]
    delta = c.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    rsi_s = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    rsi = float(rsi_s.iloc[-1]) if not math.isnan(float(rsi_s.iloc[-1])) else 50.0
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    hist_m = macd - sig
    ll, hh = lo.rolling(14).min(), hi.rolling(14).max()
    k = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    d = k.rolling(3).mean()
    out = {"rsi": round(rsi, 1),
           "macd": round(float(macd.iloc[-1]), 4), "macd_signal": round(float(sig.iloc[-1]), 4),
           "macd_hist": round(float(hist_m.iloc[-1]), 4),
           "macd_hist_rising": bool(float(hist_m.iloc[-1]) > float(hist_m.iloc[-4])),
           "stoch_k": round(float(k.iloc[-1]), 1), "stoch_d": round(float(d.iloc[-1]), 1),
           "roc10": round((float(c.iloc[-1]) / float(c.iloc[-11]) - 1) * 100, 2),
           "roc20": round((float(c.iloc[-1]) / float(c.iloc[-21]) - 1) * 100, 2)}
    # דיברגנס: המחיר עושה שיא/שפל חדש של 30 יום אבל ה-RSI לא מאשר
    w = 30
    if len(c) > 2 * w:
        cw, rw = c.iloc[-w:], rsi_s.iloc[-w:]
        prev_c, prev_r = c.iloc[-2 * w:-w], rsi_s.iloc[-2 * w:-w]
        if float(cw.iloc[-1]) >= float(cw.max()) * 0.995 and float(cw.max()) > float(prev_c.max()) \
                and float(rw.max()) < float(prev_r.max()):
            out["divergence"] = "bear"
        elif float(cw.iloc[-1]) <= float(cw.min()) * 1.005 and float(cw.min()) < float(prev_c.min()) \
                and float(rw.min()) > float(prev_r.min()):
            out["divergence"] = "bull"
    return out


def tech_breakdown(hist, last, atr):
    # מחזיר None אם אין מספיק נתונים. כשל כאן לא עוצר את שאר הניתוח.
    try:
        if len(hist) < 120:
            return None
        closes = hist["Close"]
        return {"levels": sr_levels(hist, last, atr), "trend": trend_info(hist, last),
                "ma": ma_info(closes, last), "mom": momentum_info(hist)}
    except Exception:
        return None
