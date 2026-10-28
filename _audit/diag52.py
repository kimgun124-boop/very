"""52주 최고가 출처 진단: 네이버 공식 vs KRX 일봉 vs NXT 일봉 최대 고가."""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data, pandas as pd
codes = ["006400", "039200", "168360", "294870", "381970", "006650", "114810", "348210", "005930", "100840",
         "241710", "023160", "014830", "060900", "002070"]
out = {}
def pages(code, trade, n=30):
    rows = []
    for pg in range(1, n + 1):
        try:
            r = data.session.get(f"https://m.stock.naver.com/api/stock/{code}/price",
                                 params={"pageSize": 20, "page": pg, "tradeType": trade}, headers=data.JSON_HEADERS, timeout=8)
            j = r.json()
        except Exception as e:
            rows.append({"err": str(e)}); break
        if not j: break
        rows += j
        if len(rows) >= 260: break
        time.sleep(0.15)
    return rows
for c in codes:
    off = data._official_52w_one(c)
    integ = data._get_json([f"{data.NAVER_MOBILE}/{c}/integration"], timeout=6) or {}
    ti = [{k: x.get(k) for k in ("code", "key", "value")} for x in (integ.get("totalInfos") or [])]
    res = {"official": off, "integration_totalInfos": ti}
    for trade in ("KRX", "NXT", None):
        rows = pages(c, trade) if trade else pages(c, "")
        df = pd.DataFrame([r for r in rows if isinstance(r, dict) and "highPrice" in r])
        if df.empty:
            res[str(trade)] = {"n": 0, "sample": rows[:2]}; continue
        df["h"] = pd.to_numeric(df["highPrice"].astype(str).str.replace(",", ""), errors="coerce")
        df = df.head(250)
        i = df["h"].idxmax()
        res[str(trade)] = {"n": len(df), "max_high": float(df["h"].max()), "date": df.loc[i, "localTradedAt"],
                           "first": df["localTradedAt"].iloc[-1], "keys": list(df.columns)[:12]}
    _, h, _ = data.fetch_history(c)
    w = h.tail(250)
    res["fchart_250_max"] = float(w["high"].max()); res["fchart_date"] = str(w.loc[w["high"].idxmax(), "date"])[:10]
    out[c] = res
    print(c, json.dumps({k: (v if k != "integration_totalInfos" else [x for x in v if "52" in str(x)]) for k, v in res.items()}, ensure_ascii=False, default=str)[:900], flush=True)
os.makedirs("out", exist_ok=True)
json.dump(out, open("out/diag52.json", "w"), ensure_ascii=False, indent=1, default=str)
