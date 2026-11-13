"""Render every main tab of app.py with AppTest and report exceptions/errors/warnings and element counts."""
import os, sys, json, time, traceback
from streamlit.testing.v1 import AppTest
APP = os.path.abspath(sys.argv[1])
OUT = sys.argv[2] if len(sys.argv) > 2 else None
os.chdir(os.path.dirname(APP))
sys.path.insert(0, os.path.dirname(APP))
src = open(APP, encoding="utf-8").read()
import ast
ns = {}
start = src.index("NAV_GROUPS = [")
end = src.index("]\n", src.index('("🚦 시장·이벤트"', start)) + 2
exec(src[start:end], ns)
tabs = [t for _, ts in ns["NAV_GROUPS"] for t in ts]
order = tabs + list(reversed(tabs))[:6]          # also switch back across groups
res = []
at = AppTest.from_file(os.path.abspath(APP), default_timeout=900)
t0 = time.time(); at.run(); print(f"first run {time.time()-t0:.1f}s exc={len(at.exception)}", flush=True)
def snap(tab, dt):
    exc = [e.value[:600] if hasattr(e, 'value') else str(e)[:600] for e in at.exception]
    err = [e.value[:300] for e in at.error]
    warn = [w.value[:200] for w in at.warning]
    info = [i.value[:200] for i in at.info]
    dfs = []
    for d in at.dataframe:
        try:
            v = d.value
            dfs.append({"shape": list(v.shape), "cols": [str(c) for c in v.columns][:30],
                        "head": v.head(8).astype(str).to_dict("records")})
        except Exception as e:  # noqa
            dfs.append({"err": str(e)[:200]})
    md = [m.value[:300] for m in at.markdown][:60]
    caps = [c.value[:300] for c in at.caption][:30]
    metrics = [(m.label, m.value) for m in at.metric][:30]
    return {"tab": tab, "sec": round(dt, 1), "exception": exc, "error": err, "warning": warn, "info": info,
            "dataframes": dfs, "markdown": md, "captions": caps, "metrics": metrics,
            "n_elems": len(list(at.main))}
res.append(snap("(초기)", time.time()-t0))
for tab in order:
    t0 = time.time()
    try:
        at.session_state["main_tab_sel"] = tab
        at.run()
        r = snap(tab, time.time()-t0)
    except Exception:
        r = {"tab": tab, "sec": round(time.time()-t0, 1), "exception": [traceback.format_exc()[-1500:]]}
    res.append(r)
    print(f"{tab:12s} {r['sec']:6.1f}s exc={len(r.get('exception',[]))} err={len(r.get('error',[]))} "
          f"warn={len(r.get('warning',[]))} dfs={len(r.get('dataframes',[]))} elems={r.get('n_elems')}", flush=True)
    for e in r.get("exception", [])[:2]:
        print("   EXC:", e[:400].replace("\n", " | "), flush=True)
    for e in r.get("error", [])[:3]:
        print("   ERR:", e[:200].replace("\n", " "), flush=True)
if OUT:
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
