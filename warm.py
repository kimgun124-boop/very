"""⚡ 빠른 시작 묶음(warm pack): 서버가 새로 켜질 때(잠에서 깸·재배포·메모리 재시작) 첫 화면을 빨리 띄우려고
GitHub Actions가 미리 받아 둔 보드 종목의 일봉·상장주식수·네이버 공식 52주 최고/최저를 한 파일로 묶어요.

- 만들기: `python warm.py build warm.npz` (GitHub Actions .github/workflows/warm.yml이 매일 아침 7:2x에 만들어
  `warm` 브랜치에 덮어써요 — main 기록은 안 늘어나요)
- 쓰기: 앱이 켜질 때 load()로 한 번 받아 창고에 미리 넣어요. 미리 넣은 값은 '오래된 값'으로 표시해서
  화면은 바로 띄우고, 최신 값은 뒤에서 새로 받아 바꿔 끼워요. 오늘 장중 봉은 실시간 시세로 붙여요(data._session_arrays).
- 파일이 없거나, 이틀 넘게 묵었거나, 데이터 버전이 다르면 쓰지 않아요(예전처럼 처음부터 받아요).
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

import numpy as np
import pandas as pd

URL = os.environ.get("WARM_URL") or "https://raw.githubusercontent.com/kimgun124-boop/very/warm/warm.npz"
MAX_AGE = 2 * 86400 + 6 * 3600        # 주말을 건너도 쓰게 이틀 반까지
FORMAT = 1
_COLS = ("open", "high", "low", "close", "volume")


def _js(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=lambda v: v.item() if hasattr(v, "item") else str(v))


def pack(histories: dict, shares: dict, h52: dict, meta: dict) -> bytes:
    codes, names, offs = [], [], [0]
    dates, cols = [], {k: [] for k in _COLS}
    for c, res in sorted(histories.items()):
        df = res[1] if res else None
        if df is None or df.empty:
            continue
        codes.append(c)
        names.append(res[0] or "")
        dates.append(pd.to_datetime(df["date"]).values.astype("datetime64[D]").astype("int32"))
        for k in _COLS:
            cols[k].append(pd.to_numeric(df[k], errors="coerce").to_numpy(dtype="float32"))
        offs.append(offs[-1] + len(df))
    buf = io.BytesIO()
    np.savez_compressed(
        buf, codes=np.array(codes, dtype="U12"), names=np.array(names, dtype="U64"),
        offs=np.array(offs, dtype="int64"), dates=np.concatenate(dates) if dates else np.zeros(0, "int32"),
        **{k: (np.concatenate(v) if v else np.zeros(0, "float32")) for k, v in cols.items()},
        shares=np.array(_js(shares)), h52=np.array(_js(h52)), meta=np.array(_js({**meta, "format": FORMAT})))
    return buf.getvalue()


def unpack(raw: bytes) -> dict:
    z = np.load(io.BytesIO(raw), allow_pickle=False)          # 받은 파일이라 pickle은 절대 안 열어요
    meta = json.loads(str(z["meta"]))
    if meta.get("format") != FORMAT:
        raise ValueError("묶음 형식이 달라요")
    codes, names, offs = z["codes"].tolist(), z["names"].tolist(), z["offs"]
    dates = z["dates"].astype("datetime64[D]").astype("datetime64[us]")
    cols = {k: z[k] for k in _COLS}
    hist = {}
    for i, c in enumerate(codes):
        a, b = int(offs[i]), int(offs[i + 1])
        df = pd.DataFrame({"date": dates[a:b], **{k: cols[k][a:b].copy() for k in _COLS}})
        hist[c] = (names[i] or None, df, None)
    return {"hist": hist, "shares": json.loads(str(z["shares"])), "h52": json.loads(str(z["h52"])), "meta": meta}


def load(data_version: str | None = None, timeout: float = 12.0) -> dict:
    """묶음을 받아 풀어요. 못 쓰면 {}(앱은 예전처럼 처음부터 받아요)."""
    try:
        import requests
        if URL.startswith("file://"):
            with open(URL[7:], "rb") as f:
                raw = f.read()
        else:
            r = requests.get(URL, timeout=timeout)
            r.raise_for_status()
            raw = r.content
        got = unpack(raw)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{exc.__class__.__name__}: {exc}"[:200]}
    meta = got["meta"]
    age = time.time() - float(meta.get("built") or 0)
    if age > MAX_AGE:
        return {"error": f"묶음이 오래됐어요({age / 3600:.0f}시간 전)", "meta": meta}
    if data_version and meta.get("data_version") and meta["data_version"] != data_version:
        return {"error": f"데이터 버전이 달라요({meta['data_version']} ≠ {data_version})", "meta": meta}
    got["age"] = age
    return got


def build(out_path: str):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import data
    import stocks
    codes = sorted({s["code"] for s in stocks.STOCKS})
    kr = [c for c in codes if data.is_kr(c)]
    os_ = [c for c in codes if not data.is_kr(c)]
    t0 = time.time()
    hist = data.fetch_histories(codes, workers=8)
    t1 = time.time()
    kr_sh, kr_diag = data.fetch_kr_shares(kr)
    os_sh, os_diag = data.fetch_overseas_shares(os_)
    t2 = time.time()
    h52 = data.fetch_official_52w(kr)
    t3 = time.time()
    ok = sum(1 for r in hist.values() if r and r[1] is not None and not r[1].empty)
    meta = {"built": time.time(), "built_kst": data.now_kst().strftime("%Y-%m-%d %H:%M"),
            "data_version": data.DATA_VERSION, "n_codes": len(codes), "n_hist": ok,
            "n_shares": len(kr_sh) + len(os_sh), "n_h52": len(h52),
            "secs": {"hist": round(t1 - t0), "shares": round(t2 - t1), "h52": round(t3 - t2)},
            "shares_diag": {"kr": kr_diag, "os": os_diag}}
    if ok < 0.8 * len(codes) or len(kr_sh) < 0.8 * len(kr):
        raise SystemExit(f"받은 게 너무 적어서 저장 안 해요: {meta}")
    raw = pack(hist, {**kr_sh, **os_sh}, h52, meta)
    with open(out_path, "wb") as f:
        f.write(raw)
    print(_js({**meta, "bytes": len(raw)}))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        build(sys.argv[2] if len(sys.argv) > 2 else "warm.npz")
    else:
        print(__doc__)
