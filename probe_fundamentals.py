# בדיקת זמינות נתונים פונדמנטליים היסטוריים ב-yfinance (חד-פעמי)
import warnings, json
import yfinance as yf
warnings.simplefilter("ignore")
out = {}
for t in ["AAPL", "TEVA", "WFC", "NVDA", "MSFT"]:
    tk = yf.Ticker(t); r = {}
    def probe(name, fn):
        try:
            v = fn()
            if v is None: r[name] = "None"
            elif hasattr(v, "shape"):
                idx = list(map(str, v.index[[0, -1]])) if len(v.index) else []
                r[name] = {"shape": list(v.shape), "index_ends": idx, "cols": list(map(str, v.columns))[:8]}
            else: r[name] = str(type(v))
        except Exception as e:
            r[name] = f"ERR {e}"[:100]
    probe("upgrades_downgrades", lambda: tk.upgrades_downgrades)
    probe("insider_transactions", lambda: tk.insider_transactions)
    probe("earnings_dates", lambda: tk.get_earnings_dates(limit=40))
    probe("quarterly_income", lambda: tk.quarterly_income_stmt)
    probe("annual_income", lambda: tk.income_stmt)
    probe("eps_trend", lambda: tk.eps_trend)
    probe("eps_revisions", lambda: tk.eps_revisions)
    probe("recommendations", lambda: tk.recommendations)
    out[t] = r
print(json.dumps(out, ensure_ascii=False, indent=1))
