# review_trades.py - סקירה שבועית של כל העסקאות והאיתותים שנרשמו: טעויות שחוזרות, הזדמנויות שפוספסו והטיות.
# הדוח נכתב ל-review/report.md. כל מסקנה מגובה במספרים, ובמדגם קטן הדוח אומר זאת במפורש
# ולא מסיק מסקנות. אין כאן ייעוץ השקעות.
import json
import math
import os
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import fetch_data as fd
import backtest as bt

OUT = "review"
MIN_N = 12            # מתחת למספר הזה לא מסיקים מסקנות על קבוצה
MISSED_LOOKBACK = 120  # ימי מסחר אחורה לבדיקת הזדמנויות שפוספסו
MISSED_STEP = 5
FWD = 20


def outcome(r):
    # תוצאה באחוזים: סגורה (ret) או שווי נוכחי (mtm)
    if r.get("status") in ("stop", "target", "expired") and r.get("ret") is not None:
        return float(r["ret"])
    if r.get("status") == "open" and r.get("mtm") is not None:
        return float(r["mtm"])
    return None


def spearman(x, y):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < MIN_N or d.x.nunique() < 3:
        return None, len(d)
    return round(float(d.x.rank().corr(d.y.rank())), 2), len(d)


def load():
    try:
        data = json.load(open("signals_log.json", encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else data.get("signals", [])


def section_overview(recs):
    rows = []
    for name, sel in (("גרסה 5 (נוכחית)", [r for r in recs if r.get("v") == 5]),
                      ("גרסה 2 (סגורות)", [r for r in recs if r.get("v") == 2 and outcome(r) is not None])):
        vals = [(outcome(r), r) for r in sel if outcome(r) is not None]
        if not vals:
            continue
        v = np.array([x for x, _ in vals])
        ex = [outcome(r) - r["spy_ret"] for _, r in vals if r.get("spy_ret") is not None]
        stops = sum(1 for _, r in vals if r.get("status") == "stop")
        rows.append({"קבוצה": name, "איתותים עם תוצאה": len(v), "ברווח %": round(float((v > 0).mean() * 100), 0),
                     "תוצאה ממוצעת %": round(float(v.mean()), 2), "חציון %": round(float(np.median(v)), 2),
                     "עודף ממוצע על S&P 500 %": round(float(np.mean(ex)), 2) if ex else None, "נסגרו בסטופ": stops})
    return rows


def find_patterns(recs):
    # כל ממצא: (כותרת, טקסט, עוצמה). עוצמה "מספיק נתונים" או "מדגם קטן".
    out = []
    done = [r for r in recs if outcome(r) is not None and r.get("v") in (2, 5)]
    closed = [r for r in done if r.get("status") in ("stop", "target", "expired")]
    if len(done) < MIN_N:
        return [("מדגם קטן", f"יש רק {len(done)} איתותים עם תוצאה, פחות מ-{MIN_N}. אי אפשר להסיק מסקנות אמינות.", "small")]

    # 1. מהירות הסטופ
    st = [r for r in closed if r["status"] == "stop" and r.get("days") is not None]
    if len(st) >= MIN_N:
        fast = sum(1 for r in st if r["days"] <= 5)
        out.append(("סטופים מהירים", f"{fast} מתוך {len(st)} הסטופים ({fast / len(st) * 100:.0f}%) נפגעו תוך 5 ימי מסחר. "
                    "זה מצביע על סטופ צפוף מדי ביחס לתנודתיות, או על כניסה בנקודה לא טובה (אחרי עלייה, קרוב להתנגדות).", "ok"))

    # 2. ריכוז באותו יום: האם הפסדים נופלים באותם תאריכים (גורם שוק משותף)
    by_close = Counter(r["closed"] for r in closed if r.get("status") == "stop" and r.get("closed"))
    if by_close:
        day, n = by_close.most_common(1)[0]
        if n >= 3:
            out.append(("הפסדים באותו יום", f"{n} סטופים נפגעו באותו תאריך ({day}). הימורים שנראו נפרדים נפלו יחד: "
                        "חשיפה משותפת לשוק, לא פיזור אמיתי.", "ok"))

    # 3. כניסה מחדש באותו נייר
    cnt = Counter(r["ticker"] for r in recs if r.get("v") in (2, 5))
    again = [t for t, c in cnt.items() if c >= 2]
    if again:
        res = defaultdict(list)
        for r in done:
            if r["ticker"] in again:
                res[r["ticker"]].append(outcome(r))
        worst = sorted(((t, sum(v)) for t, v in res.items()), key=lambda x: x[1])[:3]
        out.append(("איתותים חוזרים על אותו נייר", f"{len(again)} ניירות קיבלו איתות יותר מפעם אחת. "
                    f"הכי פחות טוב: " + ", ".join(f"{t} ({s:+.1f}% מצטבר)" for t, s in worst) + ". כלל ההמתנה אחרי סטופ (10 ימים) נועד לצמצם את זה.", "ok"))

    # 4. מתיחות בכניסה: מפסידים לעומת מרוויחים
    lo = [r for r in done if outcome(r) <= 0 and r.get("ext_atr") is not None]
    wi = [r for r in done if outcome(r) > 0 and r.get("ext_atr") is not None]
    if len(lo) >= 6 and len(wi) >= 6:
        a, b = np.mean([r["ext_atr"] for r in lo]), np.mean([r["ext_atr"] for r in wi])
        rl, rw = np.mean([r["rsi"] for r in lo if r.get("rsi")]), np.mean([r["rsi"] for r in wi if r.get("rsi")])
        verdict = ("המפסידים נכנסו גבוה יותר מעל הממוצע: סימן לרדיפה אחרי מחיר." if a > b + 0.3 else
                   "אין הבדל ברור בין מפסידים למרוויחים: רדיפה אחרי מחיר אינה ההסבר הראשי.")
        out.append(("כניסה אחרי עלייה", f"מרחק ממוצע מהממוצע ל-20 יום בכניסה: מפסידים {a:.1f} ATR, מרוויחים {b:.1f} ATR; RSI {rl:.0f} מול {rw:.0f}. {verdict}", "ok"))

    # 5. האם הציון קשור לתוצאה
    rho, n = spearman([r["score"] for r in done], [outcome(r) for r in done])
    if rho is not None:
        txt = ("יש קשר חיובי" if rho > 0.2 else "אין קשר ברור" if abs(rho) <= 0.2 else "הקשר הפוך")
        out.append(("ציון מול תוצאה", f"מתאם דרגות בין הציון לתוצאה: {rho:+.2f} (על {n} איתותים). {txt}. "
                    "אם הציון לא מנבא, אין סיבה לתת משקל לפער של כמה נקודות בין איתותים.", "ok"))
    low = [r for r in done if r["score"] < 20]
    if len(low) >= 6:
        out.append(("איתותים חלשים מאוד", f"{len(low)} מתוך {len(done)} האיתותים ({len(low) / len(done) * 100:.0f}%) קיבלו ציון מתחת ל-20. "
                    f"תוצאתם הממוצעת {np.mean([outcome(r) for r in low]):+.1f}%. אלה כמעט 'אין דעה', והכללתם מגדילה רעש. כדאי סף ציון מינימלי.", "ok"))

    # 6. סוגי נכסים
    byc = defaultdict(list)
    for r in done:
        byc[r["asset_class"]].append(outcome(r))
    cls = [(c, len(v), np.mean(v), np.mean([x > 0 for x in v]) * 100) for c, v in byc.items() if len(v) >= 6]
    if cls:
        cls.sort(key=lambda x: x[2])
        out.append(("לפי סוג נכס", "; ".join(f"{c}: {n} איתותים, ממוצע {m:+.1f}%, {w:.0f}% ברווח" for c, n, m, w in cls) +
                    ". הבדלים בין סוגים בדגימה קטנה הם רעש, אלא אם חוזרים לאורך זמן.", "ok"))

    # 7. כמה מההפסד הוא השוק
    both = [r for r in done if r.get("spy_ret") is not None]
    if len(both) >= MIN_N:
        raw = np.mean([outcome(r) for r in both]); sp = np.mean([r["spy_ret"] for r in both])
        out.append(("חלק השוק בתוצאה", f"תוצאה ממוצעת {raw:+.1f}% לעומת {sp:+.1f}% ל-S&P 500 באותן תקופות. "
                    f"הפער ({raw - sp:+.1f}%) הוא התרומה של בחירת הניירות עצמה.", "ok"))

    # 8. רכיבי הציון
    comp_rows = []
    for key in ("price", "tech", "wyckoff", "analyst", "news", "theme"):
        xs = [(r["comps"].get(key), outcome(r)) for r in done if r.get("comps") and r["comps"].get(key) is not None]
        pos = [y for x, y in xs if x > 0]; neg = [y for x, y in xs if x <= 0]
        if len(pos) >= 5 and len(neg) >= 5:
            comp_rows.append(f"{fd.LABELS.get(key, key)}: חיובי {np.mean(pos):+.1f}% ({len(pos)}), שלילי {np.mean(neg):+.1f}% ({len(neg)})")
    if comp_rows:
        out.append(("תוצאה לפי רכיב", " | ".join(comp_rows) + ". אם רכיב חיובי לא מביא תוצאה טובה יותר משלילי, הוא לא מוסיף מידע.", "ok"))
    return out


def missed_opportunities(recs):
    # מהם הניירות שעלו הכי הרבה ב-20 הימים שאחרי כל נקודה, ומה המערכת (רכיבי המחיר) אמרה עליהם באותו זמן.
    rows, capture = [], {"winners": 0, "buy": 0, "all": 0, "all_buy": 0}
    logged = defaultdict(set)
    for r in recs:
        logged[r["ticker"]].add(r["date"])
    allr = []
    for cls, tickers in fd.UNIVERSE.items():
        for t in dict.fromkeys(tickers):
            h = bt.load_history(t)
            if h is None:
                continue
            n = len(h)
            for idx in range(n - MISSED_LOOKBACK - FWD, n - FWD - 1, MISSED_STEP):
                if idx < bt.WINDOW:
                    continue
                try:
                    p = fd.analyze_hist(h.iloc[idx - bt.WINDOW + 1: idx + 1], t, cls)
                    if not p:
                        continue
                    comps = fd.available_components(p)
                    cw = sum(fd.CLUSTER_W[k] for k in fd.CLUSTER_KEYS if k in comps)
                    cl = sum(fd.CLUSTER_W[k] * comps[k] for k in fd.CLUSTER_KEYS if k in comps) / cw
                    fwd = (float(h["Close"].iloc[idx + FWD]) / float(h["Close"].iloc[idx]) - 1) * 100
                    allr.append({"ticker": t, "class": cls, "date": h.index[idx].strftime("%Y-%m-%d"), "fwd": fwd,
                                 "cluster": cl, "rsi": p["rsi"], "ext": p["ext_atr"], "logged": t in logged})
                except Exception:
                    continue
    if not allr:
        return [], {}
    df = pd.DataFrame(allr)
    thr = df.fwd.quantile(0.9)
    win = df[df.fwd >= thr]
    stats = {"נקודות בדיקה": int(len(df)), "סף 10% העליונים (תשואת 20 יום)": round(float(thr), 1),
             "אחוז קנייה (אשכול מחיר חיובי) בכל הנקודות": round(float((df.cluster > 0).mean() * 100), 0),
             "אחוז קנייה בין המנצחים": round(float((win.cluster > 0).mean() * 100), 0),
             "אחוז מנצחים עם דגל מתיחות (RSI>=75 או 3 ATR)": round(float(((win.rsi >= 75) | (win.ext >= 3)).mean() * 100), 0),
             "אחוז מנצחים שסומנו בכלל באיתות": round(float(win.logged.mean() * 100), 0)}
    seen = set()
    for _, r in win.sort_values("fwd", ascending=False).iterrows():
        if r["ticker"] in seen:
            continue
        seen.add(r["ticker"])
        why = ("הרכיבים הטכניים היו דוביים" if r["cluster"] < -5 else "אין דעה ברורה" if abs(r["cluster"]) <= 5 else "היה איתות חיובי")
        if r["rsi"] >= 75 or r["ext"] >= 3:
            why += ", וניירות מתוחים היו נפסלים בעבר"
        rows.append({"נייר": r["ticker"], "תאריך": r["date"], "עלייה ב-20 יום %": round(float(r["fwd"]), 1),
                     "ציון מחיר אז": round(float(r["cluster"]), 0), "הסבר": why})
        if len(rows) >= 12:
            break
    return rows, stats


def md_table(rows):
    if not rows:
        return "(אין נתונים)\n"
    cols = list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |")
    return "\n".join(out) + "\n"


RULES = [
    ("כבר פועל אוטומטית", [
        "המתנה של 10 ימים אחרי סטופ על אותו נייר (מונע ניסיון חוזר מיידי).",
        "תקרת חשיפה כוללת של 100% על כל האיתותים הפתוחים ולא רק על איתותי היום.",
        "תקרות לפי סוג נכס (אג\"ח 10%, קריפטו 5%, סחורות 15%) וסינון דוחות רבעוניים קרובים.",
        "בדיקת כפילות: איתות חדש שמתואם מאוד לפוזיציה קיימת מסומן באזהרה.",
        "רשימת בדיקה לפני כניסה בכל איתות (יחס סיכוי/סיכון, התנגדות בדרך, מגמה, כפילות, סטופ מוגן ברמה).",
        "שמירת כל האיתותים והרכיבים ביומן, ובדיקה היסטורית ושבועית כדי לא להסתמך על רושם."]),
    ("מומלץ לאמץ (החלטה שלך)", [
        "סף ציון מינימלי (למשל 25) כדי לא לתת משקל לאיתותים של 'אין דעה'.",
        "מקסימום 10-12 פוזיציות פתוחות בו זמנית, ולא להוסיף כשהתיק מלא אלא אחרי סגירה.",
        "יציאה לפי זמן: איתות שלא זז תוך 30 יום נסגר, בלי 'לחכות עוד קצת'.",
        "לא מזיזים סטופ לכיוון ההפסד, ולא מוסיפים לפוזיציה מפסידה.",
        "יומן החלטות קצר לכל עסקה בפועל (למה נכנסתי, מה היה התרחיש, מה סגר אותה) ומילוי של portfolio.json עם ההחזקות האמיתיות.",
        "סקירה שבועית אחת (הדוח הזה) במקום בדיקה יומית של המחירים."]),
]


def main():
    warnings.simplefilter("ignore")
    os.makedirs(OUT, exist_ok=True)
    recs = load()
    L = ["# סקירת עסקאות", "", f"נוצר: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}. {len(recs)} רשומות ביומן האיתותים.", ""]
    ov = section_overview(recs)
    L += ["## תמונת מצב", "", md_table(ov)]
    pats = find_patterns(recs)
    L += ["## טעויות והטיות שחוזרות", ""]
    for title, text, kind in pats:
        L += [f"**{title}.** {text}", ""]
    try:
        missed, stats = missed_opportunities(recs)
    except Exception as e:
        print(f"שגיאה בבדיקת הזדמנויות שפוספסו: {e}")
        missed, stats = [], {}
    if stats:
        L += ["## הזדמנויות שפוספסו", "",
              "ה-10% העליונים בעלייה ב-20 הימים שאחרי כל נקודת בדיקה (4 חודשים אחרונים), ומה המערכת אמרה עליהם בתחילת המהלך:", ""]
        L += [f"- {k}: {v}" for k, v in stats.items()]
        L += ["", md_table(missed),
              "קריאה: אם אחוז הקנייה בין המנצחים דומה לאחוז הקנייה הכללי, רכיבי המחיר לא מזהים מראש את המנצחים (אותה מסקנה כמו בבדיקה ההיסטורית). "
              "הטיית הדיעבד חלה כאן: תמיד קל לראות מי עלה אחרי שזה קרה.", ""]
    for title, items in RULES:
        L += [f"## כללים וכלים: {title}", ""] + [f"- {x}" for x in items] + [""]
    L.append("אין בדוח ייעוץ השקעות. מדגם קטן הוא רעש, וממצא שלא חוזר בכמה סקירות לא נחשב ממצא.")
    open(os.path.join(OUT, "report.md"), "w", encoding="utf-8").write("\n".join(L))
    json.dump({"overview": ov, "patterns": [{"title": a, "text": b} for a, b, _ in pats], "missed": missed, "missed_stats": stats},
              open(os.path.join(OUT, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n".join(L)[:6000])


if __name__ == "__main__":
    main()
