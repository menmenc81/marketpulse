# MarketPulse - סקריפט יומי: סורק כמה סוגי נכסים, מדרג הזדמנויות,
# מחשב רמות כניסה / יעד (לימיט) / סטופ לוס, בונה דשבורד ושולח התראה לטלגרם.
#
# הרמות מחושבות אוטומטית לפי תנודתיות (ATR) ואינן ייעוץ השקעות.

import os
import math
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

# ------------------------------------------------------------------
# הגדרות שאפשר לשנות בקלות
# ------------------------------------------------------------------
DASHBOARD_PER_CLASS = 3      # כמה איתותים להציג בדשבורד לכל סוג נכס
TELEGRAM_PER_CLASS = 2       # כמה איתותים לשלוח בטלגרם לכל סוג נכס
MIN_ALERT_SCORE = 20         # סף ציון מינימלי כדי להיכלל בהתראת טלגרם
STOP_ATR_MULT = 2.0          # מרחק הסטופ לוס, בכפולות של ATR
TARGET_ATR_MULT = 3.0        # מרחק היעד (לימיט), בכפולות של ATR

# ------------------------------------------------------------------
# רשימות המעקב, מחולקות לסוגי נכסים.
# מניות ישראליות נכתבות עם הסיומת .TA (בורסת תל אביב).
# טיקר שלא נמצא פשוט מדולג, והריצה ממשיכה כרגיל.
# ------------------------------------------------------------------
UNIVERSE = {
    "מניות ארצות הברית": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA",
        "SNOW", "IOT", "CRWD", "NFLX", "AMD", "ORCL", "ADBE", "CRM",
        "INTC", "QCOM", "TXN", "NOW", "PANW", "FTNT", "DDOG", "NET",
        "ZS", "MDB", "SHOP", "UBER", "ABNB", "PLTR", "AMAT", "MU",
        "LRCX", "KLAC", "ANET", "SNPS", "CDNS", "INTU", "ADP",
        "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "SCHW", "C",
        "BLK", "SPGI", "BRK-B",
        "TEVA", "JNJ", "PFE", "UNH", "LLY", "MRK", "ABBV", "TMO", "AMGN",
        "GILD", "ISRG", "SYK", "ELV", "CI",
        "COST", "WMT", "TGT", "HD", "LOW", "NKE", "SBUX", "MCD", "LULU",
        "DIS", "CMG", "YUM", "ORLY", "AZO",
        "XOM", "CVX", "CAT", "BA", "GE", "HON", "UPS", "LMT", "RTX", "DE",
        "MMM", "EMR", "ETN", "NEE", "LIN", "AMT",
        "CMCSA", "T", "VZ", "TMUS",
        "PG", "KO", "PEP", "CL", "MDLZ", "PM", "MO",
        "DAL", "AAL", "MAR", "BKNG",
        "F", "GM", "RIVN",
        "PYPL", "COIN", "ROKU", "SPOT", "SOFI", "DKNG", "RBLX",
    ],
    "מניות ישראליות": [
        "LUMI.TA", "POLI.TA", "DSCT.TA", "MZTF.TA", "FIBI.TA", "ICL.TA",
        "NICE.TA", "ESLT.TA", "BEZQ.TA", "TEVA.TA", "AZRG.TA", "AMOT.TA",
        "MLSR.TA", "SAE.TA", "STRS.TA", "ORL.TA", "DLEKG.TA", "ENLT.TA",
        "NVMI.TA", "TSEM.TA", "ELAL.TA", "HARL.TA", "PHOE.TA", "CLIS.TA",
        "MGDL.TA", "ALHE.TA", "FTAL.TA", "PTNR.TA", "CEL.TA", "OPCE.TA",
    ],
    "מדדים ו-ETF": [
        "SPY", "QQQ", "DIA", "IWM", "VTI", "VT", "EFA", "EEM", "VGK",
        "EWJ", "FXI", "EWZ", "INDA", "EIS",
        "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB",
        "XLRE", "XLC", "SMH", "ARKK",
    ],
    "אגרות חוב": [
        "TLT", "IEF", "SHY", "IEI", "TLH", "GOVT", "BND", "AGG", "BNDX",
        "LQD", "VCSH", "HYG", "JNK", "TIP", "EMB", "MUB",
    ],
    "סחורות": [
        "GLD", "SLV", "USO", "UNG", "DBC", "PDBC", "CPER", "DBA", "WEAT",
        "PPLT",
    ],
    "קריפטו": [
        "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "ADA-USD",
        "DOGE-USD", "AVAX-USD", "LINK-USD", "LTC-USD",
    ],
}


def clamp(value, low, high):
    return max(low, min(high, value))


def currency_of(ticker):
    # מחזיר סמל מטבע ומחלק להמרה. מניות תל אביב מתומחרות באגורות.
    if ticker.endswith(".TA"):
        return "₪", 100.0
    return "$", 1.0


def fmt_price(value, symbol):
    if value >= 1:
        return f"{symbol}{value:,.2f}"
    return f"{symbol}{value:.4f}"


def fetch_and_score(ticker):
    # מושך נתונים לנייר ערך אחד ומחשב ציון, כיוון ורמות כניסה/יעד/סטופ.
    try:
        hist = yf.Ticker(ticker).history(period="90d")
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None
        hist = hist.dropna(subset=["Close"])
        if len(hist) < 30:
            return None

        closes = hist["Close"]
        last = float(closes.iloc[-1])
        if last <= 0:
            return None

        # תנודתיות יומית - משמשת לנרמול כדי שאפשר יהיה להשוות בין סוגי נכסים
        daily = closes.pct_change().dropna()
        vol = float(daily.std()) if len(daily) > 5 else 0.01
        if not vol or math.isnan(vol):
            vol = 0.01

        # מומנטום: שינוי ב-5 ימים, מנורמל לפי תנודתיות
        ret5 = last / float(closes.iloc[-6]) - 1
        mom_z = ret5 / (vol * math.sqrt(5))
        momentum_score = clamp(50 + mom_z * 20, 0, 100)

        # מגמה: מרחק מהממוצע ל-50 יום, מנורמל לפי תנודתיות
        ma = float(closes.iloc[-50:].mean())
        trend_z = ((last - ma) / ma) / (vol * math.sqrt(25))
        trend_score = clamp(50 + trend_z * 20, 0, 100)

        # נפח מסחר: היום מול הממוצע של 20 הימים הקודמים
        volume_ratio = 1.0
        if "Volume" in hist.columns:
            volumes = hist["Volume"]
            idx = -2 if ticker.endswith("-USD") else -1
            avg_vol = float(volumes.iloc[-21:-1].mean())
            if avg_vol > 0:
                volume_ratio = float(volumes.iloc[idx]) / avg_vol
        volume_score = clamp(volume_ratio * 50, 0, 100)

        # כיוון וחוזק האיתות
        directional = (momentum_score + trend_score) / 2 - 50
        multiplier = 0.5 + 0.5 * clamp(volume_ratio, 0.5, 2.0)
        signed = directional * multiplier
        direction = "buy" if signed >= 0 else "sell"
        score = int(min(100, round(abs(signed) * 2)))

        # ATR - טווח תנועה יומי ממוצע, בסיס לחישוב סטופ ויעד
        prev_close = closes.shift(1)
        true_range = pd.concat(
            [
                hist["High"] - hist["Low"],
                (hist["High"] - prev_close).abs(),
                (hist["Low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = float(true_range.iloc[-14:].mean())
        if not atr or math.isnan(atr):
            atr = last * vol

        if direction == "buy":
            stop = max(last - STOP_ATR_MULT * atr, last * 0.5)
            target = last + TARGET_ATR_MULT * atr
        else:
            stop = last + STOP_ATR_MULT * atr
            target = max(last - TARGET_ATR_MULT * atr, last * 0.1)

        symbol, divisor = currency_of(ticker)

        # עבור סימן "מכירה" הפסים מציגים את עוצמת החולשה
        if direction == "sell":
            momentum_bar = 100 - momentum_score
            trend_bar = 100 - trend_score
        else:
            momentum_bar = momentum_score
            trend_bar = trend_score

        return {
            "ticker": ticker,
            "name": ticker,
            "direction": direction,
            "label": "כניסה (קנייה)" if direction == "buy" else "יציאה / מכירה",
            "score": score,
            "entry": fmt_price(last / divisor, symbol),
            "target": fmt_price(target / divisor, symbol),
            "stop": fmt_price(stop / divisor, symbol),
            "target_pct": f"{(target - last) / last * 100:+.1f}%",
            "stop_pct": f"{(stop - last) / last * 100:+.1f}%",
            "momentum_bar": round(momentum_bar),
            "volume_bar": round(volume_score),
            "trend_bar": round(trend_bar),
        }
    except Exception as e:
        print(f"דילוג על {ticker}: {e}")
        return None


def add_name(pick):
    # מוסיף שם חברה/קרן קריא, רק לאיתותים שנבחרו (כדי לא להאט את הריצה)
    try:
        info = yf.Ticker(pick["ticker"]).info
        name = info.get("shortName") or info.get("longName")
        if name:
            pick["name"] = name
    except Exception:
        pass
    return pick


def israel_now_text():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Jerusalem")).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")


def build_card(p):
    cls = "buy" if p["direction"] == "buy" else "sell"
    note = ""
    if cls == "sell":
        note = '<div class="note">איתות שלילי - רלוונטי למי שמחזיק את הנייר, או לפוזיציית שורט.</div>'
    return f"""
    <div class="card {cls}">
      <div class="card-top">
        <div>
          <div class="ticker">{p['ticker']}</div>
          <div class="company">{p['name']}</div>
        </div>
        <div class="badge {cls}">{p['label']}</div>
      </div>
      <div class="levels">
        <div class="lvl"><div class="lvl-label">כניסה</div><div class="lvl-value">{p['entry']}</div></div>
        <div class="lvl"><div class="lvl-label">יעד (לימיט)</div><div class="lvl-value up">{p['target']}</div><div class="lvl-pct">{p['target_pct']}</div></div>
        <div class="lvl"><div class="lvl-label">סטופ לוס</div><div class="lvl-value down">{p['stop']}</div><div class="lvl-pct">{p['stop_pct']}</div></div>
      </div>
      <div class="signals">
        <div class="signal-row"><span class="signal-label">מומנטום</span><div class="bar-track"><div class="bar-fill" style="width:{p['momentum_bar']}%"></div></div></div>
        <div class="signal-row"><span class="signal-label">נפח מסחר</span><div class="bar-track"><div class="bar-fill" style="width:{p['volume_bar']}%"></div></div></div>
        <div class="signal-row"><span class="signal-label">מגמה</span><div class="bar-track"><div class="bar-fill" style="width:{p['trend_bar']}%"></div></div></div>
      </div>
      <div class="card-foot">
        <div class="mono">ציון {p['score']}</div>
        <div class="mono">סיכוי/סיכון 1:{TARGET_ATR_MULT / STOP_ATR_MULT:.1f}</div>
      </div>
      {note}
    </div>
    """


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MarketPulse</title>
<link href="https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root{ --ink:#0F1418; --ink-2:#161D24; --paper:#F5F1E8; --paper-dim:#C9C4B6;
    --teal:#4FB8A0; --teal-dim:#2E4A44; --coral:#E0604A; --coral-dim:#4A2E28;
    --gold:#D9A441; --slate:#8A93A0; --line:#232C34; }
  *{box-sizing:border-box; margin:0; padding:0;}
  body{ background:var(--ink); color:var(--paper); font-family:'Rubik',sans-serif; padding:0 0 60px; }
  .mono{ font-family:'JetBrains Mono',monospace; }
  header{ padding:36px 24px 28px; border-bottom:1px solid var(--line); }
  .header-inner{ max-width:1000px; margin:0 auto; }
  .eyebrow{ color:var(--gold); font-family:'JetBrains Mono',monospace; font-size:12px; letter-spacing:.12em; text-transform:uppercase; margin-bottom:10px; }
  h1{ font-size:32px; font-weight:800; }
  .as-of{ margin-top:14px; font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--paper-dim); }
  main{ max-width:1000px; margin:0 auto; padding:0 24px; }
  section{ margin-top:40px; }
  .section-head{ display:flex; align-items:baseline; justify-content:space-between; margin-bottom:16px; border-bottom:1px solid var(--line); padding-bottom:10px; }
  .section-title{ font-size:19px; font-weight:600; }
  .section-note{ font-size:12px; color:var(--slate); font-family:'JetBrains Mono',monospace; }
  .picks{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
  @media (max-width:820px){ .picks{ grid-template-columns:1fr; } }
  .card{ background:var(--ink-2); border:1px solid var(--line); border-radius:10px; padding:20px; }
  .card-top{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:14px; gap:8px; }
  .ticker{ font-family:'JetBrains Mono',monospace; font-size:19px; font-weight:700; }
  .company{ color:var(--slate); font-size:12px; margin-top:2px; }
  .badge{ font-family:'Rubik',sans-serif; font-size:11px; font-weight:600; padding:4px 9px; border-radius:20px; white-space:nowrap; }
  .badge.buy{ background:var(--teal-dim); color:var(--teal); }
  .badge.sell{ background:var(--coral-dim); color:var(--coral); }
  .levels{ display:flex; gap:10px; margin:6px 0 16px; padding:12px 0; border-top:1px solid var(--line); border-bottom:1px solid var(--line); }
  .lvl{ flex:1; }
  .lvl-label{ font-size:10.5px; color:var(--slate); }
  .lvl-value{ font-family:'JetBrains Mono',monospace; font-size:13.5px; font-weight:700; margin-top:3px; }
  .lvl-value.up{ color:var(--teal); }
  .lvl-value.down{ color:var(--coral); }
  .lvl-pct{ font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--slate); margin-top:2px; }
  .signals{ display:flex; flex-direction:column; gap:7px; margin-bottom:16px; }
  .signal-row{ display:flex; align-items:center; gap:8px; font-size:11.5px; }
  .signal-label{ width:70px; color:var(--slate); flex-shrink:0; }
  .bar-track{ flex:1; height:5px; background:var(--line); border-radius:3px; overflow:hidden; }
  .bar-fill{ height:100%; border-radius:3px; background:var(--teal); }
  .card.sell .bar-fill{ background:var(--coral); }
  .card-foot{ display:flex; justify-content:space-between; padding-top:12px; border-top:1px solid var(--line); font-size:12px; }
  .note{ margin-top:10px; font-size:11px; color:var(--slate); line-height:1.5; }
  .empty{ color:var(--slate); font-size:13px; padding:10px 0; }
  footer{ max-width:1000px; margin:50px auto 0; padding:20px 24px 0; border-top:1px solid var(--line); font-size:11.5px; color:var(--slate); line-height:1.7; }
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <div class="eyebrow">MarketPulse - עדכון אוטומטי יומי</div>
    <h1>הזדמנויות היום</h1>
    <div class="as-of mono">עודכן לאחרונה: __NOW__ (שעון ישראל) | נסרקו __SCANNED__ ניירות ערך</div>
  </div>
</header>
<main>
__SECTIONS__
</main>
<footer>
  <p>זהו כלי עזר להצגת מידע בלבד ואינו מהווה ייעוץ השקעות או המלצה לבצע עסקה. הציון מבוסס על מחיר, נפח מסחר ומגמה בלבד (ללא דוחות כספיים, המלצות אנליסטים או חדשות). רמות הכניסה, היעד והסטופ לוס מחושבות אוטומטית לפי תנודתיות היסטורית (ATR), הן אינן מותאמות אישית אליך, ואינן מבטיחות תוצאה. מחירי מניות ישראליות מומרים משערי אגורות לשקלים. כל החלטת השקעה היא באחריותך בלבד.</p>
</footer>
</body>
</html>
"""


def build_html(by_class, scanned):
    sections = ""
    for asset_class, picks in by_class.items():
        if picks:
            cards = "".join(build_card(p) for p in picks)
            body = f'<div class="picks">{cards}</div>'
        else:
            body = '<div class="empty">לא נמצאו נתונים לסוג נכס זה בריצה הנוכחית.</div>'
        sections += f"""
  <section>
    <div class="section-head">
      <div class="section-title">{asset_class}</div>
      <div class="section-note">{len(picks)} איתותים</div>
    </div>
    {body}
  </section>
"""
    html = HTML_TEMPLATE.replace("__NOW__", israel_now_text())
    html = html.replace("__SCANNED__", str(scanned))
    html = html.replace("__SECTIONS__", sections)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html נוצר בהצלחה.")


def build_telegram_messages(by_class):
    lines = ["MarketPulse - עדכון יומי", ""]
    any_signal = False
    for asset_class, picks in by_class.items():
        strong = [p for p in picks if p["score"] >= MIN_ALERT_SCORE][:TELEGRAM_PER_CLASS]
        if not strong:
            continue
        any_signal = True
        lines.append(f"=== {asset_class} ===")
        for p in strong:
            lines.append(f"{p['ticker']} - {p['label']} (ציון {p['score']})")
            lines.append(f"מחיר כניסה: {p['entry']}")
            lines.append(f"יעד (לימיט): {p['target']} ({p['target_pct']})")
            lines.append(f"סטופ לוס: {p['stop']} ({p['stop_pct']})")
            lines.append("")
    if not any_signal:
        lines.append("אין היום איתותים חזקים.")
        lines.append("")
    lines.append("הרמות מחושבות אוטומטית לפי תנודתיות. אינן ייעוץ השקעות.")

    # טלגרם מגביל הודעה ל-4096 תווים, לכן מפצלים בעת הצורך
    messages, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > 3800:
            messages.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        messages.append(current)
    return messages


def send_telegram_alert(by_class):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("לא הוגדרו פרטי טלגרם - מדלג על שליחת התראה.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for text in build_telegram_messages(by_class):
        try:
            response = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
            if response.status_code == 200:
                print("הודעת טלגרם נשלחה בהצלחה.")
            else:
                print(f"שליחת הודעת טלגרם נכשלה: {response.status_code} {response.text}")
        except Exception as e:
            print(f"שגיאה בשליחת הודעת טלגרם: {e}")


def main():
    by_class = {}
    scanned = 0
    skipped = []

    for asset_class, tickers in UNIVERSE.items():
        print(f"סורק: {asset_class} ({len(tickers)} ניירות)")
        results = []
        for ticker in dict.fromkeys(tickers):
            data = fetch_and_score(ticker)
            if data:
                results.append(data)
                scanned += 1
            else:
                skipped.append(ticker)
        results.sort(key=lambda x: x["score"], reverse=True)
        top = results[:DASHBOARD_PER_CLASS]
        by_class[asset_class] = [add_name(p) for p in top]

    if skipped:
        print("ניירות שדולגו (לא נמצאו נתונים): " + ", ".join(skipped))

    build_html(by_class, scanned)
    send_telegram_alert(by_class)


if __name__ == "__main__":
    main()
