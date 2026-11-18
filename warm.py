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


def _frames_arrays(prefix: str, frames: dict) -> dict:
    """{코드: 표 또는 None} → npz에 넣을 배열들(pickle 없이 열 단위로). 열 종류(dtype)는 그대로 되살려요."""
    codes = sorted(c for c, v in frames.items() if v is not None and hasattr(v, "columns") and len(v))
    nones = sorted(c for c, v in frames.items() if v is None or not hasattr(v, "columns") or not len(v))
    out = {f"{prefix}__codes": np.array(codes, dtype="U12"), f"{prefix}__none": np.array(nones, dtype="U12")}
    if not codes:
        out[f"{prefix}__schema"] = np.array("[]")
        return out
    cols = list(frames[codes[0]].columns)
    for c in codes:                                   # 열 구성이 다른 표가 섞이면 첫 표 기준(없는 열은 빈 값)
        frames[c] = frames[c].reindex(columns=cols)
    offs = np.cumsum([0] + [len(frames[c]) for c in codes]).astype("int64")
    out[f"{prefix}__offs"] = offs
    schema = []
    for i, col in enumerate(cols):
        parts = [frames[c][col] for c in codes]
        ser = pd.concat(parts, ignore_index=True)
        kind = str(ser.dtype)
        if kind.startswith("datetime64"):
            arr, k = pd.to_datetime(ser).values, "dt"
        elif kind == "bool":
            arr, k = ser.to_numpy(dtype=bool), "bool"
        elif kind.startswith(("int", "uint", "float", "Int", "Float")):
            arr, k = pd.to_numeric(ser, errors="coerce").to_numpy(dtype="float64"), kind
        else:
            null = ser.isna().to_numpy()
            out[f"{prefix}__{i}__null"] = null
            arr, k = ser.astype(str).to_numpy(dtype="U"), "obj"
        out[f"{prefix}__{i}"] = arr
        schema.append([str(col), k])
    out[f"{prefix}__schema"] = np.array(json.dumps(schema, ensure_ascii=False))
    # 표마다 원래 열 종류(정수 열에 빈 값이 있던 표만 float 등)를 그대로 되살리려고 따로 적어 둬요
    out[f"{prefix}__dtypes"] = np.array(json.dumps([[str(t) for t in frames[c].dtypes] for c in codes]))
    return out


def _frames_from(z, prefix: str) -> dict:
    if f"{prefix}__codes" not in z.files:
        return {}
    codes = z[f"{prefix}__codes"].tolist()
    res = {c: None for c in z[f"{prefix}__none"].tolist()}
    schema = json.loads(str(z[f"{prefix}__schema"]))
    if not codes or not schema:
        return res
    offs = z[f"{prefix}__offs"]
    cols = {}
    for i, (name, k) in enumerate(schema):
        a = z[f"{prefix}__{i}"]
        if k == "obj":
            a = a.astype(object)
            a[z[f"{prefix}__{i}__null"]] = None
        elif k not in ("dt", "bool"):
            try:
                a = a.astype(k) if not k.startswith(("Int", "Float")) else pd.array(a).astype(k)
            except (TypeError, ValueError):
                pass                                   # 정수 열에 빈 값이 섞였으면 float 그대로
        cols[name] = a
    per = json.loads(str(z[f"{prefix}__dtypes"])) if f"{prefix}__dtypes" in z.files else None
    names = [n for n, _ in schema]
    for j, c in enumerate(codes):
        a, b = int(offs[j]), int(offs[j + 1])
        df = pd.DataFrame({n: (v[a:b].copy() if hasattr(v, "copy") else v[a:b]) for n, v in cols.items()})
        if per:
            for n, t in zip(names, per[j]):
                if str(df[n].dtype) != t:
                    try:
                        df[n] = df[n].astype(t)
                    except (TypeError, ValueError):
                        pass
        res[c] = df
    return res


def pack(histories: dict, shares: dict, h52: dict, meta: dict, frames: dict | None = None) -> bytes:
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
        shares=np.array(_js(shares)), h52=np.array(_js(h52)), meta=np.array(_js({**meta, "format": FORMAT})),
        **{k: v for name, fr in (frames or {}).items() for k, v in _frames_arrays(name, dict(fr)).items()})
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
    frames = {name: _frames_from(z, name) for name in ("trends", "fins", "monthly", "misc")}
    return {"hist": hist, "shares": json.loads(str(z["shares"])), "h52": json.loads(str(z["h52"])), "meta": meta, **frames}


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
    trends = data.fetch_stock_trends(kr, workers=8)          # 외국인·기관 수급(최근 20거래일)
    fins = data.fetch_financials_many(kr, workers=8)         # 연간 영업이익(실적+전망)
    monthly = data.fetch_monthlies(codes)                    # 역대 최고가용 월봉
    monthly = {c: (None if m is None or m.empty or "high" not in m else
                   pd.DataFrame({"date": pd.to_datetime(m["date"]).values,
                                 "high": pd.to_numeric(m["high"], errors="coerce").astype("float32").values}))
               for c, m in monthly.items()}
    try:
        deposits = data.fetch_deposits()                     # 증시 자금(예탁금·신용잔고)
    except Exception:  # noqa: BLE001
        deposits = None
    t4 = time.time()
    ok = sum(1 for r in hist.values() if r and r[1] is not None and not r[1].empty)
    meta = {"built": time.time(), "built_kst": data.now_kst().strftime("%Y-%m-%d %H:%M"),
            "data_version": data.DATA_VERSION, "n_codes": len(codes), "n_hist": ok,
            "n_shares": len(kr_sh) + len(os_sh), "n_h52": len(h52),
            "secs": {"hist": round(t1 - t0), "shares": round(t2 - t1), "h52": round(t3 - t2), "extra": round(t4 - t3)},
            "n_trends": sum(v is not None for v in trends.values()), "n_fins": sum(v is not None for v in fins.values()),
            "n_monthly": sum(v is not None for v in monthly.values()),
            "shares_diag": {"kr": kr_diag, "os": os_diag}}
    if ok < 0.8 * len(codes) or len(kr_sh) < 0.8 * len(kr):
        raise SystemExit(f"받은 게 너무 적어서 저장 안 해요: {meta}")
    raw = pack(hist, {**kr_sh, **os_sh}, h52, meta, frames={"trends": trends, "fins": fins, "monthly": monthly,
                                                                "misc": {"deposits": deposits}})
    with open(out_path, "wb") as f:
        f.write(raw)
    print(_js({**meta, "bytes": len(raw)}))


# ─────────────────────────── 전 종목 묶음(scan.npz) ───────────────────────────
# 🏁 신고가 후보·주요500 범위가 쓰는 코스피·코스닥 전 종목(약 2,500개) 일봉 300일 + 네이버 공식 52주 최고·최저.
# 예전엔 서버가 켜질 때마다 이걸 종목마다 네이버에서 받느라 몇 분 걸렸어요 → 아침에 한 번 받아 두고 앱은 바로 써요.
# 앱은 묶음의 마지막 봉 날짜(thru)까지만 '완성 봉'으로 믿고, 그 뒤는 예전처럼 새로 받아 붙여요(오늘 장중 봉은 실시간 시세).
SCAN_URL = os.environ.get("SCAN_URL") or "https://raw.githubusercontent.com/kimgun124-boop/very/warm/scan.npz"
SCAN_FORMAT = 1


def pack_scan(arrs: dict, h52: dict, uni: list, meta: dict) -> bytes:
    codes = sorted(c for c, a in arrs.items() if a is not None and len(a[0]))
    offs = np.cumsum([0] + [len(arrs[c][0]) for c in codes]).astype("int64")
    buf = io.BytesIO()
    cat = (lambda i, dt: np.concatenate([np.asarray(arrs[c][i], dtype=dt) for c in codes]) if codes else np.zeros(0, dt))
    np.savez_compressed(buf, codes=np.array(codes, dtype="U12"), offs=offs,
                        dates=cat(0, "datetime64[D]").astype("int32"),
                        **{k: cat(i + 1, "float32") for i, k in enumerate(_COLS)},
                        h52=np.array(_js(h52)), uni=np.array(_js(uni)), meta=np.array(_js({**meta, "format": SCAN_FORMAT})))
    return buf.getvalue()


def unpack_scan(raw: bytes) -> dict:
    z = np.load(io.BytesIO(raw), allow_pickle=False)
    meta = json.loads(str(z["meta"]))
    if meta.get("format") != SCAN_FORMAT:
        raise ValueError("전 종목 묶음 형식이 달라요")
    codes, offs = z["codes"].tolist(), z["offs"]
    dates = z["dates"].astype("datetime64[D]")
    cols = [z[k] for k in _COLS]
    arrs = {}
    for i, c in enumerate(codes):
        a, b = int(offs[i]), int(offs[i + 1])
        arrs[c] = (dates[a:b].copy(), *[col[a:b].copy() for col in cols])
    return {"arr": arrs, "h52": json.loads(str(z["h52"])), "uni": json.loads(str(z["uni"])), "meta": meta}


def load_scan(data_version: str | None = None, timeout: float = 25.0) -> dict:
    try:
        import requests
        if SCAN_URL.startswith("file://"):
            with open(SCAN_URL[7:], "rb") as f:
                raw = f.read()
        else:
            r = requests.get(SCAN_URL, timeout=timeout)
            r.raise_for_status()
            raw = r.content
        got = unpack_scan(raw)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{exc.__class__.__name__}: {exc}"[:200]}
    meta = got["meta"]
    age = time.time() - float(meta.get("built") or 0)
    if age > MAX_AGE:
        return {"error": f"전 종목 묶음이 오래됐어요({age / 3600:.0f}시간 전)", "meta": meta}
    if data_version and meta.get("data_version") and meta["data_version"] != data_version:
        return {"error": f"데이터 버전이 달라요({meta['data_version']} ≠ {data_version})", "meta": meta}
    got["age"] = age
    return got


def build_scan(out_path: str):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import data
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    uni = data.fetch_krx_universe()
    codes = sorted({u["code"] for u in uni})
    arrs = {}
    def one(c):
        for k in range(3):                              # 네이버가 잠깐 막으면 천천히 다시
            h = data.fetch_history(c, data.SCAN_BARS)[1]
            if h is not None and not h.empty:
                return c, data.bars_to_arr(h)
            time.sleep(1.5 * (k + 1))
        return c, None
    # 장중에 만들면 오늘 봉은 아직 완성 전이라 빼요(앱은 오늘 봉을 실시간 시세로 붙여요)
    now = data.now_kst()
    exp = data.last_session_day()
    from datetime import time as dtime
    done_today = now.weekday() >= 5 or now.time() >= dtime(15, 40) or now.time() < dtime(9, 0)
    thru = exp if done_today else np.busday_offset(exp, -1, roll="forward")
    with ThreadPoolExecutor(max_workers=8) as pool:
        for c, a in pool.map(one, codes):
            if a is not None:
                keep = a[0] <= thru
                if keep.any():
                    arrs[c] = tuple(x[keep] for x in a)
    t1 = time.time()
    h52 = data.fetch_official_52w(codes)
    try:
        ind = data.fetch_industry_map(codes)                 # 전 종목 공식 업종(주요500 범위 분류)
    except Exception:  # noqa: BLE001
        ind = ({}, {})
    t2 = time.time()
    last = max((a[0][-1] for a in arrs.values() if len(a[0])), default=None)
    meta = {"built": time.time(), "built_kst": data.now_kst().strftime("%Y-%m-%d %H:%M"), "data_version": data.DATA_VERSION,
            "n_uni": len(codes), "n_arr": len(arrs), "n_h52": len(h52), "last": str(last) if last is not None else None, "thru": str(thru),
            "secs": {"hist": round(t1 - t0), "h52": round(t2 - t1)}}
    if len(arrs) < 0.9 * len(codes) or len(codes) < 1500:
        raise SystemExit(f"받은 게 너무 적어서 저장 안 해요: {meta}")
    meta["n_ind"] = len(ind[0] or {})
    raw = pack_scan(arrs, h52, uni, {**meta, "ind": ind[0] or {}, "ind_diag": ind[1] or {}})
    with open(out_path, "wb") as f:
        f.write(raw)
    print(_js({**meta, "bytes": len(raw)}))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        build(sys.argv[2] if len(sys.argv) > 2 else "warm.npz")
    elif len(sys.argv) >= 2 and sys.argv[1] == "scan":
        build_scan(sys.argv[2] if len(sys.argv) > 2 else "scan.npz")
    else:
        print(__doc__)
