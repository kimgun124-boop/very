"""보드 숫자 사실 대조: 보드와 같은 방식으로 만든 표 vs 다음(Daum) 금융 시세(독립 출처) vs 야후."""
import os, sys, json, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests, pandas as pd, numpy as np
from concurrent.futures import ThreadPoolExecutor
import data, stocks
OUT = sys.argv[1]
kr = []
seen = set()
for s in stocks.STOCKS:
    c = str(s.get("code") or "")
    if re.fullmatch(r"\d{6}", c) and c not in seen:
        seen.add(c); kr.append(s)
codes = [s["code"] for s in kr]
t0 = time.time()
hist = data.fetch_histories(codes)
quotes, qerr = data.fetch_quotes(codes)
official = data.fetch_official_52w(codes)
df = data.build_table(kr, hist, quotes, {}, data.fetch_fx(), official52=official)
print(f"board table {len(df)} rows in {time.time()-t0:.0f}s, quote err={qerr}, official={len(official)}", flush=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.daum.net/"})
def daum(code):
    for _ in range(3):
        try:
            r = S.get(f"https://finance.daum.net/api/quotes/A{code}", params={"summary": "false", "changeStatistics": "true"}, timeout=10)
            if r.status_code == 200:
                j = r.json()
                return {"d_name": j.get("name"), "d_price": j.get("tradePrice"), "d_hi52": j.get("high52wPrice"),
                        "d_lo52": j.get("low52wPrice"), "d_prev": j.get("prevClosingPrice"), "d_market": j.get("market")}
        except Exception:
            pass
        time.sleep(1)
    return {}
with ThreadPoolExecutor(6) as ex:
    dm = dict(zip(codes, ex.map(daum, codes)))
rows = []
for s in kr:
    c = s["code"]
    b = df[df["code"] == c]
    b = b.iloc[0].to_dict() if len(b) else {}
    d = dm.get(c) or {}
    q = quotes.get(c) or {}
    rows.append({"code": c, "name_board": s["name"], "name_naver": b.get("naver_name") or (hist.get(c) or (None,))[0],
                 "group": s["group"], "price": b.get("price"), "q_price": q.get("price"), "q_status": q.get("status"),
                 "high52": b.get("high52"), "high52_calc": b.get("high52_calc"), "h52_src": b.get("h52_src"),
                 "low52": b.get("low52"), "off_hi": (official.get(c) or {}).get("high52"),
                 "days_since_high": b.get("days_since_high"), "to_high": b.get("to_high"), "rs": b.get("rs"),
                 "hist_len": len((hist.get(c) or (None, pd.DataFrame()))[1]), "hist_err": (hist.get(c) or (None, None, None))[2],
                 "hist_last": str((hist.get(c) or (None, pd.DataFrame(columns=['date'])))[1]["date"].iloc[-1])[:10]
                 if len((hist.get(c) or (None, pd.DataFrame()))[1]) else None,
                 **d})
t = pd.DataFrame(rows)
num = lambda x: pd.to_numeric(t[x], errors="coerce")
t["price_diff%"] = (num("price") / num("d_price") - 1) * 100
t["hi52_diff%"] = (num("high52") / num("d_hi52") - 1) * 100
t["lo52_diff%"] = (num("low52") / num("d_lo52") - 1) * 100
norm = lambda s: re.sub(r"[\s·()㈜]", "", str(s or "")).upper()
t["name_mismatch"] = [bool(d) and norm(a) != norm(d) for a, d in zip(t["name_board"], t["d_name"])]
t.to_csv(OUT + "/fact_check.csv", index=False, encoding="utf-8-sig")
bad = t[(t["price_diff%"].abs() > 0.5) | (t["hi52_diff%"].abs() > 1) | (t["lo52_diff%"].abs() > 1) | t["name_mismatch"]
        | t["price"].isna() | t["d_price"].isna()]
bad.to_csv(OUT + "/fact_bad.csv", index=False, encoding="utf-8-sig")
# 지수 60일선 (보드 vs 야후)
idx = data.fetch_index_histories()
mk = {}
for sym, ysym in (("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")):
    s1 = data.index_summary(idx[sym][0])
    try:
        import yfinance as yf
        y = yf.Ticker(ysym).history(period="6mo")["Close"]
        mk[sym] = {"board": s1, "yahoo_last": float(y.iloc[-1]), "yahoo_ma60": float(y.tail(60).mean()),
                   "board_last_date": str(idx[sym][0]["date"].iloc[-1])[:10]}
    except Exception as e:
        mk[sym] = {"board": s1, "err": str(e)}
json.dump(mk, open(OUT + "/index_check.json", "w"), ensure_ascii=False, indent=1, default=str)
print("bad rows:", len(bad))
print(bad[["code", "name_board", "d_name", "price", "d_price", "price_diff%", "high52", "d_hi52", "hi52_diff%", "h52_src", "hist_len", "hist_err"]].head(60).to_string())
print(json.dumps(mk, ensure_ascii=False, default=str)[:1500])
