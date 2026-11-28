# risk_report.py - ניתוח תיק וסיכון: חשיפת יתר, קורלציות נסתרות, נקודות חולשה, איזון מחדש לפי סיכון
# ואפשרויות גידור לירידה של 20% בשוק.
#
# מה נחשב "התיק": אם יש קובץ portfolio.json (החזקות אמיתיות, ראו README), משתמשים בו.
# אחרת, התיק הוא כל האיתותים הפתוחים במעקב ואיתותי היום, כאילו הלכת אחרי כולם.
# כל המספרים באחוזים מהתיק, כי גודל התיק לא ידוע למערכת. אין כאן ייעוץ השקעות.
import json
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

PORTFOLIO_FILE = "portfolio.json"
CORR_HIGH = 0.70            # מתאם שנחשב גבוה
CORR_HIDDEN = 0.60          # מתאם בין סוגי נכסים/תמות שונים שכבר מפתיע
TARGET_VOL = 12.0           # תנודתיות שנתית יעד לתיק (אחוזים) להצעת הקטנת חשיפה
CRASH = 20.0                # תרחיש הירידה בשוק (אחוזים)


def load_positions(log, top, today, total_cap=100.0):
    # מחזיר (positions, source, raw_total). positions: ticker, asset_class, w (אחוזי תיק), stop_pct, entry, cluster
    if os.path.exists(PORTFOLIO_FILE):
        try:
            data = json.load(open(PORTFOLIO_FILE, encoding="utf-8"))
            pos = [{"ticker": t, "asset_class": v.get("asset_class", ""), "w": float(v["weight"]),
                    "stop_pct": v.get("stop_pct"), "cluster": v.get("group", t), "mtm": None}
                   for t, v in data.items() if float(v.get("weight", 0)) > 0]
            raw = sum(p["w"] for p in pos)
            return pos, "החזקות מהקובץ portfolio.json", raw
        except Exception as e:
            print(f"שגיאה בקריאת portfolio.json: {e}")
    pos, seen = [], set()
    for r in log:
        if r.get("status") == "open" and r.get("shown") and r.get("direction") == "buy" and r.get("position_pct"):
            seen.add(r["ticker"])
            pos.append({"ticker": r["ticker"], "asset_class": r["asset_class"], "w": float(r["position_pct"]),
                        "stop_pct": abs(r["stop"] - r["entry"]) / r["entry"] * 100 if r.get("entry") else None,
                        "cluster": r.get("cluster", r["ticker"]), "mtm": r.get("mtm")})
    for p in top:
        if p["ticker"] not in seen and p["direction"] == "buy" and p.get("position_pct"):
            pos.append({"ticker": p["ticker"], "asset_class": p["asset_class"], "w": float(p["position_pct"]),
                        "stop_pct": abs(p["raw_stop"] - p["raw_entry"]) / p["raw_entry"] * 100,
                        "cluster": p.get("cluster_name", p["ticker"]), "mtm": None, "today": True})
    raw = sum(p["w"] for p in pos)
    return pos, "איתותי קנייה פתוחים ושל היום", raw


def fetch_returns(tickers):
    closes = {}
    for t in tickers + ["SPY"]:
        try:
            h = yf.Ticker(t).history(period="1y", auto_adjust=True)
            if h is not None and len(h) > 120:
                s = h["Close"].dropna()
                s.index = pd.to_datetime(s.index.date)
                closes[t] = s[~s.index.duplicated(keep="last")]
        except Exception as e:
            print(f"risk: דילוג על {t}: {e}")
    df = pd.DataFrame(closes).sort_index().ffill(limit=3)
    return df.pct_change()


def _bs_put(spot, strike, t_years, vol, r=0.04):
    from math import erf, exp, log, sqrt
    if t_years <= 0 or vol <= 0:
        return max(strike - spot, 0.0)
    d1 = (log(spot / strike) + (r + vol ** 2 / 2) * t_years) / (vol * sqrt(t_years))
    d2 = d1 - vol * sqrt(t_years)
    cdf = lambda x: 0.5 * (1 + erf(x / sqrt(2)))
    return strike * exp(-r * t_years) * cdf(-d2) - spot * cdf(-d1)


def put_prices():
    # עלות פוט על S&P 500 (SPY) כאחוז מערך הנכס: פוט 8% מחוץ לכסף, וספרד 5%/20% מחוץ לכסף, כ-3 חודשים.
    # מנסים שרשרת אופציות אמיתית, ואם לא מצליחים משתמשים בהערכה לפי VIX.
    out = {"source": "הערכה לפי VIX (מודל בלק-שולס, לא מחיר שוק)"}
    try:
        spy = yf.Ticker("SPY")
        spot = float(spy.history(period="5d")["Close"].iloc[-1])
        vix = float(yf.Ticker("^VIX").history(period="5d")["Close"].iloc[-1]) / 100.0
        out["spot"], out["vix"] = spot, vix * 100
        exps = spy.options
        today = datetime.utcnow().date()
        pick = None
        for e in exps:
            days = (datetime.strptime(e, "%Y-%m-%d").date() - today).days
            if days >= 60:
                pick = (e, days)
                break
        if pick:
            e, days = pick
            chain = spy.option_chain(e).puts
            def mid(k):
                row = chain.iloc[(chain["strike"] - k).abs().argsort()[:1]]
                bid, ask, last = float(row["bid"].iloc[0]), float(row["ask"].iloc[0]), float(row["lastPrice"].iloc[0])
                strike = float(row["strike"].iloc[0])
                px = (bid + ask) / 2 if bid > 0 and ask > 0 else last
                return strike, px
            k92, p92 = mid(spot * 0.92)
            k95, p95 = mid(spot * 0.95)
            k80, p80 = mid(spot * 0.80)
            if p92 > 0 and p95 > p80 > 0:
                out.update(put_strike_pct=round((1 - k92 / spot) * 100, 1), put_cost_pct=p92 / spot * 100,
                           spread_lo=round((1 - k95 / spot) * 100, 1), spread_hi=round((1 - k80 / spot) * 100, 1),
                           spread_cost_pct=(p95 - p80) / spot * 100, days=days, source=f"שרשרת אופציות SPY, פקיעה {e}")
                return out
        days = 90
        t = days / 365
        skew = lambda otm: vix * (1 + 0.012 * otm)       # פוטים רחוקים יקרים יותר (סקיו), הערכה גסה
        c92 = _bs_put(100, 92, t, skew(8)); c95 = _bs_put(100, 95, t, skew(5)); c80 = _bs_put(100, 80, t, skew(20))
        out.update(put_strike_pct=8.0, put_cost_pct=c92, spread_lo=5.0, spread_hi=20.0, spread_cost_pct=c95 - c80, days=days)
    except Exception as e:
        print(f"risk: לא ניתן לחשב מחירי אופציות: {e}")
        out.update(put_strike_pct=8.0, put_cost_pct=1.2, spread_lo=5.0, spread_hi=20.0, spread_cost_pct=1.3, days=90,
                   source="הערכה גסה (אין נתוני שוק)")
    return out


def _groups(corr, names, thr):
    parent = {n: n for n in names}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            c = corr.loc[a, b]
            if not math.isnan(c) and c >= thr:
                parent[find(a)] = find(b)
    comp = {}
    for n in names:
        comp.setdefault(find(n), []).append(n)
    return [v for v in comp.values() if len(v) >= 2]


def analyze(positions, raw_total, class_caps=None, pos_cap=25.0, total_cap=100.0, portfolio_ils=None):
    class_caps = class_caps or {}
    names = [p["ticker"] for p in positions]
    rets = fetch_returns(names)
    names = [n for n in names if n in rets.columns]
    positions = [p for p in positions if p["ticker"] in names]
    if len(names) < 3 or "SPY" not in rets.columns:
        return None
    scale = min(1.0, total_cap / raw_total) if raw_total else 1.0
    w = np.array([p["w"] * scale / 100 for p in positions])       # שבר מהתיק
    exposure = float(w.sum())
    sub = rets[names]
    cov = sub.cov().fillna(0.0) * 252
    corr = sub.corr()
    var_m = float(rets["SPY"].var())
    beta = np.array([float(sub[n].cov(rets["SPY"]) / var_m) if var_m > 0 else 1.0 for n in names])
    vol_i = np.sqrt(np.diag(cov.to_numpy()))
    Sw = cov.to_numpy() @ w
    port_var = float(w @ Sw)
    port_vol = math.sqrt(max(port_var, 0)) * 100                   # אחוזי תיק (שנתי)
    contrib = (w * Sw / port_var) if port_var > 0 else np.zeros_like(w)
    port_beta = float((w * beta).sum())                            # בטא לתיק כולו (מזומן = 0)
    eig = np.linalg.eigvalsh(corr.fillna(0.0).to_numpy())[::-1]
    eig = np.clip(eig, 0, None)
    n_eff = float(eig.sum() ** 2 / (eig ** 2).sum()) if (eig ** 2).sum() > 0 else float(len(names))
    pc1 = float(eig[0] / eig.sum())
    iu = np.triu_indices(len(names), 1)
    cv = corr.to_numpy()[iu]
    avg_corr = float(np.nanmean(cv)) if len(cv) else 0.0

    pairs = []
    for i, j in zip(*iu):
        c = float(corr.iloc[i, j])
        if not math.isnan(c) and c >= CORR_HIDDEN:
            hidden = (positions[i]["asset_class"] != positions[j]["asset_class"]) or (positions[i]["cluster"] != positions[j]["cluster"])
            pairs.append({"a": names[i], "b": names[j], "corr": round(c, 2), "hidden": hidden})
    pairs.sort(key=lambda x: -x["corr"])
    clusters = []
    for g in _groups(corr, names, CORR_HIGH):
        wsum = sum(w[names.index(n)] for n in g) * 100
        clusters.append({"names": g, "weight": round(float(wsum), 1)})
    clusters.sort(key=lambda c: -c["weight"])

    by_class = {}
    for p, wi in zip(positions, w):
        by_class[p["asset_class"]] = by_class.get(p["asset_class"], 0) + wi * 100
    ils = sum(wi for p, wi in zip(positions, w) if p["ticker"].endswith(".TA")) * 100

    # תרחיש: ירידה של CRASH% במדד S&P 500
    stop = np.array([p["stop_pct"] if p.get("stop_pct") else 100.0 for p in positions])
    loss_beta = float((w * np.clip(beta, 0, None) * CRASH).sum())                       # לפי בטא
    loss_corr1 = float((w * np.maximum(np.clip(beta, 1, None), 1.0) * CRASH).sum())       # משבר: הכל נופל לפחות כמו השוק
    loss_stops = float((w * np.minimum(np.clip(beta, 0, None) * CRASH, stop)).sum())       # אם הסטופים מתבצעים בלי פער

    # נקודות חולשה
    weak = []
    for i, p in enumerate(positions):
        share = contrib[i] * 100
        if w[i] > 0 and share > 2 * w[i] / max(w.sum(), 1e-9) * 100 and share > 8:
            weak.append(f"{names[i]}: תורם {share:.0f}% מהסיכון אבל מהווה {w[i] / w.sum() * 100:.0f}% מהחשיפה (תנודתי מאוד: {vol_i[i] * 100:.0f}% שנתי)")
        if beta[i] > 1.6:
            weak.append(f"{names[i]}: בטא {beta[i]:.1f}, נופל חזק יותר מהשוק")
        if p.get("mtm") is not None and p["mtm"] <= -8 and p.get("stop_pct"):
            weak.append(f"{names[i]}: {p['mtm']:+.1f}% מהכניסה, קרוב לסטופ")
    if raw_total > total_cap * 1.05:
        weak.append(f"סך הפוזיציות המוצעות לכל האיתותים הפתוחים הוא {raw_total:.0f}% מהתיק. "
                    f"מי שמבצע את כולם נמצא בחשיפת יתר. כאן הן הוקטנו ל-{total_cap:g}%.")
    top_w = sorted(zip(names, w), key=lambda x: -x[1])[:3]
    if sum(x[1] for x in top_w) / max(exposure, 1e-9) > 0.45:
        weak.append("ריכוז: שלוש הפוזיציות הגדולות הן " + ", ".join(f"{n} {x * 100:.0f}%" for n, x in top_w))
    for c, v in by_class.items():
        if v > 60:
            weak.append(f"{c}: {v:.0f}% מהתיק בסוג נכס אחד")
    if ils > 25:
        weak.append(f"{ils:.0f}% מהתיק במניות תל אביב (חשיפה לשקל ולשוק מקומי אחד)")

    # איזון מחדש: משקל הפוך לתנודתיות (risk parity), באותה חשיפה כוללת, עם תקרות
    inv = 1.0 / np.maximum(vol_i, 1e-6)
    target = inv / inv.sum() * exposure * 100
    for _ in range(5):                                            # תקרות: פוזיציה וסוג נכס
        for i, p in enumerate(positions):
            cap = min(pos_cap, class_caps.get(p["asset_class"], pos_cap))
            target[i] = min(target[i], cap)
        free = exposure * 100 - target.sum()
        room = np.array([min(pos_cap, class_caps.get(p["asset_class"], pos_cap)) - target[i] for i, p in enumerate(positions)])
        if free > 0.01 and room.sum() > 0.01:
            target += np.minimum(room, room / room.sum() * free)
    rebalance = []
    for i, p in enumerate(positions):
        cur, tgt = w[i] * 100, target[i]
        rebalance.append({"ticker": names[i], "current": round(float(cur), 1), "target": round(float(tgt), 1),
                          "risk_share": round(float(contrib[i] * 100), 1), "vol": round(float(vol_i[i] * 100), 0)})
    rebalance.sort(key=lambda r: -abs(r["target"] - r["current"]))
    # הקטנת חשיפה לתנודתיות יעד
    new_exposure = min(exposure, exposure * TARGET_VOL / port_vol) if port_vol > 0 else exposure

    # גידורים: הערכים באחוזי תיק
    pp = put_prices()
    hedge_base = port_beta                                         # אחוז מהתיק שנחשף לשוק
    d = CRASH
    put_pay = max(0.0, d - pp["put_strike_pct"]) - pp["put_cost_pct"]                       # רווח פוט על 1 יחידת נכס
    spread_pay = min(max(0.0, d - pp["spread_lo"]), pp["spread_hi"] - pp["spread_lo"]) - pp["spread_cost_pct"]
    contract_ils, impractical = None, False
    if portfolio_ils and pp.get("spot"):
        try:
            fx = float(yf.Ticker("ILS=X").history(period="5d")["Close"].iloc[-1])
            contract_ils = pp["spot"] * 100 * fx
            impractical = contract_ils > portfolio_ils
        except Exception as e:
            print(f"risk: לא ניתן לקבל שער דולר-שקל: {e}")
    hedges = [
        {"name": "ללא גידור", "cost": 0.0, "loss": loss_beta, "note": "הפסד צפוי לפי בטא"},
        {"name": f"פוט על S&P 500, {pp['put_strike_pct']:g}% מחוץ לכסף, כיסוי מלא", "cost": hedge_base * pp["put_cost_pct"],
         "loss": loss_beta - hedge_base * put_pay,
         "note": f"עלות {pp['put_cost_pct']:.1f}% מערך הכיסוי, לכ-{pp['days']} ימים. ההגנה מתחילה אחרי ירידה של {pp['put_strike_pct']:g}%"},
        {"name": f"ספרד פוטים ({pp['spread_lo']:g}%-{pp['spread_hi']:g}% מתחת למחיר), כיסוי מלא", "cost": hedge_base * pp["spread_cost_pct"],
         "loss": loss_beta - hedge_base * spread_pay,
         "note": f"זול יותר ({pp['spread_cost_pct']:.1f}%), אבל ההגנה נעצרת בירידה של {pp['spread_hi']:g}%"},
        {"name": "קרן הפוכה ל-S&P 500 (SH) שמכסה חצי מהחשיפה לשוק", "cost": 0.0, "loss": loss_beta * 0.5,
         "note": f"בערך {hedge_base * 50:.0f}% מהתיק בקרן ההפוכה, ממומן ממזומן או ממכירת פוזיציות. אין עלות פרמיה, אבל גם מגבילה רווח בעליות, ומתפרקת בטווח ארוך"},
    ]
    if new_exposure < exposure * 0.98:
        hedges.append({"name": f"הקטנת חשיפה ל-{new_exposure * 100:.0f}% (תנודתיות יעד {TARGET_VOL:g}%)", "cost": 0.0,
                       "loss": loss_beta * (new_exposure / exposure if exposure else 1),
                       "note": "הדרך הזולה והפשוטה ביותר: פחות מניות, יותר מזומן"})
    else:
        hedges.append({"name": "הקטנת חשיפה", "cost": 0.0, "loss": loss_beta,
                       "note": f"לא נדרשת: התנודתיות הנוכחית ({port_vol:.1f}%) נמוכה מהיעד ({TARGET_VOL:g}%)"})
    for h in hedges:
        h["cost"], h["loss"] = round(h["cost"], 2), round(h["loss"], 1)
        if impractical and ("פוט" in h["name"] or "ספרד" in h["name"]):
            h["note"] = "לא מעשי בגודל תיק זה: " + h["note"]
    return {
        "exposure": round(exposure * 100, 1), "raw_total": round(raw_total, 1), "n": len(names),
        "port_vol": round(port_vol, 1), "port_beta": round(port_beta, 2), "n_eff": round(n_eff, 1), "pc1": round(pc1 * 100, 0),
        "avg_corr": round(avg_corr, 2), "pairs": pairs[:10], "clusters": clusters[:6], "by_class": {k: round(v, 1) for k, v in by_class.items()},
        "scenario": {"crash": CRASH, "beta": round(loss_beta, 1), "crisis": round(loss_corr1, 1), "stops": round(loss_stops, 1)},
        "weak": weak[:10], "rebalance": rebalance[:12], "new_exposure": round(new_exposure * 100, 0),
        "portfolio_ils": portfolio_ils, "contract_ils": contract_ils, "options_impractical": impractical,
        "hedges": hedges, "hedge_source": pp["source"], "vix": pp.get("vix"),
        "betas": {n: round(float(b), 2) for n, b in zip(names, beta)},
        "_corr": corr,
    }


def overlap_notes(top, rep, thr=CORR_HIGH):
    # לכל איתות חדש: האם הוא מתואם מאוד עם פוזיציה פתוחה (פחות פיזור מהנראה)
    notes = {}
    if not rep:
        return notes
    corr = rep["_corr"]
    held = set(corr.columns)
    for p in top:
        t = p["ticker"]
        if t in corr.columns:
            others = [(o, float(corr.loc[t, o])) for o in held if o != t and not math.isnan(float(corr.loc[t, o]))]
            others = sorted(others, key=lambda x: -x[1])[:2]
            hi = [f"{o} ({c:.2f})" for o, c in others if c >= thr]
            if hi:
                notes[t] = "מתואם מאוד לפוזיציות קיימות: " + ", ".join(hi) + ". הוספה כאן לא מוסיפה פיזור."
    return notes


def _e(x):
    import html
    return html.escape(str(x))


def build_risk_html(rep, source):
    if not rep:
        return ""
    sc = rep["scenario"]
    h = ['<div class="section-head">ניתוח תיק וסיכון</div>', '<div class="risk">']
    h.append(f'<div class="note">בסיס הניתוח: {_e(source)}. {rep["n"]} פוזיציות, חשיפה כוללת {rep["exposure"]:g}% מהתיק. '
             f'כל המספרים באחוזי תיק. אין כאן ייעוץ השקעות.</div>')
    h.append('<div class="risk-grid">'
             f'<div><b class="mono">{rep["port_vol"]:g}%</b><span>תנודתיות שנתית של התיק</span></div>'
             f'<div><b class="mono">{rep["port_beta"]:g}</b><span>בטא לשוק (1 = כמו S&P 500)</span></div>'
             f'<div><b class="mono">{rep["n_eff"]:g}</b><span>הימורים בלתי תלויים אמיתיים מתוך {rep["n"]}</span></div>'
             f'<div><b class="mono">{rep["pc1"]:g}%</b><span>מהסיכון נובע מגורם משותף אחד</span></div></div>')
    pv = rep.get("portfolio_ils")
    ils = (lambda pct: f" (כ-{pct * pv / 100:,.0f} ₪)") if pv else (lambda pct: "")
    h.append(f'<div class="why-line"><span class="why-tag">ירידה של {sc["crash"]:g}%</span>'
             f'הפסד צפוי לתיק: <b class="mono">{sc["beta"]:g}%</b>{ils(sc["beta"])} לפי בטא, '
             f'<b class="mono">{sc["crisis"]:g}%</b>{ils(sc["crisis"])} במשבר (כשהכול נופל יחד), '
             f'ו-<b class="mono">{sc["stops"]:g}%</b> אם כל הסטופים מתבצעים בלי פער מחיר.</div>')
    if rep["weak"]:
        h.append('<div class="group-title">נקודות חולשה</div>' + "".join(f'<div class="note warn">{_e(x)}</div>' for x in rep["weak"]))
    if rep["clusters"] or rep["pairs"]:
        h.append('<div class="group-title">קורלציות נסתרות</div>')
        for c in rep["clusters"]:
            h.append(f'<div class="why-line"><span class="why-tag">קבוצה צמודה</span>{_e(", ".join(c["names"]))} - '
                     f'יחד {c["weight"]:g}% מהתיק, נעים כמעט כמו נייר אחד</div>')
        hid = [p for p in rep["pairs"] if p["hidden"]][:5]
        for p in hid:
            h.append(f'<div class="why-line"><span class="why-tag">זוג נסתר</span>{_e(p["a"])} ו-{_e(p["b"])}: מתאם {p["corr"]:.2f}, למרות שנראים כהימורים שונים</div>')
    if rep["by_class"]:
        h.append('<div class="group-title">פילוח לפי סוג נכס</div><div class="why-line">' +
                 " · ".join(f"{_e(k)} {v:g}%" for k, v in sorted(rep["by_class"].items(), key=lambda x: -x[1])) + "</div>")
    money = (lambda pct: f" ({pct * pv / 100:,.0f} ₪)") if pv else (lambda pct: "")
    rb = "".join(f'<tr><td class="mono">{_e(r["ticker"])}</td><td>{r["current"]:g}%{money(r["current"])}</td><td><b>{r["target"]:g}%</b>{money(r["target"])}</td>'
                 f'<td>{r["risk_share"]:g}%</td><td>{r["vol"]:g}%</td></tr>' for r in rep["rebalance"])
    h.append('<div class="group-title">איזון מחדש לפי סיכון (משקל הפוך לתנודתיות, באותה חשיפה, עם תקרות)</div>'
             f'<table class="rtab"><tr><th>נייר</th><th>משקל נוכחי</th><th>משקל מוצע</th><th>חלק מהסיכון</th><th>תנודתיות שנתית</th></tr>{rb}</table>')
    if rep["new_exposure"] < rep["exposure"]:
        h.append(f'<div class="note warn">כדי להגיע לתנודתיות יעד של {TARGET_VOL:g}% בשנה, החשיפה הכוללת צריכה לרדת מ-{rep["exposure"]:g}% ל-{rep["new_exposure"]:g}% מהתיק (השאר במזומן).</div>')
    hb = "".join(f'<tr><td>{_e(x["name"])}</td><td>{x["cost"]:g}%{money(x["cost"])}</td><td><b>{x["loss"]:g}%</b>{money(x["loss"])}</td><td>{_e(x["note"])}</td></tr>' for x in rep["hedges"])
    h.append(f'<div class="group-title">הגנה מפני ירידה של {sc["crash"]:g}% בשוק (מקור מחירי האופציות: {_e(rep["hedge_source"])})</div>'
             f'<table class="rtab"><tr><th>אסטרטגיה</th><th>עלות (אחוזי תיק)</th><th>הפסד בירידה של {sc["crash"]:g}%</th><th>הערות</th></tr>{hb}</table>')
    if rep.get("options_impractical") and rep.get("contract_ils"):
        h.append(f'<div class="note warn">חוזה אופציה אחד על SPY מכסה כ-{rep["contract_ils"]:,.0f} ₪, פי {rep["contract_ils"] / pv:.0f} משווי התיק שלך ({pv:,.0f} ₪). '
                 'לכן פוטים על SPY אינם מעשיים לתיק בגודל כזה. החלופות המעשיות: הקטנת חשיפה למזומן, או קרן הפוכה (SH) בסכום קטן.</div>')
    h.append('<div class="note">מספר חוזים בקירוב: שווי התיק כפול אחוז הכיסוי לחלק ב-(100 כפול מחיר SPY). אופציות כרוכות בסיכון ובמורכבות, ופוט מפסיד את כל הפרמיה אם השוק לא יורד. '
             'הפסד בפועל יהיה שונה מהחישוב: מתאמים משתנים במשבר, ויכולים להיות פערי מחיר בסטופים.</div>')
    h.append("</div>")
    return "".join(h)


def risk_telegram_lines(rep):
    if not rep:
        return []
    sc = rep["scenario"]
    L = [f"ניתוח תיק: חשיפה {rep['exposure']:g}%, תנודתיות {rep['port_vol']:g}%, בטא {rep['port_beta']:g}, "
         f"{rep['n_eff']:g} הימורים בלתי תלויים מתוך {rep['n']}.",
         f"בירידה של {sc['crash']:g}% בשוק: הפסד צפוי {sc['beta']:g}% מהתיק" +
         (f" (כ-{sc['beta'] * rep['portfolio_ils'] / 100:,.0f} ₪)" if rep.get("portfolio_ils") else "") + f", עד {sc['crisis']:g}% במשבר."]
    if rep["raw_total"] > rep["exposure"] * 1.05:
        L.append(f"אזהרה: סך הפוזיציות לכל האיתותים הפתוחים הוא {rep['raw_total']:g}% מהתיק.")
    for c in rep["clusters"][:2]:
        L.append(f"קבוצה צמודה: {', '.join(c['names'][:6])} ({c['weight']:g}% מהתיק).")
    return L
