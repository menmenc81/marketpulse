# analyze_fundamentals.py - בודק (אחרי שנאספו מספיק תמונות מצב) אילו גורמים פונדמנטליים מנבאים תשואה.
# לכל גורם ולכל תאריך: מתאם דרגות (Spearman) בין הגורם לתשואה עודפת על S&P 500 ב-20 ימי מסחר שאחרי האיסוף.
# כשמעט תאריכים, התוצאה לא אמינה, והסקריפט אומר זאת במפורש.
import glob, math, os
import numpy as np, pandas as pd
import fetch_data as fd
import backtest as bt

MIN_DATES = 40        # מינימום תאריכי איסוף למסקנה
HZ = 20
HIGHER_IS_BETTER = {"returnOnEquity": 1, "returnOnAssets": 1, "profitMargins": 1, "operatingMargins": 1, "grossMargins": 1,
                    "revenueGrowth": 1, "earningsGrowth": 1, "freeCashflow": 1, "eps_cur_year_chg90d": 1,
                    "eps_next_year_chg90d": 1, "insider_buy_n90": 1, "insider_buy_value90": 1,
                    "trailingPE": -1, "forwardPE": -1, "priceToBook": -1, "enterpriseToEbitda": -1,
                    "debtToEquity": -1, "shortPercentOfFloat": -1}


def main():
    files = sorted(glob.glob("fundamentals/2*.csv"))
    if not files:
        print("אין נתונים לניתוח"); return
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    dates = sorted(df.date.unique())
    spy = bt.load_history("SPY")["Close"]
    fwd = {}
    for t in df.ticker.unique():
        h = bt.load_history(t)
        if h is None:
            continue
        c = h["Close"]
        for d in dates:
            i = c.index.searchsorted(pd.Timestamp(d), side="right") - 1
            if i >= 0 and i + HZ < len(c):
                a, b = c.index[i], c.index[i + HZ]
                fwd[(t, d)] = (c.iloc[i + HZ] / c.iloc[i] - spy.asof(b) / spy.asof(a)) * 100
    df["fwd"] = [fwd.get((t, d)) for t, d in zip(df.ticker, df.date)]
    df = df.dropna(subset=["fwd"])
    n_dates = df.date.nunique()
    lines = ["# בדיקת גורמים פונדמנטליים", "", f"תאריכי איסוף עם תשואה ידועה: {n_dates}. ניירות: {df.ticker.nunique()}.", ""]
    if n_dates < MIN_DATES:
        lines += [f"**אין עדיין מספיק נתונים למסקנה.** צריך לפחות {MIN_DATES} תאריכי איסוף, והתוצאות למטה הן רק להתרשמות.", ""]
    rows = []
    for f, sign in HIGHER_IS_BETTER.items():
        if f not in df.columns:
            continue
        ics = []
        for d, g in df.groupby("date"):
            g = g[[f, "fwd"]].dropna()
            if len(g) >= 30 and g[f].nunique() > 5:
                ics.append(sign * g[f].rank().corr(g["fwd"].rank()))
        ics = pd.Series(ics)
        if len(ics) >= 3:
            t = ics.mean() / (ics.std() / math.sqrt(max(1, len(ics) / (HZ / 5)))) if ics.std() > 0 else None
            rows.append({"גורם": f, "כיוון": "גבוה טוב" if sign > 0 else "נמוך טוב", "תאריכים": len(ics),
                         "IC ממוצע": round(float(ics.mean()), 4), "t (שמרני)": None if t is None else round(float(t), 2)})
    if rows:
        out = pd.DataFrame(rows).sort_values("IC ממוצע", ascending=False)
        lines += ["| " + " | ".join(out.columns) + " |", "|" + "|".join("---" for _ in out.columns) + "|"]
        lines += ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |" for r in out.values]
    open("fundamentals/report.md", "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
