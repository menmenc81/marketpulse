# backtest_events.py - בדיקה היסטורית של אירועים: הפתעות בדוחות ושינויי המלצות אנליסטים.
# הכניסה תמיד בסגירה של היום המסחר שאחרי האירוע (או יום התגובה לדוח), כדי לא להניח שידענו מראש.
# התשואה מוצגת כעודף על S&P 500 (SPY) באותה תקופה. מובהקות: ממוצע לכל חודש קלנדרי (שמרני).
import math, os, json, warnings
import numpy as np, pandas as pd
import yfinance as yf
import fetch_data as fd
import backtest as bt

OUT = "backtest"
HZ = (5, 10, 20)
MAXT = int(os.environ.get("BT_MAX_TICKERS", "0"))


def fwd_rows(closes, spy, dates, entry_i, extra):
    if entry_i is None or entry_i + max(HZ) >= len(dates):
        return None
    row = dict(extra)
    for h in HZ:
        a, b = dates[entry_i], dates[entry_i + h]
        row[f"ex{h}"] = round(((closes[entry_i + h] / closes[entry_i]) - (spy.asof(b) / spy.asof(a))) * 100, 3)
    row["date"] = dates[entry_i]
    return row


def next_close_index(dates, day, same_day_ok):
    i = dates.searchsorted(pd.Timestamp(day), side="left" if same_day_ok else "right")
    return int(i) if i < len(dates) else None


def collect(spy):
    earn, rat = [], []
    n = 0
    for ticker in dict.fromkeys(fd.UNIVERSE["מניות ארצות הברית"]):
        h = bt.load_history(ticker)
        if h is None:
            continue
        n += 1
        closes, dates = h["Close"].to_numpy(float), h.index
        tk = yf.Ticker(ticker)
        try:
            ed = tk.get_earnings_dates(limit=50)
            ed = ed.dropna(subset=["Surprise(%)"]) if ed is not None else None
            if ed is not None:
                for ts, r in ed.iterrows():
                    after_close = ts.hour >= 16
                    i = next_close_index(dates, ts.tz_localize(None).normalize(), same_day_ok=not after_close)
                    # יום התגובה: אחרי הסגירה = היום שאחרי; לפני הפתיחה = אותו יום. הכניסה בסגירת יום התגובה.
                    row = fwd_rows(closes, spy, dates, i, {"ticker": ticker, "surprise": float(r["Surprise(%)"])})
                    if row:
                        earn.append(row)
        except Exception as e:
            print(f"{ticker} דוחות: {e}")
        try:
            ug = tk.upgrades_downgrades
            if ug is not None and len(ug):
                ug = ug.copy()
                ug.index = pd.to_datetime(ug.index).tz_localize(None) if ug.index.tz is None else ug.index.tz_convert(None)
                ug["day"] = ug.index.normalize()
                for day, g in ug.groupby("day"):
                    up = int((g["Action"] == "up").sum()); down = int((g["Action"] == "down").sum())
                    pt_raise = int((g["priceTargetAction"] == "Raises").sum()); pt_cut = int((g["priceTargetAction"] == "Lowers").sum())
                    i = next_close_index(dates, day, same_day_ok=False)
                    row = fwd_rows(closes, spy, dates, i, {"ticker": ticker, "up": up, "down": down, "pt_raise": pt_raise, "pt_cut": pt_cut})
                    if row and (up or down or pt_raise or pt_cut):
                        rat.append(row)
        except Exception as e:
            print(f"{ticker} המלצות: {e}")
        print(f"{ticker}: {n} ניירות | דוחות {len(earn)} | המלצות {len(rat)}")
        if MAXT and n >= MAXT:
            break
    return pd.DataFrame(earn), pd.DataFrame(rat)


def summarize(name, g):
    if len(g) == 0:
        return None
    g = g.copy(); g["month"] = pd.to_datetime(g["date"]).dt.to_period("M")
    row = {"קבוצה": name, "אירועים": int(len(g))}
    for h in HZ:
        row[f"עודף על S&P {h} ימים %"] = round(float(g[f"ex{h}"].mean()), 3)
    m = g.groupby("month")["ex10"].mean()
    row["t (10 ימים)"] = round(float(m.mean() / (m.std() / math.sqrt(len(m)))), 2) if len(m) > 5 and m.std() > 0 else None
    row["אחוז מנצחים את S&P (10 ימים)"] = round(float((g["ex10"] > 0).mean() * 100), 1)
    return row


def md(rows):
    rows = [r for r in rows if r]
    cols = list(rows[0].keys())
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"] +
                     ["| " + " | ".join("" if r.get(c) is None else str(r[c]) for c in cols) + " |" for r in rows]) + "\n"


def main():
    warnings.simplefilter("ignore")
    os.makedirs(OUT, exist_ok=True)
    spy = bt.load_history("SPY")["Close"]
    earn, rat = collect(spy)
    res = {}
    if len(earn):
        e = earn
        res["הפתעות בדוחות (כניסה בסגירת יום התגובה)"] = [summarize(n, g) for n, g in [
            ("כל הדוחות", e), ("הפתעה שלילית מעל 5%-", e[e.surprise < -5]), ("הפתעה בין 5%- ל-0", e[(e.surprise >= -5) & (e.surprise < 0)]),
            ("הפתעה בין 0 ל-5%", e[(e.surprise >= 0) & (e.surprise < 5)]), ("הפתעה חיובית מעל 5%", e[e.surprise >= 5]),
            ("הפתעה חיובית מעל 15%", e[e.surprise >= 15])]]
        e = e.assign(year=pd.to_datetime(e["date"]).dt.year)
        res["הפתעה חיובית מעל 5%, לפי שנה"] = [summarize(str(y), g) for y, g in e[e.surprise >= 5].groupby("year")]
    if len(rat):
        r = rat
        res["המלצות אנליסטים (כניסה בסגירה שאחרי האירוע)"] = [summarize(n, g) for n, g in [
            ("כל הימים עם אירוע", r), ("שדרוג דירוג (ללא שדרוגים נגדיים)", r[(r.up > 0) & (r.down == 0)]),
            ("הורדת דירוג", r[(r.down > 0) & (r.up == 0)]), ("העלאת יעד מחיר", r[(r.pt_raise > 0) & (r.pt_cut == 0)]),
            ("הורדת יעד מחיר", r[(r.pt_cut > 0) & (r.pt_raise == 0)]),
            ("שני אנליסטים ומעלה שדרגו באותו יום", r[(r.up >= 2) & (r.down == 0)])]]
        r = r.assign(year=pd.to_datetime(r["date"]).dt.year)
        res["שדרוג דירוג, לפי שנה"] = [summarize(str(y), g) for y, g in r[(r.up > 0) & (r.down == 0)].groupby("year")]
    lines = ["# בדיקת אירועים: דוחות והמלצות אנליסטים", "",
             "תשואה עודפת על S&P 500, כניסה בסגירה שאחרי האירוע. מניות ארצות הברית בלבד. הטיית הישרדות ובלי עמלות. "
             "t מחושב לפי ממוצע חודשי, ומעל כ-2 מצביע על מובהקות. משווים כל קבוצה לשורה \"כל הדוחות\" או \"כל הימים\", ולא לאפס.", ""]
    for k, rows in res.items():
        rows = [x for x in rows if x]
        if rows:
            lines += [f"## {k}", "", md(rows)]
    open(os.path.join(OUT, "events_report.md"), "w", encoding="utf-8").write("\n".join(lines))
    json.dump(res, open(os.path.join(OUT, "events_results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("הושלם")


if __name__ == "__main__":
    main()
