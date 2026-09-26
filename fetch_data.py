# MarketPulse - סקריפט יומי למשיכת נתוני מניות ובניית דשבורד.
#
# מה הסקריפט עושה: מגדיר רשימת מניות, מושך נתוני מחיר ונפח מסחר אמיתיים
# דרך yfinance (חינמי), מחשב ציון הזדמנות פשוט, ובונה מכך index.html.
#
# הערה: הציון בשלב זה מבוסס רק על נתוני מחיר ונפח מסחר (ללא המלצות
# אנליסטים או סנטימנט חדשות - אלה יתווספו כשנחבר מקור נתונים בתשלום).

import os
import requests
import yfinance as yf
from datetime import datetime

# ------------------------------------------------------------------
# שלב 1: רשימת המניות למעקב. אפשר להוסיף/להסיר טיקרים כרצונך.
# ------------------------------------------------------------------
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA",
    "SNOW", "IOT", "TEVA", "LULU", "CRWD", "NFLX", "AMD",
    "ORCL", "COST", "JPM", "V", "DIS",
]


def fetch_and_score(ticker: str):
    # מושך נתונים עבור מניה אחת ומחזיר מילון עם הציון והפרטים שלה.
    try:
        stock = yf.Ticker(ticker)
        # מושכים 60 ימי מסחר אחרונים כדי לחשב ממוצע נע ומגמה
        hist = stock.history(period="60d")
        if hist.empty or len(hist) < 20:
            return None

        last_price = hist["Close"].iloc[-1]
        price_5d_ago = hist["Close"].iloc[-6] if len(hist) >= 6 else hist["Close"].iloc[0]
        momentum_pct = ((last_price - price_5d_ago) / price_5d_ago) * 100

        avg_volume = hist["Volume"].iloc[-20:].mean()
        today_volume = hist["Volume"].iloc[-1]
        volume_ratio = today_volume / avg_volume if avg_volume > 0 else 1

        ma50 = hist["Close"].mean()  # ממוצע פשוט כתחליף למגמה
        trend_pct = ((last_price - ma50) / ma50) * 100

        # נרמול כל מרכיב לציון 0-100 (הערכה פשוטה, אפשר לשפר בהמשך)
        momentum_score = max(0, min(100, 50 + momentum_pct * 5))
        volume_score = max(0, min(100, volume_ratio * 50))
        trend_score = max(0, min(100, 50 + trend_pct * 3))

        composite = round((momentum_score + volume_score + trend_score) / 3)

        direction = "buy" if momentum_pct >= 0 else "sell"

        info = stock.info
        company_name = info.get("shortName", ticker)
        sector = info.get("sector", "לא ידוע")
        market_cap = info.get("marketCap", 0)

        return {
            "ticker": ticker,
            "company": company_name,
            "sector": sector,
            "price": f"${last_price:,.2f}",
            "cap": format_market_cap(market_cap),
            "direction": direction,
            "momentum_score": round(momentum_score),
            "volume_score": round(volume_score),
            "trend_score": round(trend_score),
            "score": composite,
            "momentum_pct": round(momentum_pct, 2),
        }
    except Exception as e:
        print(f"שגיאה במשיכת נתונים עבור {ticker}: {e}")
        return None


def format_market_cap(value):
    # הופך מספר גדול (כמו 1700000000000) לפורמט קריא כמו $1.7T
    if not value:
        return "לא זמין"
    if value >= 1e12:
        return f"${value / 1e12:.1f}T"
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    return f"${value:,.0f}"


def build_html(picks):
    # בונה את קובץ ה-HTML הסופי מרשימת ההזדמנויות שנבחרו.
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    cards_html = ""
    for p in picks:
        badge_class = "buy" if p["direction"] == "buy" else "sell"
        badge_text = "כניסה" if p["direction"] == "buy" else "יציאה"
        cards_html += f"""
        <div class="card {badge_class}">
          <div class="card-top">
            <div>
              <div class="ticker">{p['ticker']}</div>
              <div class="company">{p['company']}</div>
            </div>
            <div class="badge {badge_class}">{badge_text}</div>
          </div>
          <div class="meta-row">
            <div class="meta-item"><div class="meta-label">מחיר</div><div class="meta-value">{p['price']}</div></div>
            <div class="meta-item"><div class="meta-label">שווי שוק</div><div class="meta-value">{p['cap']}</div></div>
            <div class="meta-item"><div class="meta-label">סקטור</div><div class="meta-value" style="font-size:12px">{p['sector']}</div></div>
          </div>
          <div class="signals">
            <div class="signal-row"><span class="signal-label">מומנטום</span><div class="bar-track"><div class="bar-fill" style="width:{p['momentum_score']}%"></div></div></div>
            <div class="signal-row"><span class="signal-label">נפח מסחר</span><div class="bar-track"><div class="bar-fill" style="width:{p['volume_score']}%"></div></div></div>
            <div class="signal-row"><span class="signal-label">מגמה</span><div class="bar-track"><div class="bar-fill" style="width:{p['trend_score']}%"></div></div></div>
          </div>
          <div class="card-foot">
            <div class="score mono">ציון {p['score']}</div>
            <div class="score mono">שינוי 5 ימים: {p['momentum_pct']}%</div>
          </div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MarketPulse</title>
<link href="https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root{{ --ink:#0F1418; --ink-2:#161D24; --paper:#F5F1E8; --paper-dim:#C9C4B6;
    --teal:#4FB8A0; --teal-dim:#2E4A44; --coral:#E0604A; --coral-dim:#4A2E28;
    --gold:#D9A441; --slate:#8A93A0; --line:#232C34; }}
  *{{box-sizing:border-box; margin:0; padding:0;}}
  body{{ background:var(--ink); color:var(--paper); font-family:'Rubik',sans-serif; padding:0 0 60px; }}
  .mono{{ font-family:'JetBrains Mono',monospace; }}
  header{{ padding:36px 24px 28px; border-bottom:1px solid var(--line); }}
  .header-inner{{ max-width:960px; margin:0 auto; }}
  .eyebrow{{ color:var(--gold); font-family:'JetBrains Mono',monospace; font-size:12px; letter-spacing:.12em; text-transform:uppercase; margin-bottom:10px; }}
  h1{{ font-size:32px; font-weight:800; }}
  .as-of{{ margin-top:14px; font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--paper-dim); }}
  main{{ max-width:960px; margin:0 auto; padding:0 24px; }}
  section{{ margin-top:40px; }}
  .picks{{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }}
  @media (max-width:760px){{ .picks{{ grid-template-columns:1fr; }} }}
  .card{{ background:var(--ink-2); border:1px solid var(--line); border-radius:10px; padding:20px; }}
  .card-top{{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:14px; }}
  .ticker{{ font-family:'JetBrains Mono',monospace; font-size:20px; font-weight:700; }}
  .company{{ color:var(--slate); font-size:12.5px; margin-top:2px; }}
  .badge{{ font-family:'JetBrains Mono',monospace; font-size:11px; font-weight:700; padding:4px 9px; border-radius:20px; }}
  .badge.buy{{ background:var(--teal-dim); color:var(--teal); }}
  .badge.sell{{ background:var(--coral-dim); color:var(--coral); }}
  .meta-row{{ display:flex; gap:14px; margin:10px 0 14px; padding:10px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }}
  .meta-item{{ flex:1; }}
  .meta-label{{ font-size:10px; color:var(--slate); font-family:'JetBrains Mono',monospace; text-transform:uppercase; }}
  .meta-value{{ font-family:'JetBrains Mono',monospace; font-size:14px; font-weight:600; margin-top:3px; }}
  .signals{{ display:flex; flex-direction:column; gap:7px; margin-bottom:16px; }}
  .signal-row{{ display:flex; align-items:center; gap:8px; font-size:11.5px; }}
  .signal-label{{ width:70px; color:var(--slate); font-family:'JetBrains Mono',monospace; flex-shrink:0; }}
  .bar-track{{ flex:1; height:5px; background:var(--line); border-radius:3px; overflow:hidden; }}
  .bar-fill{{ height:100%; border-radius:3px; background:var(--teal); }}
  .card.sell .bar-fill{{ background:var(--coral); }}
  .card-foot{{ display:flex; justify-content:space-between; padding-top:14px; border-top:1px solid var(--line); font-size:12px; }}
  footer{{ max-width:960px; margin:50px auto 0; padding:20px 24px 0; border-top:1px solid var(--line); font-size:11.5px; color:var(--slate); line-height:1.7; }}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <div class="eyebrow">MarketPulse · עדכון אוטומטי יומי</div>
    <h1>הזדמנויות היום</h1>
    <div class="as-of mono">עודכן לאחרונה: {now_str}</div>
  </div>
</header>
<main>
  <section>
    <div class="picks">{cards_html}</div>
  </section>
  <footer>
    <p>זהו כלי עזר להצגת מידע בלבד ואינו מהווה ייעוץ השקעות. הציון מבוסס כרגע על נתוני מחיר ונפח מסחר בלבד (ללא המלצות אנליסטים או ניתוח סנטימנט חדשות, שיתווספו בשלב הבא). כל החלטת השקעה היא באחריות המשתמש בלבד.</p>
  </footer>
</main>
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html נוצר בהצלחה.")


def send_telegram_alert(top_picks):
    # שולח הודעת טלגרם עם 2-3 ההזדמנויות הבולטות ביותר.
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("לא הוגדרו פרטי טלגרם (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) - מדלג על שליחת התראה.")
        return

    # לוקחים רק את 2-3 המובילות ביותר עבור ההתראה (לא את כל 5)
    alert_picks = top_picks[:3]

    lines = ["MarketPulse - עדכון יומי", ""]
    for p in alert_picks:
        direction_label = "כניסה" if p["direction"] == "buy" else "יציאה"
        lines.append(f"{p['ticker']} ({p['company']})")
        lines.append(f"איתות: {direction_label} | ציון: {p['score']} | מחיר: {p['price']}")
        lines.append("")

    message = "\n".join(lines)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=15)
        if response.status_code == 200:
            print("הודעת טלגרם נשלחה בהצלחה.")
        else:
            print(f"שליחת הודעת טלגרם נכשלה: {response.status_code} {response.text}")
    except Exception as e:
        print(f"שגיאה בשליחת הודעת טלגרם: {e}")


def main():
    results = []
    for ticker in WATCHLIST:
        print(f"מושך נתונים עבור {ticker}...")
        data = fetch_and_score(ticker)
        if data:
            results.append(data)

    # מיון מהציון הגבוה ביותר לנמוך ביותר, ובחירת 5 המובילות
    results.sort(key=lambda x: x["score"], reverse=True)
    top_picks = results[:5]

    build_html(top_picks)
    send_telegram_alert(top_picks)


if __name__ == "__main__":
    main()
