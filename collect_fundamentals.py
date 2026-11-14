# collect_fundamentals.py - אוסף כל יום תמונת מצב פונדמנטלית של מניות ארצות הברית:
# רווחיות, ערך, חוב, צמיחה, שינויי תחזיות רווח ורכישות/מכירות של בעלי עניין.
# הנתונים נשמרים בקובץ חודשי ב-fundamentals/. אחרי כמה חודשים אפשר לבדוק ב-analyze_fundamentals.py
# אם מי מהגורמים באמת מנבא תשואה (ל-yfinance אין היסטוריה פונדמנטלית נקייה, ולכן אוספים קדימה).
import csv, os, warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import pandas as pd
import yfinance as yf
import fetch_data as fd

OUT_DIR = "fundamentals"
INFO_FIELDS = ["marketCap", "trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda", "returnOnEquity",
               "returnOnAssets", "profitMargins", "operatingMargins", "grossMargins", "debtToEquity", "currentRatio",
               "freeCashflow", "revenueGrowth", "earningsGrowth", "targetMeanPrice", "recommendationMean",
               "numberOfAnalystOpinions", "shortPercentOfFloat", "heldPercentInsiders", "heldPercentInstitutions", "beta"]
COLUMNS = ["date", "ticker"] + INFO_FIELDS + ["eps_cur_year_chg90d", "eps_next_year_chg90d", "eps_up30", "eps_down30",
                                              "insider_buy_n90", "insider_sell_n90", "insider_buy_value90", "insider_sell_value90"]


def eps_changes(tk):
    out = {}
    try:
        tr = tk.eps_trend
        for per, key in (("0y", "eps_cur_year_chg90d"), ("+1y", "eps_next_year_chg90d")):
            cur, old = float(tr.loc[per, "current"]), float(tr.loc[per, "90daysAgo"])
            if old and old == old and cur == cur:
                out[key] = round((cur - old) / abs(old) * 100, 2)
    except Exception:
        pass
    try:
        rv = tk.eps_revisions
        out["eps_up30"] = int(rv.loc["0y", "upLast30days"]); out["eps_down30"] = int(rv.loc["0y", "downLast30days"])
    except Exception:
        pass
    return out


def insider_counts(tk, today):
    out = {}
    try:
        df = tk.insider_transactions
        if df is None or df.empty:
            return out
        df = df.copy()
        df["d"] = pd.to_datetime(df["Start Date"], errors="coerce")
        df = df[df["d"] >= pd.Timestamp(today) - pd.Timedelta(days=90)]
        txt = df["Text"].fillna("").str.lower()
        buys, sells = df[txt.str.contains("purchase")], df[txt.str.contains("sale")]
        out = {"insider_buy_n90": int(len(buys)), "insider_sell_n90": int(len(sells)),
               "insider_buy_value90": float(pd.to_numeric(buys["Value"], errors="coerce").sum()),
               "insider_sell_value90": float(pd.to_numeric(sells["Value"], errors="coerce").sum())}
    except Exception:
        pass
    return out


def snapshot(ticker, today):
    tk = yf.Ticker(ticker)
    row = {"date": today, "ticker": ticker}
    try:
        info = tk.info or {}
        for f in INFO_FIELDS:
            v = info.get(f)
            if isinstance(v, (int, float)):
                row[f] = v
    except Exception:
        pass
    row.update(eps_changes(tk))
    row.update(insider_counts(tk, today))
    return row


def main():
    warnings.simplefilter("ignore")
    os.makedirs(OUT_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(OUT_DIR, today[:7] + ".csv")
    tickers = list(dict.fromkeys(fd.UNIVERSE["מניות ארצות הברית"]))
    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(lambda t: snapshot(t, today), tickers))
    rows = [r for r in rows if len(r) > 3]
    new = not os.path.exists(path)
    if not new:      # אם כבר נאסף היום, מחליפים ולא מכפילים
        old = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r["date"] != today]
    else:
        old = []
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader(); w.writerows(old); w.writerows(rows)
    print(f"נאספו נתונים פונדמנטליים ל-{len(rows)} מתוך {len(tickers)} מניות, נשמר ב-{path}")


if __name__ == "__main__":
    main()
