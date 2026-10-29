"""밸류체인 신고가 보드.

실행: streamlit run app.py   (윈도우는 run.bat 더블클릭)
"""
from __future__ import annotations

import gc
import hashlib
import html
from urllib.parse import quote_plus
import re
import os

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

import importlib
import threading
import time
from datetime import date, time as dtime

# (메모리) 리눅스 메모리 할당기 설정 — 무거운 import 전에.
# 여러 스레드가 동시에 시세·일봉을 받으면 스레드마다 따로 메모리 칸(arena)을 만들어 쓰고 난 뒤에도 쥐고 있어요.
# 칸을 2개로 묶고(MALLOC_ARENA_MAX=2와 같음), 다 쓴 메모리는 malloc_trim으로 운영체제에 돌려줘요.
try:
    import ctypes as _ctypes
    _LIBC = _ctypes.CDLL("libc.so.6")
    _LIBC.mallopt(-8, 2)          # M_ARENA_MAX
except Exception:
    _LIBC = None


def malloc_trim() -> None:
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            pass


_TRIM_T = [0.0]       # (주의) app.py는 새로고침마다 다시 실행돼요 → 아래 import data 뒤에서 data 모듈 값으로 바꿔 끼워요


def malloc_info() -> dict:
    """할당기 상태(MB): used = 실제로 쓰는 중, free_held = 비었지만 운영체제에 안 돌려주고 쥐고 있는 양."""
    if _LIBC is None:
        return {}
    try:
        class _MI2(_ctypes.Structure):
            _fields_ = [(n, _ctypes.c_size_t) for n in ("arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks",
                                                        "fsmblks", "uordblks", "fordblks", "keepcost")]
        _LIBC.mallinfo2.restype = _MI2
        m = _LIBC.mallinfo2()
        return {"heap": (m.arena + m.hblkhd) / 1e6, "used": (m.uordblks + m.hblkhd) / 1e6, "free_held": m.fordblks / 1e6}
    except Exception:
        return {}


def proc_mem() -> dict:
    """/proc/self/status: 지금(VmRSS)·최고점(VmHWM) MB."""
    out = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(("VmRSS", "VmHWM")):
                    out[line.split(":")[0]] = int(line.split()[1]) / 1e3
    except OSError:
        pass
    return out


def _prune_frame(o) -> int:
    """pandas 3는 표에서 열을 꺼낼 때마다 '복사 없이 같이 쓰기' 기록(약한 참조)을 표 안에 쌓아요.
    오래 들고 있는 표(일봉·월봉·수급·실적)를 1분마다 읽으면 이 기록이 끝없이 늘어서 메모리가 불어났어요.
    has_reference()를 부르면 pandas가 이미 사라진 기록을 지워요(살아 있는 건 그대로라 결과는 같아요)."""
    n = 0
    refs = []
    try:
        if hasattr(o, "_mgr"):                       # 표·열(DataFrame·Series): 값 칸들 + 행 이름표 + 열 이름표
            refs += [getattr(b, "refs", None) for b in o._mgr.blocks]
            for ix in (getattr(o, "index", None), getattr(o, "columns", None)):
                refs.append(getattr(ix, "_references", None))
        else:                                        # 행 이름표(Index) 자체
            refs.append(getattr(o, "_references", None))
    except Exception:
        return 0
    for r in refs:
        try:
            if r is not None and len(r.referenced_blocks) > 1:
                k = len(r.referenced_blocks)
                r.has_reference()
                n += k - len(r.referenced_blocks)
        except Exception:
            pass
    return n


def prune_pandas_refs(root, depth: int = 0, seen: set | None = None) -> int:
    """root(사전·목록·튜플) 안의 표를 찾아 쌓인 기록을 지워요. 지운 개수를 돌려줘요."""
    import pandas as _pd
    seen = set() if seen is None else seen
    if depth > 5 or id(root) in seen:
        return 0
    seen.add(id(root))
    if isinstance(root, (_pd.DataFrame, _pd.Series, _pd.Index)):
        return _prune_frame(root)
    n = 0
    if isinstance(root, dict):
        for v in list(root.values()):
            n += prune_pandas_refs(v, depth + 1, seen)
    elif isinstance(root, (list, tuple)):
        for v in list(root):
            n += prune_pandas_refs(v, depth + 1, seen)
    return n


_STORE_ROOTS: list = []     # 오래 들고 있는 창고들(아래에서 채워요)


def trim_maybe(every: float = 60.0) -> None:
    """(메모리) 쓰고 난 메모리를 운영체제에 돌려줘요. 서버 전체에서 every초에 한 번만(몇 ms 걸려요).
    1분마다 창고 속 표의 pandas 기록을 정리하고, 3분마다는 서버에 살아 있는 표 전부를 한 번 훑어요."""
    import gc as _gc
    import time as _t
    now = _t.time()
    every = float(os.environ.get("TRIM_EVERY") or every)       # (테스트용)
    if now - _TRIM_T[0] < every:
        return
    _TRIM_T[0] = now
    stat = _TRIM_T[1] if len(_TRIM_T) > 1 else None
    pruned = 0
    for get in ([] if os.environ.get("PRUNE_OFF") else list(_STORE_ROOTS)):
        try:
            pruned += prune_pandas_refs(get())
        except Exception:
            pass
    if len(_TRIM_T) > 2 and now - _TRIM_T[2] > float(os.environ.get("SWEEP_EVERY") or 180) and not os.environ.get("PRUNE_OFF"):
        _TRIM_T[2] = now
        import pandas as _pd
        for o in _gc.get_objects():
            if isinstance(o, (_pd.DataFrame, _pd.Series, _pd.Index)):
                pruned += _prune_frame(o)
    try:
        data._mt_prune()                         # 이미 쌓인 끝난 스레드 목록도 비워요
    except Exception:
        pass
    _gc.collect()
    malloc_trim()
    if isinstance(stat, dict):
        stat["pruned"] = stat.get("pruned", 0) + pruned
        stat["runs"] = stat.get("runs", 0) + 1


import data
import holdings
import friends
import reports
import stocks as stock_list
import theme
import chart_draw
import recovery
import tracker
import cashflow
import views
import market as msig
import scout
import bigpicture
import zongbae
import bear_check

_TRIM_T = data.__dict__.setdefault("_TRIM_T", [0.0, {}, 0.0])   # [마지막 정리 시각, 정리 통계, 마지막 전체 훑기 시각]
while len(_TRIM_T) < 3:                                            # 예전 코드가 만든 [시각]만 있는 경우
    _TRIM_T.append({} if len(_TRIM_T) == 1 else 0.0)

try:     # (원인 찾기) 서버가 켜지고 라이브러리만 올린 직후 메모리 — data 모듈에 한 번만 적어 둬요(새로고침마다 다시 안 재요)
    with open("/proc/self/statm") as _f:
        data.__dict__.setdefault("_BOOT_RSS", int(_f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6)
except (OSError, ValueError):
    pass

st.set_page_config(page_title="밸류체인 신고가 보드", page_icon="📈", layout="wide")

# 크롬 자동 번역이 화면 글자를 바꿔 끼우면 'removeChild' 오류로 앱이 멈춰요. 한국어 페이지로 알리고 번역을 막아요.
_NO_TRANSLATE_JS = """<script>
try { const d = (window.parent && window.parent.document) || document;
  d.documentElement.setAttribute('lang', 'ko'); d.documentElement.setAttribute('translate', 'no');
  d.documentElement.classList.add('notranslate'); d.body.classList.add('notranslate');
  if (!d.querySelector('meta[name=google][content=notranslate]')) {
    const m = d.createElement('meta'); m.name = 'google'; m.content = 'notranslate'; d.head.appendChild(m); }
} catch (e) {}
</script>"""
try:        # 새 Streamlit: st.html로 바로 실행(components.html은 곧 없어져요)
    with st.container(key="no_translate", height=1, border=False):
        st.html(_NO_TRANSLATE_JS, unsafe_allow_javascript=True)
except TypeError:
    import streamlit.components.v1 as _components
    _components.html(_NO_TRANSLATE_JS, height=0)

# 카드·표의 종목 링크(?chart=코드)로 들어오면 그 종목 차트를 바로 보여줘요
_qp_chart = st.query_params.get("chart")
if _qp_chart:
    st.session_state["chart_code"] = _qp_chart
    st.session_state["detail_code"] = _qp_chart
    st.session_state["main_tab_sel"] = "🕯️ 차트"       # 카드·신고가 후보에서 누르면 차트 탭으로 바로
    del st.query_params["chart"]

# (진단) ?diag=종목코드 → 네이버 일봉 원본을 출처별로 나란히(공개 시세만, 저장 안 함). KRX·넥장 섞임 확인용
_qp_diag = str(st.query_params.get("diag") or "")
if re.fullmatch(r"\d{6}", _qp_diag):
    import requests as _rq
    st.markdown(f"### 진단: {_qp_diag} 일봉 원본")
    _end = data.now_kst().strftime("%Y%m%d")
    _start = (data.now_kst() - pd.Timedelta(days=20)).strftime("%Y%m%d")
    _tries = [
        ("fchart", data.FCHART_URL, {"symbol": _qp_diag, "timeframe": "day", "count": 8, "requestType": 0}),
        ("siseJson", data.SISEJSON_URL, {"symbol": _qp_diag, "requestType": 1, "startTime": _start, "endTime": _end,
                                         "timeframe": "day"}),
        ("api chart", f"https://api.stock.naver.com/chart/domestic/item/{_qp_diag}/day",
         {"startDateTime": _start + "0000", "endDateTime": _end + "2359"}),
        ("api chart KRX", f"https://api.stock.naver.com/chart/domestic/item/{_qp_diag}/day",
         {"startDateTime": _start + "0000", "endDateTime": _end + "2359", "tradeType": "KRX"}),
        ("m price", f"https://m.stock.naver.com/api/stock/{_qp_diag}/price", {"pageSize": 8, "page": 1}),
        ("m price KRX", f"https://m.stock.naver.com/api/stock/{_qp_diag}/price", {"pageSize": 8, "page": 1, "tradeType": "KRX"}),
        ("m price NXT", f"https://m.stock.naver.com/api/stock/{_qp_diag}/price", {"pageSize": 8, "page": 1, "tradeType": "NXT"}),
        ("stock api price KRX", f"{data.STOCK_API}/domestic/detail/{_qp_diag}/price", {"pageSize": 8, "page": 1, "tradeType": "KRX"}),
    ]
    for _nm, _url, _pr in _tries:
        try:
            _r = data.session.get(_url, params=_pr, headers=data.JSON_HEADERS, timeout=8)
            _txt = data.decode(_r.content)
            st.markdown(f"**{_nm}** · {_r.status_code} · {_r.url[:160]}")
            st.code(re.sub(r"\s+", " ", _txt)[:1500])
        except Exception as _e:
            st.markdown(f"**{_nm}** · 실패 {type(_e).__name__}")
    try:
        _rows = data.daily_prices(_qp_diag, pages=5)
        _bad = data.bar_chain_check(_rows)
        _mis = []
        for _n, _c in zip(_rows, _rows[1:]):
            if _n.get("chg") is not None and _c.get("close"):
                _mis.append(abs(_c["close"] / (_n["close"] - _n["chg"]) - 1) * 100)
        st.markdown(f"**연속성 점검** · {len(_rows)}일 · 어긋남 중앙값 {np.median(_mis) if _mis else 0:.2f}% · "
                    f"최대 {max(_mis) if _mis else 0:.2f}% · 1% 넘는 날 {sum(1 for x in _mis if x > 1)}일")
        st.code(str(_bad) or "깨진 봉 없음")
    except Exception as _e:
        st.markdown(f"연속성 점검 실패 {type(_e).__name__}: {_e}")
    st.stop()

REQUIRED = ("is_kr", "market_of", "quote_url", "INDEXES", "fetch_index_histories", "index_summary",
            "fetch_kr_shares", "fetch_fx", "format_krw", "fetch_investor_flows", "fetch_market_overview",
            "market_mood", "fetch_stock_trends", "trend_summary", "resolve_codes", "INST_DETAIL",
            "breakout_hold", "add_rs_ranks", "atr_pct", "BO_MODES",
            "fetch_financials", "fetch_financials_many", "earnings_trend",
            "fetch_monthlies", "newhigh_flags", "ma_signal", "index_rs",
            "buy_screen", "buy_checks", "position_plan", "BUY_RULES", "BUY_DEFAULTS", "EXTRA_KEYS",
            "add_leader_ranks", "sector_leaders", "momentum_engine", "next_leader_sectors", "NEXT_DEFAULTS",
            "nh_candidates", "nh_prefilter", "fetch_nxt_quotes", "fetch_official_52w", "NHC_DEFAULTS", "NHC_STATES", "volume_surges", "VS_DEFAULTS",
            "SCENARIO_PRESETS", "SCN_WINDOWS", "op_growth", "rotation_confirm", "sector_money_radar", "money_stats", "SCN_INFO", "basket_stats", "classify_scenario", "scenario_paths",
            "live_volume", "sector_money", "session_frac", "fetch_chart", "chart_with_live", "CHART_TF",
            "market_turnover", "fetch_market_turnover_hist", "fetch_krx_universe", "classify_industry", "fetch_theme_map", "fix_bars", "fetch_industry_map", "is_halted",
            "pullback_screen", "pullback_setup", "PB_DEFAULTS",
            "scan_frame", "bars_to_arr", "merge_arr", "arr_df", "last_session_day", "SCAN_BARS",
            "ia_buy_signals", "market_health", "fetch_deposits", "exposure_for", "IBD_LADDER")
DATA_VERSION = "2026-10-28-ibdexp"   # data.py의 DATA_VERSION과 같아야 해요


def _data_stale() -> bool:
    return any(not hasattr(data, n) for n in REQUIRED) or getattr(data, "DATA_VERSION", None) != DATA_VERSION


if _data_stale():
    # GitHub에서 파일을 바꾼 직후, 서버가 예전 data.py를 기억하고 있는 경우가 있어 한 번 새로 읽어 봅니다.
    data = importlib.reload(data)
    stock_list = importlib.reload(stock_list)
_missing = [n for n in REQUIRED if not hasattr(data, n)]
if getattr(data, "DATA_VERSION", None) != DATA_VERSION:
    _missing.append(f"버전 {DATA_VERSION}")
# 새로 생긴 모듈도 서버가 예전 내용을 기억하면 새로 읽어요(GitHub에 올린 직후 '... has no attribute' 방지)
MARKET_VERSION = "2026-10-28-lastftd"
_MOD_REQ = {"msig": ("MARKET_VERSION", "analyze", "action_text"),
            "scout": ("index_phase", "combine", "resilient", "candidates", "pick", "early_buys", "volume_profile", "PLAYBOOK"),
            "bigpicture": ("summary", "bucket_summary", "tag", "BIG"),
            "views": ("NJ_VIEWS", "KQ_VIEWS", "KF_VIEWS", "ZB_VIEWS", "AI_VIEWS"),
            "zongbae": ("ZB_VERSION", "VIEWS", "checks", "verdict", "candidates", "market_type", "summary_line")}
_MOD_VER = {"msig": ("MARKET_VERSION", MARKET_VERSION), "scout": ("SCOUT_VERSION", "2026-10-28-ibdexp")}


def _old_ver(alias, mod) -> bool:
    v = _MOD_VER.get(alias)
    return bool(v) and getattr(mod, v[0], None) != v[1]


for _alias, _attrs in _MOD_REQ.items():
    _mod = globals()[_alias]
    if any(not hasattr(_mod, a_) for a_ in _attrs) or _old_ver(_alias, _mod):
        globals()[_alias] = importlib.reload(_mod)
    _mod = globals()[_alias]
    if any(not hasattr(_mod, a_) for a_ in _attrs) or _old_ver(_alias, _mod):
        _missing.append(f"{_mod.__name__}.py 새 파일")
REPORTS_VERSION = "2026-10-06-autolearn"
if getattr(reports, "REPORTS_VERSION", None) != REPORTS_VERSION:
    reports = importlib.reload(reports)
if getattr(reports, "REPORTS_VERSION", None) != REPORTS_VERSION:
    _missing.append("reports.py 새 파일")
HOLDINGS_VERSION = "2026-10-29-zb"
if getattr(holdings, "HOLDINGS_VERSION", None) != HOLDINGS_VERSION:
    holdings = importlib.reload(holdings)
if getattr(holdings, "HOLDINGS_VERSION", None) != HOLDINGS_VERSION:
    _missing.append("holdings.py 새 파일")
if _missing:
    st.error("GitHub의 data.py가 예전 내용이에요. 저장소에서 data.py를 열어 새 파일 내용으로 바꿔 주세요. "
             "'data (1).py'처럼 이름이 바뀐 파일이 따로 올라가 있지 않은지도 확인해 주세요. "
             f"(없는 기능: {', '.join(_missing)})")
    st.stop()

STOCKS = stock_list.STOCKS
GROUP_ORDER = stock_list.GROUP_ORDER
SECTOR_ORDER = stock_list.SECTOR_ORDER
TAGS = getattr(stock_list, "TAGS", {})
for _s in STOCKS:
    _s.setdefault("tags", [])
    _s.setdefault("notes", [])


@st.cache_data(ttl=3600, show_spinner="새로 추가된 종목의 코드를 네이버에서 찾는 중이에요.")
def load_pending_codes(names: tuple[str, ...]):
    return data.resolve_codes(names)


# stocks.py에서 코드를 ""로 둔 종목은 네이버 검색으로 코드를 찾아 붙여요(1시간마다 다시 확인).
PENDING_NAMES = tuple(r[2] for r in getattr(stock_list, "PENDING", []))
PENDING_MISS: dict = {}
if PENDING_NAMES and hasattr(stock_list, "attach"):
    _resolved, PENDING_MISS = load_pending_codes(PENDING_NAMES)
    STOCKS = STOCKS + stock_list.attach(_resolved)

# 앱에서 직접 넣은 리포트(user_reports.json)의 종목을 보드에 합쳐요
@st.cache_resource
def _sync_from_github() -> str:
    """(Hugging Face 등 다른 서버용) 앱이 켜질 때 한 번, GitHub에 저장된 보유 종목·비밀번호·리포트를 받아와요.
    Streamlit 서버는 저장소를 그대로 받아 오지만, 다른 서버는 코드만 옮겨 오기 때문이에요."""
    def sec(k, d=""):
        try:
            v = st.secrets.get(k)
        except Exception:
            v = None
        return str(v or os.environ.get(k, d) or "")
    token = sec("GITHUB_TOKEN")
    if not token:
        return "GitHub 토큰 없음(이 서버의 파일만 사용)"
    repo, branch = sec("GITHUB_REPO", "kimgun124-boop/very"), sec("GITHUB_BRANCH") or None
    got = []
    base = os.path.dirname(os.path.abspath(__file__))
    for name in ("user_holdings.json", "user_settings.json", "user_reports.json", "user_tracker.json"):
        d = reports.fetch_github_json(name, token, repo, branch)
        if d is not None:
            try:
                with open(os.path.join(base, name), "w", encoding="utf-8") as f:
                    import json as _json
                    _json.dump(d, f, ensure_ascii=False, indent=1)
                got.append(name)
            except OSError:
                pass
    return "GitHub에서 받음: " + (", ".join(got) or "없음")


SYNC_STATUS = _sync_from_github()
USER_STORE = reports.load_local()
STOCKS, TAGS = reports.merge(STOCKS, TAGS, USER_STORE)
for _s in STOCKS:
    _s.setdefault("tags", [])
    _s.setdefault("notes", [])
if any(s["sector"] not in SECTOR_ORDER for s in STOCKS):
    SECTOR_ORDER = SECTOR_ORDER + sorted({s["sector"] for s in STOCKS} - set(SECTOR_ORDER))
if any(s["group"] not in GROUP_ORDER for s in STOCKS):
    GROUP_ORDER = GROUP_ORDER + sorted({s["group"] for s in STOCKS} - set(GROUP_ORDER))


def _secret(key: str, default: str = "") -> str:
    try:
        v = st.secrets.get(key)
    except Exception:
        v = None
    return str(v or os.environ.get(key, default) or "")


# ─────────────────────────── 🛡️ 보안: 차단 · 앱 전체 잠금 · 접속 기록 ───────────────────────────
SEC_MAX_FAIL = 5            # 한 IP가 연속 5번 틀리면
SEC_WAIT_SEC = 3600         # 1시간 못 들어와요
SEC_AUTOBAN = 15            # 하루에 15번 넘게 틀리면 영구 차단 목록에 올려요


@st.cache_resource
def _sec_store() -> dict:
    """모든 접속이 같이 쓰는 보안 기록(서버 메모리). 앱을 다시 켜면 비워져요(차단 목록은 파일에 저장)."""
    return {"fails": {}, "log": [], "lock": threading.Lock()}


def _client_ip() -> str:
    try:
        h = st.context.headers
        xff = h.get("X-Forwarded-For") or h.get("x-forwarded-for")
        if isinstance(xff, str) and xff:
            return xff.split(",")[0].strip()
    except Exception:
        pass
    try:
        ip = st.context.ip_address
        return ip if isinstance(ip, str) and ip else "알 수 없음"
    except Exception:
        return "알 수 없음"


def _client_ua() -> str:
    try:
        ua = st.context.headers.get("User-Agent")
        return ua[:160] if isinstance(ua, str) else ""
    except Exception:
        return ""


def _sec_log(event: str, ip: str, ok: bool | None = None):
    store = _sec_store()
    with store["lock"]:
        store["log"].append({"시각": data.now_kst().strftime("%m/%d %H:%M:%S"), "IP": ip, "일": event,
                             "결과": "" if ok is None else ("성공" if ok else "실패"), "브라우저": _client_ua()})
        del store["log"][:-400]


def _blocked_ips(cfg: dict) -> set:
    sec = {x.strip() for x in _secret("BLOCKED_IPS").split(",") if x.strip()}
    return sec | {b.get("ip") for b in cfg.get("blocked_ips", []) if b.get("ip")}


def _app_gate():
    """모든 화면보다 먼저: 차단된 IP는 막고, '앱 전체 잠금'이 켜져 있으면 비밀번호 없이는 아무것도 안 보여요."""
    ip = _client_ip()
    cfg = holdings.load_settings()
    if not st.session_state.get("_visit_logged"):
        st.session_state["_visit_logged"] = True
        _sec_log("접속", ip)
    if ip in _blocked_ips(cfg):
        st.error("접근이 차단된 주소예요.")
        st.stop()
    sec_lock = _secret("APP_LOCK").lower()
    lock_on = (bool(cfg.get("app_lock")) or sec_lock in ("on", "true", "1", "yes")) and sec_lock != "off"
    if not lock_on or not holdings.has_password(cfg) or st.session_state.get("app_unlocked") \
            or st.session_state.get("friend_id"):
        return
    store = _sec_store()
    now = time.time()
    rec = store["fails"].setdefault(ip, {"n": 0, "day": 0, "until": 0.0, "t0": now})
    if now - rec["t0"] > 86400:
        rec.update(day=0, t0=now)
    st.markdown('<div class="gate-wrap"><div class="gate-card"><div class="gate-logo">📈</div>'
                '<div class="gate-title">밸류체인 신고가 보드</div>'
                '<div class="gate-sub">비공개 앱이에요. 비밀번호를 넣어야 볼 수 있어요.</div></div></div>',
                unsafe_allow_html=True)
    c = st.columns([1, 1.2, 1])[1]
    with c:
        if rec["until"] > now:
            st.error(f"너무 많이 틀려서 잠시 막혔어요. {int((rec['until'] - now) // 60) + 1}분 뒤에 다시 시도하세요.")
            st.stop()
        pw = st.text_input("비밀번호", type="password", key="gate_pw", label_visibility="collapsed", placeholder="비밀번호")
        go = st.button("들어가기", type="primary", width="stretch", key="gate_go")
        tried = go or (pw and pw != st.session_state.get("gate_last"))
        if tried and pw:
            st.session_state["gate_last"] = pw
            if holdings.check_password(cfg, pw):
                rec.update(n=0)
                _sec_log("앱 잠금 해제", ip, True)
                st.session_state.update(app_unlocked=True, hold_unlocked=True, hold_seen=now)
                st.rerun()
            rec["n"] += 1
            rec["day"] += 1
            _sec_log("앱 비밀번호", ip, False)
            if rec["day"] >= SEC_AUTOBAN and ip != "알 수 없음":      # 괘씸한 시도: 영구 차단 목록으로
                cfg.setdefault("blocked_ips", []).append({"ip": ip, "at": data.now_kst().strftime("%Y-%m-%d %H:%M"),
                                                          "memo": f"하루 {rec['day']}번 비밀번호 틀림(자동 차단)"})
                holdings.save_settings_local(cfg)
                if _secret("GITHUB_TOKEN"):
                    reports.save_github(cfg, _secret("GITHUB_TOKEN"), _secret("GITHUB_REPO", "kimgun124-boop/very"),
                                        _secret("GITHUB_BRANCH") or None, path="user_settings.json")
            if rec["n"] >= SEC_MAX_FAIL:
                rec.update(n=0, until=now + SEC_WAIT_SEC)
                st.error(f"{SEC_MAX_FAIL}번 틀려서 {SEC_WAIT_SEC // 60}분 동안 막았어요.")
                st.stop()
            st.error(f"비밀번호가 달라요. ({rec['n']}/{SEC_MAX_FAIL})")
        lock = cfg.get("lock") or {}
        if lock.get("hint"):
            st.caption(f"💡 힌트: {lock['hint']}")
        st.caption("비밀번호를 잊었으면(앱 주인만): Streamlit Secrets에 APP_LOCK = \"off\"를 넣고 들어가서 "
                   "💼 내 보유 탭의 '비밀번호를 잊으셨나요?'로 다시 정한 뒤 APP_LOCK을 지우세요.")
        if _friends_key():
            with st.expander("👥 친구는 내 비밀번호로 들어가기"):
                _friend_login_form("gate")
    st.stop()


# ─────────────────────────── 👥 친구 계정(로그인·저장) ───────────────────────────
@st.cache_resource(show_spinner=False)
def _local_key() -> str:
    """Secrets에 아무것도 없을 때 쓰는 이 서버만의 열쇠. 저장소에는 안 올라가요(서버 안 파일)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".friends_local_key")
    try:
        with open(path, encoding="utf-8") as f:
            k = f.read().strip()
            if k:
                return k
    except OSError:
        pass
    import secrets as _sc
    k = "local:" + _sc.token_urlsafe(24)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(k)
    except OSError:
        pass
    return k


def _friends_keys() -> list[str]:
    """계정 파일 암호화 열쇠 후보. 맨 앞이 지금 쓰는 열쇠예요.
    USERS_KEY가 있으면 그걸, 없으면 이미 Secrets에 있는 GITHUB_TOKEN으로 열쇠를 만들어요(따로 넣을 것 없음).
    나중에 USERS_KEY를 넣거나 토큰을 바꿔도, 예전 열쇠(USERS_KEY_OLD)로 한 번 열어서 새 열쇠로 다시 잠가요."""
    keys = []
    for k in (_secret("USERS_KEY"), ("gh:" + _secret("GITHUB_TOKEN")) if _secret("GITHUB_TOKEN") else "",
              _secret("USERS_KEY_OLD"), _local_key()):
        if k and k not in keys:
            keys.append(k)
    return keys


def _friends_key() -> str:
    ks = _friends_keys()
    return ks[0] if ks else ""


def _open_blob(blob, keys):
    for k in keys:
        d = friends.decrypt(blob, k)
        if d is not None:
            return d
    return None


def _friends_db() -> dict:
    """열쇠가 바뀌면(토큰을 넣거나 바꾸면) 기억해 둔 것을 버리고 다시 읽어요."""
    sig = hashlib.sha256("|".join(_friends_keys()).encode("utf-8")).hexdigest()[:16]
    return _friends_db_load(sig)


@st.cache_resource(show_spinner=False)
def _friends_db_load(sig: str) -> dict:
    """모든 접속이 같이 쓰는 친구 계정·관심종목(서버 메모리 한 벌). 켜질 때 GitHub에서 한 번 받아요.
    열쇠가 맞지 않으면 data=None으로 두고, 저장도 막아서 남의 자료를 덮어쓰지 않아요."""
    keys = _friends_keys()
    if not keys:
        return {"data": None, "err": "nokey"}
    token = _secret("GITHUB_TOKEN")
    if token:
        blob = reports.fetch_github_json(friends.GH_PATH, token, _secret("GITHUB_REPO", "kimgun124-boop/very"),
                                         _secret("GITHUB_BRANCH") or None)
        if blob is not None:
            d = _open_blob(blob, keys)
            if d is None:
                return {"data": None, "err": "badkey"}
            friends.save_local(d, keys[0])
            return {"data": d, "err": ""}
    try:
        with open(friends.FRIENDS_FILE, encoding="utf-8") as f:
            import json as _json
            d = _open_blob(_json.load(f), keys)
    except OSError:
        d = friends.empty()
    except ValueError:
        d = None
    return {"data": d, "err": "" if d is not None else "badkey"}


def _friends_save() -> tuple[bool, str]:
    db = _friends_db()
    if db["data"] is None:
        return False, "계정 저장소를 열 수 없어서 저장하지 않았어요."
    key = _friends_key()
    with friends._LOCK:
        blob = friends.save_local(db["data"], key)
    token = _secret("GITHUB_TOKEN")
    if not token:
        return True, "이 서버에만 임시로 저장했어요(GITHUB_TOKEN이 없어서 앱을 다시 켜면 사라질 수 있어요)."
    return reports.save_github(blob, token, _secret("GITHUB_REPO", "kimgun124-boop/very"),
                               _secret("GITHUB_BRANCH") or None, path=friends.GH_PATH)


SIGNUP_PER_IP_DAY = 3      # 한 IP에서 하루에 만들 수 있는 계정 수


def _login_limited(ip: str) -> tuple[dict, bool]:
    """로그인 실패 횟수(IP별). 막혔으면 (기록, True)."""
    store = _sec_store()
    now = time.time()
    rec = store["fails"].setdefault("wl:" + ip, {"n": 0, "day": 0, "until": 0.0, "t0": now})
    if now - rec["t0"] > 86400:
        rec.update(day=0, t0=now)
    return rec, rec["until"] > now


def _friend_login_form(prefix: str, after=None) -> None:
    """비밀번호 하나로 들어가기 + 처음이면 비밀번호 만들기. 앱 잠금 화면과 ⭐ 내 종목 탭이 같이 써요."""
    db = _friends_db()
    if db["data"] is None:
        if db["err"] == "nokey":
            st.info("친구 계정 기능을 쓰려면 앱 주인이 Streamlit Secrets에 GITHUB_TOKEN을 넣어야 해요.")
        else:
            st.error("계정 저장소의 열쇠가 맞지 않아요. 앱 주인에게 알려 주세요(GitHub 토큰을 바꿨다면 Secrets에 USERS_KEY_OLD = \"gh:예전토큰\").")
        return
    ip = _client_ip()

    def fail(rec, what: str):
        rec["n"] += 1
        rec["day"] += 1
        _sec_log(what, ip, False)
        if rec["n"] >= SEC_MAX_FAIL:
            rec.update(n=0, until=time.time() + SEC_WAIT_SEC)
            return f"{SEC_MAX_FAIL}번 틀려서 {SEC_WAIT_SEC // 60}분 동안 막았어요."
        return None

    def enter(uid: str, what: str):
        _sec_log(what, ip, True)
        st.session_state["friend_id"] = uid
        for k in ("my_sel", "my_view", "my_g"):
            st.session_state.pop(k, None)
        if after:
            after()

    t_in, t_up = st.tabs(["🔑 들어가기", "🆕 처음이면 비밀번호 만들기"])
    with t_in:
        rec, blocked = _login_limited(ip)
        if blocked:
            st.error(f"너무 많이 틀려서 잠시 막혔어요. {int((rec['until'] - time.time()) // 60) + 1}분 뒤에 다시 시도하세요.")
        else:
            with st.form(f"{prefix}_login", clear_on_submit=True, border=False):
                pw = st.text_input("내 비밀번호", type="password", key=f"{prefix}_pw", placeholder="내 비밀번호")
                go = st.form_submit_button("들어가기", type="primary", width="stretch")
            if go and pw:
                uid = friends.login(db["data"], pw)
                if uid:
                    rec.update(n=0)
                    enter(uid, "친구 들어가기")
                    st.rerun()
                msg = fail(rec, "친구 비밀번호")
                st.error(msg or f"맞는 비밀번호가 없어요. ({rec['n']}/{SEC_MAX_FAIL})")
            st.caption("비밀번호를 잊었으면 앱 주인에게 임시 비밀번호를 받아서 들어온 뒤 바꾸세요.")
    with t_up:
        with st.form(f"{prefix}_signup", clear_on_submit=False, border=False):
            nick = st.text_input("이름(화면에 보일 이름, 선택)", key=f"{prefix}_nick", max_chars=20)
            npw = st.text_input(f"내 비밀번호({friends.MIN_PW}자 이상)", type="password", key=f"{prefix}_npw")
            npw2 = st.text_input("비밀번호 한 번 더", type="password", key=f"{prefix}_npw2")
            go = st.form_submit_button("만들고 바로 들어가기", type="primary", width="stretch")
        if go:
            rec, blocked = _login_limited(ip)
            store = _sec_store()
            day = data.now_kst().strftime("%Y%m%d")
            made = store.setdefault("signups", {}).get(f"{day}:{ip}", 0)
            if blocked:
                st.error("잠시 막혔어요. 나중에 다시 시도하세요.")
            elif made >= SIGNUP_PER_IP_DAY:          # 같은 곳에서 계정을 마구 만드는 것만 막아요
                st.error("오늘은 이 기기에서 더 만들 수 없어요. 내일 다시 하거나 앱 주인에게 말해 주세요.")
            elif npw != npw2:
                st.error("비밀번호 두 개가 달라요.")
            else:
                uid, err = friends.sign_up(db["data"], npw, nick)
                if err == "taken":       # 남의 비밀번호를 알아내는 데 쓰이지 않게 틀린 로그인처럼 세요
                    msg = fail(rec, "친구 만들기(쓰는 비밀번호)")
                    st.error(msg or "이 비밀번호는 쓸 수 없어요. 다른 비밀번호로 정해 주세요.")
                elif err:
                    st.error(err)
                else:
                    store["signups"][f"{day}:{ip}"] = made + 1
                    ok, msg = _friends_save()
                    enter(uid, "친구 만들기")
                    st.success("만들었어요! 다음부터는 이 비밀번호만 치면 들어와요." + ("" if ok else f" (주의: {msg})"))
                    time.sleep(0.6)
                    st.rerun()
        st.caption("아이디 없이 비밀번호로 내 계정을 찾아요. 그래서 남이 짐작하기 어렵게, 다른 사이트와 다른 비밀번호로 정하세요. "
                   "비밀번호는 암호화(해시)해서 저장돼서 앱 주인도 볼 수 없어요.")


UP, DOWN = "#D6333B", "#1F66C9"        # 한국식: 상승 빨강, 하락 파랑
NEW_HIGH_BG = "#FFF1C9"                # 신고가 행 강조
REFRESH = {"끄기": None, "30초": 30, "1분": 60, "5분": 300}
ALL_CODES = tuple(sorted({s["code"] for s in STOCKS}))
KR_CODES = tuple(c for c in ALL_CODES if data.is_kr(c))
OS_CODES = tuple(c for c in ALL_CODES if not data.is_kr(c))
UNIT = {"KRW": "원", "USD": "달러", "JPY": "엔", "EUR": "유로", "AUD": "호주달러", "HKD": "홍콩달러", "TWD": "대만달러", "GBp": "펜스",
        "CNY": "위안", "CHF": "스위스프랑"}


def fmt_price(value, currency: str) -> str:
    """원화는 정수, 외화는 소수 둘째 자리까지."""
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.0f}" if currency in ("KRW", "JPY") else f"{value:,.2f}"

st.markdown(
    """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css');
.stApp, .stApp p, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stMarkdown {
  font-family: 'Pretendard Variable', Pretendard, 'Malgun Gothic', sans-serif;
}
.stApp h1 { font-weight: 800; letter-spacing: -0.025em; }
.status { color: #51616C; font-size: 0.88rem; margin: -0.4rem 0 0.9rem; }
.radar { background: #17232E; border-radius: 14px; padding: 1.1rem 1.25rem 1.2rem; margin: 0 0 1.2rem; }
.radar-title { color: #FFFFFF; font-size: 1.05rem; font-weight: 700; }
.radar-rule { color: #9FB0BA; font-size: 0.84rem; margin: 0.15rem 0 0.85rem; }
.chips { display: flex; flex-wrap: wrap; gap: 0.6rem; }
.chip { background: #213241; border: 1px solid #304657; border-radius: 10px;
        padding: 0.6rem 0.8rem; min-width: 11rem; max-width: 22rem; }
.chip-top { display: flex; justify-content: space-between; align-items: baseline; gap: 0.8rem; }
.chip-group { color: #FFFFFF; font-weight: 700; font-size: 0.95rem; }
.chip-count { white-space: nowrap; flex-shrink: 0; color: #F2B134; font-weight: 800; font-size: 1.35rem; font-variant-numeric: tabular-nums; }
.chip-count small { font-size: 0.72rem; font-weight: 600; margin-left: 0.1rem; }
.chip-names { color: #B9C7CF; font-size: 0.8rem; line-height: 1.45; margin-top: 0.2rem; }
.radar-empty { color: #9FB0BA; font-size: 0.9rem; }
/* ── 사이드바(보기 설정) ── */
[data-testid="stSidebar"] { background: #F6F8F9; border-right: 1px solid #E3E8EB; }
[data-testid="stSidebar"] .block-container, [data-testid="stSidebarUserContent"] { padding-top: 1.2rem; }
.sb-title { font-size: 1.15rem; font-weight: 800; color: #16212B; letter-spacing: -0.02em;
            padding-bottom: 0.55rem; margin-bottom: 0.4rem; border-bottom: 2px solid #16212B; }
[data-testid="stSidebar"] label p { font-size: 0.8rem !important; font-weight: 700 !important; color: #3A4852 !important; }
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button { border-radius: 8px; }
[data-testid="stSidebar"] [role="radiogroup"]:has(> button[data-variant="segmented_control"]) {
  display: flex !important; flex-wrap: nowrap !important; width: 100%; }
[data-testid="stSidebar"] button[data-variant="segmented_control"] {
  flex: 1 1 0 !important; min-width: 0 !important; padding: 0.35rem 0.2rem !important; }
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[data-selected="true"] {
  background: #16212B !important; border-color: #16212B !important; }
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button[data-selected="true"] p {
  color: #FFFFFF !important; font-weight: 700; }
[data-testid="stSidebar"] [data-testid="stExpander"] details { background: #FFFFFF; }
[data-testid="stSidebar"] [class*="st-key-sb_box_"] { background: #FFFFFF; border-radius: 12px !important;
  border: 1px solid #E3E8EB !important; padding: 0.75rem 0.75rem 0.8rem !important; gap: 0.45rem !important; }
.sb-head { display: flex; justify-content: space-between; align-items: baseline;
  padding-bottom: 0.35rem; border-bottom: 1px solid #EEF1F3; margin-bottom: 0.1rem; }
.sb-head span { font-size: 0.86rem; font-weight: 800; color: #16212B; }
.sb-head em { font-style: normal; font-size: 0.72rem; font-weight: 700; color: #7A8A94; }
.sb-cat { font-size: 0.7rem; font-weight: 800; color: #7A8A94; letter-spacing: 0.02em; margin: 0.45rem 0 0;
  display: flex; align-items: center; gap: 0.4rem; }
.sb-cat::after { content: ""; flex: 1; height: 1px; background: #EEF1F3; }
.sb-cat { line-height: 1.1rem; height: 1.1rem; }
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(.sb-cat),
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(.sb-head) { margin-bottom: 0 !important; overflow: visible !important; }
[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.sb-cat) { height: auto !important; min-height: 1.4rem; overflow: visible !important; }
[data-testid="stSidebarHeader"] { height: 2.2rem !important; min-height: 2.2rem !important; padding-top: 0.4rem !important; }
[data-testid="stSidebar"] .st-key-sb_box_sector [data-testid="stHorizontalBlock"] { gap: 0.35rem !important; }
[data-testid="stSidebar"] .st-key-sb_box_sector [data-testid="stColumn"] { min-width: 0 !important; }
[data-testid="stSidebar"] .st-key-sb_box_sector button {
  min-height: 2.05rem; height: 2.05rem; padding: 0 0.45rem !important; border-radius: 8px;
  justify-content: flex-start; border: 1px solid #DCE3E7; background: #F6F8F9; }
[data-testid="stSidebar"] .st-key-sb_box_sector button p {
  font-size: 0.78rem !important; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
[data-testid="stSidebar"] .st-key-sb_box_sector button[kind="primary"] { background: #16212B; border-color: #16212B; }
[data-testid="stSidebar"] .st-key-sb_box_sector button[kind="primary"] p { color: #FFFFFF !important; }
[data-testid="stSidebar"] .st-key-sb_box_sector button:hover { border-color: #16212B; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary p { font-weight: 700; color: #16212B; }
.ch-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.35rem 0.7rem; margin: 0.3rem 0 0.4rem; }
.ch-head { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.8rem 1rem; margin: 0.6rem 0 0.7rem !important; }
.ch-head b { font-size: 1.7rem; font-weight: 800; color: #16212B; letter-spacing: -0.02em; }
.ch-code { font-size: 0.95rem; color: #7A8A94; }
.ch-px { font-size: 1.6rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.ch-px small { font-size: 1.05rem; font-weight: 700; margin-left: 0.25rem; }
.ch-sub { font-size: 0.95rem; color: #51616C; flex-basis: 100%; }
[class*="st-key-"][class*="chart_bar"], .st-key-chart_search {
  background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.75rem 0.9rem 0.85rem; }
[class*="st-key-"][class*="chart_bar"] label p, .st-key-chart_search label p {
  font-size: 0.9rem !important; font-weight: 800 !important; color: #16212B !important; }
[class*="st-key-"][class*="chart_bar"] button[data-variant="segmented_control"] {
  min-height: 2.5rem; padding: 0.35rem 0.4rem !important; }
[class*="st-key-"][class*="chart_bar"] button p { font-size: 0.98rem !important; font-weight: 700; }
[class*="st-key-"][class*="chart_bar"] button[data-selected="true"] { background: #16212B !important; border-color: #16212B !important; }
[class*="st-key-"][class*="chart_bar"] button[data-selected="true"] p { color: #FFFFFF !important; }
.st-key-chart_search input { font-size: 1rem !important; min-height: 2.6rem; }
.st-key-chart_search [data-baseweb="select"] div { font-size: 1rem; }
.st-key-chart_search [data-testid="stButtonGroup"] button { min-height: 2.2rem; }
.st-key-chart_search [data-testid="stButtonGroup"] button p { font-size: 0.95rem !important; }
.st-key-chart_search [data-testid="stButtonGroup"] button[data-selected="true"] { background: #16212B !important; }
.st-key-chart_search [data-testid="stButtonGroup"] button[data-selected="true"] p { color: #FFFFFF !important; }
/* ── 코스피·코스닥 거래대금 ── */
.tv-box { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 14px; padding: 0.9rem 1rem 0.8rem; margin: 0.2rem 0 1rem; }
.tv-title { display: flex; justify-content: space-between; align-items: baseline; gap: 0.6rem; margin-bottom: 0.7rem; }
.tv-title b { font-size: 1.08rem; font-weight: 800; color: #16212B; }
.tv-title span { font-size: 0.8rem; color: #7A8A94; font-weight: 600; }
.tv-chips { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.6rem; margin-bottom: 0.8rem; }
.tv-chip { background: #F6F8F9; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.65rem 0.85rem; }
.tv-chip.tot { background: #16212B; border-color: #16212B; }
.tv-chip.tot .tv-chip-h, .tv-chip.tot .tv-chip-f { color: #9FB0BA; }
.tv-chip.tot .tv-chip-v, .tv-chip.tot .tv-chip-s { color: #FFFFFF !important; }
.tv-chip-h { font-size: 0.8rem; color: #6B7A84; font-weight: 700; }
.tv-chip-v { font-size: 1.6rem; font-weight: 800; color: #16212B; font-variant-numeric: tabular-nums; line-height: 1.25; }
.tv-chip-v small { font-size: 0.9rem; margin-left: 0.1rem; }
.tv-chip-s { font-size: 0.92rem; font-weight: 800; margin-top: 0.1rem; }
.tv-chip-f { font-size: 0.76rem; color: #7A8A94; margin-top: 0.2rem; }
.tv-wrap { overflow-x: auto; }
.tv-tbl { width: 100%; border-collapse: collapse; font-size: 0.88rem; font-variant-numeric: tabular-nums; white-space: nowrap; }
.tv-tbl th { font-size: 0.76rem; color: #6B7A84; font-weight: 700; text-align: right; padding: 0.45rem 0.55rem;
  border-bottom: 2px solid #E3E8EB; background: #F6F8F9; }
.tv-tbl td { text-align: right; padding: 0.5rem 0.55rem; border-bottom: 1px solid #EEF1F3; color: #16212B; }
.tv-tbl .l { text-align: left; }
.tv-tbl td.now { font-weight: 800; }
.tv-tbl tbody tr:nth-child(2n) td { border-bottom: 2px solid #E3E8EB; }
.tv-x { min-width: 11rem; }
.tv-bar { position: relative; height: 6px; background: #EEF1F3; border-radius: 3px; margin-bottom: 0.2rem; }
.tv-bar i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; }
.tv-bar b { position: absolute; left: 50%; top: -3px; bottom: -3px; width: 1.5px; background: #16212B; }
.tv-x span { font-size: 0.82rem; font-weight: 700; }
.tv-note { font-size: 0.74rem; color: #7A8A94; margin-top: 0.55rem; line-height: 1.5; }
.st-key-no_translate { display: none !important; }
.rp-badges { display: flex; flex-wrap: wrap; gap: 0.45rem; margin: 0 0 0.7rem; }
.rp-badge { font-size: 0.82rem; border-radius: 999px; padding: 0.3rem 0.75rem; border: 1px solid #E3E8EB; background: #FFFFFF; color: #51616C; }
.rp-badge b { margin-right: 0.25rem; }
.rp-badge.on { border-color: #2E9D5B; color: #1E7A45; background: #EEF8F2; }
.rp-badge.off { border-color: #E3C7A0; color: #9A6417; background: #FDF6EC; }
[class*="st-key-rp_box_"] { background: #FFFFFF; border-radius: 12px !important; }
/* 메인 탭 색칠이 탭 안쪽의 작은 탭(차트·실적·뉴스 등)에는 번지지 않게 */
.st-key-main_body [role="tab"], .st-key-main_body [role="tab"]:nth-child(n) {
  --gc: #16212B; --gt: transparent; background: transparent !important; border: none !important;
  border-bottom: 2px solid transparent !important; border-radius: 0 !important; margin-left: 0 !important;
  padding: 0.4rem 0.8rem !important; }
.st-key-main_body [role="tab"]::before { content: none !important; }
.st-key-main_body [role="tab"][aria-selected="true"] { background: transparent !important;
  border-bottom: 2px solid #16212B !important; }
.st-key-main_body [role="tab"][aria-selected="true"] p { color: #16212B !important; font-weight: 800; }
.st-key-main_body [role="tab"] p { font-size: 0.9rem !important; }
/* 잠금 화면 */
.st-key-lock_box { background: #FFFFFF; border-radius: 14px !important; max-width: 560px; }
.lk-title { font-size: 1.2rem; font-weight: 800; color: #16212B; }
.lk-sub { font-size: 0.88rem; color: #51616C; margin: 0.2rem 0 0.6rem; }
.lk-code { border: 2px dashed #16212B; border-radius: 12px; padding: 0.8rem 1rem; background: #FFFBEA; margin: 0.4rem 0; max-width: 560px; }
.lk-code b { display: block; font-size: 0.9rem; }
.lk-code span { display: block; font-size: 1.6rem; font-weight: 800; letter-spacing: 0.12em; font-family: ui-monospace, Consolas, monospace; margin: 0.2rem 0; }
.lk-code em { font-style: normal; font-size: 0.82rem; color: #7A5A00; }
/* 보유 종목 탭 · 한눈에 표 */
.st-key-hold_tabs [role="tablist"] { flex-wrap: wrap; gap: 0.35rem !important; border-bottom: none !important; }
.st-key-main_body .st-key-hold_tabs [role="tab"],
.st-key-main_body .st-key-hold_tabs [role="tab"]:nth-child(n) {
  border: 1px solid #DCE3E7 !important; border-radius: 999px !important; background: #FFFFFF !important;
  padding: 0.35rem 0.85rem !important; }
.st-key-main_body .st-key-hold_tabs [role="tab"][aria-selected="true"] {
  background: #16212B !important; border-color: #16212B !important; }
.st-key-main_body .st-key-hold_tabs [role="tab"][aria-selected="true"] p { color: #FFFFFF !important; }
.st-key-main_body .st-key-hold_tabs [role="tab"] p { font-size: 0.92rem !important; font-weight: 700; }
.ho-wrap { overflow-x: auto; background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 14px; padding: 0.3rem 0.5rem; }
.ho-tbl { width: 100%; border-collapse: collapse; font-size: 0.9rem; white-space: nowrap; font-variant-numeric: tabular-nums; }
.ho-tbl th { font-size: 0.76rem; color: #6B7A84; text-align: right; padding: 0.55rem 0.6rem; border-bottom: 2px solid #E3E8EB; }
.ho-tbl td { text-align: right; padding: 0.6rem; border-bottom: 1px solid #EEF1F3; color: #16212B; }
.ho-tbl .l { text-align: left; }
.ho-tbl tr:last-child td { border-bottom: none; }
.ho-code { font-size: 0.75rem; color: #7A8A94; margin-left: 0.35rem; }
.ho-badge { font-size: 0.8rem; font-weight: 800; padding: 0.2rem 0.6rem; border-radius: 999px; border: 1.5px solid; }
.ho-sd { min-width: 8rem; } .ho-sd span { font-weight: 800; font-size: 0.85rem; }
.ho-bar { height: 5px; background: #FDECEC; border-radius: 3px; margin-bottom: 0.2rem; }
.ho-bar i { display: block; height: 100%; background: #2E9D5B; border-radius: 3px; }
.ho-memo { color: #51616C; max-width: 16rem; overflow: hidden; text-overflow: ellipsis; }
/* 앱 전체 잠금 화면 */
.gate-wrap { display: flex; justify-content: center; margin: 12vh 0 1rem; }
.gate-card { text-align: center; }
.gate-logo { font-size: 2.6rem; }
.gate-title { font-size: 1.5rem; font-weight: 800; color: #16212B; margin-top: 0.3rem; }
.gate-sub { font-size: 0.92rem; color: #51616C; margin-top: 0.3rem; }
.cap-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 0.5rem; margin: 0.4rem 0; }
.cap-row > div { background: #F6F8F9; border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.55rem 0.75rem; }
.cap-row em { display: block; font-style: normal; font-size: 0.76rem; color: #6B7A84; font-weight: 700; }
.cap-row b { font-size: 1.15rem; font-variant-numeric: tabular-nums; }
.cap-row span { display: block; font-size: 0.82rem; font-weight: 800; }
.pr-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 0.6rem; }
.pr-card { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.7rem 0.9rem; }
.pr-card b { font-size: 0.95rem; color: #16212B; }
.pr-card ul { margin: 0.3rem 0 0 1rem; padding: 0; font-size: 0.85rem; color: #33424C; }
.ho-acct { font-size: 0.72rem; margin-left: 0.35rem; color: #51616C; }
/* 깊은 분석 */
.dp-head { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; margin: 0.5rem 0 0.4rem; }
.dp-score { color: #FFFFFF; font-weight: 800; border-radius: 8px; padding: 0.25rem 0.6rem; font-size: 0.95rem; }
.dp-chip { border: 1.5px solid; border-radius: 999px; padding: 0.15rem 0.55rem; font-size: 0.8rem; font-weight: 700; background: #FFFFFF; }
.dp-head em { font-style: normal; font-size: 0.72rem; color: #7A8A94; margin-left: auto; }
.dp-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 0.5rem; margin-bottom: 0.5rem; }
.dp-block { border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.55rem 0.7rem; background: #FFFFFF; }
.dp-block > b { font-size: 0.88rem; }
.dp-row { margin-top: 0.3rem; } .dp-row span { display: block; font-size: 0.82rem; font-weight: 800; }
.dp-row em { font-style: normal; font-size: 0.8rem; color: #51616C; }
.dp-row.bad span { color: #B3261E; } .dp-row.good span { color: #1E7A45; }
.dp-pyr { border: 1px dashed #6B3FC4; border-radius: 10px; padding: 0.55rem 0.7rem; background: #FAF7FF; margin-bottom: 0.5rem; }
.dp-pyr b { font-size: 0.88rem; color: #4B2A99; } .dp-pyr div { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.35rem; }
.dp-cond { font-size: 0.78rem; border-radius: 6px; padding: 0.15rem 0.45rem; }
.dp-cond.ok { background: #EEF8F2; color: #1E7A45; } .dp-cond.no { background: #F3F4F6; color: #7A8A94; }
.dp-plan { border-left: 4px solid #2E6B6F; background: #F4F9F9; border-radius: 6px; padding: 0.55rem 0.8rem; margin-bottom: 0.5rem; }
.dp-plan b { font-size: 0.88rem; } .dp-plan div { display: flex; gap: 0.6rem; margin-top: 0.3rem; font-size: 0.84rem; }
.dp-plan span { min-width: 5.5rem; font-weight: 800; } .dp-plan em { font-style: normal; color: #33424C; }
/* ── 💼 내 보유 ── */
[class*="st-key-hold_card_"] { background: #FFFFFF; border-radius: 14px !important; }
.hd-top { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.hd-name { font-size: 1.35rem; font-weight: 800; color: #16212B; }
.hd-code { font-size: 0.85rem; color: #7A8A94; margin: 0 0.5rem 0 0.35rem; }
.hd-px { font-size: 1.2rem; font-weight: 800; margin-right: 0.5rem; font-variant-numeric: tabular-nums; }
.hd-pnl { font-size: 1.05rem; font-weight: 800; }
.hd-verdict { font-size: 0.95rem; font-weight: 800; padding: 0.35rem 0.8rem; border-radius: 999px; border: 1.5px solid; }
.hd-sub { font-size: 0.85rem; color: #51616C; margin: 0.3rem 0 0.6rem; }
.hd-checks { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 0.4rem; }
.hd-chk { border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.45rem 0.65rem; background: #F9FAFB; }
.hd-chk span { display: block; font-size: 0.84rem; font-weight: 800; color: #16212B; }
.hd-chk em { font-style: normal; font-size: 0.8rem; color: #51616C; }
.hd-chk.bad { border-color: #F2B8BB; background: #FDF3F3; }
.hd-chk.warn { border-color: #EED9A8; background: #FFFAEE; }
.hd-chk.good { border-color: #CBE7D5; background: #F4FBF6; }
.hd-act { margin: 0.6rem 0 0.4rem; padding: 0.55rem 0.8rem; border-left: 4px solid #16212B; background: #F6F8F9; border-radius: 6px; }
.hd-act b { font-size: 0.86rem; } .hd-act ul { margin: 0.2rem 0 0 1rem; padding: 0; font-size: 0.86rem; }
.hd-news { display: flex; flex-direction: column; gap: 0.3rem; }
.hd-news a { display: block; text-decoration: none; padding: 0.4rem 0.55rem; border: 1px solid #EEF1F3; border-radius: 8px; background: #FFFFFF; }
.hd-news a:hover { border-color: #16212B; }
.hd-news span { display: block; font-size: 0.88rem; color: #16212B; font-weight: 600; }
.hd-news em { font-style: normal; font-size: 0.74rem; color: #7A8A94; }
/* ── 공통 정리 ── */
[data-testid="stCaptionContainer"] p { color: #7A8A94 !important; font-size: 0.78rem !important; }
.stApp h4 { font-size: 1.05rem !important; font-weight: 800 !important; margin: 0.6rem 0 0.3rem !important; }
[data-testid="stTabs"] [role="tablist"] { gap: 0.2rem; border-bottom: 1px solid #DCE3E7; }
[data-testid="stTabs"] button[role="tab"] { padding: 0.45rem 0.8rem; border-radius: 8px 8px 0 0; }
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] { background: #FFFFFF; }
[data-testid="stTabs"] button[role="tab"] p { font-weight: 700; }
[data-testid="stExpander"] details { border: 1px solid #E3E8EB !important; border-radius: 10px !important; background: #FFFFFF; }
[data-testid="stExpander"] summary p { font-size: 0.86rem; color: #51616C; }
[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.55rem 0.8rem; }
[data-testid="stMetricLabel"] p { font-size: 0.78rem !important; color: #6B7A84 !important; }
[data-testid="stMetricValue"] { font-size: 1.45rem !important; font-weight: 800 !important; }
[data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; }
.sec-h { display: flex; align-items: baseline; justify-content: space-between; gap: 0.6rem; margin: 0.2rem 0 0.5rem; }
.sec-h b { font-size: 1.12rem; font-weight: 800; color: #16212B; }
.sec-h span { font-size: 0.78rem; color: #7A8A94; }
/* 돈의 방향 */
.verdict { border-left: 6px solid var(--vc); background: #FFFFFF; border-radius: 12px; padding: 0.8rem 1rem;
  box-shadow: 0 1px 2px rgba(0,0,0,.04); margin-bottom: 0.7rem; }
.v-top { display: flex; justify-content: space-between; gap: 0.5rem; font-size: 0.76rem; color: #7A8A94; }
.v-lab { font-weight: 700; letter-spacing: .02em; }
.v-title { font-size: 1.55rem; font-weight: 900; color: var(--vc); line-height: 1.25; margin-top: 0.1rem; }
.v-act { font-size: 0.95rem; font-weight: 600; color: #26343E; margin-top: 0.15rem; }
.tiles-wrap { display: grid; grid-template-columns: 2fr 1fr; gap: 0.8rem; margin-bottom: 0.8rem; }
.tiles-h { font-size: 0.8rem; color: #51616C; margin-bottom: 0.3rem; font-weight: 600; }
.tiles-h b { color: #16212B; } .tiles-h small { color: #8A979F; font-weight: 500; }
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.4rem; }
.tiles.t2 { grid-template-columns: repeat(2, 1fr); }
.tile { position: relative; background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.45rem 0.55rem; }
.tile.ok { border-color: #9ED3B2; background: #F1FAF4; } .tile.no { border-color: #F0C2C4; background: #FDF4F4; }
.t-ic { position: absolute; top: 0.35rem; right: 0.5rem; font-weight: 900; font-size: 0.85rem; }
.tile.ok .t-ic { color: #1B8A4B; } .tile.no .t-ic { color: #C0262E; } .tile.na .t-ic { color: #8A979F; }
.t-lb { font-size: 0.7rem; color: #6B7A84; } .t-v { font-size: 1.05rem; font-weight: 800; color: #16212B;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
.lead-chips { display: flex; flex-wrap: wrap; gap: 0.3rem; margin-top: 0.4rem; }
.lc { font-size: 0.72rem; font-weight: 700; padding: 0.12rem 0.45rem; border-radius: 999px; }
.lc.ok { background: #E3F4EA; color: #1B6B3E; } .lc.no { background: #FBE3E4; color: #A2343B; }
.mv-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem 0.6rem; background: #FFFFFF;
  border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.55rem 0.8rem; margin-bottom: 0.5rem; font-size: 0.88rem; }
.mv { font-weight: 800; } .mv.out { color: #1F66C9; } .mv.in { color: #D6333B; }
.mv-arrow { font-size: 1.1rem; color: #8A979F; font-weight: 800; }
.mv-total { margin-left: auto; font-size: 0.78rem; color: #6B7A84; }
.mini { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 10px; overflow: hidden; }
.mini-row { display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; padding: 0.45rem 0.7rem;
  border-top: 1px solid #F0F3F5; font-size: 0.86rem; }
.mini-row:first-child { border-top: none; }
.mini-row small { display: block; color: #7A8A94; font-size: 0.72rem; margin-top: 0.05rem; }
.mini-row span { white-space: nowrap; font-weight: 700; font-size: 0.8rem; color: #26343E; }
@media (max-width: 640px) {
  .tiles-wrap { grid-template-columns: 1fr; }
  .tiles { grid-template-columns: repeat(2, 1fr); }
  .v-title { font-size: 1.3rem; }
  .v-top { flex-direction: column; gap: 0; }
  [data-testid="stMetric"] { padding: 0.4rem 0.55rem; }
  [data-testid="stMetricValue"] { font-size: 1.15rem !important; }
  [data-testid="stTabs"] button[role="tab"] { padding: 0.35rem 0.5rem; }
}
.st-key-mobile_view { display: none; }
.kcards { display: flex; flex-direction: column; gap: 0.45rem; }
.stApp a.kcard { display: block; background: #FFFFFF; border: 1px solid #DCE3E7; border-radius: 12px;
  padding: 0.65rem 0.8rem 0.6rem; text-decoration: none; color: #16212B; }
.stApp a.kcard.hot { background: #FFF8E3; border-color: #EFD48A; }
.k-top { display: flex; justify-content: space-between; align-items: baseline; gap: 0.5rem; }
.k-name { font-weight: 800; font-size: 1.02rem; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.k-crown { font-size: 0.68rem; font-weight: 800; background: #F2B134; color: #16212B; border-radius: 6px; padding: 0.05rem 0.35rem;
  vertical-align: 2px; }
.k-px { font-weight: 700; font-size: 0.95rem; white-space: nowrap; font-variant-numeric: tabular-nums; }
.k-chg { font-weight: 800; margin-left: 0.25rem; }
.k-keys { display: grid; grid-template-columns: 1fr 1.45fr 0.6fr; gap: 0.35rem; margin: 0.5rem 0 0.4rem; }
.k-keys div { background: #F2F5F7; border-radius: 8px; padding: 0.3rem 0.45rem; min-width: 0; }
.k-keys small { display: block; font-size: 0.64rem; color: #6B7A84; line-height: 1.2; }
.k-keys b { display: block; font-size: 0.92rem; font-weight: 800; color: #16212B; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.k-keys b.hot { color: #D6333B; } .k-keys b.near { color: #C27C0E; }
.k-keys b.first { color: #D6333B; } .k-keys b.brk { color: #B8452C; } .k-keys b.off { color: #6B7A84; font-weight: 700; }
.k-keys b.rs { color: #D6333B; }
.k-sub { font-size: 0.74rem; color: #51616C; line-height: 1.45; }
.k-flow { display: flex; justify-content: space-between; gap: 0.5rem; font-size: 0.72rem; color: #51616C; margin-top: 0.15rem;
  font-variant-numeric: tabular-nums; }
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-left: 0.7rem !important; padding-right: 0.7rem !important; }
  .st-key-mobile_view div[role="radiogroup"] { gap: 0.25rem 0.6rem; }
  .st-key-mobile_view div[role="radiogroup"] label p { font-size: 0.8rem; }
  [data-testid="stTabs"] button p { font-size: 0.85rem; }
}

.m-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.4rem; margin: 0 0 0.8rem; }
.m-kpis div { background: #E7EDEF; border-radius: 10px; padding: 0.5rem 0.3rem; text-align: center; }
.m-kpis span { display: block; font-size: 0.68rem; color: #51616C; line-height: 1.25; }
.m-kpis b { font-size: 1.15rem; font-weight: 800; color: #16212B; font-variant-numeric: tabular-nums; }
.cards { display: flex; flex-direction: column; gap: 0.5rem; }
.stApp a.card { display: block; background: #FFFFFF; border: 1px solid #DCE3E7; border-radius: 12px;
  padding: 0.7rem 0.85rem; text-decoration: none; color: #16212B; }
.stApp a.card.hot { background: #FFF6DA; border-color: #EFD48A; }
.c-top, .c-mid, .c-meta { display: flex; justify-content: space-between; align-items: baseline; gap: 0.6rem; }
.c-name, .c-price { font-weight: 700; font-size: 1.02rem; }
.c-price, .c-chg, .c-meta { font-variant-numeric: tabular-nums; }
.c-mid { font-size: 0.78rem; color: #51616C; margin-top: 0.1rem; }
.c-chg { font-weight: 700; }
.c-bar { height: 5px; background: #E3E9EC; border-radius: 3px; margin: 0.5rem 0 0.35rem; overflow: hidden; }
.c-bar i { display: block; height: 100%; background: #2E6B6F; border-radius: 3px; }
.c-meta { font-size: 0.78rem; color: #51616C; }
.c-meta b { color: #16212B; }
.c-desc { font-size: 0.76rem; color: #6B7A84; margin-top: 0.3rem; line-height: 1.4;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.c-tag { display: inline-block; font-size: 0.66rem; font-weight: 700; padding: 0.05rem 0.4rem;
  border-radius: 6px; margin-left: 0.35rem; vertical-align: 2px; }
.c-tag.new { background: #F2B134; color: #16212B; }
.c-tag.al { background: #D9E8E6; color: #2E6B6F; }
@media (max-width: 640px) {
  .st-key-desk_kpis, .st-key-desk_table { display: none !important; }
  .st-key-mobile_view { display: flex !important; }
  .stApp h1 { font-size: 1.6rem !important; }
  .radar { padding: 0.85rem 0.9rem; }
  .chip { flex: 1 1 calc(50% - 0.3rem); max-width: none; min-width: 0; padding: 0.5rem 0.6rem; }
  .chip-group { font-size: 0.85rem; }
  .chip-count { font-size: 1.1rem; }
  .chip-names { font-size: 0.72rem; }
  [data-testid="stMainBlockContainer"] { padding-top: 2.5rem; }
  [data-testid="stMetricValue"] { font-size: 1.35rem !important; }
}
.idx-row { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 0.6rem; margin: 0 0 0.5rem; }
.idx-badge.neutral { background: #E7EDEF; color: #51616C; }
.idx-row .idx-val { font-size: 1.15rem; }
.idx-row .idx-chg { display: block; margin-left: 0; }
.flow { background: #FFFFFF; border: 1px solid #DCE3E7; border-radius: 12px; padding: 0.7rem 0.9rem; margin: 0 0 0.5rem; }
.flow-title { font-size: 0.85rem; color: #51616C; margin-bottom: 0.35rem; }
.flow table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.flow th { font-size: 0.78rem; color: #8A979F; font-weight: 600; text-align: right; padding: 0.15rem 0.3rem; }
.flow th:first-child, .flow td:first-child { text-align: left; }
.flow td { font-size: 0.92rem; font-weight: 700; text-align: right; padding: 0.25rem 0.3rem; border-top: 1px solid #EEF2F4; }
.flow td small { display: block; font-size: 0.7rem; font-weight: 500; color: #8A979F; }
.idx { background: #FFFFFF; border: 1px solid #DCE3E7; border-radius: 12px; padding: 0.7rem 0.9rem; }
.idx-name { font-size: 0.85rem; color: #51616C; }
.idx-name small { margin-left: 0.3rem; color: #8A979F; }
.idx-val { font-size: 1.35rem; font-weight: 800; color: #16212B; font-variant-numeric: tabular-nums; }
.idx-chg { font-size: 0.85rem; font-weight: 700; margin-left: 0.35rem; font-variant-numeric: tabular-nums; }
.idx-badge { display: inline-block; font-size: 0.72rem; font-weight: 700; padding: 0.1rem 0.45rem;
  border-radius: 6px; margin-top: 0.25rem; font-variant-numeric: tabular-nums; }
.idx-badge.on { background: #D9E8E6; color: #2E6B6F; }
.idx-badge.off { background: #F4E1E2; color: #A2343B; }
.idx-err { font-size: 0.85rem; color: #8A979F; margin-top: 0.3rem; }
@media (max-width: 640px) {
  .idx-row { gap: 0.35rem; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .flow { padding: 0.55rem 0.6rem; }
  .flow td { font-size: 0.8rem; }
  .idx { padding: 0.5rem 0.55rem; }
  .idx-name { font-size: 0.74rem; }
  .idx-name small { display: none; }
  .idx-val { font-size: 1.0rem; }
  .idx-chg { display: block; margin-left: 0; font-size: 0.76rem; }
  .idx-badge { font-size: 0.62rem; padding: 0.05rem 0.3rem; }
}
.sig { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.7rem 0.9rem;
       margin-bottom: 0.6rem; display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.6rem 1.2rem; }
.sig-mk { display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem 0.9rem; }
.sig-name { font-weight: 800; font-size: 1rem; color: #16212B; min-width: 3.2rem; }
.sig-item { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.8rem; color: #51616C; }
.tl { display: inline-flex; gap: 3px; background: #1C2530; border-radius: 999px; padding: 3px 6px; }
.tl i { width: 15px; height: 15px; border-radius: 50%; background: #D9DEE2; font-style: normal; font-size: 0.6rem;
        font-weight: 800; color: #FFFFFF; display: inline-flex; align-items: center; justify-content: center; }
.tl i.G { background: #1FA75A; } .tl i.Y { background: #F2B134; color: #3A2A00; } .tl i.R { background: #D6333B; }
.sig-txt.G { color: #1B8A4B; font-weight: 700; } .sig-txt.Y { color: #A87400; font-weight: 700; }
.sig-txt.R { color: #C0262E; font-weight: 700; }
.sig-rs { font-size: 0.8rem; color: #51616C; } .sig-rs b { color: #16212B; font-size: 0.92rem; }
@media (max-width: 640px) { .sig { grid-template-columns: 1fr; } }
.mk-row { display: grid; grid-template-columns: 1fr 1fr 1.05fr; gap: 0; background: #FFFFFF;
  border: 1px solid #E3E8EB; border-radius: 14px; overflow: hidden; margin: 0 0 0.6rem; }
.mk { padding: 0.95rem 1.2rem 0.85rem; border-right: 1px solid #EEF1F3; }
.mk-name { font-size: 0.98rem; font-weight: 700; color: #16212B; display: flex; justify-content: space-between; align-items: center; }
.mk-name small { font-size: 0.72rem; font-weight: 500; color: #8A979F; }
.mk-val { font-size: 1.55rem; font-weight: 800; color: #16212B; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.mk-chg { font-size: 0.95rem; font-weight: 700; margin-left: 0.45rem; font-variant-numeric: tabular-nums; }
.mk-bar { display: flex; height: 5px; border-radius: 3px; overflow: hidden; margin: 0.65rem 0 0.35rem; background: #E3E9EC; }
.mk-bar i { display: block; height: 100%; }
.mk-cnt { display: flex; justify-content: space-between; font-size: 0.8rem; font-variant-numeric: tabular-nums; color: #6B7A84; }
.mk-cnt .u { color: #D6333B; } .mk-cnt .d { color: #1F66C9; }
.mk-sub { display: flex; justify-content: space-between; gap: 0.4rem; margin-top: 0.45rem; font-size: 0.74rem; color: #8A979F;
  font-variant-numeric: tabular-nums; }
.mk-sub b { font-weight: 700; }
.mk-b60 { display: flex; justify-content: space-between; align-items: center; gap: 0.4rem; margin-top: 0.4rem;
  padding: 0.3rem 0.5rem; border-radius: 6px; background: #F3F6F7; font-size: 0.76rem; color: #51616C; }
.mk-b60 b { font-size: 0.92rem; font-weight: 800; }
.mk-b60 .gauge { flex: 1; height: 6px; border-radius: 3px; background: #E2E8EB; position: relative; overflow: hidden; min-width: 40px; }
.mk-b60 .gauge i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; }
.mood { background: #FFF6EC; padding: 0.9rem 1.1rem 0.8rem; }
.mood-top { display: flex; justify-content: space-between; align-items: center; font-weight: 700; color: #16212B; font-size: 0.95rem; }
.mood-top .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: #F29A18; margin-right: 0.35rem; }
.mood-top .lbl { color: #E0781A; font-weight: 800; }
.mood-gauge { position: relative; height: 4px; border-radius: 2px; margin: 0.6rem 0 0.7rem;
  background: linear-gradient(90deg, #1F66C9, #B9C4CC 50%, #D6333B); }
.mood-gauge i { position: absolute; top: -4px; width: 12px; height: 12px; margin-left: -6px; border-radius: 50%;
  background: #FFFFFF; border: 2px solid #F29A18; }
.mood-row { display: grid; grid-template-columns: 3.2rem 1fr 5.4rem; align-items: center; gap: 0.5rem;
  font-size: 0.8rem; color: #51616C; margin-top: 0.3rem; }
.mood-row .bar { position: relative; height: 5px; background: #ECE4DA; border-radius: 3px; }
.mood-row .bar i { position: absolute; top: 0; height: 100%; border-radius: 3px; }
.mood-row b { text-align: right; font-variant-numeric: tabular-nums; }
.mood-note { font-size: 0.68rem; color: #9A8F84; margin-top: 0.45rem; }
.inst { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 0.85rem; }
.inst th { color: #8A979F; font-weight: 600; text-align: right; padding: 0.2rem 0.35rem; font-size: 0.76rem; white-space: nowrap; }
.inst td { text-align: right; padding: 0.25rem 0.35rem; border-top: 1px solid #EEF2F4; font-weight: 600; white-space: nowrap; }
.inst th:first-child, .inst td:first-child { text-align: left; }
.c-flow { font-size: 0.74rem; color: #51616C; margin-top: 0.2rem; font-variant-numeric: tabular-nums; }
.notes { margin: 0.2rem 0 0; padding-left: 1.2rem; font-size: 0.88rem; line-height: 1.55; color: #26343E; }
.notes li { margin: 0.12rem 0; }
@media (max-width: 640px) {
  .mk-row { grid-template-columns: 1fr 1fr; }
  .mood { grid-column: 1 / -1; border-top: 1px solid #F1E6D8; }
  .mk { padding: 0.7rem 0.75rem 0.65rem; }
  .mk-val { font-size: 1.2rem; }
  .mk-chg { display: block; margin-left: 0; font-size: 0.8rem; }
  .mk-sub { flex-direction: column; gap: 0.05rem; }
}
</style>
""",
    unsafe_allow_html=True,
)

theme.apply()    # 🎨 디자인(색·카드·글꼴)만 덮어써요. 지우면 예전 모습
_app_gate()      # 🛡️ 모든 화면보다 먼저(스타일만 입힌 뒤)


# ─────────────────────────── 데이터 기억(속도) ───────────────────────────
# 예전에는 기억 시간(30분·5분·2분…)이 지나면 그 순간 화면이 멈추고 수백 종목을 다시 받았어요.
# 이제는 처음 한 번만 기다리고, 그 뒤로는 지난 데이터를 바로 보여주면서 뒤에서 새로 받아 바꿔 끼워요.
# (여러 사람이 봐도 한 번만 받고, 복사 없이 같은 데이터를 같이 써서 메모리도 덜 써요.)
@st.cache_resource
def _bg_store() -> dict:
    return {"lock": threading.Lock(), "items": {}}


# Streamlit 무료 서버는 앱마다 메모리를 690MB~2.7GB 사이로 그때그때 나눠 줘요 → 가장 적을 때(690MB)에 맞춰요
IS_STREAMLIT_CLOUD = os.path.exists("/mount/src")
def _mem_limit_mb() -> float:
    """이 서버가 쓸 수 있는 메모리 한도(MB). 컨테이너 한도(cgroup) → 기계 전체 메모리 순서로 읽어요.
    Streamlit Community Cloud는 앱당 최대 2.7GB(최소 보장 690MB)라서 못 읽으면 2,700MB로 봐요."""
    if os.environ.get("MEM_LIMIT_MB"):
        return float(os.environ["MEM_LIMIT_MB"])
    vals = []
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as f:
                v = f.read().strip()
            if v.isdigit() and int(v) < 1 << 46:           # 'max'·엄청 큰 수 = 한도 없음
                vals.append(int(v) / 1e6)
        except OSError:
            pass
    try:
        with open("/proc/meminfo") as f:
            vals.append(int(f.readline().split()[1]) / 1e3)
    except (OSError, ValueError, IndexError):
        pass
    lim = min(vals) if vals else 0.0
    if IS_STREAMLIT_CLOUD:
        lim = min(lim or 2700.0, 2700.0)
    return lim or 2000.0


MEM_LIMIT = _mem_limit_mb()
# (2026.10.18) 예전 기준(Cloud 380/480MB)은 앱이 켜지기만 해도 넘는 값(라이브러리만 약 250MB, 평소 700MB대)이라
# 뒤에서 새로 받기·작은 종목·월봉이 늘 멈춰 있었어요 → 실제 한도의 비율로 정해요(Cloud 2.7GB면 약 1,200 / 1,600MB).
MEM_SOFT = int(os.environ.get("MEM_SOFT_MB") or MEM_LIMIT * 0.45)   # 넘으면 뒤쪽 새로 받기 멈춤
MEM_HARD = int(os.environ.get("MEM_HARD_MB") or MEM_LIMIT * 0.60)   # 넘으면 기억 비우기(차트·뉴스·화면 밖 일봉)
BIG_SERVER = int(os.environ.get("MEM_SOFT_MB") or 0) >= 2000         # Hugging Face(16GB)처럼 따로 크게 잡아 준 서버


def _mem_log(n_rows: int):
    """(원인 찾기) 5분마다 서버 로그에 메모리와 기억 창고 크기를 한 줄씩 남겨요 — Manage app → 로그에서 보여요."""
    now = time.time()
    if now - _GC.get("logt", 0) < 300:
        return
    _GC["logt"] = now
    try:
        S = _hist_store()
        from streamlit.runtime import get_instance
        rt = get_instance()
        n_sess = len(rt._session_mgr.list_active_sessions()) if hasattr(rt, "_session_mgr") else -1
    except Exception:
        S, n_sess = {"data": {}, "light": {}}, -1
    print(f"[MEM] {time.strftime('%m/%d %H:%M:%S')} rss={rss_mb():.0f}MB sessions={n_sess} "
          f"hist={len(S['data'])} light={len(S['light'])} swr={len(_bg_store()['items'])} rows={n_rows} "
          f"threads={threading.active_count()}", flush=True)


def _deep_bytes(obj, seen: set | None = None, depth: int = 0) -> int:
    """대략적인 메모리 크기(표·배열·사전·목록을 따라 들어가며 더해요). 같은 물건은 한 번만 세요."""
    import sys as _sys
    seen = set() if seen is None else seen
    if id(obj) in seen or depth > 6:
        return 0
    seen.add(id(obj))
    if isinstance(obj, pd.DataFrame):
        try:
            return int(obj.memory_usage(deep=True, index=True).sum()) + 2000
        except Exception:
            return 0
    if isinstance(obj, pd.Series):
        return int(obj.memory_usage(deep=True, index=True)) + 500
    if isinstance(obj, np.ndarray):
        return int(obj.nbytes) + 112
    if isinstance(obj, dict):
        return _sys.getsizeof(obj) + sum(_deep_bytes(k, seen, depth + 1) + _deep_bytes(v, seen, depth + 1)
                                         for k, v in list(obj.items()))
    if isinstance(obj, (list, tuple, set, frozenset)):
        return _sys.getsizeof(obj) + sum(_deep_bytes(v, seen, depth + 1) for v in list(obj))
    try:
        return _sys.getsizeof(obj)
    except TypeError:
        return 0


def mem_report() -> list[tuple[str, float]]:
    """(원인 찾기) 기억 창고별 대략 크기(MB). 사이드바 맨 아래 '서버 메모리'와 서버 로그에 나와요."""
    out = []
    S = _hist_store()
    out.append(("보드 일봉", _deep_bytes(S["data"])))
    out.append(("작은 종목 고가", _deep_bytes(S["light"])))
    try:
        out.append(("전 종목 일봉(🏁)", _deep_bytes(_scan_store()["arr"])))
    except Exception:
        pass
    out.append(("월봉", _deep_bytes(_monthly_store()["items"])))
    kinds: dict[str, int] = {}
    for k, v in list(_bg_store()["items"].items()):
        name = k[0] if isinstance(k, tuple) else str(k)
        kinds[name] = kinds.get(name, 0) + _deep_bytes(v.get("value"))
    for name, b in sorted(kinds.items(), key=lambda x: -x[1])[:6]:
        out.append((f"뒤에서 받은 {name}", b))
    for nm in ("_trend_cache", "_fin_cache", "_metrics_memo", "_ADX_CACHE", "_breadth_memo", "_flow_memo"):
        if hasattr(data, nm):
            out.append((f"data.{nm}", _deep_bytes(getattr(data, nm))))
    try:
        from streamlit.runtime.caching import get_data_cache_stats_provider
        per: dict[str, int] = {}
        stats = get_data_cache_stats_provider().get_stats()
        if isinstance(stats, dict):          # 새 Streamlit: {묶음 이름: [CacheStat, ...]}
            stats = [x for v in stats.values() for x in (v or [])]
        for stat in stats:
            nm = str(getattr(stat, "cache_name", "?")).split(".")[-1]
            per[nm] = per.get(nm, 0) + int(getattr(stat, "byte_length", 0) or 0)
        for nm, b in sorted(per.items(), key=lambda x: -x[1])[:6]:
            out.append((f"캐시 {nm}", b))
    except Exception:
        pass
    try:                                     # 접속 중인 화면(세션)마다 들고 있는 상태
        from streamlit.runtime import get_instance
        sm = get_instance()._session_mgr
        sess = sm.list_active_sessions() if hasattr(sm, "list_active_sessions") else []
        tot = 0
        for si in sess:
            ss = getattr(getattr(si, "session", None), "_session_state", None)
            if ss is not None:
                try:
                    tot += _deep_bytes({k: ss[k] for k in ss.filtered_state})
                except Exception:
                    pass
        out.append((f"접속 화면 {len(sess)}개 상태", tot))
    except Exception:
        pass
    return [(n, b / 1e6) for n, b in sorted(out, key=lambda x: -x[1])]


def rss_mb() -> float:
    """지금 앱 서버가 쓰는 메모리(MB)."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        return 0.0


def _free_extra():
    """(메모리) 모의 계좌·피어 계산 결과와 보드 밖 종목 일봉 기억을 비워요 — 다시 열면 새로 계산해요."""
    _memo_store().get("tab", {}).clear()
    for nm in ("_PB_SIM_MEMO", "_T126_SIM_MEMO", "_PEER_MEMO", "_MKT_MEMO", "_T126_MEMO"):
        d = globals().get(nm)
        if isinstance(d, dict):
            d.clear()
    for nm in ("load_hist_one", "load_surprises", "load_stakes"):
        f = globals().get(nm)
        if f is not None and hasattr(f, "clear"):
            try:
                f.clear()
            except Exception:
                pass


def mem_guard(keep: set | None = None):
    """(메모리) 한도에 가까우면 차트·뉴스 기억, 작은 종목 기록, 화면 밖 일봉, 옛 데이터를 비워요."""
    m = rss_mb()
    if m < MEM_HARD:
        return m
    try:
        load_chart_all.clear()
        load_evidence.clear()
    except Exception:
        pass
    _free_extra()
    S = _hist_store()
    with S["lock"]:
        S["light"].clear()
        if keep is not None:
            for c in [c for c in S["data"] if c not in keep]:
                S["data"].pop(c, None)
                S["t"].pop(c, None)
    store = _bg_store()
    with store["lock"]:
        latest = {}
        for k, v in store["items"].items():
            if isinstance(k, tuple) and (k[0] not in latest or v["t"] > store["items"][latest[k[0]]]["t"]):
                latest[k[0]] = k
        for k in [k for k in store["items"] if isinstance(k, tuple) and latest.get(k[0]) != k and not store["items"][k]["busy"]]:
            store["items"].pop(k, None)
    gc.collect()
    return rss_mb()


def swr(key, ttl: int, loader, spinner: str | None = None, first_wait: bool = True, empty=None):
    """key의 데이터를 돌려줘요. ttl초가 지났으면 지난 값을 먼저 주고 뒤에서 loader()로 새로 받아요.
    first_wait=False면 처음에도 기다리지 않고 empty를 준 뒤 뒤에서 받아요(없어도 화면이 되는 데이터용)."""
    store = _bg_store()
    with store["lock"]:
        if key not in store["items"] and isinstance(key, tuple):
            # (메모리) 종목 구성이 바뀔 때마다 새 키가 생겨 옛 데이터가 쌓였어요 → 같은 종류는 최근 2개만 남겨요
            same = [k for k, v in store["items"].items() if isinstance(k, tuple) and k[0] == key[0] and not v["busy"]]
            for k in sorted(same, key=lambda k: store["items"][k]["t"])[:-1]:
                store["items"].pop(k, None)
        item = store["items"].setdefault(key, {"value": None, "t": 0.0, "busy": False})
        has_value = item["value"] is not None
        need = (not has_value) or time.time() - item["t"] >= ttl
        start_bg = need and not item["busy"] and (has_value or not first_wait)
        if start_bg and rss_mb() > MEM_SOFT:           # 메모리가 빠듯하면 새로 받기는 다음으로
            start_bg = False
        if start_bg:
            item["busy"] = True

    def work():
        try:
            item.update(value=loader(), t=time.time())
        except Exception:   # 네트워크 오류: 지난 값을 두고 1분 뒤 다시
            item["t"] = time.time() - ttl + 60
        finally:
            item["busy"] = False

    if start_bg:
        threading.Thread(target=work, daemon=True).start()
    if has_value:
        return item["value"]
    if not first_wait:
        return empty
    with st.spinner(spinner or "데이터를 불러오는 중이에요."):
        with store["lock"]:
            mine = not item["busy"]
            if mine:
                item["busy"] = True
        if mine:
            try:
                item.update(value=loader(), t=time.time())
            finally:
                item["busy"] = False
        else:                       # 다른 사람이 이미 받는 중이면 끝날 때까지 기다려요
            while item["busy"] and item["value"] is None:
                time.sleep(0.3)
            if item["value"] is None:
                item.update(value=loader(), t=time.time())
    return item["value"]


def swr_clear(*prefixes: str):
    store = _bg_store()
    with store["lock"]:
        for k in [k for k in store["items"] if isinstance(k, tuple) and k[0] in prefixes]:
            store["items"].pop(k, None)


CORE_CAP = 3000e8          # 처음 화면: 시가총액 3,000억 이상
CORE_N = 500               # 최대 500종목(공부 자료 종목 먼저)
HIST_TTL = 1800


@st.cache_resource
def _hist_store() -> dict:
    """(속도) 일봉을 종목마다 따로 기억해요. 종목 구성이 바뀌어도 새로 들어온 종목만 받아요."""
    return {"data": {}, "t": {}, "light": {}, "lock": threading.Lock(),
            "bg": {"running": False, "queue": set(), "total": 0, "done": 0},
            "lbg": {"running": False, "queue": set(), "total": 0, "done": 0}}


def _hist_put(got: dict):
    S = _hist_store()
    now = time.time()
    with S["lock"]:
        for c, res in got.items():
            ok = res is not None and res[1] is not None and not res[1].empty
            if ok or c not in S["data"]:
                S["data"][c] = res
            S["t"][c] = now if ok else now - HIST_TTL + 120      # 못 받았으면 2분 뒤 다시


LIGHT_TTL = 3600


def _light_put(got: dict):
    """(메모리) 화면에 안 나오는 작은 종목은 일봉 표 대신 최근 260일 고가·종가만 가볍게 기억해요."""
    S = _hist_store()
    now = time.time()
    with S["lock"]:
        for c, res in got.items():
            df = res[1] if res else None
            if df is not None and not df.empty:
                S["light"][c] = (pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype="float32")[-260:], now)
            elif c not in S["light"]:
                S["light"][c] = (None, now - LIGHT_TTL + 300)


def _light_bg(codes):
    S = _hist_store()
    with S["lock"]:
        new = set(codes) - S["lbg"]["queue"]
        S["lbg"]["queue"].update(new)
        S["lbg"]["total"] += len(new)
        if S["lbg"]["running"] or not S["lbg"]["queue"]:
            return
        S["lbg"]["running"] = True

    def run():
        try:
            while True:
                with S["lock"]:
                    batch = list(S["lbg"]["queue"])[:100]
                    S["lbg"]["queue"].difference_update(batch)
                if not batch:
                    break
                _light_put(data.fetch_histories(batch, workers=4))
                with S["lock"]:
                    S["lbg"]["done"] += len(batch)
                trim_maybe(20)
                time.sleep(0.5)
        finally:
            with S["lock"]:
                S["lbg"]["running"] = False
                if not S["lbg"]["queue"]:
                    S["lbg"].update(total=0, done=0)
    threading.Thread(target=run, daemon=True).start()


def get_light(codes) -> dict:
    """작은 종목의 최근 260일 고가. 🏁 전 종목 창고에 있으면 거기서 바로 꺼내고(따로 안 받아요), 없는 종목만 받아요."""
    S = _hist_store()
    now = time.time()
    born = S.setdefault("born", now)
    try:
        arrs = _scan_store()["arr"]
    except NameError:
        arrs = {}
    from_scan = {c: arrs[c][2][-260:] for c in codes if c in arrs}
    codes = [c for c in codes if c not in from_scan]
    if from_scan:                                  # 창고에 있는 종목은 예전 가벼운 기록을 지워요(같은 걸 두 번 들고 있지 않게)
        with S["lock"]:
            for c in from_scan:
                S["light"].pop(c, None)
    need = [c for c in codes if c not in S["light"] or now - S["light"][c][1] > LIGHT_TTL]
    if need and now - born > 45 and rss_mb() < MEM_SOFT:   # (메모리) 켜진 직후·메모리 빠듯할 땐 작은 종목은 나중에
        _light_bg(need)
    return {**from_scan, **{c: S["light"][c][0] for c in codes if c in S["light"] and S["light"][c][0] is not None}}


def _hist_drop(keep: set):
    """(메모리) 화면에서 빠진 종목의 일봉 표는 비워요(다시 보이면 새로 받아요)."""
    S = _hist_store()
    with S["lock"]:
        for c in [c for c in S["data"] if c not in keep]:
            S["data"].pop(c, None)
            S["t"].pop(c, None)


def _hist_bg(codes):
    """뒤에서 천천히 받기(화면을 막지 않아요). 한 번에 하나의 작업만 돌아요."""
    S = _hist_store()
    with S["lock"]:
        new = set(codes) - S["bg"]["queue"]
        S["bg"]["queue"].update(new)
        S["bg"]["total"] += len(new)
        if S["bg"]["running"] or not S["bg"]["queue"]:
            return
        S["bg"]["running"] = True

    def run():
        try:
            while True:
                with S["lock"]:
                    batch = list(S["bg"]["queue"])[:150]
                    S["bg"]["queue"].difference_update(batch)
                if not batch:
                    break
                _hist_put(data.fetch_histories(batch, workers=6))
                with S["lock"]:
                    S["bg"]["done"] += len(batch)
                trim_maybe(20)
                time.sleep(0.3)
        finally:
            with S["lock"]:
                S["bg"]["running"] = False
                if not S["bg"]["queue"]:
                    S["bg"].update(total=0, done=0)
    threading.Thread(target=run, daemon=True).start()


def get_histories(codes, wait: bool = True, label: str = "") -> dict:
    """codes의 일봉. 처음 보는 종목은 wait=True면 받아서 보여주고, 오래된 종목은 뒤에서 새로 받아요."""
    S = _hist_store()
    now = time.time()
    missing = [c for c in codes if c not in S["data"]]
    stale = [c for c in codes if c in S["data"] and now - S["t"].get(c, 0) > HIST_TTL]
    if missing and wait:
        with st.spinner(label or f"일봉 {len(missing):,}종목을 불러오는 중이에요(처음 한 번만)."):
            _hist_put(data.fetch_histories(missing, workers=8))
        missing = []
    if (stale or missing) and rss_mb() < MEM_SOFT:
        _hist_bg(stale + missing)
    d = S["data"]
    return {c: d[c] for c in codes if c in d}


def _recent_high(hist, quote, days: int) -> bool:
    """최근 days거래일 안에 52주 신고가를 썼는지(오늘 실시간 고가 포함). hist = 일봉 표 또는 고가 배열."""
    if hist is None or len(hist) < 60:
        return False
    h = (np.asarray(hist, dtype=float) if isinstance(hist, np.ndarray)
         else pd.to_numeric(hist["high"], errors="coerce").to_numpy(dtype=float))[-250:]
    hi52 = np.nanmax(h)
    qh = max(float((quote or {}).get("high") or 0), float((quote or {}).get("price") or 0))
    if qh and qh >= hi52 * 0.999:
        return True
    last = len(h) - 1 - int(np.nanargmax(h[::-1]))
    return (len(h) - 1 - last) <= days


def _first_high_ago(hist, lookback: int = 20) -> int | None:
    """최근 lookback거래일 안에서 52주 신고가를 '처음' 쓴 날이 며칠 전인지(없으면 None)."""
    if hist is None or len(hist) < 260:
        return None
    h = pd.to_numeric(hist["high"], errors="coerce").to_numpy(dtype=float)
    prior = pd.Series(h).shift(1).rolling(250, min_periods=200).max().to_numpy()
    tail = np.arange(len(h) - lookback, len(h))
    hits = [i for i in tail if prior[i] == prior[i] and h[i] > prior[i]]
    return (len(h) - 1 - hits[0]) if hits else None


@st.cache_resource
def _memo_store() -> dict:
    """(속도) app.py는 새로고침마다 처음부터 다시 실행돼서 보통 변수는 사라져요 → 계산 기억은 여기에 둬요."""
    return {"first": {}, "leader": {}}


_DATA_VER = {"v": None}


def set_data_ver(df: pd.DataFrame, interval: int | None):
    """(속도) 이번 새로고침의 데이터 버전 = 새로고침 간격 단위 시각 + 보이는 종목 구성.
    같은 버전 안에서는 탭을 오가거나 버튼을 눌러도 무거운 계산을 다시 하지 않아요(다른 사람 화면과도 같이 써요)."""
    sec = max(20, int(interval or 60))
    try:
        comp = hash(tuple(df["code"]))
    except Exception:  # noqa: BLE001
        comp = len(df)
    _DATA_VER["v"] = (int(time.time() // sec), len(df), comp)


def tab_memo(name: str, key, fn):
    """같은 데이터 버전 + 같은 key면 지난 계산을 그대로 돌려줘요. 이름마다 마지막 1개만 기억(메모리 작게)."""
    store = _memo_store().setdefault("tab", {})
    k = (_DATA_VER["v"], key)
    hit = store.get(name)
    if hit is not None and hit[0] == k:
        return hit[1]
    val = fn()
    store[name] = (k, val)
    return val


def lead_groups(df: pd.DataFrame):
    """data.leading_groups를 데이터 버전마다 한 번만."""
    return tab_memo(f"leading_groups_{len(df)}", (recent_days, min_count), lambda: data.leading_groups(df, recent_days, min_count))


def mark_sector_first(df: pd.DataFrame, histories: dict) -> pd.DataFrame:
    """깡토: 변동성 장에선 섹터에서 '가장 먼저' 신고가를 쓴 종목을 우선 본다 → sector_first 표시."""
    if df.empty or "days_since_high" not in df:
        return df
    _FIRST_MEMO = _memo_store()["first"]
    recent = df[pd.to_numeric(df["days_since_high"], errors="coerce") <= 20]
    ago = {}
    for c in recent["code"]:
        res = histories.get(c)
        if res and res[1] is not None:
            m = _FIRST_MEMO.get(c)                 # (속도) 일봉이 그대로면 지난 계산을 다시 써요
            if m is not None and m[0] is res[1]:
                a = m[1]
            else:
                a = _first_high_ago(res[1])
                _FIRST_MEMO[c] = (res[1], a)
            if a is not None:
                ago[c] = a
    for c in [c for c in _FIRST_MEMO if c not in histories]:      # (메모리) 화면에서 빠진 종목은 정리
        _FIRST_MEMO.pop(c, None)
    df = df.assign(nh_first_ago=df["code"].map(ago), sector_first=False)
    sub = df[df["nh_first_ago"].notna()]
    for _, g in sub.groupby("group"):
        if len(g) >= 2:
            df.loc[g["nh_first_ago"].idxmax(), "sector_first"] = True
    return df


def core_view(quotes: dict, shares: dict) -> tuple[set, set, dict]:
    """주요 500(시총 3,000억↑, 공부 자료 종목 먼저) + 시총이 작아도 최근 신고가 종목."""
    board_kr = [x["code"] for x in STOCKS if data.is_kr(x["code"])]
    all_kr = board_kr + list(EXTRA_CODES)

    def cap(c):
        q, n = quotes.get(c) or {}, shares.get(c)
        return (q.get("price") or 0) * (n or 0)
    caps = {c: cap(c) for c in all_kr}
    known = sum(1 for v in caps.values() if v)
    if known < 100:                                   # 시가총액을 아직 못 받았으면 공부 자료 종목으로
        core = set(board_kr)
    else:
        b = sorted([c for c in board_kr if caps[c] >= CORE_CAP], key=lambda c: -caps[c])
        e = sorted([c for c in EXTRA_CODES if caps[c] >= CORE_CAP], key=lambda c: -caps[c])
        core = set((b + e)[:CORE_N])
    rest = [c for c in all_kr if c not in core]
    _scan_bg(tuple(sorted(set(all_kr))))              # 🏁 전 종목 창고를 채우면 작은 종목 고가도 거기서 꺼내 써요
    light = get_light(rest)                           # 작은 종목은 뒤에서, 고가만 가볍게
    small_hi = {c for c, hs in light.items() if _recent_high(hs, quotes.get(c), max(5, recent_days))}
    S = _hist_store()["lbg"]
    return core, small_hi, {"rest": len(rest), "loaded": len(light), "bg": dict(S)}


def load_histories(codes: tuple[str, ...]):
    return swr(("hist_kr", codes), 1800, lambda: data.fetch_histories(codes),
               "2년치 일봉을 불러오는 중이에요. 처음 한 번만 몇 초 걸려요.")


# (주의) app.py 변수는 새로고침마다 0으로 돌아가서 '2분 뒤 다시' 같은 간격이 지켜지지 않았어요 → 서버에 기억
_UNI_RETRY = _memo_store().setdefault("uni_retry", {"t": 0.0})


def load_universe() -> list[dict]:
    """전체 종목 목록(하루 기억). 못 받았으면(빈 목록) 하루 동안 비워 두지 않고 2분마다 다시 시도해요."""
    val = swr(("krx_universe", "v2"), 86400, data.fetch_krx_universe, "코스피·코스닥 전체 종목 목록을 불러오는 중이에요.")
    if not val and time.time() - _UNI_RETRY["t"] > 120:
        _UNI_RETRY["t"] = time.time()
        swr_clear("krx_universe")
    return val or []


def load_industry_map(codes: tuple[str, ...]) -> tuple[dict, dict]:
    """전 종목 공식 업종(하루 기억). 목록과 따로 받아서, 목록을 예전에 받아 뒀어도 업종은 새로 채워져요."""
    got = swr(("industry_map", "v2"), 86400, lambda: data.fetch_industry_map(codes),
              "전 종목의 업종(네이버 업종 · WICS)을 확인하는 중이에요. 처음 한 번 10~30초 걸려요.")
    if not got or not got[0]:
        if time.time() - _UNI_RETRY.get("ind", 0) > 180:          # 못 받았으면 3분 뒤 다시
            _UNI_RETRY["ind"] = time.time()
            swr_clear("industry_map")
        return {}, (got[1] if got else {})
    return got


def load_themes() -> dict:
    """네이버 테마(하루 기억, 기다리지 않음 — 받는 동안은 테마 태그만 잠깐 비어 있어요)."""
    return swr(("naver_themes",), 86400, data.fetch_theme_map, first_wait=False, empty={}) or {}


def load_histories_extra(codes: tuple[str, ...]):
    """보드 밖 코스피·코스닥 종목 일봉(따로 기억해서 보드 종목은 다시 받지 않아요)."""
    return swr(("hist_all", codes), 1800, lambda: data.fetch_histories(codes, workers=16),
               f"코스피·코스닥 전체 {len(codes):,}종목의 일봉을 받는 중이에요. 처음 한 번은 1~3분 걸려요.")


def load_histories_overseas(codes: tuple[str, ...]):
    return swr(("hist_os", codes), 900, lambda: data.fetch_histories(codes), "해외 종목 일봉을 불러오는 중이에요.")


def load_official_52w(codes: tuple[str, ...]) -> dict:
    """네이버 공식 52주 최고·최저(10분마다). 일봉으로 계산한 값과 대조해서 다르면 공식값으로 고쳐요."""
    return swr(("h52", codes), 600, lambda: data.fetch_official_52w(codes),
               "네이버 공식 52주 최고·최저가와 대조하는 중이에요. 처음 한 번만 몇 초 걸려요.")


@st.cache_resource
def _monthly_store():
    return {"lock": threading.Lock(), "items": {}, "busy": False}


def _slim_monthly(df_):
    """(메모리) 월봉은 역대 최고가 계산에 날짜·고가만 써요 → 두 열만 float32로."""
    if df_ is None or df_.empty or "high" not in df_:
        return None
    return pd.DataFrame({"date": pd.to_datetime(df_["date"]).values,
                         "high": pd.to_numeric(df_["high"], errors="coerce").astype("float32").values})


def load_monthlies(codes: tuple[str, ...], trim: bool = True):
    """역대 최고가 계산용 월봉(종목별로 6시간 기억).
    예전엔 종목 구성이 조금만 바뀌어도(작은 신고가 종목 추가 등) 600종목 월봉을 처음부터 다시 받아서,
    그동안 '역사적 신고가'가 비고 서버도 바빴어요 → 이제 없는 종목·오래된 종목만 뒤에서 더 받아요."""
    ms = _monthly_store()
    now = time.time()
    with ms["lock"]:
        items = ms["items"]
        have = {c: items[c][1] for c in codes if c in items}
        need = tuple(c for c in codes if c not in items or now - items[c][0] > 21600)
        start = bool(need) and not ms["busy"] and rss_mb() <= MEM_SOFT
        if start:
            ms["busy"] = True
        if trim and len(items) > max(1500, len(codes) * 2):  # (메모리) 오래 안 본 종목은 정리(후보 탭처럼 일부만 볼 땐 안 지워요)
            keep = set(codes)
            for c in sorted((c for c in items if c not in keep), key=lambda c: items[c][0])[: len(items) - len(codes)]:
                items.pop(c, None)

    def work():
        try:
            for i in range(0, len(need), 120):                   # 120종목씩 나눠 받아서 받은 만큼 바로 쓰게
                part = need[i:i + 120]
                got = data.fetch_monthlies(part)
                t = time.time()
                with ms["lock"]:
                    for c in part:
                        df_ = _slim_monthly(got.get(c))
                        if df_ is not None:
                            ms["items"][c] = (t, df_)
                        elif c not in ms["items"]:
                            ms["items"][c] = (t - 21600 + 1800, None)   # 못 받은 종목은 30분 뒤 다시
        except Exception:
            pass
        finally:
            ms["busy"] = False

    if start:
        threading.Thread(target=work, daemon=True).start()
    return {c: v for c, v in have.items() if v is not None}


@st.cache_resource
def _shares_store():
    return {"t": 0.0, "t_retry": 0.0, "data": {}, "kr": {}, "os": {}, "fails": {}}


def load_shares() -> dict:
    """상장주식수: 12시간마다 전체를 다시 받고, 빠진 종목은 10분마다 그것만 다시 시도해요(종목당 3번까지)."""
    store = _shares_store()
    store.setdefault("fails", {})
    store.setdefault("t_retry", 0.0)
    now = time.time()
    full = now - store["t"] >= 43200
    missing = [c for c in ALL_CODES + EXTRA_CODES if c not in store["data"] and store["fails"].get(c, 0) < 3]
    big_new = len(missing) > 200 and now - store.get("t_big", 0.0) >= 120     # 전체 종목을 막 켰을 때는 바로 받기
    if not full and not (missing and (now - store["t_retry"] >= 600 or big_new)):
        return store
    if big_new:
        store["t_big"] = now
    kr_t = KR_CODES + EXTRA_CODES if full else tuple(c for c in missing if data.is_kr(c))
    os_t = OS_CODES if full else tuple(c for c in missing if not data.is_kr(c))
    with st.spinner("시가총액 계산용 상장주식수를 불러오는 중이에요."):
        kr, kr_diag = data.fetch_kr_shares(kr_t) if kr_t else ({}, {})
        os_, os_diag = data.fetch_overseas_shares(os_t) if os_t else ({}, {})
    got = {**kr, **os_}
    if full:
        store["fails"] = {}
    for c in kr_t + os_t:
        if c not in got:
            store["fails"][c] = store["fails"].get(c, 0) + 1
    store["data"] = {**store["data"], **got}
    if kr_t:
        store["kr"] = kr_diag
    if os_t:
        store["os"] = os_diag
    store["t_retry"] = now
    if full:
        store["t"] = now if len(kr) >= 0.8 * max(1, len(kr_t)) else now - 43200 + 600
    return store


def load_fx():
    return swr(("fx",), 3600, data.fetch_fx, "환율을 불러오는 중이에요.")


def load_indexes():
    return swr(("indexes",), 120, data.fetch_index_histories, "지수·환율·유가를 불러오는 중이에요.")


@st.cache_data(ttl=30, show_spinner=False)
def load_overview():
    return data.fetch_market_overview()


def load_flows():
    overview = load_overview()
    return swr(("flows",), 300, lambda: data.fetch_investor_flows(overview), "투자자별 순매수를 불러오는 중이에요.")


def load_trends(codes: tuple[str, ...]) -> dict:
    """종목별 수급. data 쪽에서 종목마다 10분씩 기억해서 자동 새로고침 때는 거의 바로 나와요."""
    with st.spinner("종목별 개인·외국인·기관 수급을 불러오는 중이에요."):
        return data.fetch_stock_trends(codes)


def price_chart(chart: pd.DataFrame, colors: list[str], height: int = 260):
    """y축을 0부터 시작하지 않는 선 차트(주가·지수용)."""
    d = chart.copy()
    d.index.name = "날짜"
    long = d.reset_index().melt("날짜", var_name="구분", value_name="값").dropna()
    c = (
        alt.Chart(long)
        .mark_line(strokeWidth=1.7)
        .encode(
            x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%y.%m", labelAngle=0, grid=False)),
            y=alt.Y("값:Q", title=None, scale=alt.Scale(zero=False)),
            color=alt.Color("구분:N", scale=alt.Scale(domain=list(chart.columns), range=colors),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), alt.Tooltip("구분:N"),
                     alt.Tooltip("값:Q", format=",.2f")],
        )
        .properties(height=height, width="container")
    )
    st.altair_chart(c)


@st.cache_data(ttl=10, show_spinner=False, max_entries=20)
def load_quotes(codes: tuple[str, ...]):
    return data.fetch_quotes(codes)


NH_FILTERS = {
    "전체": None,
    "일봉 52주 신고가(오늘)": "nh52_d", "주봉 52주 신고가(이번 주)": "nh52_w", "월봉 52주 신고가(이번 달)": "nh52_m",
    "일봉 역대 신고가(오늘)": "ath_d", "주봉 역대 신고가(이번 주)": "ath_w", "월봉 역대 신고가(이번 달)": "ath_m",
}

# ─────────────────────────── 사이드바 ───────────────────────────
MARKETS = {"한국": "KR", "미국": "US", "기타": "OTHER"}
# 산업을 큰 묶음으로 나눠 칸으로 보여줘요. 여기 없는 산업은 '기타'로 가요.
SECTOR_CATS = [
    ("반도체 · IT", ["반도체", "글로벌 반도체", "AI·IT", "AI 인프라(해외)", "AI 소프트웨어(해외)", "OLED", "디스플레이",
                    "IT하드웨어", "소프트웨어·인터넷", "통신", "미디어·엔터·게임", "데이터센터 전력", "우주 데이터센터"]),
    ("에너지 · 산업재", ["2차전지", "전력·에너지", "풍력", "전기·전자장비", "에너지·유틸리티", "조선", "방산·우주항공",
                      "기계", "건설", "산업재", "자동차", "화학", "철강·금속", "종이·포장", "전략광물", "운송·물류", "상업서비스"]),
    ("소비 · 바이오 · 금융", ["화장품", "음식료", "소비재", "소비자 서비스", "유통·무역", "바이오·헬스케어", "금융·지주",
                           "부동산"]),
    ("기타", ["기타", "기타(리포트 스크린)", "해외 기타", "기타 업종"]),
]
SECTOR_SHORT = {"소프트웨어·인터넷": "SW·인터넷", "미디어·엔터·게임": "엔터·게임", "에너지·유틸리티": "에너지·유틸",
                "전기·전자장비": "전기장비", "방산·우주항공": "방산·우주", "소비자 서비스": "레저·교육",
                "바이오·헬스케어": "바이오·헬스", "기타(리포트 스크린)": "리포트", "AI 인프라(해외)": "AI 인프라", "AI 소프트웨어(해외)": "AI SW",
                "글로벌 반도체": "반도체", "해외 기타": "기타",
                "바이오·헬스케어": "바이오·헬스", "데이터센터 전력": "DC 전력", "우주 데이터센터": "우주 DC"}


def _mkt(code: str) -> str:
    m = data.market_of(code)[0]
    return m if m in ("KR", "US") else "OTHER"


def _sb_head(text: str, right: str = ""):
    st.markdown(f'<div class="sb-head"><span>{text}</span><em>{right}</em></div>', unsafe_allow_html=True)


def _toggle_sector(key: str, sec: str | None):
    cur = list(st.session_state.get(key, []))
    if sec is None:
        cur = []
    elif sec in cur:
        cur.remove(sec)
    else:
        cur.append(sec)
    st.session_state[key] = cur


with st.sidebar:
    st.markdown('<div class="sb-title">보기 설정</div>', unsafe_allow_html=True)

    # ① 시장
    with st.container(border=True, key="sb_box_market"):
        _sb_head("시장")
        market_label = st.segmented_control("시장", list(MARKETS), default="한국", required=True,
                                            key="sb_market2", width="stretch", label_visibility="collapsed") or "한국"
        market = MARKETS[market_label]
        scope_all = False
        scope_core = False
        if market == "KR":
            scope = st.segmented_control("종목 범위", ["내 보드", "주요 500"] if IS_STREAMLIT_CLOUD else ["내 보드", "주요 500", "전체"],
                                         default="주요 500" if BIG_SERVER else "내 보드", required=True,
                                         key="sb_scope3", width="stretch",
                                         format_func=lambda x: {"주요 500": "주요500+신고가", "전체": "전체", "내 보드": "내 보드"}[x],
                                         help="내 보드(기본·가벼움): 공부 자료 종목만.\n"
                                              "주요500+신고가: 시가총액 3,000억 이상 500개 + 시총이 작아도 최근 신고가 종목(작은 종목은 뒤에서 채워요).\n"
                                              "전체: 코스피·코스닥 모두 — 서버 메모리를 많이 써서 필요할 때만 켜세요.")
            scope_all = scope in ("주요 500", "전체")
            scope_core = scope == "주요 500"
    # 전체 범위: 보드에 없는 상장 종목을 '코스피 전체 / 코스닥 전체' 산업으로 붙여요(세부 분류 = 한국거래소 업종)
    EXTRA_STOCKS = []
    IND_MAP, IND_DIAG = {}, {}
    if scope_all:
        _have = {x["code"] for x in STOCKS}
        _uni = load_universe()
        IND_MAP, IND_DIAG = load_industry_map(tuple(sorted(u["code"] for u in _uni if u["code"] not in _have)))
        for u in _uni:
            if u["code"] not in _have:
                ind = (u.get("industry") or IND_MAP.get(u["code"]) or "").strip()
                EXTRA_STOCKS.append({
                    "sector": data.classify_industry(ind) if ind else "기타 업종",
                    "group": ind or f"{u['market']} 업종 확인 중",
                    "name": u["name"], "code": u["code"],
                    "desc": u.get("product") or (f"{ind} 업종" if ind else ""),
                    "src": f"분류 근거: 공식 업종 '{ind}' (네이버 증권 업종 분류 · 없으면 한국거래소 업종)" if ind else
                           "분류 근거: 업종 정보를 아직 못 받았어요",
                    "tags": [], "notes": []})
    STOCKS_VIEW = STOCKS + EXTRA_STOCKS
    # 네이버 테마(2차전지·원전·방산 등 업종으로 안 나뉘는 흐름)를 '🏷️ 테마' 태그로 붙여요 — 원본 목록은 건드리지 않고 복사본에
    THEMES = load_themes() if scope_all else {}
    TAGS_VIEW = dict(TAGS)
    if THEMES:
        by_code: dict[str, list[str]] = {}
        for tname, codes in THEMES.items():
            tag = f"🏷️ {tname}"
            TAGS_VIEW[tag] = codes
            for c in codes:
                by_code.setdefault(c, []).append(tag)
        STOCKS_VIEW = [dict(x, tags=list(x.get("tags") or []) + by_code[x["code"]]) if x["code"] in by_code else x
                       for x in STOCKS_VIEW]
    EXTRA_CODES = tuple(sorted(x["code"] for x in EXTRA_STOCKS))
    for _x in EXTRA_STOCKS:
        if _x["sector"] not in SECTOR_ORDER:
            SECTOR_ORDER = SECTOR_ORDER + [_x["sector"]]
        if _x["group"] not in GROUP_ORDER:
            GROUP_ORDER = GROUP_ORDER + [_x["group"]]
    with st.sidebar:
        if scope_all and EXTRA_STOCKS:
            n_ind = sum(1 for x in EXTRA_STOCKS if x["sector"] != "기타 업종")
            st.caption(f"보드 {len(STOCKS):,} + 상장 종목 {len(EXTRA_STOCKS):,}개 = {len(STOCKS_VIEW):,}개 · "
                       f"업종 분류 {n_ind:,}/{len(EXTRA_STOCKS):,}"
                       + (" (" + ", ".join(f"{k} {v:,}" for k, v in IND_DIAG.items() if v) + ")" if IND_DIAG else ""))
            if n_ind < len(EXTRA_STOCKS) * 0.5 and st.button("업종 다시 확인하기", key="ind_retry", width="stretch"):
                swr_clear("industry_map")
                st.rerun()
        elif scope_all:
            diag = " · ".join(f"{k} {v}" for k, v in getattr(data, "UNIVERSE_DIAG", {}).items()) or "응답 없음"
            st.warning(f"코스피·코스닥 전체 목록을 아직 못 받았어요({diag}). 지금은 내 보드 종목만 보여요. "
                       "2분마다 자동으로 다시 시도해요.")
            if st.button("전체 목록 다시 받기", key="uni_retry", width="stretch"):
                _UNI_RETRY["t"] = 0.0
                swr_clear("krx_universe")
                st.rerun()
    mkt_stocks = [x for x in STOCKS_VIEW if _mkt(x["code"]) == market]

    # ② 산업 — 묶음별 2칸 격자. 아무것도 안 고르면 그 시장 전체
    sector_choices = [sec for sec in SECTOR_ORDER if any(x["sector"] == sec for x in mkt_stocks)]
    sector_choices += sorted({x["sector"] for x in mkt_stocks} - set(sector_choices))
    sec_count = {sec: sum(x["sector"] == sec for x in mkt_stocks) for sec in sector_choices}
    sel_key = f"sb_sel_{market}"
    st.session_state.setdefault(sel_key, [])
    sectors = [x for x in st.session_state[sel_key] if x in sector_choices]
    with st.container(border=True, key="sb_box_sector"):
        _sb_head("산업", f"{len(sectors)}개 선택" if sectors else "전체")
        st.button(f"전체  {len(mkt_stocks)}", key=f"sb_all_{market}", width="stretch",
                  type="primary" if not sectors else "secondary",
                  on_click=_toggle_sector, args=(sel_key, None))
        placed = set()
        cats = [(nm, [x for x in secs if x in sec_count]) for nm, secs in SECTOR_CATS]
        rest = [x for x in sector_choices if not any(x in secs for _, secs in cats)]
        if rest:
            cats[-1] = (cats[-1][0], cats[-1][1] + rest)
        for cat, secs in cats:
            if not secs:
                continue
            st.markdown(f'<div class="sb-cat">{cat}</div>', unsafe_allow_html=True)
            for i in range(0, len(secs), 2):
                cols = st.columns(2, gap="small")
                for col, sec in zip(cols, secs[i:i + 2]):
                    col.button(f"{SECTOR_SHORT.get(sec, sec)}  {sec_count[sec]}", key=f"sb_sec_{market}_{sec}",
                               width="stretch", type="primary" if sec in sectors else "secondary",
                               on_click=_toggle_sector, args=(sel_key, sec))
    active_sectors = sectors or sector_choices

    # ③ 종목 찾기 — 세부 분류 · 리포트 태그 · 검색
    with st.container(border=True, key="sb_box_find"):
        _sb_head("종목 찾기")
        group_choices = [g for g in GROUP_ORDER
                         if any(x["group"] == g and x["sector"] in active_sectors for x in mkt_stocks)]
        group_pick = st.selectbox("세부 분류", ["전체", *group_choices], key=f"sb_group_{market}")
        groups = [] if group_pick == "전체" else [group_pick]
        _have_tags = {t for x in mkt_stocks for t in (x.get("tags") or [])}
        tag_choices = [t for t in TAGS_VIEW if t in _have_tags]
        tags = st.multiselect("리포트 · 테마 태그", tag_choices, placeholder="선택 안 함 (예: 2차전지, 원자력)", key=f"sb_tags_{market}",
                              help="📥·리포트 태그 = 공부 자료, 🏷️ = 네이버 테마. 고르면 위의 산업·분류와 관계없이 그 태그 종목만 보여줘요.")
        query = st.text_input("종목 검색", placeholder="이름이나 코드", key="sb_query")

    # ④ 조건 필터 — 평소엔 접어 둬요
    with st.expander("📐 조건 필터", expanded=False):
        max_drop = st.slider("52주 최고가에서 몇 % 이내만 볼까요", 0, 90, 90, step=5,
                             help="10으로 두면 최고가 대비 -10% 이내 종목만 보여줘요. 90이면 전체.")
        min_rs = st.slider("종합 RS 이상", 0, 99, 0, step=5, help="0이면 전체. 70으로 두면 RS 70 이상만")
        nh_filter = st.selectbox("신고가 봉 필터", list(NH_FILTERS), index=0,
                                 help="지금 봉(오늘 일봉·이번 주 주봉·이번 달 월봉)의 고가가 52주 또는 역대(상장 이후) 최고가를 넘은 종목만")
        show_halted = st.toggle("거래정지 종목도 보기", value=False,
                                help="최근 5거래일 동안 거래가 한 주도 없는 종목(거래정지·상장폐지 절차 등)은 가격이 멈춰 있어서 "
                                     "'신고가까지 0%'처럼 보여요. 기본으로 빼 두고, 켜면 '⛔ 거래정지'로 표시해서 보여줘요.")
        only_aligned = st.toggle("정배열 종목만", help="현재가 > 20일선 > 60일선 > 120일선")
        only_holding = st.toggle("신고가 돌파 유지 종목만",
                                 help="직전 52주 최고가를 종가로 돌파한 뒤 한 번도 그 아래에서 마감하지 않은 종목만")
        only_earn = st.toggle("영업이익·EPS 정배열만",
                              help="최근 실적 연도부터 컨센서스(E)까지 영업이익과 EPS가 해마다 모두 증가한 국내 종목만. "
                                   "전망(E)이 없는 종목·적자 종목·해외 종목은 빠져요. 처음 켤 때 종목 수에 따라 30초~1분 걸려요.")
        earn_years = st.radio("실적 정배열 판정: 최근 실적 몇 년부터", [2, 3], index=1, horizontal=True,
                              format_func=lambda n: f"{n}년 + 전망(E)")
        show_flow = st.toggle("종목별 수급 열 보기", value=True, disabled=(market != "KR"),
                              help="국내 종목의 최근 거래일 개인·외국인·기관 순매수(억원, 종가로 환산한 추정치)와 5일 누적을 표에 붙여요. "
                                   "보고 있는 종목 중 앞쪽 150개까지만 불러와요.")
        show_flow = show_flow and market == "KR"   # 해외는 수급 자료가 없어서 열을 숨겨요

    with st.expander("🧭 주도섹터 기준", expanded=False):
        recent_days = st.slider("신고가로 인정할 기간(거래일)", 1, 20, 5)
        min_count = st.slider("분류 안 신고가 종목 수", 2, 5, 2)

    bo_mode = "line"   # 돌파선 = 돌파한 날 넘어선 직전 52주 최고가

    st.divider()
    refresh_label = st.segmented_control("자동 새로고침", list(REFRESH), default="1분", required=True,
                                         key="sb_refresh", width="stretch") or "1분"
    b1, b2 = st.columns(2)
    if b1.button("시세 새로고침", width="stretch"):
        load_quotes.clear()
    if b2.button("일봉 다시 받기", width="stretch", help="52주 최고가가 이상해 보일 때 눌러요."):
        swr_clear("hist_kr", "hist_os", "monthly")
        _shares_store().update(t=0.0, ok=False)
        load_quotes.clear()


# ─────────────────────────── 화면 조각 ───────────────────────────
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["code"].map(_mkt) == market]
    if "halted" in df and not show_halted:
        df = df[df["halted"] != True]  # noqa: E712
    if tags:
        f = df[df["tags"].apply(lambda ts: any(t in ts for t in tags))]
    else:
        f = df[df["sector"].isin(active_sectors)]
        if groups:
            f = f[f["group"].isin(groups)]
    if query.strip():
        q = query.strip().lower()
        f = f[f["name"].str.lower().str.contains(q, regex=False) | f["code"].str.contains(q, regex=False)]
    if max_drop < 90:
        f = f[f["gap"].notna() & (f["gap"] >= -max_drop)]
    if only_aligned:
        f = f[f["aligned"] == True]  # noqa: E712
    if only_holding:
        f = f[f["bo_status"] == "유지"]
    if NH_FILTERS[nh_filter]:
        f = f[f[NH_FILTERS[nh_filter]] == True]  # noqa: E712
    if min_rs > 0:
        f = f[f["rs"].notna() & (f["rs"] >= min_rs)]
    return f.sort_values("gap", ascending=False, na_position="last")


def _sign_color(v) -> str:
    if v is None or pd.isna(v) or v == 0:
        return "#51616C"
    return UP if v > 0 else DOWN


def _flow_cell(v, digits: int = 0) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f'<span style="color:{_sign_color(v)}">{v:+,.{digits}f}</span>'


def _eok(v) -> str:
    """억원 값 → '+5,473억' / '-1.2조'."""
    if v is None or pd.isna(v):
        return "-"
    return f"{v / 1e4:+,.2f}조" if abs(v) >= 1e4 else f"{v:+,.0f}억"


def _breadth60(board: pd.DataFrame | None) -> dict:
    """코스피·코스닥별로 '60일선 위에 있는 종목 비율'. {'코스피': (비율%, 위 종목 수, 전체 수)}
    지금 보드에 올라온 종목(내 보드 / 주요500+신고가 / 전체) 기준이에요."""
    if board is None or board.empty or "above60" not in board.columns:
        return {}
    # (속도) 전체 종목 목록을 기다리지 않아요 — 아직 없으면 이번엔 비율을 빼고, 뒤에서 받은 뒤부터 보여줘요
    uni = swr(("krx_universe", "v2"), 86400, data.fetch_krx_universe, first_wait=False, empty=[]) or []
    mk = {x["code"]: x.get("market") for x in uni}
    b = board[board["code"].map(data.is_kr)][["code", "above60"]].copy()
    b["mk"] = b["code"].map(mk)
    b = b[b["above60"].notna()]
    out = {}
    for name in data.MARKETS:
        sub = b[b["mk"] == name]
        if len(sub) >= 10:
            up = int(sub["above60"].astype(bool).sum())
            out[name] = (up / len(sub) * 100, up, len(sub))
    return out


def _market_card(name: str, sm: dict | None, hist_sm: dict | None, b60: tuple | None = None) -> str:
    """네이버 증권 첫 화면처럼: 지수, 등락률·전일대비, 상승/보합/하락 막대, 투자자별 순매수."""
    sm = sm or {}
    last = sm.get("last") if sm.get("last") is not None else (hist_sm or {}).get("last")
    if last is None:
        return f'<div class="mk"><div class="mk-name">{name}</div><div class="idx-err">불러오지 못했어요</div></div>'
    rate = sm.get("rate") if sm.get("rate") is not None else (hist_sm or {}).get("change")
    diff = sm.get("diff")
    if diff is None and hist_sm and hist_sm.get("change") is not None:
        diff = last - last / (1 + hist_sm["change"] / 100)
    color = _sign_color(rate)
    arrow = "▲" if (diff or 0) > 0 else ("▼" if (diff or 0) < 0 else "")
    chg = (f'<span class="mk-chg" style="color:{color}">{rate:+.2f}% '
           f'<span style="font-weight:600">{arrow}{abs(diff):,.2f}</span></span>') if rate is not None and diff is not None else ""
    badge = ""
    if hist_sm and hist_sm.get("above60") is not None:
        cls, word = ("on", "위") if hist_sm["above60"] else ("off", "아래")
        badge = f'<small><span class="idx-badge {cls}" style="margin:0">지수 60일선 {word} {hist_sm["dist60"]:+.1f}%</span></small>'
    html_ = f'<div class="mk"><div class="mk-name">{name}{badge}</div><span class="mk-val">{last:,.2f}</span>{chg}'
    if b60:
        pct, up, tot60 = b60
        col = "#2E6B6F" if pct >= 60 else ("#C58A12" if pct >= 40 else "#A2343B")
        html_ += (f'<div class="mk-b60" title="60일 이동평균선 위에서 거래 중인 종목 비율 ({up:,}/{tot60:,}종목)">'
                  f'<span>60일선 위 종목<small style="color:#8A979F"> 보드</small></span><span class="gauge"><i style="width:{pct:.0f}%;background:{col}"></i></span>'
                  f'<b style="color:{col}">{pct:.0f}%</b><small>{up:,}/{tot60:,}</small></div>')
    b = sm.get("breadth")
    if b:
        up = (b.get("상승") or 0) + (b.get("상한") or 0)
        flat = b.get("보합") or 0
        down = (b.get("하락") or 0) + (b.get("하한") or 0)
        tot = max(1.0, up + flat + down)
        html_ += (f'<div class="mk-bar"><i style="width:{up / tot * 100:.1f}%;background:#EF7A80"></i>'
                  f'<i style="width:{flat / tot * 100:.1f}%;background:#C9D1D6"></i>'
                  f'<i style="width:{down / tot * 100:.1f}%;background:#6FA0E6"></i></div>'
                  f'<div class="mk-cnt"><span class="u">↗{up:,.0f}</span><span>{flat:,.0f}</span>'
                  f'<span class="d">↘{down:,.0f}</span></div>')
    d = sm.get("deal")
    if d:
        cells = "".join(f'<span>{k} <b style="color:{_sign_color(d.get(k))}">{_eok(d.get(k))}</b></span>'
                        for k in ("외국인", "기관", "개인"))
        html_ += f'<div class="mk-sub">{cells}</div>'
    return html_ + "</div>"


def _mood_card(overview: dict) -> str:
    mood = data.market_mood(overview)
    label, ratio = mood if mood else ("-", 0.5)
    tot = {k: 0.0 for k in data.INVESTORS}
    got = False
    for sm in overview.values():
        d = (sm or {}).get("deal") or {}
        for k in data.INVESTORS:
            if d.get(k) is not None:
                tot[k] += d[k]
                got = True
    rows = ""
    if got:
        mx = max(1.0, max(abs(v) for v in tot.values()))
        for k in ("외국인", "기관", "개인"):
            v = tot[k]
            w = abs(v) / mx * 50
            pos = f"left:50%;width:{w:.1f}%" if v >= 0 else f"left:{50 - w:.1f}%;width:{w:.1f}%"
            rows += (f'<div class="mood-row"><span>{k}</span><span class="bar"><i style="{pos};background:{_sign_color(v)}"></i></span>'
                     f'<b style="color:{_sign_color(v)}">{_eok(v)}</b></div>')
    else:
        rows = '<div class="mood-note">투자자별 순매수를 불러오지 못했어요.</div>'
    return (f'<div class="mood"><div class="mood-top"><span><span class="dot"></span>오늘의 시장</span>'
            f'<span class="lbl">{label}</span></div>'
            f'<div class="mood-gauge"><i style="left:{ratio * 100:.0f}%"></i></div>{rows}'
            f'<div class="mood-note">분위기는 코스피·코스닥 상승 종목 비율({ratio * 100:.0f}%) 기준, 순매수는 두 시장 합계예요.</div></div>')


def render_flows():
    """시장별 개인·외국인·기관 순매수(억원) + 기관 세부(연기금·투신 등)와 20일 추이."""
    flows = load_flows()
    latest = [df["date"].iloc[-1] for df in flows.values() if not df.empty]
    if not latest:
        st.caption("시장 전체 투자자별 일별 추이를 불러오지 못했어요. 잠시 뒤 자동으로 다시 시도해요.")
        return
    with st.expander(f"💰 투자자별 순매수 · 연기금·투신 ({max(latest):%m/%d}, 억원)"):
        cols = data.INVESTORS + data.INST_DETAIL
        head = "".join(f"<th>{c}</th>" for c in cols)
        body = ""
        for market, df in flows.items():
            if df.empty:
                continue
            last = df.iloc[-1]
            cum5 = df.tail(5)[cols].sum(min_count=1)
            body += f"<tr><td>{market} <small>{last['date']:%m/%d}</small></td>" + "".join(
                f"<td>{_flow_cell(last[c])}</td>" for c in cols) + "</tr>"
            body += f"<tr><td style='color:#8A979F'>{market} 5일</td>" + "".join(
                f"<td>{_flow_cell(cum5[c])}</td>" for c in cols) + "</tr>"
        st.markdown(f'<div style="overflow-x:auto"><table class="inst"><tr><th></th>{head}</tr>{body}</table></div>',
                    unsafe_allow_html=True)
        tabs = st.tabs(list(flows))
        for tab, (market, df) in zip(tabs, flows.items()):
            with tab:
                if df.empty:
                    st.caption("데이터를 불러오지 못했어요.")
                    continue
                pick = st.radio("보기", ["개인·외국인·기관", "기관 세부"], horizontal=True, key=f"flowpick_{market}",
                                label_visibility="collapsed")
                series = data.INVESTORS if pick == "개인·외국인·기관" else data.INST_DETAIL
                sub = df.tail(20).dropna(axis=1, how="all")
                series = [c for c in series if c in sub.columns]
                if not series:
                    st.caption("기관 세부는 새 네이버 API에서만 받아와요. 지금은 불러오지 못했어요.")
                    continue
                long = sub.melt("date", value_vars=series, var_name="투자자", value_name="순매수")
                palette = ["#9AA9B3", "#5B47F5", "#F2B134"] if len(series) == 3 else \
                    ["#5B47F5", "#A59BFA", "#F2B134", "#9AA9B3", "#C77D4B", "#D6333B", "#51616C"]
                chart = (
                    alt.Chart(long).mark_bar()
                    .encode(
                        x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False)),
                        xOffset=alt.XOffset("투자자:N", sort=series),
                        y=alt.Y("순매수:Q", title="억원"),
                        color=alt.Color("투자자:N", sort=series, scale=alt.Scale(domain=series, range=palette[:len(series)]),
                                        legend=alt.Legend(orient="bottom", title=None)),
                        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "투자자:N", alt.Tooltip("순매수:Q", format="+,.0f")],
                    )
                    .properties(height=240, width="container")
                )
                st.altair_chart(chart)
        st.caption("네이버 증권 투자자별 매매동향 기준. 기관 = 금융투자+보험+투신(사모)+은행+기타금융+연기금이고, "
                   "기타법인은 기관에 넣지 않아요. 장중 값은 잠정치예요.")


def _light(sig: dict | None) -> str:
    """G·Y·R 세 칸 신호등. 켜진 칸에만 색과 글자."""
    on = sig["code"] if sig else None
    return '<span class="tl">' + "".join(
        f'<i class="{c}">{c}</i>' if c == on else "<i></i>" for c in ("G", "Y", "R")) + "</span>"


def _sig_item(label: str, sig: dict | None, n: int) -> str:
    if not sig:
        return f'<span class="sig-item">{label} {_light(None)} <span>-</span></span>'
    slope = "상승" if sig["rising"] else "하락"
    return (f'<span class="sig-item" title="{n}일선 {sig["ma"]:,.2f}, {n}일선 기울기 {slope}">{label} {_light(sig)} '
            f'<span class="sig-txt {sig["code"]}">{sig["text"]}</span>'
            f'<span>{n}일선 {sig["dist"]:+.1f}%</span></span>')


def _signal_panel(hist: dict, board: pd.DataFrame | None) -> str:
    """시장신호: 코스피·코스닥 단기(20일선)·장기(60일선) 신호등 + 지수 RS."""
    cells = ""
    for name, sym in data.MARKETS.items():
        df = hist.get(sym, (data.empty_frame(), None))[0]
        s20, s60 = data.ma_signal(df, 20), data.ma_signal(df, 60)
        rs = data.index_rs(df, board) if board is not None else {}
        n = lambda v: "-" if v is None else f"{v:.0f}"  # noqa: E731
        cells += (f'<div class="sig-mk"><span class="sig-name">{name}</span>'
                  f'{_sig_item("단기", s20, 20)}{_sig_item("장기", s60, 60)}'
                  f'<span class="sig-rs">RS <b>{n(rs.get("rs"))}</b> · 1M {n(rs.get("rs_1m"))} · '
                  f'3M {n(rs.get("rs_3m"))} · 6M {n(rs.get("rs_6m"))}</span></div>')
    return f'<div class="sig">{cells}</div>'


def load_turnover_hist() -> dict:
    return swr(("turnover",), 1800, data.fetch_market_turnover_hist, first_wait=False, empty={})


def _shares_txt(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v / 1e8:,.2f}억주" if v >= 1e8 else f"{v / 1e4:,.0f}만주"


def _money_txt(v) -> str:
    return "-" if v is None or pd.isna(v) else data.format_krw(v)


def _ratio_cell(x) -> str:
    if x is None or pd.isna(x):
        return '<td class="tv-x">-</td>'
    pct = (x - 1) * 100
    col = UP if pct >= 10 else (DOWN if pct <= -10 else "#51616C")
    word = "많음" if pct >= 10 else ("적음" if pct <= -10 else "비슷")
    w = max(4, min(100, x / 2 * 100))
    return (f'<td class="tv-x"><div class="tv-bar"><i style="width:{w:.0f}%;background:{col}"></i><b></b></div>'
            f'<span style="color:{col}">{x:.2f}배 · {pct:+.0f}% {word}</span></td>')


def _pct_cell(v) -> str:
    if v is None or pd.isna(v):
        return "<td>-</td>"
    col = UP if v >= 10 else (DOWN if v <= -10 else "#51616C")
    return f'<td style="color:{col};font-weight:700">{v:+.0f}%</td>'


def render_turnover(overview: dict):
    """코스피·코스닥 오늘 거래대금·거래량(실시간)과 평소(5·20·60일 평균) 대비."""
    hist = load_turnover_hist()
    if not hist:
        st.caption("코스피·코스닥 거래대금 평균을 불러오는 중이에요. 잠시 뒤 새로고침하면 나와요.")
        return
    res = {m: data.market_turnover(hist.get(m), overview.get(m)) for m in data.MARKETS}
    res = {m: r for m, r in res.items() if r}
    if not res:
        st.caption("코스피·코스닥 거래대금 자료를 받지 못했어요.")
        return
    any_r = next(iter(res.values()))
    frac, is_open = any_r["frac"], any_r["open"]
    when = f"장 진행 {frac * 100:.0f}% · 실시간" if is_open else "장 마감 기준(오늘 또는 마지막 거래일)"

    # 요약 카드: 시장별 오늘 거래대금
    chips = []
    for m, r in res.items():
        v = r["val"]
        x = v["x_now"]
        col = UP if (x or 1) >= 1.1 else (DOWN if (x or 1) <= 0.9 else "#51616C")
        chips.append(
            f'<div class="tv-chip"><div class="tv-chip-h">{m} 거래대금</div>'
            f'<div class="tv-chip-v">{_money_txt(v["now"])}<small>원</small></div>'
            f'<div class="tv-chip-s" style="color:{col}">{"이 시각 평소" if is_open else "20일 평균"}의 '
            f'{(x if is_open else (v["now"] / v["a20"] if v["now"] else float("nan"))):.2f}배</div>'
            f'<div class="tv-chip-f">마감 예상 {_money_txt(v["proj"])} · 20일 평균 {_money_txt(v["a20"])}</div></div>')
    tot_now = sum((r["val"]["now"] or 0) for r in res.values())
    tot_same = sum(r["val"]["same_time"] for r in res.values())
    chips.append(
        f'<div class="tv-chip tot"><div class="tv-chip-h">두 시장 합계</div>'
        f'<div class="tv-chip-v">{_money_txt(tot_now)}<small>원</small></div>'
        f'<div class="tv-chip-s">{"이 시각 평소" if is_open else "20일 평균"}의 {tot_now / tot_same if tot_same else float("nan"):.2f}배</div>'
        f'<div class="tv-chip-f">{when}</div></div>')

    rows = []
    for m, r in res.items():
        for key, lab, fmt in (("val", "거래대금", _money_txt), ("vol", "거래량", _shares_txt)):
            v = r[key]
            rows.append(
                f'<tr><td class="l"><b>{m}</b></td><td class="l">{lab}</td>'
                f'<td class="now">{fmt(v["now"])}</td><td>{fmt(v["same_time"]) if is_open else "-"}</td>'
                f'{_ratio_cell(v["x_now"] if is_open else (v["now"] / v["a20"] if v["now"] else None))}'
                f'<td>{fmt(v["proj"]) if is_open else fmt(v["now"])}</td>'
                f'<td>{fmt(v["a5"])}</td><td>{fmt(v["a20"])}</td><td>{fmt(v["a60"])}</td>'
                f'{_pct_cell(v["vs5"])}{_pct_cell(v["vs20"])}{_pct_cell(v["vs60"])}</tr>')
    head = ("<tr><th class='l'>시장</th><th class='l'>항목</th><th>오늘 지금까지</th><th>같은 시각 평소</th>"
            "<th>평소 대비</th><th>마감 예상</th><th>5일 평균</th><th>20일 평균</th><th>60일 평균</th>"
            "<th>예상 vs 5일</th><th>예상 vs 20일</th><th>예상 vs 60일</th></tr>")
    st.markdown(
        f'<div class="tv-box"><div class="tv-title"><b>💵 코스피 · 코스닥 거래대금 · 거래량</b><span>{when}</span></div>'
        f'<div class="tv-chips">{"".join(chips)}</div>'
        f'<div class="tv-wrap"><table class="tv-tbl"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>'
        f'<div class="tv-note">같은 시각 평소 = 20일 평균 × 장 진행 비율 · 마감 예상 = 지금 속도 그대로 15:30까지 갔을 때 · '
        f'장 초반·막판은 거래가 몰려서 예상이 크게 나올 수 있어요 · 평균은 30분마다, 오늘 값은 새로고침마다 갱신</div></div>',
        unsafe_allow_html=True)


HERO_CSS = """<style>
.hx-top{display:flex;justify-content:space-between;align-items:center;margin:.1rem 0 .5rem;font-size:.82rem;color:#5B6670}
.hx-top b{color:#1C2430;font-size:.95rem}
.hx-live{background:#F1F3F6;border-radius:8px;padding:4px 10px;font-weight:700;color:#26343E;white-space:nowrap}
.hx-live i{display:inline-block;width:8px;height:8px;border-radius:50%;background:#2EAD5B;margin-right:6px}
.hx-live.off i{background:#9AA4AE}.hx-live small{color:#8A93A0;font-weight:500;margin-left:6px}
.hx-row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:12px}
.hx-card{background:#fff;border:1px solid #E7E9EE;border-radius:16px;padding:16px 20px 14px;box-shadow:0 1px 3px rgba(20,30,50,.04)}
.hx-name{font-size:.8rem;font-weight:700;color:#3A4450;letter-spacing:.02em}.hx-name span{color:#8A93A0;font-weight:500}
.hx-v{display:flex;align-items:baseline;gap:22px;margin:4px 0 2px;flex-wrap:wrap}
.hx-v b{font-size:2.1rem;font-weight:800;color:#141B24;letter-spacing:-.02em}
.hx-v span{font-size:.95rem;font-weight:700}
.hx-sub{font-size:.78rem;color:#7A8490;margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.hx-chip{border-radius:6px;padding:1px 7px;font-weight:700;font-size:.72rem}
.hx-chip.G{background:#E6F6EC;color:#1E8C4A}.hx-chip.Y{background:#FFF4D6;color:#A26B00}.hx-chip.R{background:#FDE8E9;color:#C0303A}
.hx-chip.n{background:#F1F3F6;color:#51616C}
.hx-br{height:8px;border-radius:5px;background:#EEF0F3;overflow:hidden;display:flex}
.hx-br i{display:block;height:100%}
.hx-brl{display:flex;justify-content:space-between;font-size:.7rem;color:#8A93A0;margin:3px 0 8px}
.hx-fl{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.hx-f{font-size:.78rem;color:#3A4450;font-weight:600}
.hx-f b{margin-left:4px}
.hx-fb{position:relative;height:6px;background:#F1F3F6;border-radius:3px;margin-top:4px}
.hx-fb i{position:absolute;top:0;bottom:0;border-radius:3px}
.hx-fb em{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:#B9C0C8}
.hx-today{display:flex;align-items:center;gap:18px;background:#fff;border:1px solid #E7E9EE;border-radius:14px;
padding:12px 20px;margin-bottom:12px;flex-wrap:wrap}
.hx-today .t{font-weight:800;color:#1C2430;font-size:.92rem;flex:none}
.hx-today .m{flex:1;min-width:200px;font-size:.82rem;color:#5B6670}
.hx-today .m b{color:#1C2430}
.hx-g{display:flex;align-items:center;gap:12px;flex:none}
.hx-gt{position:relative;width:190px;height:6px;border-radius:3px;background:linear-gradient(90deg,#6FA0E6,#E9ECF0 50%,#EF7A80)}
.hx-gt i{position:absolute;top:-5px;width:14px;height:14px;border-radius:50%;background:#fff;border:3px solid #D6333B;transform:translateX(-50%)}
.hx-gt i.dn{border-color:#1F66C9}
.hx-gl{font-weight:800;font-size:.95rem}
.hx-top em{font-style:normal}
@media (max-width:700px){.hx-d{display:none}.hx-row{grid-template-columns:1fr;gap:10px}.hx-card{padding:12px 14px}.hx-v b{font-size:1.7rem}
.hx-v{gap:12px}.hx-gt{width:130px}.hx-today{padding:10px 14px;gap:10px}}
</style>"""


def _hero_flow_cells(deal: dict | None) -> str:
    d = deal or {}
    vals = {k: d.get(k) for k in ("외국인", "기관", "개인")}
    mx = max([abs(v) for v in vals.values() if v is not None and v == v] or [1.0]) or 1.0
    out = ""
    for k, v in vals.items():
        if v is None or v != v:
            out += f'<div class="hx-f">{k} <b>-</b><div class="hx-fb"><em></em></div></div>'
            continue
        w = abs(v) / mx * 50
        col = _sign_color(v)
        pos = f"left:50%;width:{w:.1f}%" if v >= 0 else f"left:{50 - w:.1f}%;width:{w:.1f}%"
        out += (f'<div class="hx-f">{k} <b style="color:{col}">{_eok(v)}</b>'
                f'<div class="hx-fb"><i style="{pos};background:{col}"></i><em></em></div></div>')
    return f'<div class="hx-fl">{out}</div>'


def _hero_card(name: str, sm: dict | None, df: pd.DataFrame | None, board: pd.DataFrame | None, b60) -> str:
    sm = sm or {}
    hsm = data.index_summary(df) if df is not None else None
    last = sm.get("last") if sm.get("last") is not None else (hsm or {}).get("last")
    eng = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}.get(name, name)
    if last is None:
        return f'<div class="hx-card"><div class="hx-name">{eng} <span>{name}</span></div><div class="idx-err">불러오지 못했어요</div></div>'
    rate = sm.get("rate") if sm.get("rate") is not None else (hsm or {}).get("change")
    diff = sm.get("diff")
    if diff is None and rate is not None:
        diff = last - last / (1 + rate / 100)
    col = _sign_color(rate)
    arrow = "▲" if (rate or 0) > 0 else ("▼" if (rate or 0) < 0 else "")
    chg = f'<span style="color:{col}">{arrow} {abs(rate or 0):.2f}% {diff:+,.2f}</span>' if rate is not None and diff is not None else ""
    s20, s60 = data.ma_signal(df, 20), data.ma_signal(df, 60)
    if s60:
        trend = {"G": "강한 상승 흐름", "Y": "방향을 고르는 중", "R": "하락 흐름"}[s60["code"]]
        sub = f'60일선 {"위" if s60["above"] else "아래"} {s60["dist"]:+.1f}% · {trend}'
    else:
        sub = "60일선 계산 중"
    chips = ""
    for lab, sg in (("단기 20일", s20), ("장기 60일", s60)):
        if sg:
            chips += f'<span class="hx-chip {sg["code"]}">{lab} {sg["text"]}</span>'
    try:
        rs = data.index_rs(df, board).get("rs") if board is not None else None
    except Exception:  # noqa: BLE001
        rs = None
    if rs is not None:
        chips += f'<span class="hx-chip n">지수 RS {rs:.0f}</span>'
    if b60:
        chips += f'<span class="hx-chip n" title="보드 종목 중 60일선 위 비율">60일선 위 종목 {b60[0]:.0f}%</span>'
    br = sm.get("breadth") or {}
    up = (br.get("상승") or 0) + (br.get("상한") or 0)
    flat = br.get("보합") or 0
    dn = (br.get("하락") or 0) + (br.get("하한") or 0)
    tot = up + flat + dn
    bar = ""
    if tot:
        bar = (f'<div class="hx-br"><i style="width:{up / tot * 100:.1f}%;background:#E5484D"></i>'
               f'<i style="width:{flat / tot * 100:.1f}%;background:#D5DAE0"></i>'
               f'<i style="width:{dn / tot * 100:.1f}%;background:#4C82D9"></i></div>'
               f'<div class="hx-brl"><span style="color:#D6333B">상승 {up:,.0f}</span><span>보합 {flat:,.0f}</span>'
               f'<span style="color:#1F66C9">하락 {dn:,.0f}</span></div>')
    return (f'<div class="hx-card"><div class="hx-name">{eng} <span>{name}</span></div>'
            f'<div class="hx-v"><b>{last:,.2f}</b>{chg}</div>'
            f'<div class="hx-sub">{sub}{chips}</div>{bar}{_hero_flow_cells(sm.get("deal"))}</div>')


def _hero_today(overview: dict, hist: dict) -> str:
    mood = data.market_mood(overview)
    ratio = mood[1] if mood else 0.5
    tot = {k: 0.0 for k in ("외국인", "기관", "개인")}
    got = False
    for sm in overview.values():
        d = (sm or {}).get("deal") or {}
        for k in tot:
            if d.get(k) is not None and d.get(k) == d.get(k):
                tot[k] += d[k]
                got = True
    if got:
        buy = [k for k in ("외국인", "기관") if tot[k] > 0]
        if len(buy) == 2:
            flow = "수급은 <b>외국인·기관 동반 순매수</b>"
        elif buy:
            other = "기관" if buy[0] == "외국인" else "외국인"
            flow = f"수급은 <b>{buy[0]} 중심 순매수</b>({other} 매도)"
        else:
            flow = "수급은 <b>외국인·기관 동반 순매도</b>" + (" — 개인이 받는 중" if tot["개인"] > 0 else "")
    else:
        flow = "투자자별 수급을 불러오는 중"
    oks = []
    for sym in data.MARKETS.values():
        sm = data.index_summary(hist.get(sym, (data.empty_frame(), None))[0])
        oks.append(None if not sm else sm.get("above60"))
    if all(o is True for o in oks):
        rule = "코스피·코스닥 모두 60일선 위 — <b>돌파매매 가능 구간</b>"
    elif any(o is False for o in oks):
        rule = "60일선 아래 지수가 있어요 — <b>원칙상 신규 돌파매매 쉬는 구간</b>"
    else:
        rule = ""
    if ratio >= 0.57:
        lab, cls, col = "상승 우위", "", "#D6333B"
    elif ratio <= 0.43:
        lab, cls, col = "하락 우위", "dn", "#1F66C9"
    else:
        lab, cls, col = "팽팽", "", "#51616C"
    return (f'<div class="hx-today"><span class="t">오늘의 시장</span>'
            f'<span class="m">{flow}{" · " + rule if rule else ""}</span>'
            f'<span class="hx-g"><span class="hx-gt" title="상승 종목 비율 {ratio * 100:.0f}%"><i class="{cls}" style="left:{ratio * 100:.0f}%"></i></span>'
            f'<span class="hx-gl" style="color:{col}">{lab}</span></span></div>')


def _hero_live(overview: dict) -> str:
    sm = overview.get("코스피") or {}
    st_ = str(sm.get("status") or "")
    t = str(sm.get("time") or "")
    hm = data.now_kst().strftime("%H:%M")
    try:
        if re.fullmatch(r"\d{12,14}", t):                # 202610281000(00)
            hm = f"{t[8:10]}:{t[10:12]}"
        elif t:
            ts = pd.Timestamp(t)
            hm = ts.strftime("%H:%M")
    except (ValueError, TypeError):
        pass
    live = st_ == "OPEN"
    return (f'<div class="hx-top"><span><b>실시간 시장</b><em class="hx-d"> · 지수 · 수급 · 오늘 돈이 몰린 섹터</em></span>'
            f'<span class="hx-live{"" if live else " off"}"><i></i>{"장중 실시간" if live else "장 마감"}<small>{hm} 기준</small></span></div>')


def render_market(board: pd.DataFrame | None = None):
    """맨 위 시장 요약: 시장신호(신호등·지수 RS) → 코스피·코스닥 카드 + 오늘의 시장 → 해외 지수·환율·유가."""
    overview = load_overview()
    hist = load_indexes()
    hist_sm = {idx["name"]: data.index_summary(hist.get(idx["symbol"], (data.empty_frame(), None))[0])
               for idx in data.INDEXES}
    try:
        b60 = _breadth60(board)
    except Exception:
        b60 = {}
    cards = "".join(_hero_card(name, overview.get(name), hist.get(sym, (data.empty_frame(), None))[0], board, b60.get(name))
                    for name, sym in data.MARKETS.items())
    st.markdown(HERO_CSS + _hero_live(overview) + f'<div class="hx-row">{cards}</div>' + _hero_today(overview, hist),
                unsafe_allow_html=True)
    with st.expander("📊 거래대금 · 평소 대비(코스피·코스닥)"):
        render_turnover(overview)

    chips = []
    for idx in data.INDEXES:
        if idx["name"] in data.MARKETS:
            continue
        sm = hist_sm.get(idx["name"])
        if not sm:
            chips.append(f'<div class="idx"><div class="idx-name">{idx["name"]}</div>'
                         f'<div class="idx-err">불러오지 못했어요</div></div>')
            continue
        color = _sign_color(sm["change"])
        if idx.get("kind", "index") == "index":
            if sm["above60"] is None:
                badge = ""
            elif sm["above60"]:
                badge = f'<span class="idx-badge on">60일선 위 {sm["dist60"]:+.1f}%</span>'
            else:
                badge = f'<span class="idx-badge off">60일선 아래 {sm["dist60"]:+.1f}%</span>'
        else:
            badge = (f'<span class="idx-badge neutral">20일 {sm["chg20"]:+.1f}%</span>'
                     if sm.get("chg20") is not None else "")
        chips.append(
            f'<div class="idx"><div class="idx-name">{idx["name"]}<small>{sm["date"]:%m/%d}</small></div>'
            f'<span class="idx-val">{idx.get("unit", "")}{sm["last"]:,.2f}</span>'
            f'<span class="idx-chg" style="color:{color}">{sm["change"]:+.2f}%</span><br>{badge}</div>'
        )
    with st.expander("🌐 해외 지수 · 환율 · 유가 · 차트"):
        st.markdown('<div class="idx-row">' + "".join(chips) + "</div>", unsafe_allow_html=True)
        tabs = st.tabs([i["name"] for i in data.INDEXES])
        for tab, idx in zip(tabs, data.INDEXES):
            with tab:
                df, _err = hist.get(idx["symbol"], (data.empty_frame(), None))
                if df.empty:
                    st.caption("지수 데이터를 불러오지 못했어요. 잠시 뒤 다시 열어 보세요.")
                    continue
                h = df.set_index("date")["close"].astype(float)
                chart = pd.DataFrame({idx["name"]: h, "20일선": h.rolling(20).mean(), "60일선": h.rolling(60).mean()}).tail(180)
                price_chart(chart, ["#111827", "#5B47F5", "#F2B134"], height=240)
        st.caption("시장신호: 단기 20일선 · 장기 60일선 — 🟢 선 위 + 선 상승 · 🟡 하나만 · 🔴 선 아래 + 선 하락. "
                   "지수 RS = 보드 종목과 같은 잣대의 1~99. 해외는 야후(15분 지연), 유가는 근월물.")
    render_flows()


def _chip_leader(df: pd.DataFrame, group: str) -> str:
    _LEADER_MEMO = _memo_store()["leader"]
    # (속도) 분류마다 표 전체를 다시 거르지 않고, 같은 표면 대장주 목록을 한 번만 만들어요
    memo = _LEADER_MEMO.get("v")
    if memo is None or memo[0] is not df:
        top = df[(df["lead_rank"] == 1) & (df["lead_ok"] == True)]  # noqa: E712
        memo = (df, dict(zip(top["group"], top["name"])) if len(top) else {})
        _LEADER_MEMO["v"] = memo
    name = memo[1].get(group)
    return f'<b style="color:#F2B134">👑 대장 {html.escape(str(name))}</b><br>' if name else ""


# ─────────────────────────── 🔥 섹터 강세 카드(첫 화면) ───────────────────────────
SM_CSS = """<style>
.sm-hd{display:flex;align-items:center;gap:10px;margin:.3rem 0 .2rem;font-size:.82rem;color:#5B6670;font-weight:700}
.sm-wrap{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:.3rem 0 .6rem}
.sm-card{background:#fff;border:1px solid #E7E9EE;border-radius:12px;overflow:hidden;box-shadow:0 1px 2px rgba(20,30,50,.05)}
.sm-head{display:flex;align-items:center;justify-content:space-between;padding:7px 10px;color:#fff;gap:6px}
.sm-head.r1{background:#C2185B}.sm-head.r2{background:#556B7A}.sm-head.r3{background:#6A55C2}.sm-head.rn{background:#3F4B57}
.sm-ttl{display:flex;align-items:center;gap:6px;font-weight:800;font-size:.92rem;min-width:0;line-height:1.15}
.sm-ttl span.nm{overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.sm-rk{background:#fff;color:#1C2430;border-radius:50%;min-width:20px;height:20px;display:inline-flex;align-items:center;
justify-content:center;font-size:.72rem;font-weight:800;flex:none}
.sm-pill{background:#fff;color:#1C2430;border-radius:6px;padding:2px 7px;text-align:center;font-weight:800;font-size:.8rem;flex:none}
.sm-sub{display:flex;justify-content:space-between;gap:6px;margin:6px 8px 2px;padding:3px 8px;background:#F2F4F7;border-radius:6px;
font-size:.68rem;font-weight:700;color:#3A4450;flex-wrap:wrap}
.sm-sub span:last-child{color:#7A8490;white-space:nowrap;font-weight:500}.sm-sub em{font-style:normal}.sm-sub em.nm2{display:none}
.sm-news{margin:2px 10px 2px;font-size:.66rem;color:#7A8490;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sm-news a{color:#5B6670;text-decoration:none}.sm-news .t{color:#6A55C2;font-weight:700;margin-right:5px}
a.sm-row{display:block;padding:5px 10px 4px;border-bottom:1px solid #F0F1F4;text-decoration:none;color:#1C2430}
a.sm-row:hover{background:#FAFAFD}
.sm-l1,.sm-l2{display:flex;justify-content:space-between;align-items:center;gap:5px}
.sm-nm{font-weight:700;font-size:.82rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.sm-nm.nh{color:#D6333B;text-decoration:underline}
.sm-chg{font-weight:800;font-size:.82rem;flex:none}.sm-chg em{font-style:normal}.sm-chg em.c1{display:none}
.sm-l2{font-size:.7rem;color:#7A8490;margin-top:1px}
.sm-tv.big{color:#D6333B;font-weight:800}
.sm-dot{width:6px;height:6px;border-radius:50%;background:#2EAD5B;display:inline-block;flex:none}
.sm-dot.off{background:#D5D9DE}
.sm-ic{display:flex;align-items:center;gap:4px;flex:none;font-size:.7rem}
.sm-bar{position:relative;height:5px;margin-top:3px;background:#EEF0F3;border-radius:2px}
.sm-bar i{position:absolute;top:0;bottom:0}
.sm-bar i.z{width:2px;background:#1C2430;top:-2px;bottom:-2px}
.sm-bar i.w{top:1.5px;bottom:1.5px}
.sm-bar i.b{top:0;bottom:0;border-radius:1px}
.sm-card details summary{list-style:none;cursor:pointer;text-align:center;font-size:.72rem;color:#8A93A0;padding:5px}
.sm-card details summary::-webkit-details-marker{display:none}
.sm-card details[open] summary .more{display:none}.sm-card details:not([open]) summary .less{display:none}
.sm-legend{font-size:.72rem;color:#8A93A0;margin:-.2rem 0 .5rem}
@media (max-width:1100px){.sm-nm,.sm-chg{font-size:.76rem}.sm-ttl{font-size:.84rem}.sm-sub{font-size:.62rem}}
@media (max-width:700px){.sm-wrap{gap:4px}.sm-card{border-radius:8px}
.sm-head{padding:4px 4px;gap:2px;flex-direction:column;align-items:stretch}
.sm-ttl{font-size:.66rem;gap:3px}.sm-rk{min-width:14px;height:14px;font-size:.55rem}
.sm-pill{font-size:.62rem;padding:0 3px;border-radius:4px}
.sm-sub,.sm-news,.sm-l2,.sm-ic{display:none}
.sm-chg em.c2{display:none}.sm-chg em.c1{display:inline}
a.sm-row{padding:3px 4px 2px}.sm-l1{gap:2px}.sm-nm{font-size:.62rem;letter-spacing:-.03em}.sm-chg{font-size:.6rem;letter-spacing:-.03em}
.sm-bar{height:3px;margin-top:2px}.sm-bar i.z{width:1px;top:-1px;bottom:-1px}.sm-bar i.w{top:1px;bottom:1px}
.sm-card details summary{font-size:.6rem;padding:3px}.sm-legend{font-size:.64rem}}
</style>"""


@st.cache_data(ttl=300, show_spinner=False, max_entries=4)
def _sm_news(codes: tuple[str, ...]) -> dict:
    """섹터 대표 종목의 가장 최근 뉴스 제목(5분 기억)."""
    from concurrent.futures import ThreadPoolExecutor as _TPE

    def one(c):
        try:
            return c, (holdings.naver_news(c, 3) or [None])[0]
        except Exception:  # noqa: BLE001
            return c, None
    with _TPE(max_workers=6) as ex:
        return dict(ex.map(one, codes))


def sector_movers(df: pd.DataFrame, by: str = "group", sort: str = "money", min_n: int = 3) -> list[dict]:
    """섹터(세부 분류·산업)별 오늘 강세: 구성 종목 등락·거래대금, 상승 종목 거래대금 순 또는 강도 순."""
    d = df[df["code"].map(data.is_kr) & pd.to_numeric(df["price"], errors="coerce").notna()].copy()
    if "halted" in d:
        d = d[d["halted"] != True]  # noqa: E712
    d = d[~d["group"].isin(list(data.NON_SECTOR_GROUPS))]
    d["chg"] = pd.to_numeric(d["change"], errors="coerce")
    d["tv"] = pd.to_numeric(d.get("tv_live"), errors="coerce").fillna(0)
    d = d.drop_duplicates("code")
    out = []
    for g, sub in d.groupby(by):
        sub = sub[sub["chg"].notna()]
        if len(sub) < min_n:
            continue
        w = sub["tv"].where(sub["tv"] > 0, 0)
        wchg = float((sub["chg"] * w).sum() / w.sum()) if w.sum() > 0 else float(sub["chg"].mean())
        up = sub[sub["chg"] > 0]
        out.append({"name": g, "chg": wchg, "tv": float(sub["tv"].sum()), "up_tv": float(up["tv"].sum()),
                    "n": len(sub), "n_up": len(up), "rows": sub.sort_values(["chg", "tv"], ascending=False)})
    key = {"money": lambda x: (-x["up_tv"] * (1 if x["chg"] > 0 else 0.01), -x["chg"]),
           "strength": lambda x: (-x["chg"], -x["tv"])}[sort]
    return sorted(out, key=key)


def _sm_bar(r, lo: float, hi: float) -> str:
    """가로 미니 캔들: 전일 종가(검은 선) 기준 저가~고가(꼬리) · 시가~현재가(몸통). 축은 카드 안에서 같아요."""
    prev = r.get("prev")
    price = r.get("price")
    if not prev or prev != prev or not price:
        return '<div class="sm-bar"></div>'
    pct = lambda v: (float(v) / float(prev) - 1) * 100  # noqa: E731
    span = (hi - lo) or 1.0
    x = lambda p: max(0.0, min(100.0, (p - lo) / span * 100))  # noqa: E731
    o = r.get("day_open")
    o = pct(o) if o and o == o else 0.0
    c = pct(price)
    dh, dl = r.get("day_high"), r.get("day_low")
    h = max(pct(dh), c, o) if dh and dh == dh else max(c, o)
    lw = min(pct(dl), c, o) if dl and dl == dl else min(c, o)
    parts = []
    if lw < 0:            # 전일 종가 아래로 내려간 꼬리 = 파랑
        parts.append(f'<i class="w" style="left:{x(lw):.1f}%;width:{x(min(0, h)) - x(lw):.1f}%;background:{DOWN}"></i>')
    if h > 0:             # 위 꼬리 = 빨강
        parts.append(f'<i class="w" style="left:{x(max(0, lw)):.1f}%;width:{x(h) - x(max(0, lw)):.1f}%;background:{UP}"></i>')
    b0, b1 = sorted((o, c))
    parts.append(f'<i class="b" style="left:{x(b0):.1f}%;width:{max(1.5, x(b1) - x(b0)):.1f}%;'
                 f'background:{UP if c >= o else DOWN}"></i>')
    parts.append(f'<i class="z" style="left:calc({x(0):.1f}% - 1px)"></i>')
    return f'<div class="sm-bar">{"".join(parts)}</div>'


def _sm_eok(v) -> str:
    if not v or v != v:
        return "-"
    return f"{v / 1e12:,.1f}조" if v >= 1e12 else f"{v / 1e8:,.0f}억"


def render_sector_movers(df: pd.DataFrame, quotes: dict):
    """첫 화면: 오늘 섹터별 강세 카드(순위 · 섹터 등락·거래대금 · 대표 뉴스 · 종목별 등락·거래대금·가로 캔들)."""
    st.markdown('<div class="sm-hd">🔥 주도 테마 · 오늘 돈이 몰린 섹터</div>', unsafe_allow_html=True)
    with st.container(key="sm_box"):
        c1, c2, c3, c4 = st.columns([1.25, 1.25, 1, 1])
        by_lab = c1.segmented_control("묶기", ["세부 분류", "산업"], default="세부 분류", key="sm_by",
                                      label_visibility="collapsed") or "세부 분류"
        sort_lab = c2.segmented_control("정렬", ["돈 몰린 순", "상승률 순"], default="돈 몰린 순", key="sm_sort",
                                        label_visibility="collapsed",
                                        help="돈 몰린 순 = 오늘 오른 종목들의 거래대금 합계가 큰 섹터부터 · 상승률 순 = 거래대금 가중 평균 등락") or "돈 몰린 순"
        n_cards = c3.selectbox("카드", [4, 8, 12, 16], index=1, key="sm_n", label_visibility="collapsed",
                               format_func=lambda n: f"섹터 {n}개")
        n_rows = c4.selectbox("종목", [5, 8, 10], index=1, key="sm_rows", label_visibility="collapsed",
                              format_func=lambda n: f"먼저 {n}종목")
    by = "group" if by_lab == "세부 분류" else "sector"
    sort = "money" if sort_lab == "돈 몰린 순" else "strength"
    secs = tab_memo(f"sector_movers_{len(df)}", (by, sort), lambda: sector_movers(df, by, sort))[:int(n_cards)]
    if not secs:
        return
    lead_codes = []
    for s in secs:
        up = s["rows"][s["rows"]["chg"] > 0]
        top = (up if len(up) else s["rows"]).sort_values("tv", ascending=False).iloc[0]
        s["lead_code"] = top["code"]
        lead_codes.append(top["code"])
    news = _sm_news(tuple(lead_codes))
    status = data.market_status(quotes)
    upd = f'{"장중" if status.startswith("장중") else "마감"} {data.now_kst():%H:%M} 업데이트'
    cards = []
    for i, s in enumerate(secs, 1):
        rows = s["rows"]
        show = rows.head(15)
        lo = float(min(-2.0, *[min(_pctv(r, "day_low"), _pctv(r, "price")) for _, r in show.iterrows()]))
        hi = float(max(2.0, *[max(_pctv(r, "day_high"), _pctv(r, "price")) for _, r in show.iterrows()]))
        pad = (hi - lo) * 0.06
        lo, hi = lo - pad, hi + pad
        items = []
        for _, r in show.iterrows():
            chg, tv = r["chg"], r["tv"]
            nh = r.get("days_since_high") == 0
            rs_ok = (r.get("rs") or 0) >= 70 and r.get("aligned") is True
            hot = (r.get("dist_ma20") or 0) >= 30
            col = UP if chg > 0 else (DOWN if chg < 0 else "#26343E")
            wt = "800" if abs(chg) >= 10 else "700"
            items.append(
                f'<a class="sm-row" target="_self" href="?chart={html.escape(str(r["code"]))}">'
                f'<div class="sm-l1"><span class="sm-nm{" nh" if nh else ""}">{html.escape(str(r["name"]))}</span>'
                f'<span class="sm-ic"><span class="sm-dot{"" if rs_ok else " off"}"></span>{"⚠️" if hot else ""}'
                f'</span><span class="sm-chg" style="color:{col};font-weight:{wt}"><em class="c2">{chg:+.2f}%</em><em class="c1">{chg:+.1f}</em></span></div>'
                f'<div class="sm-l2"><span>{r["price"]:,.0f}</span>'
                f'<span class="sm-tv{" big" if tv >= 1e11 else ""}">{_sm_eok(tv)}</span></div>'
                f'{_sm_bar(r, lo, hi)}</a>')
        first, rest = items[:int(n_rows)], items[int(n_rows):]
        more = (f'<details><summary><span class="more">더보기 {len(rest)}</span><span class="less">접기</span></summary>'
                f'{"".join(rest)}</details>') if rest else ""
        nw = news.get(s["lead_code"])
        news_html = ""
        if nw:
            tm = str(nw.get("time") or "")[-5:]
            news_html = (f'<div class="sm-news"><span class="t">{html.escape(tm)}</span>'
                         f'<a href="{html.escape(nw["url"])}" target="_blank" title="{html.escape(nw["title"])}">'
                         f'{html.escape(nw["title"])}</a></div>')
        rk = "r1" if i == 1 else ("r2" if i == 2 else ("r3" if i == 3 else "rn"))
        cards.append(
            f'<div class="sm-card"><div class="sm-head {rk}"><div class="sm-ttl"><span class="sm-rk">{i}</span>'
            f'<span class="nm">{html.escape(str(s["name"]))}</span></div>'
            f'<span class="sm-pill" style="color:{UP if s["chg"] > 0 else (DOWN if s["chg"] < 0 else "#1C2430")}">{s["chg"]:+.1f}%</span></div>'
            f'<div class="sm-sub"><span><em class="nm2">{html.escape(str(s["name"]))} 강세 종목 · </em>거래대금 {_sm_eok(s["tv"])} · {s["n_up"]}/{s["n"]} 상승</span>'
            f'<span>{upd}</span></div>'
            f'{news_html}{"".join(first)}{more}</div>')
    st.markdown(SM_CSS + f'<div class="sm-wrap">{"".join(cards)}</div>'
                f'<div class="sm-legend">{html.escape(status)} · 섹터 등락 = 거래대금 가중 평균 · 거래대금 = 섹터 전체 합계 · '
                f'<span style="color:{UP};text-decoration:underline">빨간 이름</span> = 오늘 52주 신고가 · '
                f'<span class="sm-dot"></span> RS 70↑ + 정배열 · ⚠️ 20일선 +30%↑ 과열 · 빨간 거래대금 = 1,000억↑ · '
                f'막대 = 전일 종가(검은 선) 대비 저가~고가(꼬리)와 시가~현재가(몸통) · 이름을 누르면 차트 · 뉴스 = 섹터 대표(거래대금 1위 상승 종목)</div>',
                unsafe_allow_html=True)


def _pctv(r, k) -> float:
    prev, v = r.get("prev"), r.get(k)
    try:
        if prev and v and prev == prev and v == v:
            return (float(v) / float(prev) - 1) * 100
    except (TypeError, ValueError):
        pass
    return 0.0


def render_radar(df: pd.DataFrame):
    leaders = lead_groups(df)
    if leaders:
        body = "".join(
            f'<div class="chip"><div class="chip-top">'
            f'<span class="chip-group">{html.escape(g)}</span>'
            f'<span class="chip-count">{len(sub)}<small>종목</small></span></div>'
            f'<div class="chip-names">{_chip_leader(df, g)}{html.escape(", ".join(sub["name"]))}</div></div>'
            for g, sub in leaders
        )
    else:
        body = ('<div class="radar-empty">지금은 조건에 맞는 분류가 없어요. '
                '왼쪽에서 신고가 인정 기간을 늘리거나 종목 수 기준을 낮춰 보세요.</div>')
    st.markdown(
        f'<div class="radar"><div class="radar-title">주도섹터 레이더</div>'
        f'<div class="radar-rule">최근 {recent_days}거래일 52주 신고가 {min_count}종목 이상</div>'
        f'<div class="chips">{body}</div></div>',
        unsafe_allow_html=True,
    )


def fmt_vol(v) -> str:
    """거래량 → '449만' / '3.5만' / '1.2억' / '8,512'."""
    if v is None or pd.isna(v):
        return "-"
    if v >= 1e8:
        return f"{v / 1e8:,.1f}억"
    if v >= 1e5:
        return f"{v / 1e4:,.0f}만"
    if v >= 1e4:
        return f"{v / 1e4:,.1f}만"
    return f"{v:,.0f}"


def _x_color(v) -> str:
    if v is None or pd.isna(v):
        return ""
    if v >= 2:
        return f"color: {UP}; font-weight: 800"
    if v >= 1.3:
        return f"color: {UP}; font-weight: 600"
    if v < 0.7:
        return f"color: {DOWN}"
    return ""


def _eok_num(v) -> str:
    """억원 값: 10억 미만은 소수 첫째 자리까지."""
    if v is None or pd.isna(v):
        return "-"
    return f"{v:+,.1f}" if abs(v) < 10 else f"{v:+,.0f}"


FLOW_HELP = ("네이버 종목별 투자자 매매동향의 순매수 수량 × 그날 종가로 환산한 추정 금액(억원). "
             "최근 거래일 값이고, 장중에는 전 거래일 값일 수 있어요. 5일은 최근 5거래일 합계예요.")


RS_HELP = ("종합 RS(1~99). 오닐 방식 가중 수익률(3개월 40% + 6·9·12개월 각 20%)을 보드 안 국내 종목끼리 순위 매긴 백분위. "
           "해외 종목은 해외 종목끼리. 전 종목 기준 사이트 RS와는 숫자가 다를 수 있어요")


def _bo_text(r) -> str | None:
    if r.get("bo_status") in ("유지", "이탈"):
        return f"{r['bo_status']} {int(r['bo_days'])}일"
    return None


def _bo_full(r) -> str:
    """돌파 상태 한 줄: '⭐ 첫 돌파 3일째' / '2번째 돌파 5일째' / '미돌파 · 이탈 4일'."""
    get = r.get if isinstance(r, dict) else (lambda k, d=None: getattr(r, k, d))
    st_, days, nth = get("bo_status"), get("bo_days"), get("bo_nth")
    if days is None or pd.isna(days):
        return "-"
    if st_ == "유지":
        if nth is not None and not pd.isna(nth) and int(nth) > 1:
            return f"{int(nth)}번째 돌파 {int(days)}일째"
        return f"⭐ 첫 돌파 {int(days)}일째"
    if st_ == "이탈":
        return f"미돌파 · 이탈 {int(days)}일"
    return "-"


BO_FULL_HELP = ("⭐ 첫 돌파 N일째 = 최근 52주 안에서 처음으로 종가가 직전 52주 최고가를 넘은 뒤 N거래일째(돌파일 = 1일째), "
                "그 뒤 한 번도 그 가격 아래에서 마감하지 않음. 2번째 돌파 = 52주 안에 한 번 돌파했다가 밀린 뒤 다시 돌파. "
                "미돌파 · 이탈 N일 = 지금의 52주 최고가 아래에서 마감한 지 N거래일")


def _bo_color(v):
    if not isinstance(v, str):
        return ""
    if v.startswith("⭐"):
        return f"color: {UP}; font-weight: 800"
    if "번째 돌파" in v:
        return f"color: {UP}; font-weight: 600"
    if v.startswith("미돌파"):
        return "color: #6B7A84"
    return ""


def _to_high_color(v):
    """신고가까지 남은 %: 3% 이내 진하게, 10% 이내 주황."""
    if v is None or pd.isna(v):
        return ""
    if v <= 3:
        return f"color: {UP}; font-weight: 800"
    if v <= 10:
        return "color: #C27C0E; font-weight: 700"
    return ""


def _nh_text(r, kind: str) -> str | None:
    """지금 봉이 신고가인 봉만 모아서 '일·주·월'처럼. 아무것도 아니면 None."""
    hit = [lab for k, lab in (("d", "일"), ("w", "주"), ("m", "월")) if r.get(f"{kind}_{k}") == True]  # noqa: E712
    return "·".join(hit) if hit else None


def _rs_color(v):
    if pd.isna(v):
        return ""
    if v >= 80:
        return f"color: {UP}; font-weight: 600"
    if v < 40:
        return f"color: {DOWN}"
    return ""


def render_table(f: pd.DataFrame):
    view = pd.DataFrame({
        "종목": [("⛔ " if h is True else "") + ("🥇 " if sf is True else "") + n
               for n, h, sf in zip(f["name"], f["halted"] if "halted" in f else [None] * len(f),
                                   f["sector_first"] if "sector_first" in f else [None] * len(f))],
        "코드": f["code"],
        "시장": f["market"],
        "분류": f["group"],
        "통화": f["currency"].map(lambda c: UNIT.get(c, c)),
        "현재가": f["price"],
        "시가총액(원)": f["cap_krw"],
        "등락률": f["change"],
        "거래량": f.get("vol_live"),
        "거래대금": f.get("tv_live"),
        "평소 대비": f.get("tv_x"),
        **({"외국인(억원)": f["flow_외국인"], "기관(억원)": f["flow_기관"], "개인(억원)": f["flow_개인"],
            "외국인 5일(억원)": f["flow5_외국인"], "기관 5일(억원)": f["flow5_기관"],
            "개인 5일(억원)": f["flow5_개인"]} if show_flow else {}),
        "52주 최고": f["high52"],
        "괴리율": f["gap"],
        "신고가까지": f["to_high"],
        "52주 위치": f["pos"],
        "신고가 후": f["days_since_high"],
        "돌파": f.apply(_bo_full, axis=1),
        "52주 신고가(일·주·월)": f.apply(lambda r: _nh_text(r, "nh52"), axis=1),
        "역대 신고가(일·주·월)": f.apply(lambda r: _nh_text(r, "ath"), axis=1),
        "섹터 순위": f.apply(lambda r: "-" if pd.isna(r["lead_rank"]) else
                         (f"👑 1/{int(r['lead_n'])}" if r["lead_rank"] == 1 and r["lead_ok"] else f"{int(r['lead_rank'])}/{int(r['lead_n'])}"), axis=1),
        "RS": f["rs"],
        "RS(1M)": f["rs_1m"],
        "RS(3M)": f["rs_3m"],
        "RS(6M)": f["rs_6m"],
        "ATR%(20일)": f["atr_pct"],
        "정배열": f["aligned"],
        "설명": f["desc"],
    })

    simple = st.toggle("간단히 보기", key="tbl_simple", help="꼭 볼 열만: 신고가까지 · 돌파 · RS · 섹터 순위 · 5일 수급")
    if simple:
        keep = ["종목", "현재가", "등락률", "거래량", "평소 대비", "신고가까지", "돌파", "RS", "섹터 순위", "분류", "시가총액(원)",
                "외국인 5일(억원)", "기관 5일(억원)", "52주 위치"]
        view = view[[c for c in keep if c in view.columns]]
    hot_mask = (pd.to_numeric(f["days_since_high"], errors="coerce") <= recent_days).values
    whole_rows = view.index[f["currency"].isin(["KRW", "JPY"]).values]
    flow_cols = [c for c in view.columns if c.endswith("(억원)")]
    for c in flow_cols:
        view[c] = pd.to_numeric(view[c], errors="coerce")

    def color_sign(v):
        if pd.isna(v) or v == 0:
            return ""
        return f"color: {UP}; font-weight: 600" if v > 0 else f"color: {DOWN}; font-weight: 600"

    pos_of = {ix: i for i, ix in enumerate(view.index)}

    def mark_new_high(row):
        hit = bool(hot_mask[pos_of[row.name]])
        return [f"background-color: {NEW_HIGH_BG}" if hit else ""] * len(row)

    _fmt = {
            "현재가": "{:,.2f}", "52주 최고": "{:,.2f}",
            "등락률": "{:+.2f}%", "괴리율": "{:.1f}%", "신고가까지": "{:+.1f}%",
            "시가총액(원)": data.format_krw,
            "신고가 후": "{:.0f}일", "52주 위치": "{:.0f}",
            "RS": "{:.0f}", "RS(1M)": "{:.0f}", "RS(3M)": "{:.0f}", "RS(6M)": "{:.0f}",
            "ATR%(20일)": "{:.1f}%",
            "거래량": fmt_vol, "거래대금": data.format_krw, "평소 대비": "{:.1f}배",
        }
    styled = (
        view.style
        .format({k: v for k, v in _fmt.items() if k in view.columns}, na_rep="-")
        .format("{:,.0f}", subset=pd.IndexSlice[whole_rows, [c for c in ("현재가", "52주 최고") if c in view.columns]], na_rep="-")
        .format(_eok_num, subset=flow_cols, na_rep="-")
        .map(color_sign, subset=["등락률"] + flow_cols)
        .map(lambda _: "font-weight: 600", subset=["종목", "RS"])
        .map(_rs_color, subset=[c for c in ("RS", "RS(1M)", "RS(3M)", "RS(6M)") if c in view.columns])
        .map(_bo_color, subset=["돌파"])
        .map(_to_high_color, subset=["신고가까지"])
        .map(_x_color, subset=[c for c in ("평소 대비",) if c in view.columns])
        .map(lambda v: f"color: {UP}; font-weight: 700" if isinstance(v, str) else "",
             subset=[c for c in ("52주 신고가(일·주·월)", "역대 신고가(일·주·월)") if c in view.columns])
        .apply(mark_new_high, axis=1)
    )
    codes_in_view = list(f["code"])

    def _pick_row():
        """한 번 누르면 아래 자세히 보기, 같은 종목을 한 번 더 누르면 🕯️ 차트 탭으로."""
        sel = st.session_state.get("list_table")
        rows = getattr(getattr(sel, "selection", None), "rows", None) or (sel or {}).get("selection", {}).get("rows", [])
        now = time.time()
        last = st.session_state.get("_list_last")
        code = codes_in_view[rows[0]] if rows and rows[0] < len(codes_in_view) else None
        # 선택된 줄을 한 번 더 누르면 선택이 풀리면서 여기로 와요 → 그게 '두 번 누르기'. (앱이 계산 중이면 늦게 올 수 있어서 넉넉히 30초)
        again = last is not None and now - last[1] < 30 and (code is None or code == last[0])
        if again:
            st.session_state["chart_code"] = last[0]
            st.session_state["main_tab_sel"] = "🕯️ 차트"
            st.session_state.pop("_list_last", None)
            return
        if code:
            st.session_state["detail_code"] = code
            st.session_state["chart_code"] = code
            st.session_state["_list_last"] = (code, now)

    st.caption("👆 종목 줄을 한 번 누르면 아래 '종목 자세히 보기', 같은 줄을 한 번 더 누르면 🕯️ 차트 탭으로 넘어가요. "
               "🥇 = 섹터에서 가장 먼저 신고가")
    st.dataframe(
        styled,
        hide_index=True,
        key="list_table", on_select=_pick_row, selection_mode="single-row",
        height=min(720, 36 * (len(view) + 1) + 4),
        column_config={
            "52주 위치": st.column_config.ProgressColumn(
                "52주 위치", min_value=0, max_value=100, format="%.0f",
                help="52주 최저가를 0, 최고가를 100으로 봤을 때 현재가 위치"),
            "괴리율": st.column_config.Column(help="현재가가 52주 최고가보다 몇 % 낮은지"),
            "신고가까지": st.column_config.Column(help="52주 최고가를 넘으려면 필요한 상승률"),
            "신고가 후": st.column_config.Column(help="52주 최고가를 찍은 뒤 지난 거래일 수. 0이면 오늘"),
            "시가총액(원)": st.column_config.Column(help="상장주식수 × 현재가, 원화 기준(조·억). 해외 종목은 환율로 환산. "
                                                           "상장주식수를 못 받은 종목은 '-'이고, 10분마다 다시 시도해요."),
            "통화": st.column_config.Column(help="현재가·52주 최고가의 단위. 국내는 원, 해외는 각 시장 통화"),
            "현재가": st.column_config.Column(help="통화 열의 단위예요"),
            "정배열": st.column_config.CheckboxColumn(help="현재가 > 20일선 > 60일선 > 120일선"),
            "종목": st.column_config.Column(pinned=True),
            "돌파": st.column_config.Column(help=BO_FULL_HELP),
            "52주 신고가(일·주·월)": st.column_config.Column(
                help="오늘 일봉·이번 주 주봉·이번 달 월봉 중 고가가 그 봉 직전 52주 최고가를 넘은 봉"),
            "역대 신고가(일·주·월)": st.column_config.Column(
                help="오늘 일봉·이번 주 주봉·이번 달 월봉 중 고가가 상장 이후 전체 최고가(월봉 기준)를 넘은 봉"),
            "RS": st.column_config.Column(help=RS_HELP),
            "RS(1M)": st.column_config.Column(help="단기 RS. 최근 1개월(21거래일) 수익률 순위(1~99)"),
            "RS(3M)": st.column_config.Column(help="최근 3개월(63거래일) 수익률 순위(1~99)"),
            "RS(6M)": st.column_config.Column(help="최근 6개월(126거래일) 수익률 순위(1~99)"),
            "ATR%(20일)": st.column_config.Column(help="최근 20거래일 평균 진폭 ÷ 현재가. 손절폭 잡을 때 참고(8%보다 크면 ATR로 확대)"),
            "설명": st.column_config.TextColumn(width="large"),
            **{c: st.column_config.Column(help=FLOW_HELP) for c in flow_cols},
        },
    )


def _n(v) -> str:
    return "-" if pd.isna(v) else f"{v:.0f}"


def _pct(v) -> str:
    return "-" if pd.isna(v) else f"{v:.1f}%"


CARD_STEP = 60   # 휴대폰 카드는 한 번에 60장씩(수백 장을 30초마다 다시 그리면 휴대폰이 버벅여요)


def _more_cards(limit: int):
    st.session_state["card_limit"] = limit + CARD_STEP


M_SORTS = ["신고가 가까운 순", "첫 돌파 우선", "RS 높은 순", "등락률 순"]


def _m_sorted(f: pd.DataFrame, how: str) -> pd.DataFrame:
    if how == "첫 돌파 우선":
        k = f.assign(_a=(f["bo_status"] != "유지").astype(int),
                     _b=pd.to_numeric(f.get("bo_nth"), errors="coerce").fillna(99),
                     _c=pd.to_numeric(f["bo_days"], errors="coerce").fillna(999))
        return k.sort_values(["_a", "_b", "_c", "to_high"]).drop(columns=["_a", "_b", "_c"])
    if how == "RS 높은 순":
        return f.sort_values("rs", ascending=False, na_position="last")
    if how == "등락률 순":
        return f.sort_values("change", ascending=False, na_position="last")
    return f.sort_values("to_high", ascending=True, na_position="last")


def _kflow(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    color = UP if v > 0 else (DOWN if v < 0 else "#51616C")
    return f'<b style="color:{color}">{_eok_num(v)}</b>'


def render_cards(f: pd.DataFrame, n_hot: int, n_near: int, n_aligned: int, fins=None):
    """휴대폰 화면용 체크 리스트. 한 장에 꼭 볼 것만: 신고가까지 · 돌파 상태 · RS를 같은 자리에 크게."""
    kpis = (
        f'<div class="m-kpis"><div><span>종목</span><b>{len(f)}</b></div>'
        f'<div><span>최근 {recent_days}일 신고가</span><b>{n_hot}</b></div>'
        f'<div><span>10% 이내</span><b>{n_near}</b></div>'
        f'<div><span>돌파 유지</span><b>{int((f["bo_status"] == "유지").sum())}</b></div></div>'
    )
    st.markdown(kpis, unsafe_allow_html=True)
    how = st.radio("정렬", M_SORTS, horizontal=True, key="m_sort", label_visibility="collapsed")
    shown = _m_sorted(f[f["price"].notna()], how)
    limit = st.session_state.get("card_limit", CARD_STEP)
    total = len(shown)
    yr = data.now_kst().year
    cards = []
    for r in shown.head(limit).itertuples():
        hot = pd.notna(r.days_since_high) and r.days_since_high <= recent_days
        if pd.isna(r.change) or r.change == 0:
            chg = f'<span class="k-chg">{"-" if pd.isna(r.change) else "0.00%"}</span>'
        else:
            chg = f'<span class="k-chg" style="color:{UP if r.change > 0 else DOWN}">{r.change:+.2f}%</span>'
        th = r.to_high
        th_cls = "" if pd.isna(th) else ("hot" if th <= 3 else ("near" if th <= 10 else ""))
        bo = _bo_full(r)
        bo_cls = "first" if bo.startswith("⭐") else ("brk" if "번째 돌파" in bo else "off")
        bo_show = bo.replace("미돌파 · ", "") if bo != "-" else "돌파 전"
        crown = ' <span class="k-crown">👑 대장</span>' if (r.lead_rank == 1 and r.lead_ok) else ""
        sub = [html.escape(r.group)]
        if pd.notna(r.cap_krw):
            sub.append(f"{data.format_krw(r.cap_krw)}원")
        if pd.notna(r.lead_rank):
            sub.append(f"섹터 {int(r.lead_rank)}/{int(r.lead_n)}")
        if fins is not None and data.is_kr(r.code):
            og = data.op_growth(fins.get(r.code), yr)
            color = UP if og["op_up"] else ("#6B7A84" if og["op_up"] is None else DOWN)
            sub.append(f'<b style="color:{color}">{og["op_txt"]}</b>')
        if r.aligned:
            sub.append("정배열")
        ath = _nh_text(r._asdict(), "ath")
        if ath:
            sub.append(f'<b style="color:{UP}">역대 신고가 {ath}</b>')
        flow = ""
        if show_flow and pd.notna(getattr(r, "flow_date", None)):
            flow = (f'<div class="k-flow">외 {_kflow(r.flow_외국인)} · 기 {_kflow(r.flow_기관)}'
                    f'<span>5일 외 {_kflow(r.flow5_외국인)} · 기 {_kflow(r.flow5_기관)}</span></div>')
        cards.append(
            f'<a class="kcard{" hot" if hot else ""}" target="_self" href="?chart={r.code}">'
            f'<div class="k-top"><span class="k-name">{html.escape(r.name)}{crown}</span>'
            f'<span class="k-px">{fmt_price(r.price, r.currency)}{"" if r.currency == "KRW" else " " + r.currency} {chg}</span></div>'
            f'<div class="k-keys">'
            f'<div><small>신고가까지</small><b class="{th_cls}">{"-" if pd.isna(th) else f"{th:+.1f}%"}</b></div>'
            f'<div><small>돌파</small><b class="{bo_cls}">{html.escape(bo_show)}</b></div>'
            f'<div><small>RS</small><b class="{"rs" if pd.notna(r.rs) and r.rs >= 80 else ""}">{_n(r.rs)}</b></div>'
            f'</div>'
            f'<div class="k-sub">{" · ".join(sub)}</div>{flow}</a>'
        )
    st.markdown('<div class="kcards">' + "".join(cards) + "</div>", unsafe_allow_html=True)
    if total > limit:
        st.button(f"카드 더 보기 ({limit}/{total})", key="card_more", on_click=_more_cards, args=(limit,))
    st.caption("노란 카드 = 최근 신고가. 신고가까지: 빨강 3% 이내 · 주황 10% 이내. "
               "⭐ 첫 돌파 = 52주 안 첫 돌파(며칠째인지 함께). 카드를 누르면 종목 화면이 열려요. 수급은 억원.")


def render_stock_flow(code: str, trends: dict):
    """종목 자세히 보기의 수급 탭: 최근 20거래일 개인·외국인·기관 순매수(억원 추정)와 외국인 보유율."""
    if not data.is_kr(code):
        st.caption("해외 종목은 투자자별 매매동향을 제공하지 않아요.")
        return
    tr = trends.get(code)
    if tr is None:
        tr = data.fetch_stock_trend(code)
    if tr is None or tr.empty:
        st.caption("이 종목의 투자자별 매매동향을 불러오지 못했어요. 잠시 뒤 다시 열어 보세요.")
        return
    sm = data.trend_summary(tr)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"외국인 ({sm['flow_date']:%m/%d})", _eok(sm["flow_외국인"]), f"5일 {_eok(sm['flow5_외국인'])}", delta_color="off")
    c2.metric(f"기관 ({sm['flow_date']:%m/%d})", _eok(sm["flow_기관"]), f"5일 {_eok(sm['flow5_기관'])}", delta_color="off")
    c3.metric(f"개인 ({sm['flow_date']:%m/%d})", _eok(sm["flow_개인"]), f"5일 {_eok(sm['flow5_개인'])}", delta_color="off")
    streak = []
    for k in ("외국인", "기관"):
        n = sm.get(f"streak_{k}") or 0
        if n:
            streak.append(f"{k} {abs(n)}일 연속 순{'매수' if n > 0 else '매도'}")
    hold = tr["외국인보유율"].dropna()
    c4.metric("외국인 보유율", f"{hold.iloc[-1]:.2f}%" if not hold.empty else "-",
              " · ".join(streak) if streak else None, delta_color="off")

    long = tr.melt("date", value_vars=[f"{k}금액" for k in data.INVESTORS], var_name="투자자", value_name="순매수")
    long["투자자"] = long["투자자"].str.replace("금액", "", regex=False)
    chart = (
        alt.Chart(long.dropna()).mark_bar()
        .encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False)),
            xOffset=alt.XOffset("투자자:N", sort=data.INVESTORS),
            y=alt.Y("순매수:Q", title="억원(추정)"),
            color=alt.Color("투자자:N", sort=data.INVESTORS,
                            scale=alt.Scale(domain=data.INVESTORS, range=["#9AA9B3", "#5B47F5", "#F2B134"]),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "투자자:N", alt.Tooltip("순매수:Q", format="+,.1f")],
        )
        .properties(height=240, width="container")
    )
    st.altair_chart(chart)
    table = tr.sort_values("date", ascending=False).head(10)
    view = pd.DataFrame({
        "날짜": table["date"].dt.strftime("%m/%d"),
        "종가": table["close"],
        "외국인(주)": table["외국인"], "기관(주)": table["기관"], "개인(주)": table["개인"],
        "외국인(억)": table["외국인금액"], "기관(억)": table["기관금액"], "개인(억)": table["개인금액"],
        "외국인 보유율": table["외국인보유율"],
    })
    num = [c for c in view.columns if c not in ("날짜",)]
    for c in num:
        view[c] = pd.to_numeric(view[c], errors="coerce")
    sign_cols = ["외국인(주)", "기관(주)", "개인(주)", "외국인(억)", "기관(억)", "개인(억)"]
    st.dataframe(
        view.style.format({"종가": "{:,.0f}", "외국인 보유율": "{:.2f}%", **{c: "{:+,.0f}" for c in sign_cols[:3]},
                           **{c: "{:+,.1f}" for c in sign_cols[3:]}}, na_rep="-")
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}", subset=sign_cols),
        hide_index=True,
    )
    st.caption("네이버 증권 종목별 투자자 매매동향 기준. 금액은 순매수 수량 × 그날 종가로 환산한 추정치예요. "
               "연기금·투신 같은 기관 세부는 종목별로는 네이버가 주지 않고(한국거래소는 로그인 필요), "
               "시장 전체 기준으로 위쪽 '투자자별 순매수 자세히'에 보여줘요.")


@st.cache_data(ttl=300, show_spinner=False, max_entries=40)
def load_chart_all(code: str) -> dict:
    """(속도) 일·주·월봉을 한 번에 동시에 받아 둬요. 봉 종류를 바꿔도 다시 기다리지 않아요."""
    from concurrent.futures import ThreadPoolExecutor
    tfs = list(data.CHART_TF)
    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(zip(tfs, pool.map(lambda tf: data.fetch_chart(code, tf), tfs)))


def load_chart(code: str, tf: str) -> pd.DataFrame:
    return load_chart_all(code).get(tf, data.empty_frame())


MA_SET = {"일봉": ((5, "5일"), (20, "20일"), (60, "60일"), (120, "120일"), (200, "200일")),
          "주봉": ((5, "5주"), (10, "10주"), (30, "30주"), (52, "52주")),
          "월봉": ((3, "3개월"), (6, "6개월"), (12, "12개월"), (24, "24개월"))}
MA_COLORS = ("#E0672B", "#2E9D5B", "#7A4FD1", "#8A96A0", "#D6336C")
PERIODS = {"일봉": {"1개월": 21, "3개월": 63, "6개월": 126, "1년": 250, "2년": 500, "전체": None},
           "주봉": {"1년": 52, "2년": 104, "5년": 260, "전체": None},
           "월봉": {"5년": 60, "10년": 120, "전체": None}}


def candle_fig(b: pd.DataFrame, tf: str, n: int | None, show_ma: bool, show_vol: bool, line: float | None = None,
               currency: str = "KRW", show_tv: bool = False):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    b = b.copy().reset_index(drop=True)
    for k, _ in MA_SET[tf]:
        b[f"ma{k}"] = b["close"].rolling(k).mean()
    if n:
        b = b.tail(n)
    x = pd.to_datetime(b["date"]).dt.strftime("%Y-%m" if tf == "월봉" else "%Y-%m-%d")
    panels = ["price"] + (["vol"] if show_vol else []) + (["tv"] if show_tv else [])
    heights = {1: [1.0], 2: [0.78, 0.22], 3: [0.68, 0.16, 0.16]}[len(panels)]
    fig = make_subplots(rows=len(panels), cols=1, shared_xaxes=True, vertical_spacing=0.025, row_heights=heights)
    up_mask = [c >= o for o, c in zip(b["open"], b["close"])]
    fmt = ",.0f" if currency in ("KRW", "JPY") else ",.2f"
    fig.add_trace(go.Candlestick(
        x=x, open=b["open"], high=b["high"], low=b["low"], close=b["close"], name="봉", showlegend=False,
        increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
        hoverlabel=dict(namelength=0)), row=1, col=1)
    if show_ma:
        for (k, lab), c in zip(MA_SET[tf], MA_COLORS):
            fig.add_trace(go.Scatter(x=x, y=b[f"ma{k}"], name=lab, mode="lines", line=dict(color=c, width=1.3),
                                     hovertemplate=f"{lab} %{{y:{fmt}}}<extra></extra>"), row=1, col=1)
    if line and tf != "월봉":
        fig.add_hline(y=line, line=dict(color="#F2B134", width=1.4, dash="dash"), row=1, col=1,
                      annotation_text=f"52주 최고 {line:{fmt}}", annotation_position="top left",
                      annotation_font=dict(size=11, color="#B7861D"))
    colors = [UP if u else DOWN for u in up_mask]
    fig.update_yaxes(side="right", gridcolor="#EEF1F3", zeroline=False, tickfont=dict(size=12))
    fig.update_yaxes(tickformat=fmt, row=1, col=1)
    if show_vol:
        r = panels.index("vol") + 1
        fig.add_trace(go.Bar(x=x, y=b["volume"], marker_color=colors, opacity=0.55, name="거래량", showlegend=False,
                             hovertemplate="거래량 %{y:,.0f}<extra></extra>"), row=r, col=1)
        fig.update_yaxes(title_text="거래량", title_font=dict(size=12, color="#7A8A94"), tickformat="~s", row=r, col=1)
    if show_tv:
        r = panels.index("tv") + 1
        # 거래대금 ≈ 평균가((고+저+종)/3) × 거래량. 국내는 억원, 해외는 백만(현지 통화)
        typ = (b["high"] + b["low"] + b["close"]) / 3
        krw = currency == "KRW"
        tv = typ * b["volume"] / (1e8 if krw else 1e6)
        unit = "억" if krw else f"M {UNIT.get(currency, currency)}"
        fig.add_trace(go.Bar(x=x, y=tv, marker_color=colors, opacity=0.75, name="거래대금", showlegend=False,
                             hovertemplate=f"거래대금 %{{y:,.0f}}{unit}<extra></extra>"), row=r, col=1)
        fig.update_yaxes(title_text=f"거래대금({unit})", title_font=dict(size=12, color="#7A8A94"), tickformat=",.0f",
                         row=r, col=1)
    fig.update_xaxes(type="category", nticks=8, showgrid=False, rangeslider_visible=False, tickangle=0,
                     tickfont=dict(size=12), showspikes=True, spikemode="across", spikethickness=1, spikecolor="#A59BFA")
    fig.update_layout(height={1: 640, 2: 780, 3: 900}[len(panels)], margin=dict(l=4, r=4, t=36, b=6),
                      hovermode="x unified", dragmode="pan", plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
                      hoverlabel=dict(font_size=13),
                      legend=dict(orientation="h", y=1.05, x=0, font=dict(size=13)),
                      font=dict(family="Pretendard, Malgun Gothic, sans-serif", size=13))
    return theme.style_fig(fig)


def candle_chart(code: str, currency: str = "KRW", quote: dict | None = None, high52: float | None = None,
                 key: str = "cc", is_index: bool = False):
    """일봉·주봉·월봉 캔들 + 이동평균 + 거래량. 국내 종목은 실시간 시세로 마지막 봉을 갱신해요."""
    with st.container(key=f"{key}_bar"):
        c1, c2, c3 = st.columns([1.0, 2.0, 1.6], gap="medium")
        tf = c1.segmented_control("봉 종류", list(data.CHART_TF), default="일봉", required=True, key=f"{key}_tf",
                                  width="stretch") or "일봉"
        periods = PERIODS[tf]
        default_p = {"일봉": "6개월", "주봉": "2년", "월봉": "10년"}[tf]
        per = c2.segmented_control("기간", list(periods), default=default_p, required=True, key=f"{key}_per_{tf}",
                                   width="stretch") or default_p
        show_opts = ["이동평균", "거래량"] if is_index else ["이동평균", "거래량", "거래대금"]
        opts = c3.segmented_control("표시", show_opts, selection_mode="multi",
                                    default=show_opts, key=f"{key}_opts2", width="stretch") or []
    with st.spinner("차트를 불러오는 중이에요."):
        bars = load_chart(code, tf)
    if bars is None or bars.empty:
        st.info("이 종목의 봉 데이터를 받지 못했어요. 잠시 뒤 다시 시도해 보세요.")
        return
    bars = data.chart_with_live(bars, quote, tf)
    fig = candle_fig(bars, tf, periods[per], "이동평균" in opts, "거래량" in opts, high52, currency, "거래대금" in opts)
    # 차트 위에 수평선·추세선·형광펜을 그리고 지울 수 있어요(그린 것은 이 브라우저에 종목별로 저장)
    drawn = chart_draw.render(fig, code, tf, currency, key=key)
    if not drawn:
        st.plotly_chart(fig, key=f"{key}_fig", config={"displaylogo": False, "scrollZoom": True,
                                                       "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"]})
    st.caption(("위 도구로 수평선·추세선·형광펜을 그리고 지우개로 지워요. 그린 것은 이 브라우저에 종목별로 저장돼요(PC·휴대폰 따로). "
                if drawn else "") + "더블클릭하면 원래대로. 거래대금은 평균가×거래량으로 계산한 추정치예요. "
               + ("마지막 봉은 실시간 시세로 갱신돼요." if quote else "해외 종목은 야후 일봉 기준이에요."))


@st.cache_data(ttl=3600, show_spinner=False, max_entries=200)
def naver_search(q: str) -> list[tuple[str, str]]:
    try:
        return data.search_stock(q)[:12]
    except Exception:
        return []


def _rep_status():
    key, token = _secret("ANTHROPIC_API_KEY"), _secret("GITHUB_TOKEN")
    repo = _secret("GITHUB_REPO", "kimgun124-boop/very")
    return {"ai": bool(key), "key": key, "model": _secret("ANTHROPIC_MODEL", reports.DEFAULT_MODEL),
            "gh": bool(token), "token": token, "repo": repo, "branch": _secret("GITHUB_BRANCH") or None,
            "pw": _secret("APP_PASSWORD")}


def _persist(store: dict, stt: dict) -> tuple[bool, str]:
    reports.save_local(store)
    if stt["gh"]:
        return reports.save_github(store, stt["token"], stt["repo"], stt["branch"])
    return True, "이 서버에만 임시로 저장했어요(앱을 다시 켜면 사라질 수 있어요)."


@st.fragment
def render_reports_tab():
    """📥 리포트: 앱에서 직접 리포트를 넣으면 종목을 뽑아 보드에 붙여요."""
    stt = _rep_status()
    board = {s["code"]: s for s in STOCKS}
    by_name = {s["name"]: s for s in STOCKS}

    st.markdown('<div class="sec-h"><b>📥 리포트 넣기</b><span>PDF·캡처 이미지·글을 넣으면 종목을 뽑아 보드에 붙여요</span></div>',
                unsafe_allow_html=True)
    badges = [("AI 분석", stt["ai"], "켜짐" if stt["ai"] else "꺼짐 · 이름 맞추기로 분석"),
              ("영구 저장", stt["gh"], "GitHub 저장소" if stt["gh"] else "꺼짐 · 앱 재시작 시 사라질 수 있음"),
              ("비밀번호", bool(stt["pw"]), "설정됨" if stt["pw"] else "없음 · 누구나 추가 가능")]
    st.markdown('<div class="rp-badges">' + "".join(
        f'<span class="rp-badge {"on" if ok else "off"}"><b>{lab}</b> {txt}</span>' for lab, ok, txt in badges) + "</div>",
        unsafe_allow_html=True)
    with st.expander("⚙️ 처음 한 번 설정하기 (AI 분석 · 영구 저장 · 비밀번호)", expanded=not (stt["ai"] and stt["gh"])):
        st.markdown(
            "Streamlit 앱 오른쪽 아래 **Manage app → ⋮ → Settings → Secrets** 칸에 아래처럼 넣고 저장하세요.\n\n"
            "```toml\nANTHROPIC_API_KEY = \"sk-ant-...\"   # AI 분석(이미지·스캔 PDF도 읽음)\n"
            "GITHUB_TOKEN = \"github_pat_...\"     # 넣은 리포트를 저장소에 영구 저장\n"
            "GITHUB_REPO = \"kimgun124-boop/very\"\n"
            "APP_PASSWORD = \"원하는 비밀번호\"      # 나만 리포트를 넣을 수 있게\n```\n"
            "- **ANTHROPIC_API_KEY**: [Claude Console](https://console.anthropic.com)에서 발급해요. "
            "Claude 구독과 별도로 쓴 만큼 요금이 나가요. 없으면 '이름 맞추기'로만 분석해요(글자로 된 PDF·글만).\n"
            "- **GITHUB_TOKEN**: GitHub → Settings → Developer settings → Fine-grained tokens → "
            "저장소 very만 고르고 **Contents: Read and write** 권한으로 만들어요. 없으면 앱을 다시 켤 때 넣은 리포트가 사라질 수 있어요.\n"
            "- **APP_PASSWORD**: 앱 주소를 아는 누구나 리포트를 넣거나 API 요금을 쓰지 못하게 막아요. 꼭 넣는 걸 권해요.")

    if stt["pw"] and not st.session_state.get("rep_ok"):
        pw = st.text_input("비밀번호", type="password", key="rep_pw")
        if pw and pw == stt["pw"]:
            st.session_state["rep_ok"] = True
            _rerun_frag()
        elif pw:
            st.error("비밀번호가 달라요.")
        _render_saved_reports(stt, editable=False)
        return

    # ① 넣기 → AI가 읽기 → 알아서 분류 → 바로 반영
    with st.container(border=True, key="rp_box_in"):
        st.markdown("**① 리포트 넣기** — PDF·캡처를 올리면 바로 읽고, 보드 분류에 맞춰 알아서 넣어요. 따로 고르거나 뺄 필요 없어요.")
        files = st.file_uploader("파일", type=["pdf", "png", "jpg", "jpeg", "webp", "txt", "md", "html"],
                                 accept_multiple_files=True, key="rep_files",
                                 help="PDF 여러 개, 캡처 이미지 여러 장을 한꺼번에 넣어도 돼요. 올리는 순간 분석이 시작돼요.")
        text = st.text_area("또는 글 붙여넣기", height=100, key="rep_text",
                            placeholder="리포트 본문, 텔레그램 요약, 기사 내용 등을 붙여넣고 '분석하기'를 누르세요.")
        methods = (["🤖 AI로 분석"] if stt["ai"] else []) + ["🔎 이름 맞추기"]
        c1, c2, c3 = st.columns([2, 1.3, 1])
        method = c1.segmented_control("분석 방법", methods, default=methods[0], required=True, key="rep_method")
        review = c2.toggle("반영 전에 확인", value=False, key="rep_review",
                           help="끄면(기본) 분석 즉시 반영돼요. 켜면 뽑은 종목을 표로 보고 고친 뒤 반영해요.")
        go = c3.button("🔍 분석하기", type="primary", width="stretch", disabled=not (files or text.strip()))
        if not stt["ai"]:
            st.caption("AI 키가 없어서 본문에 나온 상장사 이름을 찾아요. 이미지·스캔 PDF는 AI 분석에서만 읽을 수 있어요.")
    payload = [(f.name, f.getvalue()) for f in (files or [])]
    sig = hashlib.sha1(b"".join(n.encode() + raw for n, raw in payload)).hexdigest() if payload else ""
    done = st.session_state.setdefault("rep_done_sigs", set())
    if payload and sig not in done and not go:
        go = True                                       # 파일을 올리면 버튼 없이 바로 분석
    if go:
        done.add(sig)
        store = reports.load_local()
        if method.startswith("🤖"):
            with st.spinner("AI가 리포트를 읽고 종목을 뽑아 분류하는 중이에요. 분량에 따라 30초~2분 걸려요."):
                res, err = reports.ai_extract(stt["key"], payload, text, stt["model"], sectors=SECTOR_ORDER,
                                              groups=sorted({s["group"] for s in STOCKS if s.get("group")}),
                                              themes=reports.known_themes(store))
            if err:
                st.error(err)
                return
            meta = {k: res.get(k, "") for k in ("title", "broker", "date", "summary", "theme")}
            rows = res.get("stocks") or []
        else:
            with st.spinner("본문에서 상장사 이름을 찾는 중이에요."):
                body = "\n".join(reports.file_text(n, raw) for n, raw in payload) + "\n" + text
                if not body.strip():
                    st.error("글자를 읽지 못했어요. 이미지·스캔 PDF는 AI 분석이 필요해요.")
                    return
                # 한 번만 스친 이름(보령·하이브처럼 일반 낱말과 겹치는 이름)은 오인식이 많아서 두 번 이상 나온 종목만
                rows = [r for r in reports.match_stocks(body, STOCKS, reports.krx_listing()) if r.get("count", 0) >= 2]
                for r in rows:
                    r["importance"] = "상" if r.get("count", 0) >= 3 else ("중" if r.get("count", 0) >= 2 else "하")
                first = (payload[0][0].rsplit(".", 1)[0] if payload else body.strip().split("\n")[0])[:30]
                meta = {"title": first, "broker": "", "date": now_date(), "summary": "", "theme": ""}
        draft = _rep_classify(rows, meta, store, board, by_name)
        source = ", ".join(n for n, _ in payload) or "붙여넣은 글"
        if review:
            st.session_state["rep_draft"] = {"meta": meta, "rows": draft, "source": source}
        else:
            pick = [r for r in draft if r["넣기"] and r["코드"]]
            if not pick:
                st.warning("넣을 종목을 찾지 못했어요. '반영 전에 확인'을 켜고 다시 분석해 보세요.")
                return
            _rep_commit(meta, pick, source, stt)
            return

    # ② (확인 모드) 고치기 → 반영
    dr = st.session_state.get("rep_draft")
    if dr:
        with st.container(border=True, key="rp_box_edit"):
            st.markdown(f"**② 뽑은 종목 확인하기** — {len(dr['rows'])}개. 분류는 이미 맞춰 두었어요. 틀린 것만 고치세요.")
            c1, c2, c3, c4 = st.columns([2.2, 1, 1, 1])
            title = c1.text_input("리포트 제목", dr["meta"].get("title", ""), key="rep_title")
            broker = c2.text_input("증권사·출처", dr["meta"].get("broker", ""), key="rep_broker")
            rdate = c3.text_input("날짜", dr["meta"].get("date", ""), key="rep_date", placeholder="26.09.28")
            theme = c4.text_input("테마", dr["meta"].get("theme", ""), key="rep_theme")
            summary = st.text_area("핵심 요약", dr["meta"].get("summary", ""), height=80, key="rep_summary")
            edited = st.data_editor(
                pd.DataFrame(dr["rows"], columns=["넣기", "종목명", "코드", "산업", "세부 분류", "한 줄 설명", "리포트 포인트", "보드에 있음"]),
                key="rep_editor", hide_index=True, num_rows="dynamic", width="stretch",
                column_config={
                    "넣기": st.column_config.CheckboxColumn(width="small"),
                    "코드": st.column_config.TextColumn(help="국내 6자리 코드, 해외는 야후 티커(NVDA, 6857.T)", width="small"),
                    "산업": st.column_config.SelectboxColumn(options=SECTOR_ORDER, required=True),
                    "한 줄 설명": st.column_config.TextColumn(width="large"),
                    "리포트 포인트": st.column_config.TextColumn(width="large"),
                    "보드에 있음": st.column_config.CheckboxColumn(disabled=True, width="small"),
                })
            pick = edited[edited["넣기"] == True]  # noqa: E712
            no_code = pick[pick["코드"].fillna("").astype(str).str.strip() == ""]
            if len(no_code):
                st.warning("코드가 비어 있는 종목은 빠져요: " + ", ".join(no_code["종목명"].astype(str)))
            c1, c2 = st.columns([3, 1])
            c1.caption(f"넣을 종목 {len(pick) - len(no_code)}개 · 이미 있는 종목은 태그와 메모만 붙어요")
            if c2.button("✅ 앱에 반영", type="primary", width="stretch", disabled=not title.strip()):
                meta = {"title": title, "broker": broker, "date": rdate, "summary": summary, "theme": theme}
                rows = [r for r in pick.to_dict("records") if str(r.get("코드") or "").strip()]
                st.session_state.pop("rep_draft", None)
                _rep_commit(meta, rows, dr["source"], stt)
            if st.button("취소", key="rep_cancel"):
                st.session_state.pop("rep_draft", None)
                _rerun_frag()
    if st.session_state.get("rep_saved_msg"):
        st.success(st.session_state.pop("rep_saved_msg"))
    _render_saved_reports(stt, editable=True)


def _rep_classify(rows: list[dict], meta: dict, store: dict, board: dict, by_name: dict) -> list[dict]:
    """AI·이름 맞추기 결과에 코드를 붙이고, 보드 분류 → 전에 배운 분류 → AI 힌트 순서로 산업·세부 분류를 정해요."""
    learned = store.get("learned", {})
    learned_by_name = {v.get("name"): k for k, v in learned.items() if v.get("name")}
    need = [r.get("name") for r in rows if not r.get("code") and r.get("name") and r.get("name") not in by_name
            and r.get("name") not in learned_by_name and (r.get("market") in (None, "", "KR"))]
    found, _miss = data.resolve_codes(tuple(need)) if need else ({}, {})
    out, seen = [], set()
    for r in rows:
        name = str(r.get("name") or "").strip()
        code = (str(r.get("code") or "").strip().upper() or found.get(name, "") or (by_name.get(name) or {}).get("code", "")
                or learned_by_name.get(name, ""))
        if re.fullmatch(r"\d{1,6}", code):
            code = code.zfill(6)
        if code and code in seen:
            continue
        seen.add(code)
        on = board.get(code)
        sector, group, desc = reports.classify(r, code, board, learned, store.get("themes", {}), meta.get("theme", ""),
                                               STOCKS, SECTOR_ORDER, list(GROUP_ORDER))
        keep = r.get("importance", "상") != "하" or bool(on) or code in learned
        out.append({"넣기": keep, "종목명": on["name"] if on else name, "코드": code, "산업": sector, "세부 분류": group,
                    "한 줄 설명": desc, "리포트 포인트": r.get("point") or "", "보드에 있음": bool(on)})
    return out


def _rep_commit(meta: dict, rows: list[dict], source: str, stt: dict):
    """리포트를 저장(같은 리포트면 바꿔 끼움)하고, 분류를 배워 두고, 보드에 바로 반영해요."""
    stocks_out = [{"name": str(r["종목명"]).strip(), "code": str(r["코드"]).strip(), "sector": r["산업"],
                   "group": str(r["세부 분류"] or "리포트 추가").strip(), "desc": str(r["한 줄 설명"] or "").strip(),
                   "point": str(r["리포트 포인트"] or "").strip()} for r in rows]
    title = (meta.get("title") or source or "리포트").strip()
    store = reports.load_local()
    rep = reports.new_report(title, meta.get("broker", ""), meta.get("date", ""), meta.get("summary", ""), stocks_out,
                             source, meta.get("theme", ""))
    how = reports.upsert(store, rep)
    reports.learn(store, rep)
    ok, msg = _persist(store, stt)
    if not ok:
        st.error(msg)
        return
    n_new = sum(1 for x in stocks_out if x["code"] not in {s["code"] for s in STOCKS})
    th = f" · 테마 '{rep['theme']}'로 묶음" if rep.get("theme") else ""
    st.session_state["rep_saved_msg"] = (f"'{title}' {len(stocks_out)}종목을 {'다시 반영' if how == 'updated' else '보드에 반영'}했어요 "
                                         f"(새 종목 {n_new}개{th}). 왼쪽 '리포트 · 테마 태그'에서 볼 수 있어요.")
    st.success(st.session_state["rep_saved_msg"] + " 보드에 반영하는 중이에요…")
    time.sleep(0.6)
    st.rerun(scope="app")


# ─────────────────────────── 💼 내 보유 ───────────────────────────
@st.cache_data(ttl=600, show_spinner=False, max_entries=60)
def load_evidence(code: str, name: str) -> dict:
    if data.MOCK:
        return holdings.mock_gather(code, name)
    return holdings.gather(code, name, data.is_kr(code))


def _persist_holdings(store: dict, stt: dict) -> tuple[bool, str]:
    holdings.save_local(store)
    if stt["gh"]:
        return reports.save_github(store, stt["token"], stt["repo"], stt["branch"], path="user_holdings.json")
    return True, "이 서버에만 임시로 저장했어요(앱을 다시 켜면 사라질 수 있어요)."


def _hold_groups(df: pd.DataFrame, hs: list) -> dict:
    """보유 종목을 보드 세부분류로 묶기 — 깡토: 같은 섹터 정찰병 우르르 점검용."""
    gmap = dict(zip(df["code"], df["group"])) if "group" in df else {}
    out: dict = {}
    for h in hs or []:
        g = gmap.get(h.get("code"))
        if g:
            out.setdefault(g, []).append(h.get("name") or h.get("code"))
    return out


def _hold_context(df: pd.DataFrame) -> dict:
    hist = load_indexes()
    parts, oks = [], []
    for name, sym in data.MARKETS.items():
        sm = data.index_summary(hist.get(sym, (data.empty_frame(), None))[0])
        ok = None if not sm or sm.get("above60") is None else bool(sm["above60"])
        oks.append(ok)
        parts.append(f"{name} " + ("-" if ok is None else f"60일선 {'위' if ok else '아래'} {sm['dist60']:+.1f}%"))
    sm_money = data.sector_money(df, "group") if "tv_live" in df else pd.DataFrame()
    dist = {name: holdings.distribution_days(hist.get(sym, (None, None))[0]) for name, sym in data.MARKETS.items()}
    vol_days = 0
    for sym in data.MARKETS.values():
        ih = hist.get(sym, (None, None))[0]
        if ih is not None and len(ih) > 21:
            ch = pd.to_numeric(ih["close"], errors="coerce").pct_change().tail(20).abs() * 100
            vol_days = max(vol_days, int((ch >= 3).sum()))
    return {"vol_mode": vol_days >= 3, "vol_days": vol_days,
            "ladder": (holdings.load().get("settings") or {}).get("ladder", "원칙"),
            "dist_days": {k: v for k, v in dist.items() if v is not None},
            "n_above60": sum(1 for x in oks if x),
            "market_ok": None if None in oks else all(oks), "market_text": " · ".join(parts),
            "idx_rs": {n: data.index_rs(hist.get(sym, (data.empty_frame(), None))[0], df).get("rs")
                       for n, sym in data.MARKETS.items()},
            "leaders": {g for g, _ in lead_groups(df)},
            "peers": {p["group"]: p for p in load_peers(df)},
            "mkt_sig": {n: (m and {k: m[k] for k in ("state", "state_txt", "em", "day", "dd", "q50", "q80")}) for n, m in market_signals().items()},
            "sector_x": dict(zip(sm_money["sector"], sm_money["x"])) if len(sm_money) else {}}


def _hold_metrics(code: str, df: pd.DataFrame, quotes: dict, name: str = "") -> tuple[dict, pd.DataFrame, dict | None]:
    """보드에 있으면 보드 지표를, 없으면 일봉으로 바로 계산해요."""
    quote = quotes.get(code)
    if quote is None and data.is_kr(code):
        quote = load_quotes((code,))[0].get(code)
    bars = load_chart(code, "일봉")
    bars_live = data.chart_with_live(bars, quote if data.is_kr(code) else None, "일봉")
    hit = df[df["code"] == code]
    if len(hit):
        m = hit.iloc[0].to_dict()
    else:
        m = data.compute_metrics(bars, quote) if bars is not None and not bars.empty else {}
    if data.is_kr(code):                       # 펀더멘털(연간 영업이익·EPS·컨센서스)과 수급(외국인·기관)
        m["_fins"] = load_fin_one(code)
        m["_flow"] = load_trends((code,)).get(code)
    m["_news"] = load_evidence(code, name or m.get("name") or code)   # 최신 뉴스·증권사 리포트(10분마다)
    return m, bars_live, quote


@st.cache_data(ttl=43200, show_spinner=False, max_entries=300)
def load_fin_one(code: str):
    return data.fetch_financials(code)


LEVEL_COLOR = {"red": ("#FDECEC", UP), "orange": ("#FDF1E7", "#D2691E"), "yellow": ("#FFF8E1", "#B7861D"),
               "green": ("#EEF8F2", "#1E7A45"), "blue": ("#EEF4FC", DOWN), "gray": ("#F1F4F6", "#51616C"),
               "purple": ("#F4EEFC", "#6B3FC4")}


def _rerun_frag():
    """칸(fragment)만 다시 그리기. 전체 실행 중이라 칸만 못 그리면 전체를 다시 그려요."""
    try:
        st.rerun(scope="fragment")
    except st.errors.StreamlitAPIException:
        st.rerun()


def _holding_card(h: dict, m: dict, bars, j: dict, quotes: dict, stt: dict):
    """보유 종목 한 개의 판단 카드(종목 탭 안)."""
    bg, fg = LEVEL_COLOR.get(j["level"], LEVEL_COLOR["gray"])
    cur = m.get("currency") or ("KRW" if data.is_kr(h["code"]) else "USD")
    price = m.get("price")
    pnl_col = UP if (j["pnl"] or 0) > 0 else DOWN
    with st.container(border=True, key=f"hold_card_{h['id']}"):
        st.markdown(
            f'<div class="hd-top"><div><b class="hd-name">{html.escape(h["name"])}</b>'
            f'<span class="hd-code">{h["code"]}</span>'
            f'<span class="hd-px">{fmt_price(price, cur)}</span>'
            f'<span class="hd-pnl" style="color:{pnl_col}">{(j["pnl"] or 0):+.2f}% · {_rtxt(j)}</span></div>'
            f'<span class="hd-verdict" style="background:{bg};color:{fg};border-color:{fg}">{j["verdict"]}</span></div>'
            f'<div class="hd-sub">평단 {fmt_price(h["avg"], cur)}'
            + (f' · {h["qty"]:,.0f}주 · 평가손익 {(price - h["avg"]) * h["qty"]:+,.0f}' if h.get("qty") and price else "")
            + (' · 🌱 가치투자 계좌(손절선 대신 가치우파·가치훼손으로 판단)' if j.get("account") == "가치" else
               f' · 손절선 {fmt_price(j["stop_price"], cur)}(여유 {(j["stop_dist"] or 0):.1f}%)'
               f' · 3R 목표 {fmt_price(j["target3r"], cur)}')
            + f' · 매수일 {h.get("buy_date", "-")}'
            + (f' · {html.escape(h["memo"])}' if h.get("memo") else "") + "</div>",
            unsafe_allow_html=True)
        icon = {"good": "✅", "warn": "⚠️", "bad": "🛑", "info": "ℹ️"}
        st.markdown('<div class="hd-checks">' + "".join(
            f'<div class="hd-chk {st_}"><span>{icon[st_]} {html.escape(item)}</span><em>{html.escape(txt)}</em></div>'
            for st_, item, txt in j["checks"]) + "</div>", unsafe_allow_html=True)
        st.markdown('<div class="hd-act"><b>원칙대로라면</b><ul>' + "".join(
            f"<li>{html.escape(a)}</li>" for a in j["actions"]) + "</ul></div>", unsafe_allow_html=True)
        _deep_view(j)
        _quick_edit(h, stt)

        t_news, t_chart = st.tabs(["📰 뉴스·리포트 근거", "🕯️ 차트"])
        with t_news:
            ev = load_evidence(h["code"], h["name"])
            if stt["ai"]:
                k = f"hold_ai_{h['id']}"
                if st.button("🤖 뉴스를 원칙과 연결해 요약", key=f"{k}_btn"):
                    with st.spinner("뉴스·리포트를 읽는 중이에요."):
                        txt, err = holdings.ai_news_summary(stt["key"], stt["model"], h["name"], j,
                                                            ev["news"] + ev["research"])
                    st.session_state[k] = txt or err
                if st.session_state.get(k):
                    st.info(st.session_state[k])
            c1, c2 = st.columns([1.6, 1])
            with c1:
                st.markdown("**최근 뉴스** (네이버 증권 · 구글 뉴스 모음)")
                if ev["news"]:
                    st.markdown('<div class="hd-news">' + "".join(
                        f'<a href="{html.escape(x["url"])}" target="_blank"><span>{html.escape(x["title"])}</span>'
                        f'<em>{html.escape(x["source"])} · {html.escape(x["time"])} · {x["portal"]}</em></a>'
                        for x in ev["news"][:12]) + "</div>", unsafe_allow_html=True)
                else:
                    st.caption("뉴스를 받지 못했어요.")
            with c2:
                st.markdown("**증권사 리포트**")
                if ev["research"]:
                    st.markdown('<div class="hd-news">' + "".join(
                        f'<a href="{html.escape(x["url"])}" target="_blank"><span>{html.escape(x["title"])}</span>'
                        f'<em>{html.escape(x["source"])} · {html.escape(x["time"])}</em></a>'
                        for x in ev["research"]) + "</div>", unsafe_allow_html=True)
                else:
                    st.caption("최근 리포트가 없거나 받지 못했어요.")
            st.caption("뉴스는 10분마다 새로 모아요. 제목만으로는 판단이 틀릴 수 있으니 원문을 확인하세요.")
        with t_chart:
            candle_chart(h["code"], cur, quotes.get(h["code"]) if data.is_kr(h["code"]) else None,
                         m.get("high52"), key=f"hold_ch_{h['id']}")


SEC_ICON = {"good": ("🟢", "#1E7A45"), "info": ("⚪", "#51616C"), "warn": ("🟡", "#B7861D"), "bad": ("🔴", UP)}


def _deep_view(j: dict):
    """점수(펀더멘털 35·추세 25·수급 15·거래량 15·섹터·시장 10) + 분석 표 + 불타기 점검 + 상황별 대응."""
    if j.get("score") is None:
        return
    secs = j.get("sections") or {}
    chips = "".join(
        f'<span class="dp-chip" style="border-color:{SEC_ICON.get(v, SEC_ICON["info"])[1]}">{SEC_ICON.get(v, SEC_ICON["info"])[0]} {k}</span>'
        for k, v in secs.items())
    gcol = {"A": "#1E7A45", "B": "#2E6B6F", "C": "#B7861D", "D": UP}.get(j.get("grade"), "#51616C")
    st.markdown(f'<div class="dp-head"><span class="dp-score" style="background:{gcol}">{j["grade"]} · {j["score"]}점</span>'
                f'{chips}<em>가중치: 펀더멘털 35 · 추세 30 · 거래량 15 · 수급 10(보너스) · 섹터·시장 10</em></div>',
                unsafe_allow_html=True)
    icon = {"good": "✅", "warn": "⚠️", "bad": "🛑", "info": "ℹ️"}

    def block(title, items):
        if not items:
            return ""
        return (f'<div class="dp-block"><b>{title}</b>' + "".join(
            f'<div class="dp-row {st_}"><span>{icon[st_]} {html.escape(it)}</span><em>{html.escape(tx)}</em></div>'
            for st_, it, tx in items) + "</div>")
    st.markdown('<div class="dp-grid">' + block("📊 펀더멘털", j.get("fund")) + block("🧭 수급(외국인·기관)", j.get("flow"))
                + block("🔊 거래량", j.get("vol")) + block("🎓 거장 관점", j.get("masters")) + "</div>", unsafe_allow_html=True)
    pyr = j.get("pyramid")
    if pyr:
        rows = "".join(f'<span class="dp-cond {"ok" if ok else "no"}">{"✔" if ok else "✖"} {html.escape(n)}'
                       f'{(" · " + html.escape(v)) if v else ""}</span>' for n, ok, v in pyr["conds"])
        st.markdown(f'<div class="dp-pyr"><b>🔥 불타기(피라미딩) 점검 — {pyr["n_ok"]}/{pyr["n"]}'
                    f'{" · 오늘 충족" if pyr["ok"] else ""}</b><div>{rows}</div></div>', unsafe_allow_html=True)
    plan = j.get("plan") or []
    if plan:
        st.markdown('<div class="dp-plan"><b>🗺️ 앞으로 이렇게 대응</b>' + "".join(
            f'<div><span>{html.escape(k)}</span><em>{html.escape(v)}</em></div>' for k, v in plan) + "</div>",
            unsafe_allow_html=True)


def _rtxt(j: dict) -> str:
    return "🌱 가치 계좌" if j.get("account") == "가치" else f"{(j.get('r') or 0):+.2f}R"


def _merge_holdings(items: list) -> list:
    """같은 종목(코드)끼리 합치기: 수량 합산, 평단은 수량 가중 평균(수량이 없으면 단순 평균), 매수일은 가장 이른 날."""
    out, by = [], {}
    for h in items:
        c = h["code"]
        if c not in by:
            by[c] = dict(h)
            out.append(by[c])
            continue
        a = by[c]
        qa, qb = float(a.get("qty") or 0), float(h.get("qty") or 0)
        if qa + qb > 0:
            a["avg"] = (a["avg"] * qa + h["avg"] * qb) / (qa + qb)
        else:
            a["avg"] = (a["avg"] + h["avg"]) / 2
        a["qty"] = qa + qb
        dates = [d for d in (a.get("buy_date"), h.get("buy_date")) if d]
        a["buy_date"] = min(dates) if dates else ""
        memos = [m for m in (a.get("memo"), h.get("memo")) if m]
        a["memo"] = " / ".join(dict.fromkeys(memos))
    return out


def _holdings_manager(store: dict, stt: dict):
    """✏️ 보유 종목 관리: 표에서 바로 고치고, 🗑️ 체크해서 지우고, 같은 종목은 합치기."""
    hs = store["holdings"]
    codes = [h["code"] for h in hs]
    dups = sorted({h["name"] for h in hs if codes.count(h["code"]) > 1})
    with st.expander(f"✏️ 보유 종목 관리 — 고치기 · 지우기 · 합치기 ({len(hs)}종목)", expanded=bool(dups)):
        if dups:
            st.warning("같은 종목이 두 번 이상 들어 있어요: " + ", ".join(dups) + " → 아래 '🔗 같은 종목 합치기'를 누르면 수량을 합치고 평단을 다시 계산해요.")
        ed = st.data_editor(pd.DataFrame([{
            "🗑️": False, "종목명": h["name"], "코드": h["code"], "평단가": h["avg"], "수량": h.get("qty") or 0.0,
            "매수일": h.get("buy_date", ""), "손절%": h.get("stop_pct"), "절반 익절함": bool(h.get("half_taken")),
            "계좌": h.get("account", "추세"), "메모": h.get("memo", ""), "가치훼손": bool(h.get("impaired")),
            "순현금(억)": h.get("net_cash"), "영업이익(억)": h.get("op"), "영업이익률(%)": h.get("opm"),
            "_id": h["id"]} for h in hs]),
            key=f"hold_editor_{len(hs)}", hide_index=True, num_rows="fixed", width="stretch",
            column_config={"_id": None,
                           "🗑️": st.column_config.CheckboxColumn("🗑️ 삭제", help="지울 종목을 체크하고 아래 '체크한 종목 삭제'", width="small"),
                           "코드": st.column_config.TextColumn(disabled=True, width="small"),
                           "종목명": st.column_config.TextColumn(disabled=True),
                           "평단가": st.column_config.NumberColumn(format="%.0f"),
                           "수량": st.column_config.NumberColumn(format="%.0f"),
                           "손절%": st.column_config.NumberColumn(help="비우면 8% 또는 ATR (추세 계좌)"),
                           "계좌": st.column_config.SelectboxColumn(options=["추세", "가치"], required=True, width="small"),
                           "가치훼손": st.column_config.CheckboxColumn(help="가치 계좌: 가치훼손 이슈가 생기면 체크 → 매도 원칙"),
                           "순현금(억)": st.column_config.NumberColumn(help="가치 계좌 절대가치 계산용"),
                           "영업이익(억)": st.column_config.NumberColumn(help="연간(또는 올해 추정) 영업이익"),
                           "영업이익률(%)": st.column_config.NumberColumn(help="배수 = 영업이익률의 절반")})
        n_del = int(ed["🗑️"].sum()) if len(ed) else 0
        c1, c2, c3 = st.columns(3)
        do_save = c1.button("💾 변경 저장", key="hm_save", type="primary", width="stretch")
        do_del = c2.button(f"🗑️ 체크한 종목 삭제 ({n_del})", key="hm_del", width="stretch", disabled=n_del == 0)
        do_merge = c3.button("🔗 같은 종목 합치기", key="hm_merge", width="stretch", disabled=not dups)
        st.caption("칸을 눌러 바로 고친 뒤 '변경 저장' · 지울 종목은 🗑️ 체크 후 '삭제' · 새 종목은 위 '➕ 보유 종목 추가'(이미 있는 종목이면 추가 매수로 합쳐져요)")
        if do_save or do_del or do_merge:
            by_id = {h["id"]: h for h in hs}
            new = []
            for _, r in ed.iterrows():
                h = by_id.get(r.get("_id"))
                if not h or (do_del and bool(r["🗑️"])):
                    continue
                sp = r.get("손절%")
                h.update(avg=float(r["평단가"]), qty=float(r["수량"] or 0), buy_date=str(r["매수일"] or ""),
                         stop_pct=float(sp) if sp == sp and sp not in (None, "", 0) else None,
                         half_taken=bool(r["절반 익절함"]), memo=str(r["메모"] or ""),
                         account=r.get("계좌") if r.get("계좌") in ("추세", "가치") else "추세",
                         impaired=bool(r.get("가치훼손")),
                         net_cash=_numor(r.get("순현금(억)")), op=_numor(r.get("영업이익(억)")),
                         opm=_numor(r.get("영업이익률(%)")))
                new.append(h)
            if do_merge:
                new = _merge_holdings(new)
            store["holdings"] = new
            ok, msg = _persist_holdings(store, stt)
            (st.success if ok else st.error)(("삭제했어요. " if do_del else "합쳤어요. " if do_merge else "저장했어요. ") + msg)
            _rerun_frag()


def _quick_edit(h: dict, stt: dict):
    """종목 탭 안에서 바로 고치기·삭제."""
    with st.expander("✏️ 이 종목 수정 · 삭제"):
        c1, c2, c3, c4 = st.columns(4)
        avg = c1.number_input("평단가", value=float(h["avg"]), step=100.0, format="%.0f", key=f"qe_avg_{h['id']}")
        qty = c2.number_input("수량", value=float(h.get("qty") or 0), step=1.0, format="%.0f", key=f"qe_qty_{h['id']}")
        stop = c3.number_input("손절 %(0 = 8%/ATR)", value=float(h.get("stop_pct") or 0), step=0.5, key=f"qe_stop_{h['id']}")
        acct = c4.selectbox("계좌", ["추세", "가치"], index=0 if h.get("account", "추세") == "추세" else 1, key=f"qe_acct_{h['id']}")
        half = st.checkbox("절반 익절함", value=bool(h.get("half_taken")), key=f"qe_half_{h['id']}")
        c1, c2, c3 = st.columns([1, 1, 2])
        if c1.button("💾 저장", key=f"qe_save_{h['id']}", type="primary", width="stretch"):
            store = holdings.load()
            for x in store["holdings"]:
                if x["id"] == h["id"]:
                    x.update(avg=avg, qty=qty, stop_pct=stop or None, account=acct, half_taken=half)
            ok, msg = _persist_holdings(store, stt)
            (st.success if ok else st.error)(msg)
            _rerun_frag()
        sure = c3.checkbox("정말 지울게요", key=f"qe_sure_{h['id']}")
        if c2.button("🗑️ 삭제", key=f"qe_del_{h['id']}", width="stretch", disabled=not sure):
            store = holdings.load()
            store["holdings"] = [x for x in store["holdings"] if x["id"] != h["id"]]
            ok, msg = _persist_holdings(store, stt)
            (st.success if ok else st.error)(f"{h['name']} 삭제했어요. " + msg)
            _rerun_frag()


def _numor(v):
    try:
        v = float(v)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _render_mode(store: dict, stt: dict, ctx: dict):
    """변동성 장세 알림 + 손절 사다리(R 단위 트레일링 스톱) 선택."""
    if ctx.get("vol_mode"):
        st.warning(f"🌊 변동성 큰 횡보장 — 최근 20거래일 중 지수가 ±3% 넘게 움직인 날 {ctx.get('vol_days')}일. "
                   "깡토 원칙: 쿠션 있는 종목은 추세가 무너지기 전까지 홀딩, 늦게 산 종목은 손절 -5∼6%·목표 +15∼18%로 타이트하게"
                   "(손익비 1:3은 그대로, 기준 가격만 좁힘). 확신이 부족하면 비중을 줄이거나 쉬어도 돼요.")
    cur = (store.get("settings") or {}).get("ladder", "원칙")
    if cur not in ("원칙", "1R", "2R", "끄기"):
        cur = "원칙"
    c1, c2 = st.columns([2, 3])
    lad = c1.segmented_control("🪜 손절 사다리(수익이 날수록 손절선 올리기)", ["원칙", "1R", "2R", "끄기"], default=cur, required=True,
                               key="hold_ladder",
                               format_func=lambda x: {"원칙": "원칙(기본)", "1R": "1R 간격", "2R": "2R 간격", "끄기": "끄기"}[x],
                               help="원칙(기본): 2R에 본전, 4R에 +1R, 5R에 +2R … / 1R 간격: 1R 오르면 본전, 2R이면 +1R … (초보·변동성 장) / 2R 간격: 2R에 본전, 4R에 +2R … "
                                    "/ 끄기: 절반 익절 전까지 처음 손절선 유지. 익숙해지면 간격을 넓혀 가라는 게 깡토 권장")
    c2.caption("예) 손절 8%·1R 간격이면 +8%에 손절선 = 평단, +16%에 손절선 = 평단+8%. 노이즈에 털릴 수 있어 익숙해지면 2R·3R로 넓혀요.")
    if lad and lad != cur:
        store.setdefault("settings", {})["ladder"] = lad
        _persist_holdings(store, stt)
        _rerun_frag()


def _render_capital(store: dict, rows, stt: dict, ctx: dict | None = None):
    """🧮 자본관리: 계좌 원금·현금을 넣으면 지금 지켜야 할 BEP(멈춤선)를 보여줘요(스승님 원칙)."""
    acc = store.setdefault("account", {})
    stock_val = sum((m.get("price") or 0) * (h.get("qty") or 0) for h, m, *_ in rows)
    equity = stock_val + float(acc.get("cash") or 0)
    principal = float(acc.get("principal") or 0)
    peak0 = float(acc.get("peak") or 0)
    today = data.now_kst().strftime("%Y-%m-%d")
    # 최고 평가액은 오를 때만 기록 — 저장소에 너무 자주 쓰지 않게(저장할 때마다 앱이 새로 배포돼요) 하루 한 번, 1% 넘게 오를 때만
    if principal and equity > peak0 * 1.01 and acc.get("peak_day") != today:
        acc.update(peak=round(equity), peak_day=today)
        _persist_holdings(store, stt)
    b = holdings.account_bep(principal, float(acc.get("peak") or 0))
    with st.expander("🧮 자본관리 — 계좌 BEP(멈춤선)", expanded=bool(b) and equity <= b.get("bep", 0) * 1.03):
        c1, c2, c3 = st.columns([1, 1, 1])
        p_in = c1.number_input("계좌 원금(원)", min_value=0.0, step=1_000_000.0, value=principal, format="%.0f", key="cap_p")
        cash = c2.number_input("현금(원)", min_value=0.0, step=100_000.0, value=float(acc.get("cash") or 0), format="%.0f",
                               key="cap_c")
        if c3.button("저장", key="cap_save", width="stretch"):
            acc.update(principal=p_in, cash=cash)
            if not acc.get("peak") or p_in > acc["peak"]:
                acc["peak"] = p_in
            ok, msg = _persist_holdings(store, stt)
            (st.success if ok else st.error)(msg)
            _rerun_frag()
        if not b:
            st.caption("원금을 넣으면 '원금 5% 이상 잃지 않기(최대 10%)', '계좌가 커지면 BEP 올리기', "
                       "'BEP에 닿으면 전액 현금화' 원칙으로 멈춤선을 계산해요. 보유 종목 수량이 있어야 평가액이 맞아요.")
            return
        dist = (equity / b["bep"] - 1) * 100
        col = UP if dist <= 0 else ("#B7861D" if dist < 3 else "#1E7A45")
        st.markdown(
            f'<div class="cap-row"><div><em>지금 평가액</em><b>{equity:,.0f}원</b></div>'
            f'<div><em>최고 평가액</em><b>{b["peak"]:,.0f}원</b></div>'
            f'<div><em>지켜야 할 BEP</em><b style="color:{col}">{b["bep"]:,.0f}원</b>'
            f'<span style="color:{col}">여유 {dist:+.1f}%</span></div>'
            f'<div><em>절대 마지노선(원금 -10%)</em><b>{b["hard"]:,.0f}원</b></div></div>', unsafe_allow_html=True)
        # 깡토 책: 한 번에 잃어도 되는 금액(계좌의 1~2%, 내 원칙 1.5%) ÷ 손절 폭(8%) = 한 종목 최대 투자금,
        # 장세에 따라 유닛 수: 오르는 장 3 · 옆으로 2 · 내리는 장 1
        n_up = (ctx or {}).get("n_above60")
        units = {2: 3, 1: 2, 0: 1}.get(n_up, 2)
        max_pos = equity * 0.015 / 0.08
        st.markdown(
            f'<div class="cap-row"><div><em>한 종목 최대 투자금(위험 1.5% ÷ 손절 8%)</em><b>{max_pos:,.0f}원</b>'
            f'<span>평가액의 {max_pos / equity * 100:.1f}%</span></div>'
            f'<div><em>지금 장세 유닛</em><b>{units}유닛</b><span>지수 60일선 위 {n_up if n_up is not None else "-"}/2 → '
            f'한 유닛 {max_pos / 4:,.0f}원(최대 비중을 4유닛으로)</span></div></div>', unsafe_allow_html=True)
        st.caption("유닛은 이긴 뒤에만 한 칸 늘리고(목표 3R에 닿아야 이긴 것) 진 뒤에는 한 칸 줄여요. 최대 8종목.")
        if equity <= b["bep"]:
            st.error("🛑 계좌가 BEP에 닿았어요. 원칙: 욕심이 나도 전액 현금화하고 투자를 멈춘 뒤 시장을 다시 관찰하기.")
        else:
            st.caption(f"BEP 단계 {b['level']} — 최고 평가액이 원금보다 10% 오를 때마다 BEP를 한 칸씩 올려요"
                       "(+10% → 본전, +20% → 원금+10% …). 운전의 브레이크처럼, BEP에서 멈출 수 있느냐가 핵심.")


PRINCIPLES = [
    ("자본관리", ["원금의 5%가 훼손되지 않게(최대 10%) — 항상 계좌 전체 기준으로 비중·손절선을 정한다",
                "계좌가 커지면 BEP도 단계적으로 올린다 / BEP에 닿으면 전액 현금화하고 시장을 다시 본다"]),
    ("추세추종", ["그룹주(섹터)가 패턴과 거래대금을 만들 때 — 섹터가 안 움직이면 개별 종목 추세도 어렵다",
                "매수 4조건: 주도섹터 · 돌파(매물대·신고가) · 장대양봉 · 거래량. 하락 뒤 반등은 돌파가 아니다",
                "주봉으로 추세(60일선 우상향) 먼저, 일봉으로 타점. 상승 → 조정 → 재돌파가 기본 스테이지",
                "RS는 지수 RS(코스피·코스닥)보다 높아야 대상 — 91이냐 98이냐보다 커트라인 위인지가 중요",
                "지지 이평선 이탈 = 패턴 훼손 → 매도(너무 좋아 보이면 절반이라도)",
                "승률 30%면 2.33R가 손익분기, 3R가 최소 목표이자 출발선 — 하방은 닫고 상방은 연다, 물타기 대신 불타기",
                "트리거가 올 때까지 기다리는 것 = 개인이 기관을 이기는 방법. 변동성 장에선 쉬어도 된다"]),
    ("가치투자", ["절대가치 = 순현금 + 영업이익 × (영업이익률의 절반) 배",
                "가치우파(하락 멈춤·횡보·조금씩 우상향) 확인 후 천천히 분할매수 — 매수 기간과 총 비중을 먼저 정한다",
                "가치훼손 이슈가 생기면(우파 → 좌파) 매도. 가치투자라고 무조건 들고 가지 않는다"]),
    ("거장 관점(유명 트레이더 매매법)", [
        "오닐: 피벗(손잡이 고점) +5% 넘게 쫓아 사지 않기 · 매수가 기준 7~8% 손절 · 지수 물량일(거래량 늘며 -0.2%↓) 25일에 4~5일이면 상승장 끝",
        "미너비니: 추세 템플릿 8가지(150·200일선 위, 200일선 상승, 50일선 최상단, 52주 저점 +25%↑·고점 -25% 이내, RS 상위) · 돌파 직후 20일선 아래 마감이면 확률 절반",
        "와인스타인: 2단계(30주선 위·상승)에서만, 4단계는 사지 않음 · 손절은 %가 아니라 돌파 자리 아래, 15%보다 멀면 안 삼",
        "쟁거: 거래량 실린 하락일엔 절반 덜고, 되돌리는 거래량 실린 상승일에 다시 채움 · 돌파 지점 아래로 되돌아오면 전량",
        "깡토 책: 기관+외국인 순매수가 거래량의 10%↑ · 주봉·일봉 정배열 · 피라미드 1차 30%→2차 15%→3차 10% · 2~3주 횡보면 시간 손절"]),
    ("깡토 Q&A · 횡보장", [
        "변동성 큰 횡보장: 지켜보기보다 타이트하게 — 손절 -5~6%면 목표 +15~18%(손익비 1:3 유지, 기준 가격만 변경)",
        "수익 쿠션 있는 종목은 추세가 무너지기 전까지 홀딩, 늦게 산 후발 종목만 타이트하게",
        "변동성 장 종목: 시총 크고 RS 높고 섹터가 같이 움직이고 거래량 최대, 섹터에서 가장 먼저 신고가 쓴 종목",
        "R 사다리: 1R 오르면 손절 본전, 2R이면 +1R … 처음엔 1R 간격, 익숙해지면 2R·3R로 넓히기",
        "수급·매크로·시즈널리티는 보너스 알파 — 메인 팩터는 추세, 그 추세를 만드는 건 이익(하이브리드)",
        "숫자 없는 내러티브 종목은 해도 되지만 비중을 줄여서, 넘버스+내러티브가 같이 있는 종목이 우선",
        "IPO 셋업: 상장 첫날 시가를 넘을 때",
        "실적이 어긋나면 — 추세 계좌: 맞았냐보다 추세가 꺾였냐 / 가치 계좌: 단순히 다음 분기로 밀린 거면 홀딩",
        "고멀티플: 추세 계좌에선 시장이 높게 쳐준다는 뜻이라 오히려 괜찮음, 가치 계좌에선 후순위"]),
    ("선매입 · 매물대 돌파", [
        "52주 신고가 전이라도: 인더스트리 액션 섹터 + 거래량 1.5배↑ 양봉 + 1년 매물대의 큰 덩어리를 종가로 돌파 + RS 70↑",
        "손절은 뚫은 매물대 아래(최대 1R 8%) · 위에 남은 매물 비율이 낮을수록, 다음 매물대가 멀수록 가볍다",
        "비중은 판단 회로의 결론(매수 1유닛 / 정찰병 3%)을 따른다 — 막힌 단계가 있으면 '선매입 후보'로만 본다"]),
    ("스승님 10/28 · 반등장 정찰병", [
        "반등 3일차가 강세로 끝나면 다음 날 시초가부터 정찰병 — 정석 FTD를 기다리지 않아도 됨",
        "의심스러운 FTD가 정석보다 좋을 때가 있다 — 주가는 의심의 벽을 타고 오른다",
        "조정 중에 견조주(하락장에서 양호한 종목)를 미리 체크 → 그중 신고가 가는 종목부터 편입",
        "그날 인더스트리 액션 섹터의 선도주(먼저 신고가)가 1순위 · 정해진 규칙대로, 수익 쿠션이 넉넉할수록 여유"]),
    ("깡토 Q&A · 급락장(2026.10)", [
        "추세추종은 늦은 매매 — 반등을 예상해 미리 사지 않고, 쉴 때는 쉰다. 재진입은 선 대신 FTD(바닥 반등 3~4일차 강한 상승) 뒤 점진적으로",
        "정찰병은 죽는 게 당연 — 그래도 쉬는 중에 조금씩 넣어 두고 달리기 시작하면 빠르게 비중 확대. 같은 섹터에 우르르 넣지 않기",
        "변동성이 커도 손절을 넓히지 않는다(넓히면 손실만 커짐). 존버는 추세추종 사전에 없다",
        "늦었다/빠르다의 기준은 섹터의 첫 신고가 — 먼저 달리고, 먼저 거래량 터지고, 먼저 신고가 가는 종목",
        "하락장에서 버틴 주도주를 길게, 과대낙폭은 반등만 빠르다. 계좌는 슈퍼루키 1~2종목이 끈다 — 늦게 사도 추세가 강하면 피라미딩"]),
    ("검증 메모", ["한국 논문(유명 트레이더 매매법 11장): 52주 최고가 하나만으로는 한국에서 예측력이 약하다는 결과도 있음 → 신고가는 주도섹터·실적·수급과 같이 볼 때 의미",
                "정리본의 [복원]·[2차] 숫자는 원전값이 아님 — 앱의 기준치는 참고용이고 바꿔 가며 확인할 자리"]),
    ("계좌 분리", ["추세 계좌와 가치 계좌는 물과 기름 — 섞지 않는다",
                 "추세 계좌에서 '많이 빠졌으니 싸다'며 사거나, 20일선 이탈 후 'AI가 좋으니까' 버티지 않는다 → 그건 가치 계좌의 일"]),
]


def _render_principles():
    with st.expander("📘 내 투자 원칙 (스승님·깡토 정리)"):
        st.markdown('<div class="pr-grid">' + "".join(
            f'<div class="pr-card"><b>{html.escape(t)}</b><ul>' + "".join(f"<li>{html.escape(x)}</li>" for x in items)
            + "</ul></div>" for t, items in PRINCIPLES) + "</div>", unsafe_allow_html=True)
        st.caption("보유 종목 판정은 이 원칙을 점검표로 옮긴 거예요. 매수·매도 추천이 아니라 원칙 확인용이에요.")


def _holdings_overview(rows, dot):
    """보유 종목 한눈에: 판정·수익률·손절선 여유를 표 하나로."""
    trs = []
    for h, m, bars, j in rows:
        cur = m.get("currency") or ("KRW" if data.is_kr(h["code"]) else "USD")
        bg, fg = LEVEL_COLOR.get(j["level"], LEVEL_COLOR["gray"])
        pnl = j["pnl"] or 0
        pc = UP if pnl > 0 else (DOWN if pnl < 0 else "#51616C")
        sd = j["stop_dist"]
        sd_col = UP if sd is not None and sd < 3 else "#16212B"
        bar_w = max(0, min(100, (sd or 0) / 15 * 100))
        trs.append(
            f'<tr><td class="l"><b>{html.escape(h["name"])}</b><span class="ho-code">{h["code"]}</span>'
            f'<span class="ho-acct">{"🌱가치" if h.get("account") == "가치" else "📈추세"}</span></td>'
            f'<td class="l"><span class="ho-badge" style="background:{bg};color:{fg};border-color:{fg}">{j["verdict"]}</span>'
            f'{(" <span class=\'ho-acct\'>" + str(j.get("grade")) + " · " + str(j.get("score")) + "점</span>") if j.get("score") is not None else ""}</td>'
            f'<td>{fmt_price(m.get("price"), cur)}</td><td>{fmt_price(h["avg"], cur)}</td>'
            f'<td style="color:{pc};font-weight:800">{pnl:+.2f}%</td><td style="color:{pc}">{_rtxt(j)}</td>'
            f'<td>{fmt_price(j["stop_price"], cur)}</td>'
            f'<td class="ho-sd"><div class="ho-bar"><i style="width:{bar_w:.0f}%"></i></div>'
            f'<span style="color:{sd_col}">{(sd or 0):.1f}%</span></td>'
            f'<td>{fmt_price(j["target3r"], cur)}</td>'
            f'<td class="l ho-memo">{html.escape(h.get("memo") or "")}</td></tr>')
    st.markdown(
        '<div class="ho-wrap"><table class="ho-tbl"><thead><tr><th class="l">종목</th><th class="l">원칙 판정</th>'
        '<th>현재가</th><th>평단</th><th>수익률</th><th>R</th><th>손절선</th><th>손절까지 여유</th><th>3R 목표</th>'
        '<th class="l">메모</th></tr></thead><tbody>' + "".join(trs) + '</tbody></table></div>',
        unsafe_allow_html=True)
    st.caption("🔴 손절·정리 조건 · 🟠 비중 축소 신호 · 🔵 3R 익절 구간 · 🟡 주의 · 🟢 원칙상 보유 — 위 탭에서 종목을 고르면 점검표·뉴스·차트가 나와요.")


LOCK_IDLE_MIN = 30        # 연 지 이만큼(분) 지나면 다시 잠가요
LOCK_MAX_FAIL = 5         # 연속으로 틀리면
LOCK_WAIT_SEC = 60        # 이만큼(초) 기다려야 해요


def _persist_settings(d: dict, stt: dict) -> tuple[bool, str]:
    holdings.save_settings_local(d)
    if stt["gh"]:
        return reports.save_github(d, stt["token"], stt["repo"], stt["branch"], path="user_settings.json")
    return True, "이 서버에만 임시로 저장했어요(GitHub 토큰이 없으면 앱을 다시 켤 때 사라질 수 있어요)."


def _pw_ok(pw: str, pw2: str) -> str | None:
    if len(pw) < 4:
        return "비밀번호는 4자 이상으로 정해 주세요."
    if pw != pw2:
        return "두 번 넣은 비밀번호가 달라요."
    return None


def _question_inputs(prefix: str, cur: str | None = None) -> tuple[str, str]:
    qs = holdings.RECOVERY_QUESTIONS
    idx = qs.index(cur) if cur in qs else (len(qs) - 1 if cur else 0)
    q = st.selectbox("복구 질문", qs, index=idx, key=f"{prefix}_q")
    if q == "직접 질문 쓰기":
        q = st.text_input("질문", value=cur if cur and cur not in qs else "", key=f"{prefix}_qc")
    a = st.text_input("답(띄어쓰기·대소문자는 상관없어요)", key=f"{prefix}_a")
    return q, a


def _show_code_once():
    code = st.session_state.get("lock_new_code")
    if code:
        st.markdown(f'<div class="lk-code"><b>🔑 복구 코드</b><span>{code}</span>'
                    '<em>비밀번호를 잊었을 때 쓰는 코드예요. 지금 한 번만 보여 드려요 — 휴대폰 메모나 종이에 꼭 적어 두세요.</em></div>',
                    unsafe_allow_html=True)
        if st.button("적어 뒀어요", key="lock_code_ack"):
            st.session_state.pop("lock_new_code", None)
            _rerun_frag()


def _lock_gate(stt: dict) -> bool:
    """보유 종목 잠금. 열렸으면 True. 잠겨 있으면 화면(만들기·열기·찾기)을 그리고 False."""
    now = time.time()
    if st.session_state.get("hold_unlocked"):
        # 자동 새로고침이 계속 돌아서 '안 쓴 시간'을 잴 수 없어요 → 연 지 LOCK_IDLE_MIN분이 지나면 다시 잠가요
        if now - st.session_state.get("hold_seen", now) > LOCK_IDLE_MIN * 60:
            st.session_state["hold_unlocked"] = False
            st.info(f"연 지 {LOCK_IDLE_MIN}분이 지나서 다시 잠갔어요.")
        else:
            return True
    cfg = holdings.load_settings()
    has_hold = bool(holdings.load().get("holdings"))
    reset_key = _secret("RESET_KEY") or _secret("APP_PASSWORD")

    with st.container(border=True, key="lock_box"):
        # ① 처음: 비밀번호 만들기
        if not holdings.has_password(cfg):
            st.markdown('<div class="lk-title">🔐 보유 종목 비밀번호 만들기</div>'
                        '<div class="lk-sub">내 보유 종목은 비밀번호를 넣어야만 보여요. 처음 한 번 정해 주세요.</div>',
                        unsafe_allow_html=True)
            if has_hold and not reset_key:
                st.error("저장된 보유 종목은 있는데 비밀번호 설정이 없어졌어요. 다른 사람이 새 비밀번호를 만들지 못하게, "
                         "Streamlit Secrets에 RESET_KEY = \"아무 문구\"를 넣은 뒤 그 문구로 다시 만들어 주세요.")
                return False
            need_key = has_hold and bool(reset_key)
            pw = st.text_input("새 비밀번호(4자 이상)", type="password", key="lk_new1")
            pw2 = st.text_input("한 번 더", type="password", key="lk_new2")
            hint = st.text_input("비밀번호 힌트(선택 · 잠금 화면에 보여요)", key="lk_hint",
                                 placeholder="예: 우리 반 번호 + 좋아하는 숫자")
            q, a = _question_inputs("lk_setq")
            rk = st.text_input("RESET_KEY(Secrets에 넣은 문구)", type="password", key="lk_rk") if need_key else ""
            if st.button("비밀번호 만들기", type="primary", key="lk_make"):
                err = _pw_ok(pw, pw2) or (None if a.strip() else "복구 질문의 답을 넣어 주세요.") \
                    or ("RESET_KEY가 달라요." if need_key and rk != reset_key else None)
                if err:
                    st.error(err)
                else:
                    code = holdings.set_password(cfg, pw, hint, q, a, new_code=True)
                    ok, msg = _persist_settings(cfg, stt)
                    if ok:
                        st.session_state.update(hold_unlocked=True, hold_seen=now, lock_new_code=code)
                        _rerun_frag()
                    st.error(msg)
            return False

        # ② 잠금 화면: 비밀번호 넣기
        lock = cfg.get("lock") or {}
        st.markdown('<div class="lk-title">🔒 내 보유 종목은 잠겨 있어요</div>'
                    '<div class="lk-sub">비밀번호를 넣으면 열려요. ' + f'열고 {LOCK_IDLE_MIN}분이 지나거나 창을 새로 열면 다시 잠겨요.</div>',
                    unsafe_allow_html=True)
        wait = st.session_state.get("lk_until", 0) - now
        if wait > 0:
            st.error(f"{LOCK_MAX_FAIL}번 틀려서 잠깐 멈췄어요. {int(wait) + 1}초 뒤에 다시 넣어 주세요.")
        c1, c2 = st.columns([3, 1])
        pw = c1.text_input("비밀번호", type="password", key="lk_pw", label_visibility="collapsed",
                           placeholder="비밀번호", disabled=wait > 0)
        go = c2.button("열기", type="primary", width="stretch", key="lk_go", disabled=wait > 0)
        tried = go or (pw and pw != st.session_state.get("lk_last"))   # 자동 새로고침 때 같은 값으로 다시 세지 않게
        if tried and pw and wait <= 0:
            st.session_state["lk_last"] = pw
            if holdings.check_password(cfg, pw):
                st.session_state.update(hold_unlocked=True, hold_seen=now, lk_fail=0)
                _rerun_frag()
            else:
                n = st.session_state.get("lk_fail", 0) + 1
                st.session_state["lk_fail"] = n
                if n >= LOCK_MAX_FAIL:
                    st.session_state.update(lk_until=now + LOCK_WAIT_SEC, lk_fail=0)
                st.error(f"비밀번호가 달라요. ({n}/{LOCK_MAX_FAIL})")
        if lock.get("hint"):
            st.caption(f"💡 힌트: {lock['hint']}")
        # 친구는 이 칸이 아니라 ⭐ 내 종목 탭에서 자기 비밀번호로 들어가요
        st.info("👥 **친구라면 여기가 아니에요.** 이 칸은 앱 주인 전용이에요. "
                "⭐ 내 종목 탭에서 내 비밀번호를 치면 내 관심종목과 포트폴리오가 나와요.")
        st.button("⭐ 내 종목 탭으로 가기", key="lk_to_watch",
                  on_click=lambda: st.session_state.update(main_tab_sel="⭐ 내 종목"))

        # ③ 비밀번호 찾기(다시 정하기)
        with st.expander("🔑 비밀번호를 잊으셨나요?"):
            st.caption("비밀번호는 암호화해서 저장해서 그대로 보여 드릴 수는 없어요. 아래 방법 중 하나로 확인하면 새 비밀번호로 바꿀 수 있어요.")
            ways = ["복구 질문"] + ["복구 코드"] + (["Secrets의 RESET_KEY"] if reset_key else [])
            way = st.segmented_control("확인 방법", ways, default="복구 질문", required=True, key="lk_way")
            if way == "복구 질문":
                st.markdown(f"**{lock.get('question', '(질문 없음)')}**")
                proof = st.text_input("답", key="lk_ans")
                ok_proof = holdings.check_answer(cfg, proof) if proof else False
            elif way == "복구 코드":
                proof = st.text_input("복구 코드(예: ABCD-EFGH-JKLM)", key="lk_code")
                ok_proof = holdings.check_code(cfg, proof) if proof else False
            else:
                proof = st.text_input("RESET_KEY", type="password", key="lk_rk2")
                ok_proof = bool(proof) and proof == reset_key
            if proof and not ok_proof:
                st.error("확인이 안 됐어요. 다시 넣어 주세요.")
            if ok_proof:
                st.success("확인됐어요. 새 비밀번호를 정해 주세요.")
                n1 = st.text_input("새 비밀번호", type="password", key="lk_r1")
                n2 = st.text_input("한 번 더", type="password", key="lk_r2")
                if st.button("새 비밀번호로 바꾸기", type="primary", key="lk_reset"):
                    err = _pw_ok(n1, n2)
                    if err:
                        st.error(err)
                    else:
                        code = holdings.set_password(cfg, n1, new_code=(way == "복구 코드"))
                        ok, msg = _persist_settings(cfg, stt)
                        if ok:
                            st.session_state.update(hold_unlocked=True, hold_seen=now, lk_fail=0,
                                                    lock_new_code=code if code else None)
                            _rerun_frag()
                        st.error(msg)
    return False


def _security_panel(stt: dict):
    """🛡️ 보안: 앱 전체 잠금 · 접속 기록 · IP 차단/해제 · 점검표."""
    cfg = holdings.load_settings()
    with st.expander("🛡️ 보안 — 앱 전체 잠금 · 접속 기록 · 차단"):
        forced = _secret("APP_LOCK").lower() in ("on", "true", "1", "yes")
        on = bool(cfg.get("app_lock")) or forced
        st.markdown(f"**앱 전체 잠금: {'🔒 켜짐' if on else '🔓 꺼짐'}** — 켜면 첫 화면부터 비밀번호가 있어야 보여요(내 보유 비밀번호와 같아요)."
                    + (" · Secrets의 APP_LOCK으로 켜져 있어요." if forced else ""))
        cur = st.text_input("지금 비밀번호(확인용)", type="password", key="sec_cur")
        if not forced and st.button("🔒 앱 전체 잠금 켜기" if not on else "🔓 앱 전체 잠금 끄기", key="sec_toggle"):
            if not holdings.check_password(cfg, cur):
                st.error("지금 비밀번호가 달라요.")
            else:
                cfg["app_lock"] = not on
                ok, msg = _persist_settings(cfg, stt)
                (st.success if ok else st.error)("앱 전체 잠금을 " + ("켰어요." if not on else "껐어요.") + " " + msg)
                st.session_state["app_unlocked"] = True
        log = list(reversed(_sec_store()["log"]))
        st.markdown(f"**최근 접속 기록** ({len(log)}건 · 앱을 다시 켜면 비워져요)")
        if log:
            st.dataframe(pd.DataFrame(log[:200]), hide_index=True, height=240, width="stretch")
        c1, c2, c3 = st.columns([1.3, 1.6, 0.8])
        ip = c1.text_input("차단할 IP", key="sec_ip", placeholder="예: 203.0.113.7")
        memo = c2.text_input("메모", key="sec_memo", placeholder="예: 비밀번호 계속 틀림")
        if c3.button("차단", key="sec_ban", width="stretch", disabled=not ip.strip()):
            if not holdings.check_password(cfg, cur):
                st.error("위에 지금 비밀번호를 넣어야 차단할 수 있어요.")
            elif ip.strip() == _client_ip():
                st.error("지금 내가 쓰는 주소는 차단할 수 없어요.")
            else:
                cfg.setdefault("blocked_ips", []).append({"ip": ip.strip(), "at": data.now_kst().strftime("%Y-%m-%d %H:%M"),
                                                          "memo": memo.strip()})
                ok, msg = _persist_settings(cfg, stt)
                (st.success if ok else st.error)(f"{ip.strip()} 차단했어요. {msg}")
        bl = cfg.get("blocked_ips", [])
        if bl:
            st.markdown(f"**차단 목록** ({len(bl)}개)")
            for i, b in enumerate(bl):
                c1, c2 = st.columns([5, 1])
                c1.caption(f"🚫 {b.get('ip')} · {b.get('at', '')} · {b.get('memo', '')}")
                if c2.button("해제", key=f"sec_unban_{i}"):
                    if holdings.check_password(cfg, cur):
                        cfg["blocked_ips"] = [x for j, x in enumerate(bl) if j != i]
                        ok, msg = _persist_settings(cfg, stt)
                        (st.success if ok else st.error)(msg)
                        _rerun_frag()
                    else:
                        st.error("위에 지금 비밀번호를 넣어야 해제할 수 있어요.")
        st.caption(f"지금 내 주소: {_client_ip()} · 한 주소가 {SEC_MAX_FAIL}번 틀리면 1시간 막고, 하루 {SEC_AUTOBAN}번 넘게 틀리면 자동으로 차단 목록에 올려요.")
        st.markdown(
            "**꼭 같이 해 두세요(앱 밖 설정)**\n"
            "1. **GitHub 저장소 비공개(Private)** — 코드·보유 종목·리포트 파일을 아무도 못 봐요. 저장소 → Settings → Change visibility.\n"
            "2. **Streamlit 공유 설정 'Only specific people'** — 내가 허락한 이메일만 앱 주소에 들어올 수 있어요. "
            "앱 오른쪽 위 Share → Who can view this app. 가장 강력한 잠금이에요.\n"
            "3. **Secrets** — API 키·토큰은 Secrets에만 두고 코드·저장소에 절대 넣지 마세요.")


def _lock_settings(stt: dict):
    """열린 상태에서: 잠그기 · 비밀번호 바꾸기 · 힌트/복구 질문 바꾸기 · 복구 코드 새로 받기."""
    cfg = holdings.load_settings()
    lock = cfg.get("lock") or {}
    c1, c2 = st.columns([5, 1])
    c1.caption(f"🔓 열림 · 연 지 {LOCK_IDLE_MIN}분이 지나면 자동으로 잠겨요"
               + (f" · 비밀번호 바꾼 날 {lock['changed_at']}" if lock.get("changed_at") else ""))
    if c2.button("🔒 잠그기", width="stretch", key="lk_lock"):
        st.session_state["hold_unlocked"] = False
        _rerun_frag()
    _show_code_once()
    with st.expander("⚙️ 비밀번호 · 힌트 · 복구 설정"):
        what = st.segmented_control("바꿀 것", ["비밀번호", "힌트", "복구 질문", "복구 코드 새로 받기"], default="비밀번호",
                                    required=True, key="lk_what")
        cur = st.text_input("지금 비밀번호(확인용)", type="password", key="lk_cur")
        if what == "비밀번호":
            n1 = st.text_input("새 비밀번호", type="password", key="lk_c1")
            n2 = st.text_input("한 번 더", type="password", key="lk_c2")
        elif what == "힌트":
            new_hint = st.text_input("새 힌트(비우면 힌트 없음)", value=lock.get("hint", ""), key="lk_ch")
        elif what == "복구 질문":
            q, a = _question_inputs("lk_chq", lock.get("question"))
        else:
            st.caption("새 코드를 받으면 예전 복구 코드는 더 이상 안 돼요.")
        if st.button("저장", type="primary", key="lk_save"):
            if not holdings.check_password(cfg, cur):
                st.error("지금 비밀번호가 달라요.")
                return
            code = None
            if what == "비밀번호":
                err = _pw_ok(n1, n2)
                if err:
                    st.error(err)
                    return
                holdings.set_password(cfg, n1)
            elif what == "힌트":
                cfg["lock"]["hint"] = new_hint.strip()
            elif what == "복구 질문":
                if not a.strip():
                    st.error("답을 넣어 주세요.")
                    return
                cfg["lock"]["question"] = q.strip()
                cfg["lock"]["answer"] = holdings._hash(holdings._norm_answer(a))
            else:
                code = holdings.new_recovery_code()
                cfg["lock"]["code"] = holdings._hash(code.replace("-", ""))
            ok, msg = _persist_settings(cfg, stt)
            if ok:
                st.session_state["lock_new_code"] = code
                st.success(f"{what} 저장했어요.")
                _rerun_frag()
            else:
                st.error(msg)


@st.fragment
def render_holdings_tab(df: pd.DataFrame, quotes: dict):
    """💼 내 보유: 평단가를 넣으면 내 원칙으로 점검하고, 뉴스·리포트 근거를 모아 보여줘요."""
    stt = _rep_status()
    _sec_h("💼 내 보유 종목", "평단가를 넣으면 내 매매 원칙으로 점검해요 · 매수·매도 추천이 아니라 원칙 점검표예요")
    if not _lock_gate(stt):
        return
    _lock_settings(stt)
    _security_panel(stt)
    store = holdings.load()
    board_labels = {s["code"]: s["name"] for s in STOCKS}

    with st.expander("➕ 보유 종목 추가", expanded=not store["holdings"]):
        c1, c2 = st.columns([1.4, 1])
        q = c1.text_input("종목 이름이나 코드", key="hold_q", placeholder="예: 인텍플러스, 064290")
        cands = []
        if q.strip():
            ql = q.strip().lower()
            cands = [(c, n) for c, n in board_labels.items() if ql in n.lower() or c.lower().startswith(ql)][:10]
            if data.is_kr(q.strip()) and len(q.strip()) == 6 and q.strip() not in board_labels:
                cands.insert(0, (q.strip(), q.strip()))
            if len(cands) < 5:
                cands += [x for x in naver_search(q.strip()) if x[0] not in {c for c, _ in cands}][:8]
        pick = c2.selectbox("찾은 종목", cands, format_func=lambda t: f"{t[1]} ({t[0]})", key="hold_pick",
                            placeholder="먼저 왼쪽에 검색", index=0 if cands else None)
        c1, c2, c3, c4 = st.columns(4)
        avg = c1.number_input("평단가", min_value=0.0, step=100.0, format="%.2f", key="hold_avg")
        qty = c2.number_input("수량(선택)", min_value=0.0, step=1.0, format="%.0f", key="hold_qty")
        bdate = c3.date_input("매수일", value=data.now_kst().date(), key="hold_date")
        stop = c4.number_input("손절 %(비우면 8% 또는 ATR)", min_value=0.0, max_value=50.0, step=0.5, value=0.0, key="hold_stop")
        c1, c2 = st.columns([3, 1])
        memo = c1.text_input("메모(선택)", key="hold_memo", placeholder="예: 후공정 섹터 2번째 돌파 진입")
        acct = c1.segmented_control("계좌", ["추세", "가치"], default="추세", required=True, key="hold_acct",
                                    format_func=lambda a: "📈 추세추종 계좌" if a == "추세" else "🌱 가치투자 계좌",
                                    help="추세·가치 계좌는 섞지 않는 게 원칙. 추세 = 돌파 매수·손절선 / 가치 = 가치우파에서 분할매수·가치훼손 시 매도")
        half = c1.checkbox("이미 절반 익절함(손절선을 본전으로)", key="hold_half")
        if c2.button("저장", type="primary", width="stretch", disabled=not (pick and avg > 0)):
            nh = holdings.new_holding(pick[0], pick[1], avg, qty, str(bdate), stop or None, half, memo, acct or "추세")
            if any(x["code"] == pick[0] for x in store["holdings"]):
                store["holdings"] = _merge_holdings(store["holdings"] + [nh])
                st.info(f"{pick[1]}은(는) 이미 있어서 추가 매수로 합쳤어요(수량 합산·평단 재계산).")
            else:
                store["holdings"].append(nh)
            ok, msg = _persist_holdings(store, stt)
            (st.success if ok else st.error)(msg)
            _rerun_frag()

    if not store["holdings"]:
        st.info("아직 넣은 보유 종목이 없어요. 위에서 종목과 평단가를 넣어 주세요.")
        return

    ctx = _hold_context(df)
    ctx["hold_groups"] = _hold_groups(df, store["holdings"])
    rows = []
    for h in store["holdings"]:
        m, bars, quote = _hold_metrics(h["code"], df, quotes, h["name"])
        rows.append((h, m, bars, holdings.judge(h, m, bars, ctx)))
    _render_mode(store, stt, ctx)
    _render_capital(store, rows, stt, ctx)
    _render_principles()

    # 요약
    tot_cost = sum(h["avg"] * h["qty"] for h, *_ in rows if h.get("qty"))
    tot_val = sum((_m.get("price") or 0) * h["qty"] for h, _m, *_ in rows if h.get("qty"))
    cnt = {}
    for *_, j in rows:
        cnt[j["verdict"]] = cnt.get(j["verdict"], 0) + 1
    c = st.columns(4)
    c[0].metric("보유 종목", f"{len(rows)}개")
    if tot_cost:
        c[1].metric("평가손익", f"{tot_val - tot_cost:+,.0f}원", f"{(tot_val / tot_cost - 1) * 100:+.2f}%")
    c[2].metric("손절·정리 신호", f"{sum(v for k, v in cnt.items() if k.startswith(('🛑', '📉', '⚠️')))}개")
    c[3].metric("시장", "60일선 위" if ctx["market_ok"] else ("60일선 아래" if ctx["market_ok"] is False else "-"),
                ctx["market_text"], delta_color="off")

    order = {"red": 0, "orange": 1, "purple": 2, "blue": 3, "yellow": 4, "green": 5, "gray": 6}
    dot = {"red": "🔴", "orange": "🟠", "purple": "🔥", "blue": "🔵", "yellow": "🟡", "green": "🟢", "gray": "⚪"}
    rows = sorted(rows, key=lambda x: order.get(x[3]["level"], 9))

    # 종목마다 탭: 맨 앞 '한눈에' + 종목 탭(판정 색 점 · 이름 · 수익률)
    labels = ["📊 한눈에"] + [f"{dot.get(j['level'], '⚪')} {h['name']} {(j['pnl'] or 0):+.1f}%" for h, m, b, j in rows]
    with st.container(key="hold_tabs"):
        try:
            tabs = st.tabs(labels, key="hold_tab_sel", on_change="rerun")
            lazy = True
        except TypeError:
            tabs = st.tabs(labels)
            lazy = False
    if (not lazy) or tabs[0].open:
        with tabs[0]:
            _holdings_overview(rows, dot)
    for tab, (h, m, bars, j) in zip(tabs[1:], rows):
        if (not lazy) or tab.open:       # (속도) 고른 종목 탭만 뉴스·차트를 불러와요
            with tab:
                _holding_card(h, m, bars, j, quotes, stt)

    _holdings_manager(store, stt)
    if stt["gh"]:
        st.caption("보유 종목은 GitHub 저장소의 user_holdings.json에 저장돼요. 저장소가 공개(Public)면 누구나 볼 수 있으니 "
                   "GitHub에서 저장소를 비공개(Private)로 바꿔 두세요.")


# ─────────────────────────── 👀 보유 살피기 ───────────────────────────
MAIN_TABS_LAZY = {"v": True}      # 메인 탭이 '보는 탭만 그리기'인지(아니면 잠금 화면을 두 탭에서 동시에 그리지 않게)
MON_AI_EVERY = 15                 # 자동 AI 판단 간격(분)


@st.cache_data(ttl=180, show_spinner=False, max_entries=80)
def load_news_live(code: str, name: str) -> dict:
    """살피기 탭 뉴스는 3분마다 새로 모아요(내 보유 탭은 10분)."""
    if data.MOCK:
        return holdings.mock_gather(code, name)
    return holdings.gather(code, name, data.is_kr(code))


@st.cache_resource(show_spinner=False)
def _mon_ai_store() -> dict:
    """AI 판단 결과를 서버에 잠깐 기억해요(휴대폰·PC에서 같이 보이게). {보유id: {res, err, ts, running}}"""
    return {"lock": threading.Lock(), "items": {}}


@st.cache_resource(show_spinner=False)
def _mon_pool():
    from concurrent.futures import ThreadPoolExecutor
    return ThreadPoolExecutor(max_workers=3)


def _mon_ai_run(hid: str, stt: dict, payload: str, j: dict):
    """AI 판단 한 번(뒤에서 돌 때도 씀). streamlit 함수는 부르지 않아요."""
    store = _mon_ai_store()
    if data.MOCK or not stt.get("key"):
        res, err = (holdings.mock_ai_judge(j), "") if data.MOCK else (None, "ANTHROPIC_API_KEY가 없어요.")
    else:
        res, err = holdings.ai_judge(stt["key"], stt["model"], payload)
    with store["lock"]:
        old = store["items"].get(hid) or {}
        store["items"][hid] = {"res": res or old.get("res"), "err": err, "ts": time.time(), "running": False,
                               "verdict_at_run": j.get("verdict")}


def _mon_ai_submit(hid: str, stt: dict, payload: str, j: dict):
    store = _mon_ai_store()
    with store["lock"]:
        it = store["items"].setdefault(hid, {})
        if it.get("running"):
            return
        it["running"] = True
    _mon_pool().submit(_mon_ai_run, hid, stt, payload, j)


def _vol50(bars) -> float | None:
    if bars is None or len(bars) < 21:
        return None
    v = pd.to_numeric(bars["volume"], errors="coerce").to_numpy(dtype=float)
    base = np.nanmean(v[-51:-1])
    return float(base) if base and not np.isnan(base) else None


def _eok(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v:+,.0f}억"


def _mon_alerts(h, m, j, quote, pace, ev) -> list[tuple[str, str]]:
    """눈에 띄게 보여줄 알림 (색, 문구)."""
    out = []
    price, stop, t3 = m.get("price"), j.get("stop_price"), j.get("target3r")
    if price and stop and h.get("account") != "가치":
        if price <= stop:
            out.append(("red", f"손절선 {stop:,.0f} 이탈"))
        elif (price / stop - 1) * 100 < 2:
            out.append(("orange", f"손절선까지 {(price / stop - 1) * 100:.1f}%"))
    if price and t3 and price >= t3 and not h.get("half_taken"):
        out.append(("blue", f"3R 목표 {t3:,.0f} 도달 — 절반 익절 구간"))
    chg = m.get("change")
    if chg is not None and not pd.isna(chg):
        if chg <= -5 and (pace or 0) >= 1.5:
            out.append(("red", f"오늘 {chg:+.1f}% · 거래량 페이스 {pace:.1f}배 — 물량 출회"))
        elif chg >= 5 and (pace or 0) >= 1.5:
            out.append(("purple", f"오늘 {chg:+.1f}% · 거래량 페이스 {pace:.1f}배 — 강한 수요"))
    n_today = sum(1 for x in (ev or {}).get("news", []) if holdings.news_is_today(x.get("time")))
    if n_today:
        out.append(("gray", f"오늘 뉴스 {n_today}건"))
    return out


MON_CSS = """<style>
.mn-strip{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px}
.mn-al{font-size:.86rem;padding:5px 10px;border-radius:999px;border:1px solid;font-weight:600}
.mn-wrap{overflow-x:auto;border:1px solid #E6E9EE;border-radius:12px;background:#fff}
.mn-tbl{width:100%;border-collapse:collapse;font-size:.9rem;white-space:nowrap}
.mn-tbl th{background:#F7F8FA;color:#51616C;font-weight:600;padding:8px 10px;text-align:right;border-bottom:1px solid #E6E9EE}
.mn-tbl td{padding:9px 10px;text-align:right;border-bottom:1px solid #F0F2F5;vertical-align:middle}
.mn-tbl .l{text-align:left}
.mn-tbl tr:last-child td{border-bottom:none}
.mn-code{color:#8A96A0;font-size:.78rem;margin-left:6px}
.mn-badge{display:inline-block;padding:2px 8px;border-radius:999px;border:1px solid;font-size:.8rem;font-weight:700}
.mn-sub{color:#8A96A0;font-size:.76rem;display:block}
.mn-ai{border:1.5px solid;border-radius:14px;padding:14px 16px;margin:6px 0 4px;background:#fff}
.mn-ai-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:6px}
.mn-ai-state{font-size:1.15rem;font-weight:800;padding:4px 12px;border-radius:10px}
.mn-ai-head{font-weight:700;font-size:1rem}
.mn-ai-meta{color:#8A96A0;font-size:.78rem;margin-left:auto}
.mn-ai h6{margin:10px 0 4px;font-size:.84rem;color:#51616C}
.mn-ai ul{margin:0 0 0 18px;padding:0}
.mn-ai li{margin:2px 0;font-size:.92rem}
.mn-sc{display:grid;grid-template-columns:minmax(120px,40%) 1fr;gap:4px 12px;font-size:.9rem}
.mn-sc span{color:#16212B;font-weight:600}
.mn-sc em{font-style:normal;color:#33424D}
.mn-note{background:#F7F8FA;border-radius:8px;padding:6px 10px;font-size:.86rem;margin-top:8px}
.mn-news a{display:block;padding:7px 0;border-bottom:1px solid #F0F2F5;text-decoration:none;color:#16212B}
.mn-news a span{font-size:.92rem}
.mn-news a em{display:block;font-style:normal;color:#8A96A0;font-size:.76rem}
.mn-new{background:#D6333B;color:#fff;border-radius:4px;font-size:.68rem;padding:1px 5px;margin-right:6px;font-weight:700}
.mn-fl{width:100%;border-collapse:collapse;font-size:.88rem}
.mn-fl th,.mn-fl td{padding:5px 6px;text-align:right;border-bottom:1px solid #F0F2F5}
.mn-fl th:first-child,.mn-fl td:first-child{text-align:left}
@media (max-width:640px){.mn-sc{grid-template-columns:1fr}.mn-ai-meta{margin-left:0}}
</style>"""


def _mon_ai_card(h: dict, j: dict, item: dict | None, stt: dict, payload: str):
    """AI 판단 카드 + 버튼."""
    res = (item or {}).get("res")
    running = (item or {}).get("running")
    c1, c2 = st.columns([1, 3])
    if c1.button("🤖 AI 판단" if not res else "🔄 다시 판단", key=f"mn_ai_{h['id']}", disabled=not (stt["ai"] or data.MOCK) or running,
                 type="primary", width="stretch"):
        with st.spinner("주가·수급·뉴스를 원칙에 대 보는 중이에요(20~40초)."):
            _mon_ai_run(h["id"], stt, payload, j)
        item = _mon_ai_store()["items"].get(h["id"])
        res = (item or {}).get("res")
    if not (stt["ai"] or data.MOCK):
        c2.caption("AI 판단을 쓰려면 Streamlit Secrets에 ANTHROPIC_API_KEY를 넣어 주세요(📥 리포트 탭 설정과 같아요).")
    elif running:
        c2.caption("⏳ 뒤에서 판단하는 중이에요. 다음 새로고침 때 나와요.")
    elif (item or {}).get("err"):
        c2.error(item["err"])
    if not res:
        if stt["ai"] or data.MOCK:
            c2.caption("버튼을 누르면 규칙 점검표 + 실시간 시세·수급·뉴스를 함께 넘겨 지금 상태와 대응을 판단해요.")
        return
    bg, fg = LEVEL_COLOR.get(res.get("level"), LEVEL_COLOR["yellow"])
    stale = item.get("verdict_at_run") and item["verdict_at_run"] != j.get("verdict")
    li = lambda xs: "<ul>" + "".join(f"<li>{html.escape(str(x))}</li>" for x in xs) + "</ul>" if xs else ""
    sc = "".join(f'<span>{html.escape(str(x.get("if")))}</span><em>→ {html.escape(str(x.get("then", "")))}</em>'
                 for x in res.get("scenarios") or [])
    st.markdown(
        f'<div class="mn-ai" style="border-color:{fg}"><div class="mn-ai-top">'
        f'<span class="mn-ai-state" style="background:{bg};color:{fg}">{html.escape(res.get("state", ""))}</span>'
        f'<span class="mn-ai-head">{html.escape(res.get("headline", ""))}</span>'
        f'<span class="mn-ai-meta">AI 판단 {html.escape(res.get("at", ""))} · 확신 {html.escape(str(res.get("confidence", "-")))}</span></div>'
        + (f'<div class="mn-note">⚠️ 판단 뒤에 규칙 판정이 바뀌었어요({html.escape(str(item["verdict_at_run"]))} → '
           f'{html.escape(str(j.get("verdict")))}). 다시 판단을 눌러 주세요.</div>' if stale else "")
        + (f"<h6>근거</h6>{li(res.get('reasons'))}" if res.get("reasons") else "")
        + (f"<h6>지금 원칙대로라면</h6>{li(res.get('now'))}" if res.get("now") else "")
        + (f'<h6>이렇게 되면</h6><div class="mn-sc">{sc}</div>' if sc else "")
        + (f'<div class="mn-note">📰 {html.escape(str(res.get("news")))}</div>' if res.get("news") else "")
        + (f'<div class="mn-note">⚖️ 규칙 판정과 비교: {html.escape(str(res.get("diff")))}</div>' if res.get("diff") else "")
        + "</div>", unsafe_allow_html=True)


def _mon_flow_view(m: dict):
    fl = holdings.flow_snapshot(m.get("_flow"))
    if not fl["rows"]:
        st.caption("수급 자료가 없어요(해외 종목이거나 아직 못 받음).")
        return
    trs = "".join(f'<tr><td>{k}</td><td style="color:{UP if (d or 0) > 0 else DOWN}">{_eok(d)}</td>'
                  f'<td style="color:{UP if d5 > 0 else DOWN}">{_eok(d5)}</td><td style="color:{UP if d20 > 0 else DOWN}">{_eok(d20)}</td></tr>'
                  for k, d, d5, d20 in fl["rows"])
    sk = fl["streak"]
    st.markdown(f'<table class="mn-fl"><tr><th>억원(추정)</th><th>{fl["date"]}</th><th>5일</th><th>20일</th></tr>{trs}</table>',
                unsafe_allow_html=True)
    st.caption(" · ".join(f"{k} {abs(v)}일 연속 {'순매수' if v > 0 else '순매도'}" for k, v in sk.items() if v)
               or "연속 순매수·순매도 없음")
    f = m["_flow"].tail(20)
    long = pd.DataFrame({"날짜": pd.to_datetime(f["date"]).tolist() * 2,
                         "구분": ["외국인"] * len(f) + ["기관"] * len(f),
                         "억원": f["외국인금액"].tolist() + f["기관금액"].tolist()})
    ch = (alt.Chart(long).mark_bar().encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False)),
        xOffset="구분:N",
        y=alt.Y("억원:Q", title=None),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["외국인", "기관"], range=["#6B3FC4", "#2E6B6F"]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=[alt.Tooltip("날짜:T", format="%m/%d"), "구분:N", alt.Tooltip("억원:Q", format=",.0f")])
        .properties(height=190, width="container"))
    st.altair_chart(ch)
    st.caption(f"종목 수급은 네이버가 주는 일별 값이라 장중엔 최근 거래일({fl['date']})까지예요. 10분마다 새로 받아요.")


def _mon_news_view(ev: dict):
    news = ev.get("news") or []
    if news:
        st.markdown('<div class="mn-news">' + "".join(
            f'<a href="{html.escape(x["url"])}" target="_blank"><span>'
            + ('<b class="mn-new">오늘</b>' if holdings.news_is_today(x.get("time")) else "")
            + f'{html.escape(x["title"])}</span><em>{html.escape(x["source"])} · {html.escape(x["time"])} · {x["portal"]}</em></a>'
            for x in news[:12]) + "</div>", unsafe_allow_html=True)
    else:
        st.caption("뉴스를 받지 못했어요.")
    if ev.get("research"):
        st.markdown("**증권사 리포트**")
        st.markdown('<div class="mn-news">' + "".join(
            f'<a href="{html.escape(x["url"])}" target="_blank"><span>{html.escape(x["title"])}</span>'
            f'<em>{html.escape(x["source"])} · {html.escape(x["time"])}</em></a>' for x in ev["research"][:5]) + "</div>",
            unsafe_allow_html=True)
    st.caption("뉴스는 3분마다 새로 모아요(네이버 증권 · 구글 뉴스). 제목만으로 판단하지 말고 원문을 확인하세요.")


def _mon_table(rows, store_items):
    trs = []
    for h, m, bars, quote, j, pace, ev in rows:
        cur = m.get("currency") or ("KRW" if data.is_kr(h["code"]) else "USD")
        bg, fg = LEVEL_COLOR.get(j["level"], LEVEL_COLOR["gray"])
        chg = m.get("change")
        cc = UP if (chg or 0) > 0 else (DOWN if (chg or 0) < 0 else "#51616C")
        pnl = j["pnl"] or 0
        pc = UP if pnl > 0 else (DOWN if pnl < 0 else "#51616C")
        fl = holdings.flow_snapshot(m.get("_flow"))
        fr = {k: (d, d5) for k, d, d5, _ in fl["rows"]}
        smart5 = sum(fr[k][1] for k in ("외국인", "기관") if k in fr) if fr else None
        n_today = sum(1 for x in (ev or {}).get("news", []) if holdings.news_is_today(x.get("time")))
        ai = (store_items.get(h["id"]) or {}).get("res")
        if ai:
            abg, afg = LEVEL_COLOR.get(ai.get("level"), LEVEL_COLOR["yellow"])
            ai_td = (f'<span class="mn-badge" style="background:{abg};color:{afg};border-color:{afg}">{html.escape(ai["state"])}</span>'
                     f'<span class="mn-sub">{html.escape(ai.get("at", ""))}</span>')
        else:
            ai_td = '<span class="mn-sub">아직 없음</span>'
        sd = j.get("stop_dist")
        trs.append(
            f'<tr><td class="l"><b>{html.escape(h["name"])}</b><span class="mn-code">{h["code"]}</span></td>'
            f'<td>{fmt_price(m.get("price"), cur)}<span class="mn-sub" style="color:{cc}">'
            f'{"-" if chg is None or pd.isna(chg) else f"{chg:+.2f}%"}</span></td>'
            f'<td style="color:{pc};font-weight:800">{pnl:+.2f}%<span class="mn-sub">{_rtxt(j)}</span></td>'
            f'<td style="color:{UP if sd is not None and sd < 3 else "#16212B"}">{"-" if sd is None else f"{sd:.1f}%"}'
            f'<span class="mn-sub">{fmt_price(j.get("stop_price"), cur)}</span></td>'
            f'<td style="font-weight:{800 if (pace or 0) >= 1.5 else 400}">{"-" if pace is None else f"{pace:.1f}배"}</td>'
            f'<td>{_eok(fr.get("외국인", (None,))[0])} / {_eok(fr.get("기관", (None,))[0])}<span class="mn-sub">{fl["date"] or ""}</span></td>'
            f'<td style="color:{UP if (smart5 or 0) > 0 else DOWN}">{_eok(smart5)}</td>'
            f'<td>{n_today or "-"}</td>'
            f'<td class="l"><span class="mn-badge" style="background:{bg};color:{fg};border-color:{fg}">{html.escape(j["verdict"])}</span></td>'
            f'<td class="l">{ai_td}</td></tr>')
    st.markdown(
        '<div class="mn-wrap"><table class="mn-tbl"><thead><tr><th class="l">종목</th><th>현재가·오늘</th><th>수익률·R</th>'
        '<th>손절까지</th><th>거래량 페이스</th><th>외국인 / 기관</th><th>외+기 5일</th><th>오늘 뉴스</th>'
        '<th class="l">원칙 판정(규칙)</th><th class="l">AI 판단</th></tr></thead><tbody>' + "".join(trs) + "</tbody></table></div>",
        unsafe_allow_html=True)
    st.caption("거래량 페이스 = 오늘 누적 거래량 ÷ (50일 평균 × 지금까지 지난 장 시간 비율). 1.5배↑ 굵게 · 수급은 억원 추정.")


def render_monitor_tab(df: pd.DataFrame, quotes: dict):
    """👀 보유 살피기: 보유 종목만 실시간(주가·수급·뉴스)으로 보고, 원칙 점검 + AI 판단까지."""
    stt = _rep_status()
    _sec_h("👀 보유 살피기", "보유 종목의 주가·수급·뉴스를 실시간으로 모아 내 원칙으로 점검하고, AI가 지금 상태와 대응을 판단해요")
    if MAIN_TABS_LAZY["v"]:
        if not _lock_gate(stt):
            return
    elif not st.session_state.get("hold_unlocked"):
        st.info("🔐 💼 내 보유 탭에서 비밀번호로 잠금을 먼저 풀어 주세요.")
        return
    st.markdown(MON_CSS, unsafe_allow_html=True)
    store = holdings.load()
    hs = store.get("holdings") or []
    if not hs:
        st.info("보유 종목이 없어요. 💼 내 보유 탭에서 종목과 평단가를 넣으면 여기서 실시간으로 살펴볼 수 있어요.")
        return

    c1, c2, c3 = st.columns([1.3, 1.3, 2])
    auto_ai = c1.toggle(f"장중 AI 자동 판단({MON_AI_EVERY}분마다)", key="mn_auto_ai", disabled=not (stt["ai"] or data.MOCK),
                        help="장중에 판단이 없거나 15분 지난 종목을 뒤에서 다시 판단해요. 화면은 기다리지 않아요. "
                             "API 요금이 종목 수 × 하루 약 25번 나가요.")
    run_all = c2.button("🤖 전체 AI 판단", key="mn_ai_all", disabled=not (stt["ai"] or data.MOCK), width="stretch")

    ctx = _hold_context(df)
    ctx["hold_groups"] = _hold_groups(df, hs)
    now = data.now_kst()
    rows = []
    for h in hs:
        m, bars, quote = _hold_metrics(h["code"], df, quotes, h["name"])
        j = holdings.judge(h, m, bars, ctx)
        pace = holdings.volume_pace((quote or {}).get("volume"), _vol50(bars), now, (quote or {}).get("status"))
        ev = load_news_live(h["code"], h["name"])
        rows.append((h, m, bars, quote, j, pace, ev))

    def payload_of(r):
        h, m, bars, quote, j, pace, ev = r
        return holdings.build_ai_payload(h, m, j, ctx, bars, quote, ev, pace)

    ai_store = _mon_ai_store()
    if run_all:
        from concurrent.futures import ThreadPoolExecutor
        with st.spinner(f"{len(rows)}종목을 AI가 원칙에 대 보는 중이에요(종목당 20~40초, 3개씩 동시에)."):
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda r: _mon_ai_run(r[0]["id"], stt, payload_of(r), r[4]), rows))
    market_open = any((r[3] or {}).get("status") == "OPEN" for r in rows) or data.market_status(quotes).startswith("장중")
    if auto_ai and market_open:
        for r in rows:
            it = ai_store["items"].get(r[0]["id"]) or {}
            if not it.get("running") and time.time() - (it.get("ts") or 0) > MON_AI_EVERY * 60:
                _mon_ai_submit(r[0]["id"], stt, payload_of(r), r[4])
    items = ai_store["items"]

    # 판정이 바뀐 종목은 알림
    prev = st.session_state.setdefault("mn_prev_verdict", {})
    for h, m, bars, quote, j, pace, ev in rows:
        if prev.get(h["id"]) and prev[h["id"]] != j["verdict"]:
            st.toast(f"{h['name']}: {prev[h['id']]} → {j['verdict']}", icon="🔔")
        prev[h["id"]] = j["verdict"]

    c3.markdown(f'<div class="status">{now:%m/%d %H:%M:%S} · {data.market_status(quotes)} · {ctx["market_text"]}</div>',
                unsafe_allow_html=True)
    if ctx.get("market_ok") is False:
        st.warning("지수가 60일선 아래예요 — 원칙상 쉬는 장(신규·추가 매수 보류, 보유 종목은 손절선 엄수).")

    tot_cost = sum(h["avg"] * h["qty"] for h, *_ in rows if h.get("qty"))
    tot_val = sum((r[1].get("price") or 0) * r[0]["qty"] for r in rows if r[0].get("qty"))
    day_pl = sum(((r[1].get("price") or 0) - (r[1].get("prev") or r[1].get("price") or 0)) * r[0]["qty"]
                 for r in rows if r[0].get("qty"))
    k = st.columns(4)
    k[0].metric("보유", f"{len(rows)}종목")
    if tot_cost:
        k[1].metric("평가손익", f"{tot_val - tot_cost:+,.0f}원", f"{(tot_val / tot_cost - 1) * 100:+.2f}%")
        k[2].metric("오늘 손익", f"{day_pl:+,.0f}원", f"{day_pl / max(tot_val - day_pl, 1) * 100:+.2f}%")
    k[3].metric("손절·축소 신호", f"{sum(1 for r in rows if r[4]['level'] in ('red', 'orange'))}종목")

    alerts = [(lv, f"{r[0]['name']} · {txt}") for r in rows for lv, txt in _mon_alerts(r[0], r[1], r[4], r[3], r[5], r[6])
              if lv != "gray"]
    if alerts:
        st.markdown('<div class="mn-strip">' + "".join(
            f'<span class="mn-al" style="background:{LEVEL_COLOR[lv][0]};color:{LEVEL_COLOR[lv][1]};border-color:{LEVEL_COLOR[lv][1]}">'
            f'{html.escape(t)}</span>' for lv, t in alerts) + "</div>", unsafe_allow_html=True)

    order = {"red": 0, "orange": 1, "purple": 2, "blue": 3, "yellow": 4, "green": 5, "gray": 6}
    dot = {"red": "🔴", "orange": "🟠", "purple": "🔥", "blue": "🔵", "yellow": "🟡", "green": "🟢", "gray": "⚪"}
    rows.sort(key=lambda r: order.get(r[4]["level"], 9))
    labels = ["📊 한눈에"] + [f"{dot.get(r[4]['level'], '⚪')} {r[0]['name']} "
                             f"{'' if r[1].get('change') is None or pd.isna(r[1].get('change')) else format(r[1]['change'], '+.1f') + '%'}"
                             for r in rows]
    with st.container(key="mon_tabs"):
        try:
            tabs = st.tabs(labels, key="mon_tab_sel", on_change="rerun")
            lazy = True
        except TypeError:
            tabs = st.tabs(labels)
            lazy = False
    if (not lazy) or tabs[0].open:
        with tabs[0]:
            _mon_table(rows, items)
    for tab, r in zip(tabs[1:], rows):
        if lazy and not tab.open:
            continue
        h, m, bars, quote, j, pace, ev = r
        with tab:
            cur = m.get("currency") or ("KRW" if data.is_kr(h["code"]) else "USD")
            chg = m.get("change")
            q = st.columns(5)
            q[0].metric("현재가", fmt_price(m.get("price"), cur),
                        None if chg is None or pd.isna(chg) else f"{chg:+.2f}%")
            q[1].metric("수익률", f"{(j['pnl'] or 0):+.2f}%", _rtxt(j), delta_color="off")
            q[2].metric("손절선", fmt_price(j.get("stop_price"), cur),
                        None if j.get("stop_dist") is None else f"여유 {j['stop_dist']:.1f}%", delta_color="off")
            q[3].metric("3R 목표", fmt_price(j.get("target3r"), cur), delta_color="off")
            q[4].metric("거래량 페이스", "-" if pace is None else f"{pace:.1f}배",
                        None if not quote or not quote.get("volume") else f"{quote['volume']:,.0f}주", delta_color="off")
            al = _mon_alerts(h, m, j, quote, pace, ev)
            if al:
                st.markdown('<div class="mn-strip">' + "".join(
                    f'<span class="mn-al" style="background:{LEVEL_COLOR[lv][0]};color:{LEVEL_COLOR[lv][1]};border-color:{LEVEL_COLOR[lv][1]}">'
                    f'{html.escape(t)}</span>' for lv, t in al) + "</div>", unsafe_allow_html=True)

            a1, a2 = st.columns([1.35, 1])
            with a1:
                st.markdown("**🤖 AI 판단** (원칙 + 실시간 주가·수급·뉴스)")
                _mon_ai_card(h, j, items.get(h["id"]), stt, payload_of(r))
            with a2:
                bg, fg = LEVEL_COLOR.get(j["level"], LEVEL_COLOR["gray"])
                st.markdown("**📏 규칙 판정** (앱이 계산한 원칙 점검)")
                st.markdown(f'<div class="mn-ai" style="border-color:{fg}"><div class="mn-ai-top">'
                            f'<span class="mn-ai-state" style="background:{bg};color:{fg}">{html.escape(j["verdict"])}</span></div>'
                            '<ul>' + "".join(f"<li>{html.escape(a)}</li>" for a in j.get("actions") or []) + "</ul>"
                            + "".join(f'<div class="mn-sub">{"✅" if s == "good" else "⚠️" if s == "warn" else "🛑" if s == "bad" else "ℹ️"} '
                                      f'{html.escape(it)} — {html.escape(tx)}</div>' for s, it, tx in (j.get("checks") or [])[:8])
                            + "</div>", unsafe_allow_html=True)
            b1, b2 = st.columns([1, 1.2])
            with b1:
                st.markdown("**🧭 수급** (외국인·기관)")
                _mon_flow_view(m)
            with b2:
                st.markdown("**📰 뉴스**")
                _mon_news_view(ev)
            st.markdown("**🕯️ 차트**")
            candle_chart(h["code"], cur, quote if data.is_kr(h["code"]) else None, m.get("high52"), key=f"mon_ch_{h['id']}")
    st.caption("AI 판단은 원칙 점검을 돕는 참고 의견이에요. 손절선·익절가 같은 숫자는 규칙 판정이 기준이고, 매매 전에는 HTS 시세를 확인하세요.")


# ─────────────────────────── ⭐ 내 종목(친구 계정: 관심종목 + 포트폴리오) ───────────────────────────
MY_TAB = "⭐ 내 종목"
WATCH_CSS = """<style>
.wl-who { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }
.wl-who b { font-size:1.05rem; } .wl-who span { color:#6B7280; font-size:.85rem; }
.wl-tbl { width:100%; border-collapse:collapse; font-size:.9rem; background:#FFFFFF; }
.wl-tbl th { background:#F6F5FD; color:#51616C; font-weight:700; padding:.5rem .6rem; text-align:right; white-space:nowrap; }
.wl-tbl td { padding:.5rem .6rem; border-top:1px solid #F0EFF7; text-align:right; white-space:nowrap; }
.wl-tbl th.l, .wl-tbl td.l { text-align:left; }
.wl-tbl td.l small { color:#8A979F; margin-left:.3rem; }
.wl-memo { color:#6B7280; max-width:14rem; overflow:hidden; text-overflow:ellipsis; }
.wl-wrap { overflow-x:auto; border:1px solid #ECEAF7; border-radius:12px; }
.pf-k { color:#51616C; font-size:.86rem; font-weight:700; margin-bottom:.15rem; }
.pf-v { font-size:1.7rem; font-weight:900; letter-spacing:-.03em; line-height:1.15; }
.pf-s { color:#6B7280; font-size:.8rem; margin-top:.35rem; line-height:1.4; }
.pf-s b { color:#26343E; font-weight:700; }
.pf-contrib { font-size:.88rem; color:#26343E; padding:.2rem 0; }
.pf-contrib span { margin-right:.9rem; white-space:nowrap; }
.wp-h { font-weight:900; font-size:1.05rem; margin:.1rem 0 .3rem; }
.wp-sel { background:#F6F5FD; border-radius:10px; padding:.45rem .7rem; font-size:.86rem; margin:.3rem 0; }
@media (max-width: 640px) { .wl-tbl { font-size:.8rem; } .wl-tbl th, .wl-tbl td { padding:.4rem .4rem; } .wl-hide-m { display:none; }
  .pf-v { font-size:1.35rem; } }
</style>"""
_FX_SUFFIX = {"L": "GBp", "HE": "EUR", "T": "JPY", "HK": "HKD", "TW": "TWD", "DE": "EUR", "PA": "EUR", "AS": "EUR",
              "AX": "AUD", "SW": "CHF", "SS": "CNY", "SZ": "CNY"}


def _cur_of(code: str, m: dict | None = None) -> str:
    c = (m or {}).get("currency")
    if isinstance(c, str) and c:
        return c
    if data.is_kr(code):
        return "KRW"
    return _FX_SUFFIX.get(code.rsplit(".", 1)[-1], "USD") if "." in code else "USD"


def _won(v, sign: bool = False) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v:+,.0f}원" if sign else f"{v:,.0f}원"


def _stock_picker(prefix: str) -> tuple[str, str] | None:
    """종목 검색 칸 → (코드, 이름). 보드 종목·네이버 검색·해외 티커(예: CVS, AAPL)."""
    board_labels = {s["code"]: s["name"] for s in STOCKS}
    q = st.text_input("종목 이름·코드·티커", key=f"{prefix}_q", placeholder="예: 삼성전자, 005930, CVS")
    qs = q.strip()
    cands = []
    if qs:
        ql = qs.lower()
        cands = [(c, n) for c, n in board_labels.items() if ql in n.lower() or c.lower().startswith(ql)][:10]
        if data.is_kr(qs) and len(qs) == 6 and qs not in board_labels:
            cands.insert(0, (qs, qs))
        if len(cands) < 5 and not re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,11}", qs):
            cands += [x for x in naver_search(qs) if x[0] not in {c for c, _ in cands}][:8]
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,11}", qs) and qs.upper() not in board_labels:
            cands.append((qs.upper(), qs.upper()))              # 해외 티커(야후 기준)
            cands += [x for x in naver_search(qs) if x[0] not in {c for c, _ in cands}][:5]
    return st.selectbox("찾은 종목", cands, format_func=lambda t: f"{t[1]} ({t[0]})", key=f"{prefix}_pick_{qs}",
                        placeholder="먼저 위에 검색", index=0 if cands else None)


def _watch_rows(items: list[dict], df: pd.DataFrame, quotes: dict, histories: dict | None = None) -> list[dict]:
    """관심종목·보유 종목 표에 쓸 값. 보드에 있는 종목은 보드 값을, 없으면 시세·일봉으로 바로 계산해요."""
    codes = tuple(dict.fromkeys(x["code"] for x in items))
    need_q = tuple(c for c in codes if data.is_kr(c) and c not in quotes)
    qs = dict(quotes)
    if need_q:
        try:
            qs.update(load_quotes(need_q)[0] or {})
        except Exception:
            pass
    kr = tuple(c for c in codes if data.is_kr(c))
    flows = {**(_TRENDS_BG.get("v") or {})}
    miss = tuple(c for c in kr if flows.get(c) is None or flows[c].empty)
    if miss:
        try:
            flows.update(load_trends(miss) or {})
        except Exception:
            pass
    now = data.now_kst()
    out = []
    for x in items:
        c = x["code"]
        q = qs.get(c)
        hit = df[df["code"] == c]
        bars = (histories or {}).get(c)
        if bars is None or getattr(bars, "empty", True):
            bars = load_chart(c, "일봉")                  # 보드에 없는 종목만 일봉을 따로 받아요
        if len(hit):
            m = hit.iloc[0].to_dict()
        else:
            m = data.compute_metrics(bars, q) if bars is not None and not bars.empty else {}
        pace = holdings.volume_pace((q or {}).get("volume"), _vol50(bars), now, (q or {}).get("status"))
        fl = holdings.flow_snapshot(flows.get(c)) if data.is_kr(c) else {"rows": []}
        fr = {k: d for k, d, *_ in fl["rows"]}
        out.append({**x, "m": m, "q": q, "pace": pace, "frg": fr.get("외국인"), "inst": fr.get("기관"),
                    "flow": flows.get(c), "cur": _cur_of(c, m)})
    return out


def _watch_table(rows: list[dict]):
    def col(v, fmt="{:+.2f}%"):
        if v is None or pd.isna(v):
            return '<td>-</td>'
        return f'<td style="color:{UP if v > 0 else DOWN if v < 0 else "inherit"}">{fmt.format(v)}</td>'

    trs = []
    for r in rows:
        m = r["m"]
        to_high = m.get("to_high")
        th = "-" if to_high is None or pd.isna(to_high) else ("🏔️ 신고가" if to_high <= 0.05 else f"{to_high:.1f}%")
        pace = r["pace"]
        trs.append(
            f'<tr><td class="l"><b>{html.escape(r["name"])}</b><small>{r["code"]}</small></td>'
            f'<td>{fmt_price(m.get("price"), r["cur"])}</td>' + col(m.get("change"))
            + f'<td>{th}</td>'
            + f'<td class="wl-hide-m">{"-" if pace is None else ("<b>" if pace >= 1.5 else "") + f"{pace:.1f}배" + ("</b>" if pace >= 1.5 else "")}</td>'
            + f'<td class="wl-hide-m" style="color:{UP if (r["frg"] or 0) > 0 else DOWN}">{_eok(r["frg"])}</td>'
            + f'<td class="wl-hide-m" style="color:{UP if (r["inst"] or 0) > 0 else DOWN}">{_eok(r["inst"])}</td>'
            + f'<td class="l wl-memo wl-hide-m">{html.escape(r.get("memo") or "")}</td></tr>')
    st.markdown('<div class="wl-wrap"><table class="wl-tbl"><thead><tr><th class="l">종목</th><th>현재가</th><th>등락</th>'
                '<th>52주 고점까지</th><th class="wl-hide-m">거래량 페이스</th><th class="wl-hide-m">외국인</th>'
                '<th class="wl-hide-m">기관</th><th class="l wl-hide-m">메모</th></tr></thead><tbody>'
                + "".join(trs) + "</tbody></table></div>", unsafe_allow_html=True)
    st.caption("외국인·기관 = 최근 거래일 순매수(억원, 추정) · 거래량 페이스 = 오늘 거래량 ÷ 50일 평균(지난 장 시간 비율로 맞춤), 1.5배↑ 굵게")


def _watch_detail(r: dict):
    m, q, cur = r["m"], r["q"], r["cur"]
    chg = m.get("change")
    st.markdown(f"#### {html.escape(r['name'])} <small style='color:#8A979F'>{r['code']}</small>", unsafe_allow_html=True)
    k = st.columns(5)
    k[0].metric("현재가", fmt_price(m.get("price"), cur), None if chg is None or pd.isna(chg) else f"{chg:+.2f}%")
    k[1].metric("52주 최고", fmt_price(m.get("high52"), cur),
                None if m.get("to_high") is None or pd.isna(m.get("to_high")) else f"고점까지 {m['to_high']:.1f}%",
                delta_color="off")
    k[2].metric("52주 최저", fmt_price(m.get("low52"), cur), delta_color="off")
    k[3].metric("거래량 페이스", "-" if r["pace"] is None else f"{r['pace']:.1f}배",
                None if not q or not q.get("volume") else f"{q['volume']:,.0f}주", delta_color="off")
    k[4].metric("외국인·기관(억)", f"{_eok(r['frg'])} / {_eok(r['inst'])}", delta_color="off")
    b1, b2 = st.columns([1, 1.2])
    with b1:
        st.markdown("**🧭 수급** (외국인·기관)")
        if r["flow"] is not None and not r["flow"].empty:
            _mon_flow_view({"_flow": r["flow"]})
        else:
            st.caption("수급 자료가 없어요(해외 종목이거나 아직 못 받음).")
    with b2:
        st.markdown("**📰 뉴스**")
        _mon_news_view(load_news_live(r["code"], r["name"]))
    st.markdown("**🕯️ 차트**")
    candle_chart(r["code"], cur, q if data.is_kr(r["code"]) else None, m.get("high52"), key=f"my_ch_{r['code']}")


# ── 오른쪽: 관심종목 패널 ──
def _my_watch_panel(uid: str, df, quotes, histories):
    db = _friends_db()
    gs = friends.groups(db["data"], uid)
    st.markdown('<div class="wp-h">⭐ 관심종목</div>', unsafe_allow_html=True)
    if "_my_g_next" in st.session_state:
        st.session_state["my_g"] = st.session_state.pop("_my_g_next")
    gi = st.segmented_control("그룹", list(range(len(gs))), key="my_g", label_visibility="collapsed",
                              format_func=lambda i: f"{gs[i]['name']} {len(gs[i]['items'])}" if i < len(gs) else "")
    gi = gi if isinstance(gi, int) and gi < len(gs) else 0
    g = gs[gi]

    c1, c2, c3 = st.columns([1.6, 1, 1])
    sort = c1.selectbox("정렬", ["사용자 정렬", "등락률 높은 순", "등락률 낮은 순", "RS 높은 순", "이름순"], key="my_sort",
                        label_visibility="collapsed")
    with c2.popover("＋ 종목", width="stretch"):
        pick = _stock_picker("my_add")
        to = st.selectbox("담을 그룹", list(range(len(gs))), index=gi, format_func=lambda i: gs[i]["name"], key="my_add_g")
        memo = st.text_input("메모(선택)", key="my_add_memo", max_chars=60)
        if st.button("담기", type="primary", width="stretch", disabled=not pick, key="my_add_go"):
            err = friends.add_item(db["data"], uid, to, pick[0], pick[1], memo)
            if err:
                st.warning(err)
            else:
                ok, msg = _friends_save()
                st.session_state["my_sel"] = (to, pick[0])
                st.session_state["_my_view_next"] = "관심종목"
                if not ok:
                    st.error(msg)
                st.rerun()
    with c3.popover("그룹", width="stretch"):
        ng = st.text_input("새 그룹 이름", key="my_ng", max_chars=14, placeholder="예: 헬스케어")
        if st.button("＋ 그룹 만들기", key="my_ng_go", width="stretch"):
            err = friends.add_group(db["data"], uid, ng)
            if err:
                st.warning(err)
            else:
                _friends_save()
                st.session_state["_my_g_next"] = len(friends.groups(db["data"], uid)) - 1
                st.rerun()
        st.divider()
        rn = st.text_input(f"'{g['name']}' 이름 바꾸기", key=f"my_rn_{gi}", max_chars=14)
        if st.button("이름 바꾸기", key=f"my_rn_go_{gi}", width="stretch", disabled=not rn.strip()):
            err = friends.rename_group(db["data"], uid, gi, rn)
            (st.warning(err) if err else (_friends_save(), st.rerun()))
        if len(gs) > 1 and st.button(f"🗑️ '{g['name']}' 그룹 지우기(종목은 첫 그룹으로)", key=f"my_gdel_{gi}", width="stretch"):
            friends.delete_group(db["data"], uid, gi)
            _friends_save()
            st.session_state.pop("my_g", None)
            st.rerun()

    items = list(g["items"])
    if not items:
        st.caption("이 그룹은 비어 있어요. ＋ 종목으로 담아 보세요.")
        return gi, g, []
    rows = _watch_rows(items, df, quotes, histories)
    tb = pd.DataFrame([{"종목명": f"{r['name']} · {r['code']}", "현재가": r["m"].get("price"),
                        "등락률": r["m"].get("change"), "RS": r["m"].get("rs"), "_c": r["code"]} for r in rows])
    for k in ("현재가", "등락률", "RS"):
        tb[k] = pd.to_numeric(tb[k], errors="coerce")
    if sort != "사용자 정렬":
        col, asc = {"등락률 높은 순": ("등락률", False), "등락률 낮은 순": ("등락률", True), "RS 높은 순": ("RS", False),
                    "이름순": ("종목명", True)}[sort]
        tb = tb.sort_values(col, ascending=asc, na_position="last")
    tb = tb.reset_index(drop=True)
    sty = tb.drop(columns="_c").style.format({"현재가": lambda v: "-" if pd.isna(v) else (f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"),
                                               "등락률": lambda v: "-" if pd.isna(v) else f"{v:+.2f}%",
                                               "RS": lambda v: "-" if pd.isna(v) else f"{v:.0f}"}) \
        .map(lambda v: "" if pd.isna(v) else f"color:{UP if v > 0 else DOWN if v < 0 else '#26343E'}", subset=["등락률"]) \
        .map(lambda v: "" if pd.isna(v) else ("color:#D6333B;font-weight:700" if v >= 80 else ""), subset=["RS"])
    ev = st.dataframe(sty, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key=f"my_tbl_{gi}_{sort}", height=min(38 * (len(tb) + 1) + 4, 560),
                      column_config={"종목명": st.column_config.TextColumn(width="medium")})
    sel_rows = (getattr(ev, "selection", None) or {}).get("rows") if ev is not None else None
    if sel_rows:
        code = tb.iloc[sel_rows[0]]["_c"]
        if st.session_state.get("my_sel") != (gi, code):
            st.session_state["my_sel"] = (gi, code)
            st.session_state["_my_view_next"] = "관심종목"
            st.rerun()
    st.caption("종목을 누르면 왼쪽에 차트·수급·뉴스가 떠요 · RS 80↑ 빨강")

    sel = st.session_state.get("my_sel")
    if sel and sel[0] == gi and any(x["code"] == sel[1] for x in items):
        it = next(x for x in items if x["code"] == sel[1])
        st.markdown(f'<div class="wp-sel">선택: <b>{html.escape(it["name"])}</b></div>', unsafe_allow_html=True)
        with st.popover("··· 이 종목 편집", width="stretch"):
            memo = st.text_input("메모", value=it.get("memo") or "", key=f"my_memo_{gi}_{it['code']}", max_chars=60)
            if st.button("메모 저장", key=f"my_memo_go_{gi}_{it['code']}", width="stretch"):
                friends.set_memo(db["data"], uid, gi, it["code"], memo)
                _friends_save()
                st.rerun()
            if len(gs) > 1:
                to = st.selectbox("다른 그룹으로 옮기기", [i for i in range(len(gs)) if i != gi],
                                  format_func=lambda i: gs[i]["name"], key=f"my_mv_{gi}_{it['code']}")
                if st.button("옮기기", key=f"my_mv_go_{gi}_{it['code']}", width="stretch"):
                    friends.move_item(db["data"], uid, gi, it["code"], to)
                    _friends_save()
                    st.session_state["my_sel"] = (to, it["code"])
                    st.rerun()
            a, b = st.columns(2)
            if a.button("⬆️ 위로", key=f"my_up_{gi}_{it['code']}", width="stretch"):
                friends.reorder_item(db["data"], uid, gi, it["code"], -1)
                _friends_save()
                st.rerun()
            if b.button("⬇️ 아래로", key=f"my_dn_{gi}_{it['code']}", width="stretch"):
                friends.reorder_item(db["data"], uid, gi, it["code"], +1)
                _friends_save()
                st.rerun()
            if st.button("🗑️ 관심종목에서 빼기", key=f"my_rm_{gi}_{it['code']}", width="stretch"):
                friends.remove_item(db["data"], uid, gi, it["code"])
                _friends_save()
                st.session_state.pop("my_sel", None)
                st.rerun()
    return gi, g, rows


# ── 왼쪽: 포트폴리오 ──
def _pf_history(u: dict, fx: dict) -> pd.DataFrame:
    """매매·입출금 기록 → 날짜별 주식 평가액·예수금·투자원금. 예수금이 모자라면 그만큼 입금한 걸로 봐요."""
    trades, cash = u.get("trades") or [], u.get("cash") or []
    dates = [t["date"] for t in trades] + [c["date"] for c in cash]
    if not dates:
        return pd.DataFrame()
    end = pd.Timestamp(data.now_kst().date())
    ev: dict = {}
    for t in trades:
        ev.setdefault(pd.Timestamp(t["date"]), []).append(("t", t))
    for c in cash:
        ev.setdefault(pd.Timestamp(c["date"]), []).append(("c", c))
    idx = pd.bdate_range(min(ev), end).union(pd.DatetimeIndex(list(ev))).sort_values()
    idx = idx[idx <= end] if len(idx[idx <= end]) else idx
    codes = sorted({t["code"] for t in trades})
    closes = {}
    for c in codes:
        b = load_chart(c, "일봉")
        if b is not None and not b.empty:
            s = pd.Series(pd.to_numeric(b["close"], errors="coerce").to_numpy(),
                          index=pd.to_datetime(b["date"]).dt.normalize())
            s = s[~s.index.duplicated(keep="last")]
            closes[c] = s.reindex(s.index.union(idx)).ffill().reindex(idx)
        else:
            closes[c] = pd.Series(np.nan, index=idx)
    curs = {t["code"]: t.get("cur", "KRW") for t in trades}
    qty = {c: 0.0 for c in codes}
    last_px: dict = {}
    cashbal = principal = 0.0
    out = []
    for d in idx:
        for kind, x in ev.get(d, []):
            if kind == "c":
                cashbal += x["amount"]
                principal += x["amount"]
            else:
                rate = fx.get(x.get("cur", "KRW"), 1.0) or 1.0
                amt, fee = x["qty"] * x["price"] * rate, (x.get("fee") or 0) * rate
                if x["side"] == "buy":
                    cashbal -= amt + fee
                    qty[x["code"]] += x["qty"]
                else:
                    cashbal += amt - fee
                    qty[x["code"]] = max(0.0, qty[x["code"]] - x["qty"])
                last_px[x["code"]] = x["price"]
        if cashbal < 0:
            principal += -cashbal
            cashbal = 0.0
        stock = 0.0
        for c in codes:
            if qty[c]:
                px = closes[c].get(d)
                px = last_px.get(c) if px is None or pd.isna(px) else px
                stock += qty[c] * (px or 0) * (fx.get(curs[c], 1.0) or 1.0)
        out.append({"date": d, "stock": stock, "cash": cashbal, "principal": principal})
    h = pd.DataFrame(out)
    h["total"] = h["stock"] + h["cash"]
    h["pnl"] = h["total"] - h["principal"]
    h["ret"] = np.where(h["principal"] > 0, h["pnl"] / h["principal"].where(h["principal"] > 0, 1) * 100, 0.0)
    return h


def _pf_trade_form(uid: str, df, quotes, held: dict):
    db = _friends_db()
    side = st.segmented_control("구분", ["매수", "매도"], default="매수", required=True, key="pf_side")
    if side == "매도" and held:
        pick = st.selectbox("판 종목", list(held), format_func=lambda c: f"{held[c]['name']} ({c}) · {held[c]['qty']:,.0f}주",
                            key="pf_sell_pick")
        pick = (pick, held[pick]["name"]) if pick else None
    else:
        pick = _stock_picker("pf_buy")
    m = {}
    if pick:
        hit = df[df["code"] == pick[0]]
        m = hit.iloc[0].to_dict() if len(hit) else {}
        if not m.get("price"):
            q = quotes.get(pick[0])
            m = {"price": (q or {}).get("price")} if q else m
    c1, c2 = st.columns(2)
    d = c1.date_input("날짜", value=data.now_kst().date(), key="pf_date")
    qty = c2.number_input("수량", min_value=0.0, step=1.0, format="%.0f", key="pf_qty")
    c1, c2 = st.columns(2)
    px0 = float(m.get("price") or 0.0) if pick else 0.0
    px = c1.number_input("가격(1주)", min_value=0.0, value=px0, step=1.0 if (pick and data.is_kr(pick[0])) else 0.01,
                         format="%.0f" if (pick and data.is_kr(pick[0])) else "%.2f", key=f"pf_px_{pick[0] if pick else ''}")
    fee = c2.number_input("수수료·세금(선택)", min_value=0.0, step=1.0, key="pf_fee")
    memo = st.text_input("메모(선택)", key="pf_memo", max_chars=60)
    if pick:
        cur = _cur_of(pick[0], m)
        st.caption(f"{pick[1]} · 통화 {cur} · 금액 {fmt_price(qty * px, cur)} {UNIT.get(cur, cur)}")
    if st.button("기록하기", type="primary", width="stretch", disabled=not (pick and qty > 0 and px > 0), key="pf_go"):
        err = friends.add_trade(db["data"], uid, str(d), pick[0], pick[1], "buy" if side == "매수" else "sell",
                                qty, px, fee, _cur_of(pick[0], m), memo)
        if err:
            st.warning(err)
        else:
            ok, msg = _friends_save()
            if not ok:
                st.error(msg)
            for k in ("pf_qty", "pf_fee", "pf_memo"):
                st.session_state.pop(k, None)
            st.rerun()


def _pf_cash_form(uid: str):
    db = _friends_db()
    kind = st.segmented_control("구분", ["입금", "출금"], default="입금", required=True, key="pc_kind")
    c1, c2 = st.columns(2)
    d = c1.date_input("날짜", value=data.now_kst().date(), key="pc_date")
    amt = c2.number_input("금액(원)", min_value=0.0, step=10000.0, format="%.0f", key="pc_amt")
    memo = st.text_input("메모(선택)", key="pc_memo", max_chars=60)
    if st.button("기록하기", type="primary", width="stretch", disabled=not amt, key="pc_go"):
        err = friends.add_cash(db["data"], uid, str(d), amt if kind == "입금" else -amt, memo)
        if err:
            st.warning(err)
        else:
            ok, msg = _friends_save()
            if not ok:
                st.error(msg)
            for k in ("pc_amt", "pc_memo"):
                st.session_state.pop(k, None)
            st.rerun()


def _my_portfolio(uid: str, u: dict, df, quotes, histories):
    db = _friends_db()
    fx = load_fx() or {}
    pos = friends.positions(u.get("trades") or [])
    held = {c: p for c, p in pos.items() if p["qty"] > 0}
    rows = _watch_rows([{"code": c, "name": p["name"]} for c, p in held.items()], df, quotes, histories) if held else []
    hist = _pf_history(u, fx)
    cash_now = float(hist["cash"].iloc[-1]) if len(hist) else 0.0
    principal = float(hist["principal"].iloc[-1]) if len(hist) else 0.0

    lines = []
    for r in rows:
        p = held[r["code"]]
        cur = p.get("cur") or r["cur"]
        rate = fx.get(cur, 1.0) or 1.0
        price = r["m"].get("price")
        prev = r["m"].get("prev")
        price = p["avg"] if price is None or pd.isna(price) else float(price)
        prev = price if prev is None or pd.isna(prev) else float(prev)
        val = p["qty"] * price * rate
        cost = p["qty"] * p["avg"] * rate
        lines.append({"code": r["code"], "name": p["name"], "qty": p["qty"], "avg": p["avg"], "price": price, "cur": cur,
                      "val": val, "cost": cost, "pnl": val - cost, "ret": (val / cost - 1) * 100 if cost else 0.0,
                      "today": p["qty"] * (price - prev) * rate, "chg": r["m"].get("change")})
    stock_now = sum(x["val"] for x in lines)
    total = stock_now + cash_now
    today = sum(x["today"] for x in lines)
    pnl_all = total - principal

    top = st.columns([1.15, 1.1, 1.1, 0.9])
    unit = top[0].segmented_control("통화", ["원화", "달러"], default="원화", required=True, key="pf_unit",
                                    label_visibility="collapsed")
    usd = fx.get("USD") or 1380.0

    def money(v, sign=False):
        if unit == "달러":
            return "-" if v is None else (f"{v / usd:+,.2f}$" if sign else f"{v / usd:,.2f}$")
        return _won(v, sign)

    top[0].markdown(f'<div class="pf-k">총자산</div><div class="pf-v">{money(total)}</div>'
                    f'<div class="pf-s">주식 <b>{money(stock_now)}</b> · 예수금 <b>{money(cash_now)}</b></div>',
                    unsafe_allow_html=True)
    per = top[1].segmented_control("기간", ["오늘", "주", "월", "올해"], default="오늘", required=True, key="pf_per",
                                   label_visibility="collapsed")
    if per == "오늘" or hist.empty:
        chg_v = today
    else:
        start = {"주": pd.Timestamp(data.now_kst().date()) - pd.Timedelta(days=7),
                 "월": pd.Timestamp(data.now_kst().date()) - pd.Timedelta(days=30),
                 "올해": pd.Timestamp(data.now_kst().year, 1, 1)}[per]
        base = hist[hist["date"] <= start]
        base_pnl = float(base["pnl"].iloc[-1]) if len(base) else 0.0
        chg_v = pnl_all - base_pnl
    kospi = (load_indexes().get(data.MARKETS.get("코스피", ""), (None, None))[0]
             if hasattr(data, "MARKETS") else None)
    kchg = None
    if kospi is not None and len(kospi) > 1:
        cl = pd.to_numeric(kospi["close"], errors="coerce").dropna()
        kchg = (cl.iloc[-1] / cl.iloc[-2] - 1) * 100 if len(cl) > 1 else None
    col = UP if chg_v > 0 else DOWN if chg_v < 0 else "#26343E"
    top[1].markdown(f'<div class="pf-k">{per} 손익</div><div class="pf-v" style="color:{col}">{money(chg_v, True)}</div>'
                    f'<div class="pf-s">코스피 <b>{"-" if kchg is None else f"{kchg:+.2f}%"}</b></div>', unsafe_allow_html=True)
    col = UP if pnl_all > 0 else DOWN if pnl_all < 0 else "#26343E"
    top[2].markdown(f'<div class="pf-k">평가손익</div><div class="pf-v" style="color:{col}">{money(pnl_all, True)}</div>'
                    f'<div class="pf-s">투자원금 <b>{money(principal)}</b>'
                    + (f' · 수익률 <b style="color:{col}">{pnl_all / principal * 100:+.2f}%</b>' if principal else "")
                    + "</div>", unsafe_allow_html=True)
    with top[3]:
        with st.popover("＋ 매매 기록", width="stretch"):
            _pf_trade_form(uid, df, quotes, held)
        with st.popover("＋ 입출금 기록", width="stretch"):
            _pf_cash_form(uid)

    movers = sorted([x for x in lines if abs(x["today"]) >= 1], key=lambda x: -abs(x["today"]))[:6]
    st.markdown('<div class="pf-contrib"><b>오늘 기여</b>&nbsp;&nbsp;' + (
        "".join(f'<span>{html.escape(x["name"])} <b style="color:{UP if x["today"] > 0 else DOWN}">{money(x["today"], True)}</b></span>'
                for x in movers) if movers else "오늘 손익 변동이 있는 종목이 없어요.") + "</div>", unsafe_allow_html=True)

    if not (u.get("trades") or u.get("cash")):
        st.info("아직 기록이 없어요. 오른쪽 위 ＋ 매매 기록으로 산 종목(날짜·수량·가격)을 넣으면 총자산·손익·자산 추이가 계산돼요. "
                "예수금까지 보려면 ＋ 입출금 기록에 넣은 돈을 적어 주세요.")
        return

    a, b = st.columns([1.7, 1])
    with a:
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        c1.markdown("**자산 추이**")
        mode = c2.segmented_control("보기", ["금액", "수익률"], default="금액", required=True, key="pf_mode",
                                    label_visibility="collapsed")
        rng = c3.selectbox("기간", ["1개월", "3개월", "6개월", "1년", "전체"], index=4, key="pf_rng", label_visibility="collapsed")
        h = hist.copy()
        if rng != "전체":
            days = {"1개월": 31, "3개월": 92, "6개월": 183, "1년": 366}[rng]
            h = h[h["date"] >= pd.Timestamp(data.now_kst().date()) - pd.Timedelta(days=days)]
        if len(h):
            h = h.assign(**{"날짜": h["date"]})
            if mode == "금액":
                long = pd.concat([h.assign(구분="총자산", 값=h["total"]), h.assign(구분="투자원금", 값=h["principal"])])
                fmt, ttl = ",.0f", "원"
            else:
                long = h.assign(구분="수익률", 값=h["ret"])
                fmt, ttl = "+.2f", "%"
            ch = (alt.Chart(long).mark_line(interpolate="monotone").encode(
                x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m.%d", labelAngle=0, grid=False)),
                y=alt.Y("값:Q", title=ttl, scale=alt.Scale(zero=False), axis=alt.Axis(format="~s" if mode == "금액" else "+.1f")),
                color=alt.Color("구분:N", scale=alt.Scale(domain=["총자산", "투자원금", "수익률"],
                                                          range=["#5B47F5", "#B9B4D8", "#5B47F5"]),
                                legend=alt.Legend(orient="top", title=None)),
                strokeDash=alt.condition(alt.datum.구분 == "투자원금", alt.value([4, 3]), alt.value([1, 0])),
                tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("값:Q", format=fmt)])
                .properties(height=260, width="container"))
            st.altair_chart(ch)
            st.caption(f"평가 변동 {money(float(h['pnl'].iloc[-1] - h['pnl'].iloc[0]), True)} · 해외 종목은 지금 환율로 환산해요.")
    with b:
        st.markdown("**분산**")
        parts = [(x["name"], x["val"]) for x in lines if x["val"] > 0] + ([("예수금", cash_now)] if cash_now > 0 else [])
        tot = sum(v for _, v in parts)
        if tot > 0:
            pdf = pd.DataFrame(parts, columns=["종목", "금액"]).sort_values("금액", ascending=False)
            pdf["비중"] = pdf["금액"] / tot * 100
            st.caption(f"최대 비중 {pdf.iloc[0]['종목']} {pdf.iloc[0]['비중']:.1f}%")
            pie = (alt.Chart(pdf).mark_arc(innerRadius=55, outerRadius=100).encode(
                theta="금액:Q", color=alt.Color("종목:N", sort=list(pdf["종목"]), legend=alt.Legend(orient="bottom", title=None, columns=2)),
                tooltip=["종목:N", alt.Tooltip("금액:Q", format=",.0f"), alt.Tooltip("비중:Q", format=".1f")])
                .properties(height=300, width="container"))
            st.altair_chart(pie)
        else:
            st.caption("구성 데이터가 없어요.")

    t1, t2, t3 = st.tabs([f"보유종목 {len(lines)}", "성과", "거래내역"])
    with t1:
        if lines:
            tb = pd.DataFrame([{"종목": f"{x['name']} · {x['code']}", "수량": x["qty"], "평단": x["avg"], "현재가": x["price"],
                                "등락률": x["chg"], "평가금액(원)": x["val"], "평가손익(원)": x["pnl"], "수익률": x["ret"],
                                "비중": x["val"] / total * 100 if total else 0} for x in sorted(lines, key=lambda x: -x["val"])])
            sty = tb.style.format({"수량": "{:,.0f}", "평단": "{:,.2f}", "현재가": "{:,.2f}", "등락률": lambda v: "-" if pd.isna(v) else f"{v:+.2f}%",
                                   "평가금액(원)": "{:,.0f}", "평가손익(원)": "{:+,.0f}", "수익률": "{:+.2f}%", "비중": "{:.1f}%"}) \
                .map(lambda v: "" if pd.isna(v) else f"color:{UP if v > 0 else DOWN if v < 0 else '#26343E'}",
                     subset=["등락률", "평가손익(원)", "수익률"])
            st.dataframe(sty, hide_index=True, width="stretch")
        else:
            st.caption("지금 가진 종목이 없어요.")
    with t2:
        perf = [{"종목": f"{p['name']} · {c}", "실현손익": p["realized"] * (fx.get(p.get("cur", "KRW"), 1.0) or 1.0),
                 "평가손익": next((x["pnl"] for x in lines if x["code"] == c), 0.0)} for c, p in pos.items()]
        if perf:
            pf = pd.DataFrame(perf)
            pf["합계"] = pf["실현손익"] + pf["평가손익"]
            pf = pf.sort_values("합계", ascending=False)
            k = st.columns(3)
            k[0].metric("실현손익", _won(pf["실현손익"].sum(), True))
            k[1].metric("평가손익(보유 중)", _won(pf["평가손익"].sum(), True))
            k[2].metric("합계", _won(pf["합계"].sum(), True))
            st.dataframe(pf.style.format({"실현손익": "{:+,.0f}", "평가손익": "{:+,.0f}", "합계": "{:+,.0f}"})
                         .map(lambda v: f"color:{UP if v > 0 else DOWN if v < 0 else '#26343E'}", subset=["실현손익", "평가손익", "합계"]),
                         hide_index=True, width="stretch")
            st.caption("실현손익 = 판 가격 − 평단(이동평균) − 수수료 · 해외 종목은 지금 환율로 환산")
    with t3:
        recs = [{"id": t["id"], "종류": "trades", "날짜": t["date"], "구분": "매수" if t["side"] == "buy" else "매도",
                 "종목·메모": f"{t['name']} ({t['code']})" + (f" · {t['memo']}" if t.get("memo") else ""),
                 "수량": f"{t['qty']:,.0f}", "가격": fmt_price(t["price"], t.get("cur", "KRW")),
                 "금액": f"{fmt_price(t['qty'] * t['price'], t.get('cur', 'KRW'))} {UNIT.get(t.get('cur', 'KRW'), '')}"}
                for t in u.get("trades") or []]
        recs += [{"id": c["id"], "종류": "cash", "날짜": c["date"], "구분": "입금" if c["amount"] > 0 else "출금",
                  "종목·메모": c.get("memo") or "", "수량": "", "가격": "", "금액": _won(abs(c["amount"]))} for c in u.get("cash") or []]
        recs.sort(key=lambda r: r["날짜"], reverse=True)
        if recs:
            st.dataframe(pd.DataFrame(recs).drop(columns=["id", "종류"]), hide_index=True, width="stretch")
            c1, c2 = st.columns([3, 1])
            pickr = c1.selectbox("지울 기록", list(range(len(recs))), index=None, placeholder="잘못 넣은 기록을 고르세요",
                                 format_func=lambda i: f"{recs[i]['날짜']} {recs[i]['구분']} {recs[i]['종목·메모']} {recs[i]['금액']}",
                                 key="pf_del_pick", label_visibility="collapsed")
            if c2.button("🗑️ 지우기", key="pf_del_go", width="stretch", disabled=pickr is None):
                friends.delete_record(db["data"], uid, recs[pickr]["종류"], recs[pickr]["id"])
                ok, msg = _friends_save()
                if not ok:
                    st.error(msg)
                st.session_state.pop("pf_del_pick", None)
                st.rerun()
        else:
            st.caption("기록이 없어요.")


def _friends_admin():
    """앱 주인(💼 내 보유 잠금을 푼 사람)만: 친구 계정 만들기·임시 비밀번호·지우기."""
    db = _friends_db()
    if db["data"] is None:
        return
    users = db["data"].get("users") or {}
    with st.expander(f"🛠️ 친구 계정 관리 (앱 주인만 보여요 · {len(users)}명)"):
        st.caption("친구는 ⭐ 내 종목 탭에서 비밀번호를 만들어 직접 들어와요. 여기서는 대신 만들어 주거나 비밀번호를 다시 정해 줄 수 있어요.")
        with st.form("wl_admin_new", clear_on_submit=True, border=False):
            c1, c2 = st.columns([2, 1])
            nick = c1.text_input("새 계정 이름")
            mk = c2.form_submit_button("계정 만들기", width="stretch")
        if mk:
            for _ in range(5):
                pw = friends.temp_password()
                uid, err = friends.sign_up(db["data"], pw, nick)
                if err != "taken":
                    break
            if err:
                st.error(err)
            else:
                ok, msg = _friends_save()
                st.success(f"만들었어요 → **{nick or '이름 없음'}** 임시 비밀번호 **{pw}** (지금만 보여요, 친구에게 전해 주고 "
                           "들어가서 바꾸라고 하세요)" + ("" if ok else f" · 주의: {msg}"))
        if users:
            for u in users.values():
                friends.migrate(u)
            keys = list(users)
            rows = [{"이름": users[k].get("name") or "(이름 없음)", "관심종목": friends.n_items(users[k]),
                     "매매 기록": len(users[k].get("trades") or []), "만든 날": users[k].get("created"),
                     "비밀번호로 들어가기": "가능" if users[k].get("pid") else "임시 비밀번호 필요"} for k in keys]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            c1, c2, c3 = st.columns([1.4, 1, 1])
            who = c1.selectbox("계정 고르기", keys, key="wl_admin_who",
                               format_func=lambda k: f"{users[k].get('name') or '(이름 없음)'} · {users[k].get('created', '')}")
            if c2.button("🔁 임시 비밀번호", key="wl_admin_reset", width="stretch"):
                for _ in range(5):
                    pw = friends.temp_password()
                    if not friends.change_pw(db["data"], who, pw):
                        break
                ok, msg = _friends_save()
                st.success(f"{users[who].get('name') or '이 계정'}의 임시 비밀번호: **{pw}** (지금만 보여요)" + ("" if ok else f" · 주의: {msg}"))
            if c3.button("🗑️ 계정 지우기", key="wl_admin_del", width="stretch"):
                st.session_state["wl_admin_confirm"] = who
            if st.session_state.get("wl_admin_confirm") == who:
                st.warning(f"{users[who].get('name') or '이 계정'}의 관심종목·포트폴리오를 모두 지울까요? 되돌릴 수 없어요.")
                if st.button("네, 지울게요", key="wl_admin_del_ok", type="primary"):
                    friends.delete_user(db["data"], who)
                    if st.session_state.get("friend_id") == who:
                        st.session_state.pop("friend_id", None)
                    st.session_state.pop("wl_admin_confirm", None)
                    ok, msg = _friends_save()
                    (st.success if ok else st.error)("지웠어요." if ok else msg)
                    st.rerun()


def render_watch_tab(df: pd.DataFrame, quotes: dict, histories: dict | None = None):
    """⭐ 내 종목: 자기 비밀번호로 들어와서, 오른쪽엔 그룹별 관심종목, 왼쪽엔 포트폴리오(또는 고른 종목 자세히)."""
    st.markdown(MON_CSS + WATCH_CSS, unsafe_allow_html=True)
    db = _friends_db()
    owner = bool(st.session_state.get("hold_unlocked"))
    if db["data"] is None and db["err"] == "nokey":
        _sec_h("⭐ 내 종목", "")
        st.info("친구 계정 기능이 아직 꺼져 있어요. 앱 주인이 Streamlit Secrets에 GITHUB_TOKEN을 넣으면 열려요.")
        return
    if not _secret("GITHUB_TOKEN") and owner:
        st.warning("지금은 계정·관심종목이 이 서버에만 저장돼요. Streamlit이 앱을 다시 켜면(재부팅·잠들었다 깨기) 사라질 수 있어요. "
                   "Secrets에 GITHUB_TOKEN을 넣으면 그때부터 GitHub에 암호화해서 영구 저장하고, 지금 계정도 그대로 옮겨져요.")
    uid = st.session_state.get("friend_id")
    u = friends.user(db["data"], uid) if (uid and db["data"] is not None) else None
    if not u:
        st.session_state.pop("friend_id", None)
        _sec_h("⭐ 내 종목", "내 비밀번호를 치면 내 관심종목과 포트폴리오가 나와요")
        c = st.columns([1, 1.3, 1])[1]
        with c:
            _friend_login_form("wl")
        if owner:
            _friends_admin()
        return

    c1, c2, c3 = st.columns([3, 1, 0.8])
    if "_my_view_next" in st.session_state:
        st.session_state["my_view"] = st.session_state.pop("_my_view_next")
    if "my_view" not in st.session_state:
        st.session_state["my_view"] = "포트폴리오" if (u.get("trades") or u.get("cash")) else "관심종목"
    view = c1.segmented_control("화면", ["관심종목", "포트폴리오"], key="my_view", label_visibility="collapsed")
    view = view or "관심종목"
    with c2.popover(f"👋 {u.get('name') or '내 계정'}", width="stretch"):
        nm = st.text_input("이름", value=u.get("name") or "", key="my_name", max_chars=20)
        if st.button("이름 저장", key="my_name_go", width="stretch"):
            u["name"] = nm.strip()[:20]
            _friends_save()
            st.rerun()
        st.divider()
        with st.form("wl_pw", clear_on_submit=True, border=False):
            old = st.text_input("지금 비밀번호", type="password")
            new = st.text_input(f"새 비밀번호({friends.MIN_PW}자 이상)", type="password")
            new2 = st.text_input("새 비밀번호 한 번 더", type="password")
            go = st.form_submit_button("비밀번호 바꾸기", width="stretch")
        if go:
            if friends.login(db["data"], old) != uid:
                st.error("지금 비밀번호가 달라요.")
            elif new != new2:
                st.error("새 비밀번호 두 개가 달라요.")
            else:
                err = friends.change_pw(db["data"], uid, new)
                if err:
                    st.error(err)
                else:
                    ok, msg = _friends_save()
                    (st.success if ok else st.error)("바꿨어요. 다음부터 새 비밀번호로 들어와요." if ok else msg)
    if c3.button("나가기", key="wl_logout", width="stretch"):
        _sec_log("친구 나가기", _client_ip())
        for k in ("friend_id", "my_sel", "my_view", "my_g"):
            st.session_state.pop(k, None)
        st.rerun()
    if owner:
        _friends_admin()

    main, side = st.columns([2.6, 1], gap="medium")
    with side:
        with st.container(border=True, key="my_side"):
            gi, g, rows = _my_watch_panel(uid, df, quotes, histories)
    with main:
        if view == "포트폴리오":
            _my_portfolio(uid, u, df, quotes, histories)
        else:
            if not rows:
                st.info("오른쪽 ⭐ 관심종목의 ＋ 종목으로 종목을 담으면 여기서 주가·수급·차트·뉴스를 볼 수 있어요.")
            else:
                _watch_table(rows)
                sel = st.session_state.get("my_sel")
                r = next((r for r in rows if sel and sel[0] == gi and r["code"] == sel[1]), rows[0])
                _watch_detail(r)
    st.caption("시세는 실시간(해외는 15분 지연), 수급은 최근 거래일 기준 추정치예요. 매매 전에는 HTS 시세를 확인하세요.")


def now_date() -> str:
    return data.now_kst().strftime("%y.%m.%d")


def _render_saved_reports(stt: dict, editable: bool):
    store = reports.load_local()
    reps = list(reversed(store.get("reports", [])))
    n_learn, n_theme = len(store.get("learned", {})), len(store.get("themes", {}))
    st.markdown(f'<div class="sec-h"><b>📚 넣은 리포트</b><span>{len(reps)}개 · 배운 종목 분류 {n_learn}개 · 테마 {n_theme}개</span></div>',
                unsafe_allow_html=True)
    if not reps:
        st.caption("아직 앱에서 넣은 리포트가 없어요.")
        return
    st.caption(f"자동 정리: 같은 리포트를 또 넣으면 바꿔 끼우고, 최근 {reports.RECENT_DAYS}일 리포트는 리포트별 태그, "
               "지난 리포트는 '📥 테마 · …' 태그로 묶여요. 한 번 분류된 종목은 다음 리포트에서도 같은 자리에 들어가요.")
    groups: dict[str, list] = {}
    for rep in reps:
        groups.setdefault(rep.get("theme") or "테마 없음", []).append(rep)
    for th, items in groups.items():
        st.markdown(f"**🗂️ {html.escape(th)}** <small style='color:#8A979F'>{len(items)}개</small>", unsafe_allow_html=True)
        for rep in items:
            _saved_report_row(rep, store, stt, editable)


def _saved_report_row(rep: dict, store: dict, stt: dict, editable: bool):
    with st.expander(f"{reports.tag_name(rep)} · {len(rep.get('stocks', []))}종목 · {rep.get('added_at', '')}"):
        if rep.get("summary"):
            st.markdown(rep["summary"])
        st.dataframe(pd.DataFrame(rep.get("stocks", [])).rename(columns={
            "name": "종목명", "code": "코드", "sector": "산업", "group": "세부 분류", "desc": "한 줄 설명", "point": "리포트 포인트"}),
            hide_index=True, width="stretch")
        if editable and st.button("🗑️ 이 리포트 지우기", key=f"rep_del_{rep['id']}"):
            store["reports"] = [r for r in store["reports"] if r.get("id") != rep["id"]]
            ok, msg = _persist(store, stt)
            (st.success if ok else st.error)(msg)
            if ok:
                time.sleep(0.6)
                st.rerun(scope="app")


@st.fragment
def render_chart_tab(df: pd.DataFrame, quotes: dict):
    """(속도) 이 탭 안에서 검색·버튼을 눌러도 이 칸만 다시 그려요. 예전엔 보드 전체(600종목 계산·모든 탭)를 다시 돌려서 느렸어요."""
    _render_chart_tab(df, quotes)


CHART_INDEXES = {"코스피": ("KOSPI", "코스피"), "코스닥": ("KOSDAQ", "코스닥"), "나스닥": ("^IXIC", None),
                 "S&P 500": ("^GSPC", None), "다우": ("^DJI", None), "니케이225": ("^N225", None)}
INDEX_CODES = {v[0]: k for k, v in CHART_INDEXES.items()}


def _render_index_chart(code: str):
    """지수 차트(코스피·코스닥은 실시간으로 마지막 봉 갱신)."""
    label = INDEX_CODES[code]
    live = None
    ov_key = CHART_INDEXES[label][1]
    if ov_key:
        try:
            ov = (load_overview() or {}).get(ov_key) or {}
            if ov.get("last"):
                live = {"price": ov["last"], "status": ov.get("status") or "OPEN"}
        except Exception:
            live = None
    bars = load_chart(code, "일봉")
    if live and bars is not None and len(bars):
        ref = float(bars["close"].iloc[-1])
        if ref and abs(live["price"] / ref - 1) > 0.3:      # 실시간 값이 이상하면(단위·자료 오류) 쓰지 않아요
            live = None
    last = live["price"] if live else (float(bars["close"].iloc[-1]) if bars is not None and len(bars) else None)
    prev = None
    if bars is not None and len(bars) >= 2:
        same_day = pd.Timestamp(bars["date"].iloc[-1]).date() == data.now_kst().date()
        prev = float(bars["close"].iloc[-2] if (same_day or not live) else bars["close"].iloc[-1])
    chg = (last / prev - 1) * 100 if last and prev else None
    col = UP if (chg or 0) > 0 else (DOWN if (chg or 0) < 0 else "#51616C")
    st.markdown(
        f'<div class="ch-head"><b>{label}</b><span class="ch-code">{code}</span>'
        f'<span class="ch-px" style="color:{col}">{last:,.2f}' + (f" <small>{chg:+.2f}%</small>" if chg is not None else "")
        + '</span><span class="ch-sub">' + ("실시간 · 마지막 봉이 새로고침마다 바뀌어요" if live else "해외 지수 · 야후 기준(지연)")
        + "</span></div>" if last else f'<div class="ch-head"><b>{label}</b></div>', unsafe_allow_html=True)
    candle_chart(code, "IDX", live, None, key="idxchart", is_index=True)


def _render_chart_tab(df: pd.DataFrame, quotes: dict):
    """🕯️ 차트: 종목을 검색해서 일봉·주봉·월봉 캔들로 봐요."""
    board = df.drop_duplicates("code")
    labels = {r.code: f"{r.name} · {r.code} · {'한국' if data.is_kr(r.code) else r.market}" for r in board.itertuples()}
    codes = list(labels)
    mine = [c for c in codes if _mkt(c) == market]
    ordered = mine + [c for c in codes if c not in mine]
    names = st.session_state.setdefault("chart_names", {})
    want = st.session_state.get("chart_code")
    if want in ordered and st.session_state.get("chart_pick") != want:
        st.session_state["chart_pick"] = want          # 표·카드에서 고른 종목으로 맞춰요
    if st.session_state.get("chart_pick") not in ordered:
        st.session_state.pop("chart_pick", None)

    with st.container(key="chart_idx_bar"):
        cur_idx = INDEX_CODES.get(st.session_state.get("chart_code"))
        if st.session_state.get("chart_idx") != cur_idx:
            st.session_state["chart_idx"] = cur_idx
        st.segmented_control("📈 지수 차트", list(CHART_INDEXES), key="chart_idx", width="stretch",
                             on_change=lambda: st.session_state.update(
                                 chart_code=CHART_INDEXES[st.session_state["chart_idx"]][0])
                             if st.session_state.get("chart_idx") else None)
    if st.session_state.get("chart_code") in INDEX_CODES:
        _render_index_chart(st.session_state["chart_code"])
        st.caption("종목 차트로 돌아가려면 아래 검색칸에서 종목을 고르세요.")
    with st.container(key="chart_search"):
        c1, c2 = st.columns([1.35, 1], gap="medium")
        q = c1.text_input("🔍 종목 검색", placeholder="종목 이름이나 코드를 치고 엔터 — 예: 삼성, 토마토, 035720",
                          key="chart_q").strip()
        c2.selectbox("📋 보드 종목에서 고르기", ordered, format_func=labels.get, key="chart_pick",
                     on_change=lambda: st.session_state.update(chart_code=st.session_state["chart_pick"]))
        if q:
            ql = q.lower()
            res = [(r.code, r.name) for r in board.itertuples()
                   if ql in str(r.name).lower() or str(r.code).lower().startswith(ql)]
            on_board = {c for c, _ in res}
            if data.is_kr(q) and len(q) == 6 and q not in on_board:
                res.insert(0, (q, q))
            for c, nm in naver_search(q):
                if c not in on_board and c not in {x for x, _ in res}:
                    res.append((c, nm))
            res = res[:18]
            if res:
                names.update({c: nm for c, nm in res})
                opts = [c for c, _ in res]
                st.pills(f"관련 종목 {len(res)}개", opts, key=f"chart_res_{q}",
                         format_func=lambda c: f"{'★ ' if c in on_board else ''}{names.get(c, c)}",
                         on_change=lambda k=f"chart_res_{q}": st.session_state.update(
                             chart_code=st.session_state[k]) if st.session_state.get(k) else None,
                         help="★ = 보드에 넣어 둔 종목. 누르면 아래에 차트가 떠요.")
            else:
                st.caption("검색 결과가 없어요. 이름을 조금 짧게 쓰거나 6자리 코드를 넣어 보세요.")
    code = st.session_state.get("chart_code") or st.session_state.get("chart_pick") or (ordered[0] if ordered else None)
    if code is None or code in INDEX_CODES:
        return
    name, currency, row = (labels.get(code) or "").split(" · ")[0] or names.get(code, code), "KRW", None
    st.session_state["chart_code"] = code
    hit = board[board["code"] == code]
    if len(hit):
        row = hit.iloc[0]
        currency = row["currency"]
    quote = quotes.get(code)
    if quote is None and data.is_kr(code) and row is None:
        got, _ = load_quotes((code,))
        quote = got.get(code)
    unit = UNIT.get(currency, currency)
    price = (quote or {}).get("price") or (row["price"] if row is not None else None)
    prev = (quote or {}).get("prev")
    chg = (price / prev - 1) * 100 if price and prev else (row["change"] if row is not None else None)
    col = UP if (chg or 0) > 0 else (DOWN if (chg or 0) < 0 else "#51616C")
    sub = []
    if row is not None:
        if pd.notna(row.get("to_high")):
            sub.append(f"52주 최고까지 {row['to_high']:+.1f}%")
        if pd.notna(row.get("rs")):
            sub.append(f"RS {row['rs']:.0f}")
        sub.append(str(row["group"]))
    st.markdown(
        f'<div class="ch-head"><b>{html.escape(str(name))}</b><span class="ch-code">{html.escape(code)}</span>'
        f'<span class="ch-px" style="color:{col}">{fmt_price(price, currency)}{unit}'
        f'{f" <small>{chg:+.2f}%</small>" if chg is not None and pd.notna(chg) else ""}</span>'
        f'<span class="ch-sub">{" · ".join(html.escape(x) for x in sub)}</span></div>', unsafe_allow_html=True)
    candle_chart(code, currency, quote if data.is_kr(code) else None,
                 row["high52"] if row is not None and pd.notna(row.get("high52")) else None, key="tabchart")


@st.fragment
def render_detail(f: pd.DataFrame, histories: dict, trends: dict):
    """(속도) 종목을 바꾸거나 차트 버튼을 눌러도 이 칸만 다시 그려요."""
    _render_detail(f, histories, trends)


def _render_detail(f: pd.DataFrame, histories: dict, trends: dict):
    options = f[f["price"].notna()]
    if options.empty:
        return
    st.subheader("종목 자세히 보기")
    labels = {r.code: f"{r.name} ({r.code})" for r in options.itertuples()}
    if st.session_state.get("detail_code") not in labels:
        st.session_state.pop("detail_code", None)
    code = st.selectbox("종목", list(labels), format_func=labels.get, label_visibility="collapsed",
                        key="detail_code")
    row = options[options["code"] == code].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    unit = UNIT.get(row.currency, row.currency)
    c1.metric("현재가", f"{fmt_price(row.price, row.currency)}{unit}",
              f"{row.change:+.2f}%" if pd.notna(row.change) else None, delta_color="off")
    c2.metric("52주 최고", f"{fmt_price(row.high52, row.currency)}{unit}", f"{row.gap:.1f}%", delta_color="off")
    c3.metric("신고가까지", f"{row.to_high:+.1f}%",
              "오늘 신고가" if row.days_since_high == 0 else
              (f"고점 후 {row.days_since_high:.0f}거래일" if pd.notna(row.days_since_high) else "고점 날짜 확인 필요"),
              delta_color="off")
    c4.metric("52주 최저", f"{fmt_price(row.low52, row.currency)}{unit}")
    if getattr(row, "h52_fixed", False) is True:
        st.caption(f"⚠️ 52주 최고·최저를 네이버 공식값으로 고쳤어요. 일봉 계산값은 최고 "
                   f"{fmt_price(row.high52_calc, row.currency)}{unit} · 최저 {fmt_price(row.low52_calc, row.currency)}{unit}였어요. "
                   "이 종목은 일봉 기준 돌파 판정(매수 후보)을 보류해요.")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("종합 RS", _n(row.rs), f"3M {_n(row.rs_3m)} · 6M {_n(row.rs_6m)}", delta_color="off", help=RS_HELP)
    d2.metric("단기 RS(1M)", _n(row.rs_1m),
              f"1개월 {row.ret_1m:+.1f}%" if pd.notna(row.ret_1m) else None, delta_color="off")
    d3.metric("ATR%(20일)", _pct(row.atr_pct), "손절폭은 8%와 ATR 중 큰 값", delta_color="off")
    if isinstance(row.bo_status, str):
        if row.bo_status == "유지":
            sub = (f"{row.bo_date:%m/%d} 돌파 · 직전 52주 최고 {fmt_price(row.bo_level, row.currency)} "
                   f"대비 {row.bo_vs:+.1f}%")
        else:
            sub = (f"52주 최고 {fmt_price(row.bo_level, row.currency)}({row.bo_date:%m/%d}) "
                   f"대비 {row.bo_vs:+.1f}%")
        d4.metric("52주 최고가 기준", f"{row.bo_status} {int(row.bo_days)}일", sub, delta_color="off")
    else:
        d4.metric("52주 최고가 기준", "-", "일봉이 부족해요", delta_color="off")
    nh52, ath = _nh_text(row, "nh52"), _nh_text(row, "ath")
    ath_note = (f"역대 최고가 {fmt_price(row.ath_price, row.currency)}{unit}" if pd.notna(row.ath_price) else "")
    st.markdown(f"**신고가 봉** — 52주: {nh52 or '없음'} · 역대: {ath or ('없음' if row.ath_ok else '월봉 못 받음')}"
                + (f"  ·  {ath_note}" if ath_note else ""))
    if pd.notna(row.cap_local):
        cap_text = f"시가총액 {data.format_local_cap(row.cap_local, row.currency)}"
        if row.currency != "KRW" and pd.notna(row.cap_krw):
            cap_text += f" (약 {data.format_krw(row.cap_krw)}원)"
        st.markdown(f"**{cap_text}**  \n상장주식수 {row.shares:,.0f}주")
    if st.button("🕯️ 차트 탭에서 크게 보기", key=f"to_chart_{code}"):
        st.session_state["chart_code"] = code
        st.session_state["main_tab_sel"] = "🕯️ 차트"
        st.rerun()
    st.write(f"**{row['group']}**  \n{row.desc}")
    if isinstance(row.get("src"), str) and row.get("src"):
        st.caption(row["src"])
    if row.tags:
        st.caption("리포트 태그: " + ", ".join(row.tags))

    notes = row.get("notes") or []
    tab_chart, tab_earn, tab_flow, tab_notes = st.tabs(["차트", "실적(영업이익·EPS)", "수급(개인·외국인·기관)",
                                                        f"자료 메모 {len(notes)}"])
    with tab_chart:
        hist = histories.get(code, (None, data.empty_frame(), None))[1]
        candle_chart(code, row.currency, _QUOTES.get(code) if data.is_kr(code) else None,
                     row.high52 if pd.notna(row.high52) else None, key="detailchart")
        if not hist.empty:
            hist_rows = row.get("bo_history")
            hist_rows = hist_rows if isinstance(hist_rows, list) else []
            if hist_rows:
                st.caption("직전 52주 최고가 종가 돌파 기록 (그 아래에서 마감한 날이 이탈일)")
                st.dataframe(pd.DataFrame(hist_rows).astype({"이탈일": str})
                             .style.format({"돌파한 직전 52주 최고가": "{:,.0f}"}), hide_index=True)
    with tab_earn:
        render_earnings(code, row.currency)
    with tab_flow:
        render_stock_flow(code, trends)
    with tab_notes:
        if notes:
            st.markdown('<ol class="notes">' + "".join(f"<li>{html.escape(n)}</li>" for n in notes) + "</ol>",
                        unsafe_allow_html=True)
            st.caption("엣지방 대화 요약(9/22~24)과 캡처 자료에서 모은 포인트예요. 매수·매도 추천이 아니에요.")
        else:
            st.caption("이 종목은 따로 모아 둔 메모가 없어요. 위 설명이 요약이에요.")


# ─────────────────────────── 매수 후보 ───────────────────────────
BUY_TIER_STYLE = {"매수 가능": ("#1B8A4B", "✅"), "종가 확인": ("#A87400", "⏳"),
                  "시장 대기": ("#C0262E", "⛔"), "1개 미충족": ("#51616C", "⚠️")}


def _buy_params() -> dict:
    """원칙은 고정값, 숫자를 정해 주지 않은 기준만 바꿀 수 있게."""
    d = data.BUY_DEFAULTS
    with st.expander("⚙️ 기준 · 계좌 금액"):
        st.caption("고정 원칙: 지수 60일선 위 · 주도섹터의 대장주 · 52주 신고가 돌파(첫 돌파 우선) · RS 70↑ & 지수 RS보다 위 · "
                   "정배열(강한 종목 면제) · ADX 20↑ · 영업이익·EPS 정배열 · 손절 8%(또는 ATR) · 위험 1.5% · 최대 8종목 · 3R 절반 익절")
        st.caption("아래 숫자는 원칙에 없어 정해 둔 기본값이에요.")
        c1, c2, c3 = st.columns(3)
        fresh = c1.number_input("돌파 후 며칠 이내", 1, 20, d["fresh_days"], key="buy_fresh",
                                help="돌파 유지 1일째 = 돌파한 날. 너무 오래된 돌파는 타점이 지났다고 봐요.")
        ext = c2.number_input("20일선 이격 최대(%)", 5.0, 50.0, d["max_ext"], step=1.0, key="buy_ext",
                              help="원칙: 이평선에서 너무 뜬(단기 과열) 종목은 피하기")
        cap = c3.number_input("최소 시가총액(억원)", 0.0, 100000.0, d["min_cap"], step=500.0, key="buy_cap",
                              help="원칙: 너무 작은 종목·테마성 소형주 피하기")
        c4, c5, c6 = st.columns(3)
        vol = c4.number_input("돌파일 거래량(50일 평균의 몇 배)", 1.0, 5.0, d["vol_mult"], step=0.1, key="buy_vol",
                              help="원칙: 3stage 돌파는 큰 거래량")
        dcr = c5.number_input("돌파일 DCR 최소(%)", 0.0, 100.0, d["dcr_min"], step=5.0, key="buy_dcr",
                              help="원칙: 강한 종가(DCR 100%에 가까울수록 고가 근처 마감)")
        top_n = st.number_input("섹터 몇 위까지 주도주로 볼까요", 1, 3, d["top_n"], key="buy_topn",
                                help="원칙은 1위(대장주)만. 대장이 아직 안 움직일 때 2위까지 보고 싶으면 2")
        first_only = st.toggle("52주 안 첫 돌파만 보기", value=d["first_only"], key="buy_first",
                               help="끄면 첫 돌파를 맨 위에 두고 2번째 이상 돌파도 보여줘요. 켜면 첫 돌파만 통과.")
        equity = c6.number_input("계좌 평가금액(원, 수량 계산용)", 0, 10_000_000_000, 0, step=1_000_000,
                                 key="buy_equity", help="0이면 수량 계산을 안 해요. 이 값은 저장되지 않아요.")
    return {**d, "top_n": int(top_n), "first_only": bool(first_only), "fresh_days": int(fresh), "max_ext": float(ext), "min_cap": float(cap),
            "vol_mult": float(vol), "dcr_min": float(dcr), "earn_years": earn_years, "equity": float(equity or 0)}


# ─────────────────────────── 차기 주도섹터 · 섹터 안 급상승 엔진 ───────────────────────────
def load_trends_all():
    """화면에 보이는 국내 종목의 외국인·기관 수급(최근 20거래일). 처음엔 기다리지 않고 뒤에서 받아요."""
    codes = VIEW_CODES.get("heavy") or KR_CODES
    return swr(("trends_all", codes), 1800, lambda: data.fetch_stock_trends(codes, workers=6), first_wait=False)


def load_fins_all():
    """화면에 보이는 국내 종목의 연간 영업이익(실적+전망). 12시간 기억, 처음엔 뒤에서 받아요."""
    codes = VIEW_CODES.get("heavy") or KR_CODES
    return swr(("fins_all", codes), 43200, lambda: data.fetch_financials_many(codes, workers=12), first_wait=False)


def load_official_52w_bg(codes: tuple[str, ...]) -> dict:
    """새로 들어온 전 종목의 네이버 공식 52주 최고·최저(1시간마다, 기다리지 않음)."""
    return swr(("h52_all", codes), 3600, lambda: data.fetch_official_52w(codes), first_wait=False, empty={}) or {}


def _ok_mark(v) -> str:
    return "✅" if v is True else ("❌" if v is False else "❔")


def _pct_txt(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v:+.1f}%"


TIER_STYLE = {"차기 주도 후보": ("🔭 후보", "#D6333B"), "주도 유지": ("👑 주도 유지", "#F2B134"),
              "관찰": ("👀 관찰", "#7FA7C9"), "해당 없음": ("· 미달", "#C9D2D8")}


def render_next(df: pd.DataFrame, fins, trends, leaders: set):
    _sec_h("🔭 차기 주도섹터", "이익증가율 가속 · 기관/외국인 수급 · 신고가 근처 종목 다수")
    d = data.NEXT_DEFAULTS
    with st.expander("⚙️ 기준"):
        c1, c2, c3 = st.columns(3)
        near_pct = c1.number_input("52주 고가 대비 % 이내", 3.0, 30.0, d["near_pct"], step=1.0, key="nx_near")
        near_rs = c2.number_input("그중 RS 최소", 0, 99, d["near_rs"], key="nx_rs")
        min_near = c3.number_input("몇 종목 이상", 1, 10, d["min_near"], key="nx_min")
        st.caption("① 분류 영업이익 합: 올해(E) 증가율 > 작년 증가율 & > 0 · ② 외국인+기관 20일·5일 모두 순매수 "
                   "(연기금·투신은 기관 합계에 포함) · ③ 위 조건의 종목 수. 3개 통과 = 후보, 2개 = 관찰. "
                   "가능성 = 이익 가속 40 · 시총 대비 수급 30 · 신고가 근처 비율 30 (섹터끼리 백분위, 비중은 제가 정한 값)")
    if fins is None or trends is None:
        st.info("업종 실적·수급을 받는 중이에요(처음 1~2분).")
    res = tab_memo("next_lead", (near_pct, near_rs, min_near, fins is None, trends is None, len(fins or {}), len(trends or {}), len(leaders)),
                   lambda: data.next_leader_sectors(df, fins, trends, leaders,
                                                    {"near_pct": near_pct, "near_rs": near_rs, "min_near": min_near}, include_all=True))
    if res.empty:
        st.info("섹터를 판정할 데이터가 아직 없어요.")
        return
    cnt = res["tier"].value_counts()
    c1, c2, c3 = st.columns(3)
    c1.metric("🔭 차기 주도 후보", f"{cnt.get('차기 주도 후보', 0)}")
    c2.metric("👑 주도 유지", f"{cnt.get('주도 유지', 0)}")
    c3.metric("👀 관찰", f"{cnt.get('관찰', 0)}")

    top = res.dropna(subset=["score"]).sort_values("score", ascending=False).head(15)
    if not top.empty:
        dom = list(TIER_STYLE)
        chart = alt.Chart(top.assign(tier_lab=top["tier"].map(lambda x: TIER_STYLE[x][0]))).mark_bar(cornerRadius=3).encode(
            y=alt.Y("group:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180)),
            x=alt.X("score:Q", title="가능성", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("tier:N", scale=alt.Scale(domain=dom, range=[TIER_STYLE[k][1] for k in dom]),
                            legend=alt.Legend(orient="top", title=None, labelExpr=
                                              "datum.label == '차기 주도 후보' ? '🔭 후보' : datum.label == '주도 유지' ? "
                                              "'👑 주도 유지' : datum.label == '관찰' ? '👀 관찰' : '· 미달'")),
            tooltip=[alt.Tooltip("group:N", title="섹터"), alt.Tooltip("tier_lab:N", title="상태"),
                     alt.Tooltip("score:Q", title="가능성", format=".0f"), alt.Tooltip("n_near:Q", title="신고가 근처")],
        ).properties(height=max(180, 26 * len(top)), width="container")
        st.altair_chart(chart)

    show_all = st.toggle("미달 섹터도 표에 보기", value=False, key="nx_all")
    full = res
    if not show_all:
        res = res[res["tier"] != "해당 없음"]
    if not res.empty:
        y = res["years"].iloc[0]
        view = pd.DataFrame({
            "상태": res["tier"].map(lambda x: TIER_STYLE[x][0]), "섹터": res["group"], "가능성": res["score"],
            f"① 이익({y[2]}E)": res.apply(lambda r: f"{_ok_mark(r['earn_ok'])} {_pct_txt(r['g_prev'])} → {_pct_txt(r['g_now'])}", axis=1),
            "② 외+기 20일(억)": res.apply(lambda r: (r["f20"] or 0) + (r["i20"] or 0) if r["flow_ok"] is not None else None, axis=1),
            "②": res["flow_ok"].map(_ok_mark) + res["both20"].map(lambda b: " ⭐" if b else ""),
            "③ 신고가 근처": res.apply(lambda r: f"{_ok_mark(r['near_ok'])} {r['n_near']}개", axis=1),
        })
        st.dataframe(
            view.style.format({"② 외+기 20일(억)": _eok_num}, na_rep="-")
            .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}; font-weight: 700",
                 subset=["② 외+기 20일(억)"]),
            hide_index=True, height=min(520, 36 * (len(view) + 1) + 4),
            column_config={"섹터": st.column_config.Column(pinned=True),
                           "가능성": st.column_config.ProgressColumn("가능성", min_value=0, max_value=100, format="%d")},
        )
    render_next_drill(df, full, fins, trends)


def render_next_drill(df: pd.DataFrame, res: pd.DataFrame, fins, trends):
    """섹터 하나를 골라 종목별로 들여다보기."""
    _sec_h("🔍 섹터 들여다보기")
    opts = res["group"].tolist()
    if not opts:
        return
    g = st.selectbox("섹터", opts, key="nx_drill",
                     format_func=lambda x: f"{x} (가능성 {res.set_index('group').loc[x, 'score']:.0f})"
                     if pd.notna(res.set_index("group").loc[x, "score"]) else x)
    m = data.momentum_engine(df, trends, fresh_days=recent_days)
    sub = m[m["group"] == g].copy()
    if sub.empty:
        return
    y = data.now_kst().year
    def og(code):
        f = (fins or {}).get(code)
        yo = data._year_op(f) if f is not None else {}
        a, b = yo.get(y - 1), yo.get(y)
        return (b / a - 1) * 100 if a and a > 0 and b is not None else None
    sub["og"] = sub["code"].map(og)
    sub = sub.sort_values("mom", ascending=False)
    view = pd.DataFrame({
        "종목": sub["name"] + sub["lead_rank"].map(lambda x: " 👑" if x == 1 else "").where(sub["lead_ok"] == True, ""),  # noqa: E712
        "급상승 점수": sub["mom"], "현재가": sub["price"], "등락률": sub["change"],
        "52주 고가 대비": sub["gap"], "RS": sub["rs"], "1개월 RS": sub["rs_1m"],
        "외국인 20일(억)": sub["f20"], "기관 20일(억)": sub["i20"],
        f"영업이익 {y}E 증가율": sub["og"], "신고가까지": sub["to_high"], "돌파": sub.apply(_bo_full, axis=1),
    })
    st.dataframe(
        view.style.format({"급상승 점수": "{:.0f}", "현재가": "{:,.0f}", "등락률": "{:+.2f}%", "52주 고가 대비": "{:+.1f}%",
                           "RS": "{:.0f}", "1개월 RS": "{:.0f}", "외국인 20일(억)": _eok_num, "기관 20일(억)": _eok_num,
                           f"영업이익 {y}E 증가율": "{:+.1f}%", "신고가까지": "{:+.1f}%"}, na_rep="-")
        .map(_bo_color, subset=["돌파"]).map(_to_high_color, subset=["신고가까지"])
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}",
             subset=["등락률", "외국인 20일(억)", "기관 20일(억)", f"영업이익 {y}E 증가율"]),
        hide_index=True, height=min(520, 36 * (len(view) + 1) + 4),
    )


def _sec_h(title: str, note: str = ""):
    st.markdown(f'<div class="sec-h"><b>{title}</b><span>{html.escape(note)}</span></div>', unsafe_allow_html=True)


def _score_bars(v: pd.DataFrame):
    """급상승 점수를 구성(수급·RS·캔들·저항 돌파)별로 쌓은 막대."""
    w = data.MOM_WEIGHTS
    parts = {"수급": ("s_flow", w["flow"]), "RS": ("s_rs", w["rs"]), "캔들": ("s_candle", w["candle"]),
             "저항 돌파": ("s_break", w["break"])}
    rows = []
    for _, r in v.iterrows():
        label = r["name"] + (" 👑" if (r["lead_rank"] == 1 and r["lead_ok"]) else "")
        for k, (col, wt) in parts.items():
            val = r[col]
            rows.append({"종목": label, "구성": k, "점수": 0 if pd.isna(val) else val * wt, "order": -r["mom"]})
    d = pd.DataFrame(rows)
    order = list(dict.fromkeys(d.sort_values("order")["종목"]))
    c = alt.Chart(d).mark_bar(cornerRadius=2).encode(
        y=alt.Y("종목:N", sort=order, title=None, axis=alt.Axis(labelLimit=140)),
        x=alt.X("점수:Q", title=None, scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("구성:N", sort=list(parts), scale=alt.Scale(domain=list(parts),
                        range=["#D6333B", "#F2B134", "#5B47F5", "#7FA7C9"]), legend=alt.Legend(orient="top", title=None)),
        order=alt.Order("구성:N"),
        tooltip=["종목:N", "구성:N", alt.Tooltip("점수:Q", format=".0f")],
    ).properties(height=max(160, 24 * len(order)), width="container")
    st.altair_chart(c)


def render_money(df: pd.DataFrame):
    """💰 거래대금: 지금 어느 섹터에 돈(거래대금)이 몰리는지 + 종목별 실시간 거래량."""
    d = df[df["code"].map(_mkt) == market].copy()
    if not {"tv_live", "tv_x", "vol_live", "vol_src"} <= set(d.columns):
        st.warning("거래량 자료를 아직 못 불러왔어요. 오른쪽 아래 'Manage app' → 'Reboot app'을 한 번 눌러 주세요.")
        return
    for c in ("tv_live", "tv_x", "vol_live", "change", "price"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d["tv_live"].notna()]
    if d.empty:
        st.info("거래량 자료가 아직 없어요. 잠시 뒤 새로고침해 보세요.")
        return
    live = (d["vol_src"] == "실시간").any()
    frac = data.session_frac()
    when = (f"실시간 · 장 진행 {frac * 100:.0f}%" if live and frac < 1 else "실시간(오늘 누적)") if live else "최근 거래일 기준"
    st.markdown(f'<div class="sec-h"><b>💰 지금 돈이 몰리는 섹터</b><span>{when} · 보드에 넣은 {market_label} 종목 기준</span></div>',
                unsafe_allow_html=True)

    by = st.segmented_control("묶는 기준", ["세부 분류", "산업"], default="세부 분류", required=True,
                              key="money_by", label_visibility="collapsed") or "세부 분류"
    sm = data.sector_money(d, "group" if by == "세부 분류" else "sector")
    total = d["tv_live"].sum()
    has = d["tv_x"].notna() & (d["tv_x"] > 0)
    base = (d.loc[has, "tv_live"] / d.loc[has, "tv_x"]).sum() if has.any() else None
    tv_b = d.loc[has, "tv_live"].sum()
    hot = sm[(sm["x"] >= 1.3) & (sm["n"] >= 2)].sort_values("x", ascending=False)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("보드 전체 거래대금", data.format_krw(total) + ("원" if market == "KR" else ""))
    c2.metric("평소 대비", f"{tv_b / base:.2f}배" if base else "-", help="직전 20거래일 평균 거래대금을 지금 시각까지로 환산해 비교")
    c3.metric("거래대금 1위 섹터", sm.iloc[0]["sector"] if len(sm) else "-",
              f"비중 {sm.iloc[0]['share']:.1f}%" if len(sm) else None, delta_color="off")
    c4.metric("평소보다 뜨거운 섹터", hot.iloc[0]["sector"] if len(hot) else "없음",
              f"{hot.iloc[0]['x']:.1f}배" if len(hot) else None, delta_color="off")

    top = sm.head(15).copy()
    top["label"] = top["sector"].astype(str).str.replace("(해외)", "", regex=False)
    top["tv_eok"] = top["tv"] / 1e8
    top["heat"] = top["x"].apply(lambda v: "평소의 2배 이상" if v == v and v >= 2 else
                                 ("1.3~2배" if v == v and v >= 1.3 else ("평소 수준" if v == v and v >= 0.7 else "평소보다 적음")))
    chart = (
        alt.Chart(top)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            y=alt.Y("label:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200, labelOverlap=False)),
            x=alt.X("tv_eok:Q", title="거래대금(억)" if market == "KR" else "거래대금(현지 통화, 억)"),
            color=alt.Color("heat:N", title="평소 대비",
                            scale=alt.Scale(domain=["평소의 2배 이상", "1.3~2배", "평소 수준", "평소보다 적음"],
                                            range=[UP, "#F08A8F", "#9FB0BA", "#7FA7DE"]),
                            legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("label:N", title="섹터"), alt.Tooltip("tv_eok:Q", title="거래대금(억)", format=",.0f"),
                     alt.Tooltip("share:Q", title="비중(%)", format=".1f"), alt.Tooltip("x:Q", title="평소 대비(배)", format=".2f"),
                     alt.Tooltip("chg:Q", title="등락(거래대금 가중, %)", format="+.2f"), alt.Tooltip("top:N", title="상위 종목")],
        )
        .properties(height=max(220, 26 * len(top)), width="container")
    )
    st.altair_chart(chart)

    view = pd.DataFrame({
        "섹터": sm["sector"], "거래대금": sm["tv"], "비중": sm["share"], "평소 대비": sm["x"],
        "등락(가중)": sm["chg"], "상승/하락": sm.apply(lambda r: f"{r['up']} / {r['down']}", axis=1),
        "종목 수": sm["n"], "거래대금 상위 종목": sm["top"],
    })
    st.dataframe(
        view.style.format({"거래대금": data.format_krw, "비중": "{:.1f}%", "평소 대비": "{:.2f}배", "등락(가중)": "{:+.2f}%"},
                          na_rep="-")
        .map(_x_color, subset=["평소 대비"])
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}; font-weight: 600",
             subset=["등락(가중)"]),
        hide_index=True, height=min(420, 36 * (len(view) + 1) + 4),
        column_config={"섹터": st.column_config.Column(pinned=True)})
    st.caption("평소 대비 = 지금까지 거래대금 ÷ (직전 20거래일 평균 거래대금 × 장 진행 비율). 1.3배 이상이면 평소보다 돈이 더 도는 섹터예요. "
               "장 초반(9시~9시 30분)에는 거래가 몰려서 배수가 크게 나오기 쉬워요. 거래대금만으로는 사는 돈인지 파는 돈인지 모르니 등락과 같이 보세요.")

    st.markdown('<div class="sec-h"><b>📊 종목별 실시간 거래량</b><span>거래대금 많은 순</span></div>', unsafe_allow_html=True)
    c1, c2 = st.columns([2, 1])
    pick = c1.selectbox("섹터", ["전체", *sm["sector"].tolist()], key="money_pick", label_visibility="collapsed")
    n_show = c2.segmented_control("표시", [30, 100, "전체"], default=30, required=True, key="money_n",
                                  label_visibility="collapsed") or 30
    col = "group" if by == "세부 분류" else "sector"
    t = d if pick == "전체" else d[d[col] == pick]
    t = t.sort_values("tv_live", ascending=False)
    if n_show != "전체":
        t = t.head(int(n_show))
    tv_view = pd.DataFrame({
        "종목": t["name"], "분류": t["group"], "현재가": t["price"], "일간%": t["change"],
        "거래량": t["vol_live"], "거래대금": t["tv_live"], "평소 대비": t["tv_x"],
    })
    whole = tv_view.index[t["currency"].isin(["KRW", "JPY"]).values]
    st.dataframe(
        tv_view.style
        .format({"현재가": "{:,.2f}", "일간%": "{:+.2f}%", "거래량": fmt_vol, "거래대금": data.format_krw,
                 "평소 대비": "{:.1f}배"}, na_rep="-")
        .format("{:,.0f}", subset=pd.IndexSlice[whole, ["현재가"]], na_rep="-")
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}; font-weight: 600", subset=["일간%"])
        .map(_x_color, subset=["평소 대비"])
        .map(lambda _: "font-weight: 600", subset=["종목"]),
        hide_index=True, height=min(760, 36 * (len(tv_view) + 1) + 4),
        column_config={"종목": st.column_config.Column(pinned=True)})
    if market == "KR":
        st.caption("거래량은 네이버 실시간 누적(몇 초 지연)이에요. 자동 새로고침 주기마다 바뀌어요. 매매 전에는 HTS로 다시 확인하세요.")
    else:
        st.caption("해외 종목은 실시간 거래량을 받지 않아서 최근 거래일 일봉 기준이에요.")


def render_engine(df: pd.DataFrame, trends, leaders: set, fins=None):
    _sec_h("🚀 섹터 안에서 치고 올라오는 종목", "영업이익 증가 종목만")
    if fins is None:
        st.info("영업이익 확인 중이에요(처음 1~2분).")
        return
    m = data.momentum_engine(df, trends, fresh_days=recent_days, fins=fins, require_op=True)
    if m.empty:
        st.info("영업이익이 늘어나는 종목 중에 순위를 매길 종목이 없어요.")
        return
    groups = (m.groupby("group")["mom"].max().sort_values(ascending=False).index.tolist())
    lead_first = [g for g in groups if g in leaders] + [g for g in groups if g not in leaders]
    opts = ["섹터별 1위 모아보기"] + lead_first
    pick = st.selectbox("섹터", opts, key="eng_group", label_visibility="collapsed",
                        format_func=lambda g: g if g == opts[0] else (f"👑 {g}" if g in leaders else g))
    if pick == opts[0]:
        v = m[m["mom_rank"] == 1].sort_values("mom", ascending=False).head(15)
    else:
        v = m[m["group"] == pick].sort_values("mom", ascending=False).head(15)
    _score_bars(v)

    view = pd.DataFrame({
        "종목": v["name"] + v["lead_rank"].map(lambda x: " 👑" if x == 1 else "").where(v["lead_ok"] == True, ""),  # noqa: E712
        "점수": v["mom"], "등락률": v["change"], "신고가까지": v["to_high"], "돌파": v.apply(_bo_full, axis=1),
        "영업이익": v["op_txt"], "섹터": v["group"],
        "외+기 5일(억)": v["fi5"],
    })
    detail = st.toggle("세부 지표", key="eng_detail")
    if detail:
        view = view.assign(**{"수급": v["s_flow"].values, "RS점수": v["s_rs"].values, "캔들": v["s_candle"].values,
                              "저항": v["s_break"].values, "1개월 RS": v["rs_1m"].values, "종합 RS": v["rs"].values,
                              "오늘 거래량": v["vol_today"].values, "오늘 DCR": v["dcr_today"].values})
    fmt = {"점수": "{:.0f}", "등락률": "{:+.2f}%", "신고가까지": "{:+.1f}%", "외+기 5일(억)": _eok_num, "수급": "{:.0f}",
           "RS점수": "{:.0f}", "캔들": "{:.0f}", "저항": "{:.0f}", "1개월 RS": "{:.0f}", "종합 RS": "{:.0f}",
           "오늘 거래량": "{:.1f}배", "오늘 DCR": "{:.0f}%"}
    st.dataframe(
        view.style.format({k: f for k, f in fmt.items() if k in view.columns}, na_rep="-")
        .map(lambda x: "" if pd.isna(x) or x == 0 else f"color: {UP if x > 0 else DOWN}", subset=["등락률", "외+기 5일(억)"])
        .map(_bo_color, subset=["돌파"]).map(_to_high_color, subset=["신고가까지"]),
        hide_index=True, height=min(460, 36 * (len(view) + 1) + 4),
        column_config={"종목": st.column_config.Column(pinned=True),
                       "점수": st.column_config.ProgressColumn("점수", min_value=0, max_value=100, format="%.0f"),
                       "돌파": st.column_config.Column(help=BO_FULL_HELP)},
    )
    with st.expander("ℹ️ 점수 기준"):
        st.markdown("- 국내 보드 종목 전체 기준 백분위를 가중 평균: **수급 30% · RS 25% · 캔들 20% · 저항 돌파 25%** (비중은 제가 정한 값)\n"
                    "- 수급 = 외국인+기관 5일 순매수 ÷ 시총 · RS = 1개월 RS + RS 가속 · 캔들 = 오늘 거래량 배수 + 종가 위치(DCR) · "
                    f"저항 돌파 = 돌파 {recent_days}거래일 이내 만점, 아니면 52주 고가에 가까울수록\n"
                    "- 올해(E) 영업이익이 작년보다 늘지 않은 종목은 빼요. 매수 판단은 🎯 매수 후보 원칙으로.")


# ─────────────────────────── 🌱 회복 리더 ───────────────────────────
def _rmet(col, label, value, delta=None, help=None):
    """화살표 없는 작은 설명 줄이 달린 숫자 칸."""
    try:
        col.metric(label, value, delta, delta_color="off", delta_arrow="off", help=help)
    except TypeError:          # 예전 Streamlit
        col.metric(label, value, delta, delta_color="off", help=help)


def _rec_idx_chart(ph: dict):
    """지수 최근 흐름 + 고점·저점 표시."""
    c = ph["series"].tail(max(ph["since"] + 40, 90))
    d = pd.DataFrame({"date": c.index, "close": c.values})
    d["ma20"] = ph["series"].rolling(20).mean().reindex(c.index).values
    base = alt.Chart(d).encode(x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0)))
    line = base.mark_line(color="#5B47F5", strokeWidth=2).encode(
        y=alt.Y("close:Q", title=None, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"), alt.Tooltip("close:Q", title="지수", format=",.2f")])
    ma = base.mark_line(color="#2E9D5B", strokeDash=[4, 3], strokeWidth=1.3).encode(y="ma20:Q")
    pts = pd.DataFrame({"date": [ph["high_date"], ph["low_date"]], "close": [ph["high"], ph["low"]],
                        "lab": [f"고점 {ph['high']:,.0f}", f"저점 {ph['low']:,.0f}"]})
    pts = pts[pts["date"] >= c.index[0]]
    mk = alt.Chart(pts).mark_point(size=80, filled=True, color="#F2B134").encode(x="date:T", y="close:Q")
    tx = alt.Chart(pts).mark_text(dy=-12, fontSize=12, fontWeight=700, color="#8A5A00").encode(x="date:T", y="close:Q", text="lab:N")
    st.altair_chart((line + ma + mk + tx).properties(height=220, width="container"))


def _rec_flow_chart(daily: pd.DataFrame, low_date):
    d = daily.tail(20).melt(id_vars="date", value_vars=["외국인", "기관"], var_name="주체", value_name="억")
    d["억"] = pd.to_numeric(d["억"], errors="coerce")
    d["day"] = pd.to_datetime(d["date"]).dt.strftime("%m/%d")
    order = list(dict.fromkeys(d.sort_values("date")["day"]))
    bars = alt.Chart(d).mark_bar(cornerRadius=2).encode(
        x=alt.X("day:O", title=None, sort=order, axis=alt.Axis(labelAngle=0, labelOverlap=True)),
        xOffset="주체:N",
        y=alt.Y("억:Q", title="순매수(억)"),
        color=alt.Color("주체:N", scale=alt.Scale(domain=["외국인", "기관"], range=["#5B47F5", "#F2B134"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("date:T", title="날짜", format="%m/%d"), "주체:N", alt.Tooltip("억:Q", format=",.0f")])
    layers = [bars]
    if low_date is not None and low_date >= pd.to_datetime(d["date"]).min():
        after = [x for x, dt in zip(d["day"], pd.to_datetime(d["date"])) if dt >= low_date]
        if after:
            layers.append(alt.Chart(pd.DataFrame({"day": [after[0]]})).mark_rule(color="#C0262E", strokeDash=[4, 3], strokeWidth=2)
                          .encode(x=alt.X("day:O", sort=order)))
    st.altair_chart(alt.layer(*layers).properties(height=200, width="container"))


def _rec_scatter(sec: pd.DataFrame, idx_fall: float | None):
    d = sec.copy()
    d["lab"] = ""
    d.loc[d.index[:6], "lab"] = d["sector"].head(6)
    kinds = ["🌱 덜 빠지고 먼저 오름", "🚀 많이 빠졌지만 빨리 회복", "🛡️ 버텼지만 회복은 느림", "🥀 많이 빠지고 회복도 느림"]
    base = alt.Chart(d).encode(
        x=alt.X("fall:Q", title="하락 구간 등락(%) → 오른쪽일수록 덜 빠짐", scale=alt.Scale(zero=False)),
        y=alt.Y("excess:Q", title="저점 이후 지수 대비(%p) ↑ 빨리 회복", scale=alt.Scale(zero=False)))
    pts = base.mark_circle(opacity=0.8, stroke="#fff", strokeWidth=1).encode(
        size=alt.Size("n:Q", legend=None, scale=alt.Scale(range=[60, 600])),
        color=alt.Color("kind:N", scale=alt.Scale(domain=kinds, range=["#1B8A4B", "#D6333B", "#7FA7C9", "#B8C2C9"]),
                        legend=alt.Legend(orient="top", title=None, columns=2)),
        tooltip=[alt.Tooltip("sector:N", title="섹터"), alt.Tooltip("score:Q", title="새싹 점수", format=".0f"),
                 alt.Tooltip("fall:Q", title="하락 구간(%)", format="+.1f"), alt.Tooltip("reb:Q", title="저점 이후(%)", format="+.1f"),
                 alt.Tooltip("excess:Q", title="지수 대비(%p)", format="+.1f"), alt.Tooltip("n:Q", title="종목 수"),
                 alt.Tooltip("top:N", title="먼저 오른 종목")])
    txt = base.mark_text(dy=-11, fontSize=11, fontWeight=600, color="#374151").encode(text="lab:N")
    rules = [alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#9CA3AF", strokeDash=[3, 3]).encode(y="y:Q")]
    if idx_fall is not None:
        rules.append(alt.Chart(pd.DataFrame({"x": [idx_fall]})).mark_rule(color="#9CA3AF", strokeDash=[3, 3]).encode(x="x:Q"))
    st.altair_chart(alt.layer(*rules, pts, txt).properties(height=420, width="container"))


def render_recovery(df: pd.DataFrame, histories: dict, trends):
    _sec_h("🌱 회복 리더", "물살이 빠진 자리에서 가장 먼저 싹트는 섹터·종목")
    c1, c2, c3 = st.columns([1, 1.4, 1])
    idx_name = c1.segmented_control("기준 지수", list(data.MARKETS), default="코스피", required=True,
                                    key="rec_idx", width="stretch") or "코스피"
    lb_lab = c2.segmented_control("저점 찾는 기간", list(recovery.LOOKBACKS), default="60거래일", required=True,
                                  key="rec_lb", width="stretch") or "60거래일"
    by_lab = c3.segmented_control("묶는 기준", ["세부 분류", "산업"], default="세부 분류", required=True,
                                  key="rec_by", width="stretch") or "세부 분류"
    sym = data.MARKETS[idx_name]
    idx_df = load_indexes().get(sym, (data.empty_frame(), None))[0]
    ph = recovery.market_phase(idx_df, recovery.LOOKBACKS[lb_lab])
    if ph is None:
        st.info("지수 일봉을 아직 못 받았어요. 잠시 뒤 다시 열어 주세요.")
        return

    # ① 시장 국면
    st.markdown(f'<div style="border-left:5px solid {ph["color"]};background:#FAFAFD;border-radius:10px;padding:10px 14px;'
                f'margin:4px 0 10px"><b style="font-size:18px;color:{ph["color"]}">{ph["label"]}</b>'
                f'<span style="color:#6B7280;margin-left:10px">{idx_name} 기준</span>'
                f'<div style="margin-top:4px;color:#374151">{html.escape(ph["advice"])}</div></div>', unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    _rmet(m1, "하락 구간 낙폭", f"{ph['dd']:+.1f}%", f"{ph['high_date']:%m/%d} → {ph['low_date']:%m/%d}")
    _rmet(m2, "저점 이후 반등", f"{ph['reb']:+.1f}%", f"저점 {ph['since']}거래일 전")
    _rmet(m3, "하락분 회복률", f"{ph['recov']:.0f}%" if ph["recov"] is not None else "-",
              f"고점까지 {-ph['now_vs_high']:.1f}% 남음" if ph["now_vs_high"] < 0 else "전고점 회복")
    _rmet(m4, "이동평균선", ("20일선 위" if ph["above20"] else "20일선 아래"),
              ("60일선 위" if ph["above60"] else "60일선 아래") if ph["above60"] is not None else None)
    _rec_idx_chart(ph)

    # ② 시장 수급: 외국인이 돌아오는가
    _sec_h("🧲 수급이 돌아오는가", f"{idx_name} 시장 전체 외국인·기관 순매수(억원) · 빨간 점선 = 지수 저점")
    fl = recovery.market_flow((load_flows() or {}).get(idx_name), ph["low_date"])
    if fl is None:
        st.info("시장 투자자별 순매수를 아직 못 받았어요.")
    else:
        f1, f2, f3, f4 = st.columns(4)
        _rmet(f1, "외국인 최근 5일", f"{fl['외국인5']:+,.0f}억",
                  f"그 전 5일 {fl['외국인5_prev']:+,.0f}억" if fl.get("외국인5_prev") is not None else None)
        _rmet(f2, "기관 최근 5일", f"{fl['기관5']:+,.0f}억",
                  f"그 전 5일 {fl['기관5_prev']:+,.0f}억" if fl.get("기관5_prev") is not None else None)
        sd = fl.get("외국인_since_days", 0)
        _rmet(f3, "저점 이후 외국인+기관", f"{fl.get('외국인_since', 0) + fl.get('기관_since', 0):+,.0f}억",
                  f"{sd}거래일" + (" (최근 30일분)" if fl.get("since_partial") else ""))
        fs, is_ = fl["외국인_streak"], fl["기관_streak"]
        _rmet(f4, "연속 순매수", f"외국인 {fs:+d}일", f"기관 {is_:+d}일",
                  help="+는 연속 순매수, −는 연속 순매도 일수")
        sig = []
        if fl["foreign_back"]:
            sig.append("🟢 외국인이 돌아오는 신호(3일 연속 순매수 또는 5일 합 순매도→순매수 전환)")
        if fl["inst_back"]:
            sig.append("🟢 기관 순매수 전환")
        if not sig:
            sig.append("⚪ 아직 외국인·기관이 뚜렷하게 돌아오지 않았어요")
        st.caption(" · ".join(sig))
        _rec_flow_chart(fl["daily"], ph["low_date"])

    # ③ 섹터: 누가 먼저 싹트나
    kr = df[df["code"].map(data.is_kr) & df["price"].notna()]
    idx_reb = recovery.index_ret(idx_df, ph["low_date"])
    idx_fall = ph["dd"]
    sdf = tab_memo("rec_stock", (str(ph["high_date"]), str(ph["low_date"]), idx_reb, len(kr), len(trends or {})),
                   lambda: recovery.stock_recovery(kr, data._closes_frame(kr, histories, kr["code"]), trends,
                                                   ph["high_date"], ph["low_date"], idx_reb))
    if sdf.empty:
        st.info("종목 일봉이 아직 부족해요. 잠시 뒤 다시 열어 주세요.")
        return
    by = "group" if by_lab == "세부 분류" else "sector"
    sec = recovery.sector_recovery(sdf, by, min_n=2, idx_fall=idx_fall)
    _sec_h("🌿 먼저 싹트는 섹터",
           f"같은 날짜 기준 · 하락 구간 {ph['high_date']:%m/%d}→{ph['low_date']:%m/%d}, 회복 구간 {ph['low_date']:%m/%d}→오늘 · "
           f"{idx_name} 저점 이후 {idx_reb:+.1f}%" if idx_reb is not None else "")
    if trends is None:
        st.caption("⏳ 종목별 외국인·기관 수급을 받는 중이에요(처음 1~2분). 받기 전엔 수급 점수는 중간값으로 계산해요.")
    if sec.empty:
        st.info("섹터로 묶을 종목이 부족해요.")
    else:
        lead = sec.iloc[0]
        k1, k2, k3, k4 = st.columns(4)
        _rmet(k1, "새싹 점수 1위", lead["sector"], f"점수 {lead['score']:.0f}")
        n_g = int((sec["kind"] == "🌱 덜 빠지고 먼저 오름").sum())
        _rmet(k2, "덜 빠지고 먼저 오른 섹터", f"{n_g}개", f"전체 {len(sec)}개 중")
        _rmet(k3, "저점 이후 신고가 종목", f"{int(sdf['nh'].sum())}개", "직전 250일 최고 종가 돌파")
        _rmet(k4, "지수보다 빨리 오른 종목", f"{(sdf['excess'] > 0).mean() * 100:.0f}%", f"{len(sdf)}종목 중")
        _rec_scatter(sec, idx_fall)
        st.caption("오른쪽 위 칸 = 지수보다 덜 빠지고 저점 이후 지수보다 빨리 오른 섹터(가장 먼저 싹트는 곳). "
                   "세로 점선 = 지수 낙폭, 가로 점선 = 지수와 같은 반등. 원 크기 = 종목 수.")
        show_all = st.toggle("모든 섹터 보기", value=False, key="rec_all")
        v = sec if show_all else sec.head(20)
        view = pd.DataFrame({
            "섹터": v["sector"], "새싹 점수": v["score"], "유형": v["kind"],
            "하락 구간": v["fall"], "저점 이후": v["reb"], "지수 대비": v["excess"], "하락 전 대비": v["vs_hi"],
            "오른 종목": v["breadth"], "20일선 위": v["above20"], "저점 후 신고가": v["n_nh"],
            "외+기 저점 후(억)": v["fi"], "외+기 사는 종목": v.apply(lambda r: f"{r['n_buy']}/{r['fi_n']}", axis=1),
            "종목 수": v["n"], "먼저 오른 종목": v["top"],
        })
        sign = lambda x: "" if pd.isna(x) or x == 0 else f"color: {UP if x > 0 else DOWN}; font-weight: 600"  # noqa: E731
        st.dataframe(
            view.style.format({"하락 구간": "{:+.1f}%", "저점 이후": "{:+.1f}%", "지수 대비": "{:+.1f}%p", "하락 전 대비": "{:+.1f}%",
                               "오른 종목": "{:.0f}%", "20일선 위": "{:.0f}%", "외+기 저점 후(억)": _eok_num}, na_rep="-")
            .map(sign, subset=["하락 구간", "저점 이후", "지수 대비", "하락 전 대비", "외+기 저점 후(억)"]),
            hide_index=True, height=min(620, 36 * (len(view) + 1) + 4),
            column_config={"섹터": st.column_config.Column(pinned=True),
                           "새싹 점수": st.column_config.ProgressColumn("새싹 점수", min_value=0, max_value=100, format="%.0f"),
                           "하락 전 대비": st.column_config.Column(help="지수 고점 날의 가격 대비 지금. 0% 이상이면 하락분을 다 되찾은 것"),
                           "오른 종목": st.column_config.Column(help="저점 이후 오른 종목 비율(확산)")})

    # ④ 종목: 누가 먼저 일어서나
    _sec_h("🌱 먼저 일어서는 종목", "지수 대비 반등 · 저점 이후 외국인+기관 · 신고가 근접")
    c1, c2, c3 = st.columns([2, 1, 1])
    opts = ["전체"] + (sec["sector"].tolist() if not sec.empty else [])
    pick = c1.selectbox("섹터", opts, key="rec_pick", label_visibility="collapsed")
    min_cap = c2.number_input("시총 최소(억)", 0, 100000, 3000, step=500, key="rec_cap")
    only_buy = c3.toggle("외+기 순매수만", value=False, key="rec_buy")
    t = sdf if pick == "전체" else sdf[sdf[by] == pick]
    t = t[(t["cap"].isna()) | (t["cap"] >= min_cap * 1e8)]
    if only_buy:
        t = t[t["fi"] > 0]
    t = t.sort_values("score", ascending=False).head(40)
    if t.empty:
        st.info("조건에 맞는 종목이 없어요.")
    else:
        view = pd.DataFrame({
            "종목": t["name"] + t["nh"].map(lambda x: " 🌱" if x else ""), "점수": t["score"], "섹터": t[by],
            "현재가": t["price"], "오늘": t["change"], "하락 구간": t["fall"], "저점 이후": t["reb"], "지수 대비": t["excess"],
            "하락 전 대비": t["vs_hi"], "신고가까지": t["to_high"], "외+기 저점 후(억)": t["fi"],
            "외국인 연속": t["streak_f"], "기관 연속": t["streak_i"],
        })
        sign = lambda x: "" if pd.isna(x) or x == 0 else f"color: {UP if x > 0 else DOWN}; font-weight: 600"  # noqa: E731
        st.dataframe(
            view.style.format({"점수": "{:.0f}", "현재가": "{:,.0f}", "오늘": "{:+.2f}%", "하락 구간": "{:+.1f}%",
                               "저점 이후": "{:+.1f}%", "지수 대비": "{:+.1f}%p", "하락 전 대비": "{:+.1f}%", "신고가까지": "{:+.1f}%",
                               "외+기 저점 후(억)": _eok_num, "외국인 연속": "{:+d}일", "기관 연속": "{:+d}일"}, na_rep="-")
            .map(sign, subset=["오늘", "하락 구간", "저점 이후", "지수 대비", "하락 전 대비", "외+기 저점 후(억)", "외국인 연속", "기관 연속"])
            .map(_to_high_color, subset=["신고가까지"]),
            hide_index=True, height=min(760, 36 * (len(view) + 1) + 4),
            column_config={"종목": st.column_config.Column(pinned=True),
                           "점수": st.column_config.ProgressColumn("점수", min_value=0, max_value=100, format="%.0f")})
        st.caption("🌱 = 저점 이후 직전 250거래일 최고 종가를 넘은 종목(가장 먼저 고개 든 종목). "
                   "외+기 저점 후 = 지수 저점 다음 날부터 오늘까지 외국인+기관 순매수 합(종목 수급은 최근 20거래일까지만 받아요).")
    with st.expander("ℹ️ 계산 기준"):
        st.markdown(
            f"- **저점** = 최근 {lb_lab} 중 {idx_name} 최저 종가, **고점** = 그 저점 전 최고 종가. 모든 섹터·종목을 이 두 날짜로 똑같이 잘라 비교해요.\n"
            "- **국면**: 낙폭 5% 미만 = 큰 하락 없음 · 저점이 2거래일 이내이고 20일선 아래 = 물살 빠지는 중 · "
            "반등 3% 미만(또는 20일선 아래·회복률 30% 미만) = 바닥 다지기 · 회복하다 20일선 아래로 쉬면 = 숨 고르기 · "
            "회복률 50%↑ & 60일선 위 = 회복 진행 · 나머지 = 새싹 돋는 중\n"
            "- **섹터 새싹 점수** = 섹터끼리 백분위 가중 평균: 지수 대비 반등 40 · 저점 후 외+기 순매수(시총 대비) 25 · "
            "하락 전 고점 대비 지금 20 · 오른 종목 비율 15. 수익률은 시총가중. (비중은 제가 정한 값)\n"
            "- **종목 점수** = 지수 대비 반등 40 · 외+기 순매수(시총 대비) 30 · 저점 후 신고가(만점) 또는 신고가까지 가까운 정도 30\n"
            "- 회복 초기엔 낙폭이 컸던 종목이 기술적 반등으로 위에 올 수 있어요. **유형**과 **신고가까지**를 같이 보고, "
            "매수 판단은 🎯 매수 후보의 원칙으로 해요.")


# ─────────────────────────── 🧭 시나리오 ───────────────────────────
SCN_COLOR = {1: "#1B8A4B", 2: "#C27C0E", 2.5: "#A87400", 3: "#1B6FD6", 4: "#C0262E", 0: "#51616C", None: "#51616C"}


def _index_ret(hist: dict, window: str) -> dict:
    out = {}
    for name, sym in data.MARKETS.items():
        h = hist.get(sym, (data.empty_frame(), None))[0]
        if h is None or h.empty or len(h) < 3:
            out[name] = None
            continue
        c = pd.Series(h["close"].to_numpy(dtype=float), index=pd.to_datetime(h["date"]).dt.normalize())
        last = c.index[-1]
        if window == "오늘":
            base = c.iloc[-2]
        elif window == "이번 주":
            before = c[c.index < last - pd.Timedelta(days=last.weekday())]
            base = before.iloc[-1] if len(before) else c.iloc[0]
        else:
            base = c.iloc[max(0, len(c) - 1 - int(window.replace("거래일", "")))]
        out[name] = (c.iloc[-1] / base - 1) * 100
    return out


def _scn_action(sid, hold: str, a: str, b: str) -> str:
    """보유 상태에 맞춘 행동. 기본(A 보유)은 효석 메모 그대로."""
    base = data.SCN_INFO[sid][2].replace("{A}", a).replace("{B}", b)
    if hold == "A 보유":
        return base
    if hold == "없음":
        return {1: f"{a} 쪽에서 매수 후보 원칙 통과 종목만", 2: f"{b} 쪽 원칙 통과 종목부터", 2.5: f"{b} 쪽 원칙 통과 종목 일부, {a} 쪽은 관찰",
                3: f"{a}·{b} 모두 원칙 통과 종목", 4: "신규 매수 쉬기(현금)", 0: "다음 신호 대기"}[sid]
    if hold == "B 보유":
        return {1: f"{b} 보유분은 5일선·50일선 점검(시간이 필요한 구간), {a} 원칙 통과 종목 검토",
                2: f"{b} 보유 유지(유리)", 2.5: f"{b} 보유 유지, {a} 흐름 확인", 3: f"{b} 유지(유리) + {a} 원칙 통과 종목",
                4: "현금 확대", 0: "다음 신호 대기"}[sid]
    return {1: f"{a} 유지, {b} 비중 점검", 2: f"{a} 일부 줄이고 {b} 늘리기", 2.5: f"{a} 급하게 줄이지 말고 {b} 흐름 확인",
            3: "둘 다 유지(유리)", 4: "현금 확대", 0: "다음 신호 대기"}[sid]


VERDICT_COLOR = {"섹터 확장": "#1B6FD6", "로테이션 확인": "#D9822B", "기존 리더 유지": "#1B8A4B",
                 "돈이 빠지는 중": "#C0262E", "확인 불가": "#8A979F"}
ZONES = [   # (x1, x2, y1, y2 — 기준 t의 배수, 'L'=끝), 이름, 색
    ("t", "L", "t", "L", "3 둘 다 감", "#1B6FD6"),
    ("-t", "t", "t", "L", "3쪽", "#6FA3E8"),
    ("-ct", "-t", "t", "L", "2↔3", "#E3B341"),
    ("-L", "-ct", "t", "L", "2 로테이션", "#D9822B"),
    ("t", "L", "-L", "t", "1 A만 감", "#1B8A4B"),
    ("-t", "t", "-L", "t", "관망", "#B9C4CC"),
    ("-L", "-t", "-L", "t", "4 현금", "#C0262E"),
]


def _quadrant_chart(ra: float, rb: float, t: float, crash: float, a_name: str, b_name: str, trail: pd.DataFrame):
    """가로 = A 수익률, 세로 = B 수익률. 칸 색 = 시나리오, 큰 점 = 지금, 선 = 최근 5거래일 누적 궤적."""
    vals = [abs(ra), abs(rb), crash * t * 1.3]
    if not trail.empty:
        vals += [trail["a"].abs().max(), trail["b"].abs().max()]
    L = max(vals) * 1.15
    num = {"t": t, "-t": -t, "ct": crash * t, "-ct": -crash * t, "L": L, "-L": -L}
    z = pd.DataFrame([{"x1": num[a], "x2": num[b], "y1": num[c], "y2": num[d], "zone": nm.replace("A", a_name),
                       "color": col, "cx": (num[a] + num[b]) / 2, "cy": (num[c] + num[d]) / 2}
                      for a, b, c, d, nm, col in ZONES])
    sx = alt.Scale(domain=[-L, L], nice=False)
    sy = alt.Scale(domain=[-L, L], nice=False)
    rects = alt.Chart(z).mark_rect(opacity=0.16).encode(
        x=alt.X("x1:Q", scale=sx, title=f"{a_name} 수익률(%) →"), x2="x2:Q",
        y=alt.Y("y1:Q", scale=sy, title=f"{b_name} 수익률(%) →"), y2="y2:Q",
        color=alt.Color("color:N", scale=None), tooltip=[alt.Tooltip("zone:N", title="시나리오")])
    labels = alt.Chart(z).mark_text(fontSize=12, fontWeight="bold", opacity=0.7).encode(
        x="cx:Q", y="cy:Q", text="zone:N", color=alt.Color("color:N", scale=None))
    zero = alt.Chart(pd.DataFrame({"v": [0]}))
    axes = zero.mark_rule(color="#8A979F", strokeDash=[3, 3]).encode(x="v:Q") + \
        zero.mark_rule(color="#8A979F", strokeDash=[3, 3]).encode(y="v:Q")
    layers = [rects, labels, axes]
    if not trail.empty:
        tr = trail.reset_index(drop=True).assign(i=lambda d: range(len(d)))
        layers.append(alt.Chart(tr).mark_line(color="#51616C", strokeWidth=1.5, point=alt.OverlayMarkDef(size=35, color="#51616C"))
                      .encode(x="a:Q", y="b:Q", order="i:Q",
                              tooltip=[alt.Tooltip("day:N", title="날짜"), alt.Tooltip("a:Q", title=a_name, format="+.2f"),
                                       alt.Tooltip("b:Q", title=b_name, format="+.2f")]))
        layers.append(alt.Chart(tr).mark_text(dy=-9, fontSize=10, color="#51616C").encode(x="a:Q", y="b:Q", text="day:N"))
    now = pd.DataFrame({"a": [ra], "b": [rb], "t": ["지금"]})
    layers.append(alt.Chart(now).mark_point(size=420, filled=True, color="#16212B", stroke="#FFFFFF", strokeWidth=2)
                  .encode(x="a:Q", y="b:Q", tooltip=[alt.Tooltip("a:Q", title=a_name, format="+.2f"),
                                                    alt.Tooltip("b:Q", title=b_name, format="+.2f")]))
    layers.append(alt.Chart(now).mark_text(dy=-18, fontSize=13, fontWeight="bold", color="#16212B")
                  .encode(x="a:Q", y="b:Q", text="t:N"))
    st.altair_chart(alt.layer(*layers).properties(height=380, width="container"))


def _daily_bars(A: dict, B: dict, a_name: str, b_name: str):
    d = pd.DataFrame({a_name: A["daily"], b_name: B["daily"]}).dropna(how="all")
    if d.empty:
        return
    d.index = [x.strftime("%m/%d") for x in d.index]
    long = d.reset_index(names="날짜").melt("날짜", var_name="바스켓", value_name="수익률")
    c = alt.Chart(long).mark_bar(cornerRadius=2).encode(
        x=alt.X("날짜:N", title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("바스켓:N", sort=[a_name, b_name]),
        y=alt.Y("수익률:Q", title="%"),
        color=alt.Color("바스켓:N", sort=[a_name, b_name], scale=alt.Scale(range=["#F2B134", "#5B47F5"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["날짜:N", "바스켓:N", alt.Tooltip("수익률:Q", format="+.2f")],
    ).properties(height=200, width="container")
    st.altair_chart(c)


def _tile(ok, label: str, value: str) -> str:
    cls = "ok" if ok is True else ("no" if ok is False else "na")
    ic = "✓" if ok is True else ("✕" if ok is False else "?")
    return (f'<div class="tile {cls}"><span class="t-ic">{ic}</span><div class="t-lb">{html.escape(label)}</div>'
            f'<div class="t-v">{html.escape(value)}</div></div>')


def render_money_confirm(df: pd.DataFrame, a_groups, b_groups, a_name: str, b_name: str, price_sid):
    c = data.rotation_confirm(df, a_groups, b_groups)
    title, _mean, act = c["verdict"]
    col = VERDICT_COLOR.get(title, "#8A979F")
    price_name = data.SCN_INFO[price_sid][0].replace("{A}", a_name).replace("{B}", b_name) if price_sid is not None else "-"
    price_map = {1: "기존 리더 유지", 2: "로테이션 확인", 2.5: "로테이션 확인", 3: "섹터 확장", 4: "돈이 빠지는 중"}
    mismatch = price_sid in price_map and price_map[price_sid] != title and title != "확인 불가"
    st.markdown(
        f'<div class="verdict" style="--vc:{col}"><div class="v-top"><span class="v-lab">돈의 방향</span>'
        f'<span class="v-price">가격 기준 {html.escape(price_name)}{" · ⚠ 돈과 다름" if mismatch else ""}</span></div>'
        f'<div class="v-title">{html.escape(title)}</div><div class="v-act">{html.escape(act.split(" · ")[0])}</div></div>',
        unsafe_allow_html=True)
    B, A = c["B"], c["A"]
    bp, bn = c["b_share"]
    b_tiles = [
        _tile(c["b_checks"][0][1], "거래대금", "-" if B["tv_ratio"] is None else f"{B['tv_ratio']:.2f}배"),
        _tile(c["b_checks"][1][1], "거래대금 비중", "-" if bn is None else f"{bp * 100:.1f}→{bn * 100:.1f}%"),
        _tile(c["b_checks"][2][1], "신규 돌파", f"{B['n_break']}개"),
        _tile(c["b_checks"][3][1], "RS 동반 상승", "-" if B["rs_up"] is None else f"{B['rs_up'] * 100:.0f}%"),
    ]
    n_def = len(c["defense"])
    a_tiles = [
        _tile(c["a_checks"][1][1], "리더 방어", f"{sum(x['ok'] for x in c['defense'])}/{n_def}" if n_def else "-"),
        _tile(c["a_checks"][0][1], "RS(1M) 중앙값", "-" if A["rs1m_med"] is None else f"{A['rs1m_med']:.0f}"),
    ]
    chips = []
    for x in c["defense"]:
        v20 = "" if x["vs20"] is None else f" {x['vs20']:+.0f}%"
        chips.append(f'<span class="lc {"ok" if x["ok"] else "no"}">{html.escape(x["name"])}{v20}</span>')
    st.markdown(
        f'<div class="tiles-wrap"><div><div class="tiles-h">{html.escape(b_name)} · 돈을 받아가나 <b>{c["b_ok"]}/4</b></div>'
        f'<div class="tiles">{"".join(b_tiles)}</div></div>'
        f'<div><div class="tiles-h">{html.escape(a_name)} · 리더가 버티나 <small>(20일선 대비)</small></div>'
        f'<div class="tiles t2">{"".join(a_tiles)}</div>'
        f'<div class="lead-chips">{"".join(chips)}</div></div></div>', unsafe_allow_html=True)


def render_money_radar(df: pd.DataFrame, leaders: set):
    st.markdown("#### 📡 섹터 간 돈의 이동")
    r, sm = tab_memo("money_radar", (recent_days, len(leaders)), lambda: data.sector_money_radar(df, leaders, {"fresh_days": recent_days}))
    if r.empty:
        st.caption("거래대금 데이터가 아직 부족해요.")
        return
    allr = sm["all_tv_ratio"]
    flow_line = (f'<span class="mv out">{html.escape(", ".join(sm["losers"]) or "—")}</span>'
                 f'<span class="mv-arrow">→</span>'
                 f'<span class="mv in">{html.escape(", ".join(sm["gainers"]) or "받는 쪽 없음")}</span>')
    total = "" if allr is None else (f'<span class="mv-total">보드 전체 거래대금 {allr:.2f}배 '
                                     f'{"▲ 새 돈" if allr >= 1.1 else ("▼ 이탈" if allr <= 0.9 else "· 비슷")}</span>')
    st.markdown(f'<div class="mv-row">{flow_line}{total}</div>', unsafe_allow_html=True)
    kind = r["state"].map(lambda x: "돈 들어옴" if x in ("💰 돈 들어오는 중", "🔥 리더 강화") else
                          ("돈 빠짐" if x in ("🧊 돈 빠지는 중", "📉 리더 약화(돈 빠짐)") else
                           ("주도 유지" if x == "👑 리더 유지" else "변화 없음")))
    d = r.assign(kind=kind, rsu=r["rs_up"].fillna(0) * 100,
                 label=r.apply(lambda x: ("👑 " if x["leading"] else "") + x["group"], axis=1))
    dom = ["돈 들어옴", "돈 빠짐", "주도 유지", "변화 없음"]
    base = alt.Chart(d).encode(
        x=alt.X("share_chg:Q", title="거래대금 비중 변화(%p) →"),
        y=alt.Y("rsu:Q", title="RS 동반 상승(%) →", scale=alt.Scale(domain=[0, 100])))
    pts = base.mark_circle(opacity=0.8, stroke="#FFFFFF", strokeWidth=1).encode(
        size=alt.Size("share_now:Q", legend=None, scale=alt.Scale(range=[40, 900])),
        color=alt.Color("kind:N", scale=alt.Scale(domain=dom, range=["#D6333B", "#1F66C9", "#F2B134", "#C9D2D8"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("group:N", title="섹터"), alt.Tooltip("state:N", title="상태"),
                 alt.Tooltip("share_prev:Q", title="이전 비중%", format=".2f"),
                 alt.Tooltip("share_now:Q", title="최근 비중%", format=".2f"),
                 alt.Tooltip("n_break:Q", title="신규 돌파"), alt.Tooltip("rsu:Q", title="RS 동반%", format=".0f")])
    txt = base.transform_filter(alt.datum.kind != "변화 없음").mark_text(dy=-12, fontSize=11, fontWeight="bold") \
        .encode(text="label:N", color=alt.Color("kind:N", scale=alt.Scale(domain=dom, range=["#B0242B", "#1A56A8", "#A87400", "#8A979F"]),
                                               legend=None))
    rules = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color="#8A979F", strokeDash=[3, 3]).encode(x="x:Q") + \
        alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(color="#8A979F", strokeDash=[3, 3]).encode(y="y:Q")
    st.altair_chart((rules + pts + txt).properties(height=380, width="container"))
    with st.expander("섹터별 숫자 보기"):
        view = pd.DataFrame({"상태": r["state"], "섹터": r["group"],
                             "비중": r.apply(lambda x: f"{x['share_prev']:.1f}→{x['share_now']:.1f}%", axis=1),
                             "변화(%p)": r["share_chg"], "거래대금": r["tv_ratio"], "신규 돌파": r["n_break"],
                             "RS 동반": r["rs_up"]})
        st.dataframe(view.style.format({"변화(%p)": "{:+.2f}", "거래대금": "{:.2f}배", "RS 동반": "{:.0%}"}, na_rep="-")
                     .map(lambda x: "" if pd.isna(x) or x == 0 else f"color: {UP if x > 0 else DOWN}", subset=["변화(%p)"]),
                     hide_index=True, column_config={"섹터": st.column_config.Column(pinned=True)})


def _mini_list(rows: list[str]) -> None:
    st.markdown('<div class="mini">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def _mini_row(name: str, right: str, sub: str) -> str:
    return (f'<div class="mini-row"><div><b>{html.escape(name)}</b><small>{sub}</small></div>'
            f'<span>{right}</span></div>')


def render_scenario(df: pd.DataFrame, histories: dict, trends, leaders: set, fins=None):
    groups_all = sorted(df["group"].dropna().unique().tolist())
    pr0 = data.SCENARIO_PRESETS["반도체 후공정 vs 전공정"]
    with st.expander("⚙️ 바스켓 · 기간 · 기준", expanded=False):
        preset = st.selectbox("프리셋", list(data.SCENARIO_PRESETS), key="scn_preset")
        pr = data.SCENARIO_PRESETS[preset]
        c1, c2 = st.columns(2)
        a_name = c1.text_input("A (지금 들고 있는/주도 쪽)", pr["a_name"], key="scn_an")
        b_name = c2.text_input("B (다음 후보 쪽)", pr["b_name"], key="scn_bn")
        a_groups = c1.multiselect("A 분류", groups_all, [g for g in pr["a_groups"] if g in groups_all], key="scn_ag")
        b_groups = c2.multiselect("B 분류", groups_all, [g for g in pr["b_groups"] if g in groups_all], key="scn_bg")
        c3, c4, c5, c6 = st.columns(4)
        window = c3.selectbox("기간", list(data.SCN_WINDOWS), index=1, key="scn_win")
        t = c4.number_input("간다/빠진다 기준(%)", 0.3, 15.0, data.SCN_WINDOWS[window], step=0.5, key=f"scn_t_{window}")
        crash = c5.number_input("과하게 빠짐(기준 ×)", 1.5, 5.0, 2.5, step=0.5, key="scn_crash")
        hold = c6.selectbox("지금 보유", ["A 보유", "B 보유", "둘 다", "없음"], key="scn_hold")
        st.caption(f"칸 = 시나리오 · 큰 점 = {window} 수익률(시총가중) · 선 = 최근 5거래일 누적 궤적. "
                   f"±{t:g}% 넘으면 간다/빠진다, A가 −{crash * t:g}% 아래면 과하게 빠짐. 행동은 돈의 방향 판정을 따라요.")
    if not a_groups or not b_groups:
        st.info("A·B 분류를 하나 이상씩 골라 주세요.")
        return
    A = data.basket_stats(df, histories, a_groups, window, trends)
    B = data.basket_stats(df, histories, b_groups, window, trends)
    cls = data.classify_scenario(A["ret_cap"], B["ret_cap"], t, crash)
    if cls["id"] is None:
        st.warning("바스켓 가격 데이터가 부족해요.")
        return

    render_money_confirm(df, a_groups, b_groups, a_name, b_name, cls["id"])

    idx = _index_ret(load_indexes(), window)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(a_name, _pct_txt(A["ret_cap"]), f"상승 {A['breadth'] or 0:.0f}%", delta_color="off")
    m2.metric(b_name, _pct_txt(B["ret_cap"]), f"상승 {B['breadth'] or 0:.0f}%", delta_color="off")
    m3.metric("코스피", _pct_txt(idx.get("코스피")))
    m4.metric("코스닥", _pct_txt(idx.get("코스닥")))

    ca, cb = st.columns([1.35, 1])
    with ca:
        a_d, b_d = A["daily"], B["daily"]
        days = [d for d in a_d.index if d in b_d.index]
        trail = pd.DataFrame({"day": [d.strftime("%m/%d") for d in days],
                              "a": ((1 + a_d.loc[days] / 100).cumprod() - 1).values * 100,
                              "b": ((1 + b_d.loc[days] / 100).cumprod() - 1).values * 100}) if days else pd.DataFrame()
        _quadrant_chart(A["ret_cap"], B["ret_cap"], t, crash, a_name, b_name, trail)
    with cb:
        st.markdown("**하루씩**")
        _daily_bars(A, B, a_name, b_name)
        paths = [p_ for p_ in data.scenario_paths(A["ret_cap"], B["ret_cap"], t, crash) if p_["dist"] > 1e-6][:2]
        if paths:
            st.markdown("**다음으로 가까운 시나리오**")
            rows = []
            for p_ in paths:
                nm = data.SCN_INFO[p_["id"]][0].replace("{A}", a_name).replace("{B}", b_name)
                mv = " · ".join(f"{lab} {x:+.1f}%p" for lab, x in ((a_name, p_["move_a"]), (b_name, p_["move_b"])) if abs(x) > 1e-6)
                rows.append(_mini_row(nm, "", mv))
            _mini_list(rows)

    m = data.momentum_engine(df, trends, fresh_days=recent_days, fins=fins, require_op=fins is not None)
    cA, cB = st.columns(2)
    with cA:
        st.markdown(f"**{a_name} 대장 · 끌고 가기**")
        rows = []
        for r in data.sector_leaders(df, a_groups).itertuples():
            g = r.ma5_vs_50
            sig = "-" if pd.isna(g) else ("🔴 정리" if g <= 0 else ("🟡 근접" if g < 3 else "🟢 보유"))
            rows.append(_mini_row(r.name, sig, f"{html.escape(r.group)} · {_bo_full(r)}"))
        if rows:
            _mini_list(rows)
        else:
            st.caption("대장주 없음")
    with cB:
        st.markdown(f"**{b_name} 담을 후보**")
        if fins is None:
            st.caption("영업이익 확인 중…")
        else:
            rows = []
            for r in m[m["group"].isin(b_groups)].sort_values("mom", ascending=False).head(5).itertuples():
                th = "-" if pd.isna(r.to_high) else f"{r.to_high:+.1f}%"
                rows.append(_mini_row(r.name + (" 👑" if (r.lead_rank == 1 and r.lead_ok) else ""),
                                      f"신고가까지 {th}", f"{_bo_full(r)} · {r.op_txt}"))
            if rows:
                _mini_list(rows)
            else:
                st.caption("조건에 맞는 종목 없음")
    st.divider()
    render_money_radar(df, leaders)


def _hold_text(r) -> str:
    """원칙: 3R 절반 익절 뒤 나머지는 일봉 5일선이 50일선에 닿으면 정리. 그 전까지 끌고 간다."""
    g = r.get("ma5_vs_50")
    if g is None or pd.isna(g):
        return "-"
    if g <= 0:
        return f"🔴 5일선이 50일선 닿음({g:+.1f}%) — 나머지 정리 신호"
    if g < 3:
        return f"🟡 5일선이 50일선 +{g:.1f}% — 가까워짐"
    return f"🟢 끌고 가기(5일선 50일선 +{g:.1f}%)"


def _hold_short(r) -> str:
    g = r.get("ma5_vs_50")
    if g is None or pd.isna(g):
        return "-"
    return f"🔴 정리 신호 {g:+.1f}%" if g <= 0 else (f"🟡 근접 {g:+.1f}%" if g < 3 else f"🟢 보유 {g:+.1f}%")


def render_leaders(df: pd.DataFrame, leaders: set):
    """주도섹터마다 대장주 1위와 끌고 가기 점검(5일선 vs 50일선)."""
    if not leaders:
        return
    lead = data.sector_leaders(df, leaders)
    if lead.empty:
        return
    _sec_h("👑 주도섹터 대장주 · 끌고 가기", "5일선이 50일선에 닿으면 나머지 정리")
    view = pd.DataFrame({
        "대장주": lead["name"], "섹터": lead["group"], "현재가": lead["price"], "등락률": lead["change"],
        "신고가까지": lead["to_high"], "돌파": lead.apply(_bo_full, axis=1), "RS": lead["rs"],
        "끌고 가기": lead.apply(_hold_short, axis=1),
    })
    st.dataframe(
        view.style.format({"현재가": "{:,.0f}", "등락률": "{:+.2f}%", "신고가까지": "{:+.1f}%", "RS": "{:.0f}"}, na_rep="-")
        .map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}", subset=["등락률"])
        .map(_bo_color, subset=["돌파"]).map(_to_high_color, subset=["신고가까지"]),
        hide_index=True, height=min(360, 36 * (len(view) + 1) + 4),
        column_config={"대장주": st.column_config.Column(pinned=True), "돌파": st.column_config.Column(help=BO_FULL_HELP)},
    )


PB_TIER = {"지금 타점": ("#1B8A4B", "✅"), "반등 대기": ("#A87400", "⏳"), "손익비 2~3": ("#51616C", "△"),
           "시장 대기": ("#C0262E", "⛔")}


def _pb_fig(code: str, quote: dict | None, r: dict):
    """눌림 후보 일봉 6개월 + 20·50일선 + 지지·손절·전고점·3R 선."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    bars = load_chart(code, "일봉")
    if bars is None or bars.empty:
        return None
    b = data.chart_with_live(bars, quote, "일봉").copy().reset_index(drop=True)
    for k in (20, 50):
        b[f"ma{k}"] = b["close"].rolling(k).mean()
    b = b.tail(125)
    x = pd.to_datetime(b["date"]).dt.strftime("%Y-%m-%d")
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.78, 0.22])
    fig.add_trace(go.Candlestick(x=x, open=b["open"], high=b["high"], low=b["low"], close=b["close"], name="일봉",
                                 increasing=dict(line=dict(color=UP), fillcolor=UP),
                                 decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN), showlegend=False), 1, 1)
    for k, col in ((20, "#F59E0B"), (50, "#16A34A")):
        fig.add_trace(go.Scatter(x=x, y=b[f"ma{k}"], mode="lines", line=dict(width=1.4, color=col), name=f"{k}일선"), 1, 1)
    vc = [UP if c_ >= o_ else DOWN for c_, o_ in zip(b["close"], b["open"])]
    fig.add_trace(go.Bar(x=x, y=b["volume"], marker_color=vc, opacity=.45, showlegend=False, name="거래량"), 2, 1)
    for y, lab, col, dash in ((r["pb_hi"], "전고점(1차 목표)", "#0F7B45", "dash"), (r["pb_t3"], "3R", "#7C3AED", "dot"),
                              (r["pb_sup"], f"지지 · {r['pb_support']}", "#64748B", "dot"),
                              (r["pb_stop"], "손절", "#DC2626", "dash")):
        fig.add_hline(y=y, line=dict(color=col, width=1.2, dash=dash), row=1, col=1,
                      annotation_text=f"{lab} {y:,.0f}", annotation_position="top right" if lab == "3R" else "top left",
                      annotation_font=dict(size=11, color=col))
    fig.update_layout(height=460, margin=dict(l=8, r=8, t=10, b=8), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.02, x=1, xanchor="right"), dragmode="pan")
    fig.update_xaxes(type="category", nticks=8)
    try:
        fig = theme.style_fig(fig)
    except Exception:
        pass
    return fig


PB_SIM_STARTS = {"하반기 첫날(7/1)": (7, 1), "올해 첫 거래일(1/2)": (1, 2), "4월 1일": (4, 1), "직접 고르기": None}
_PB_SIM_MEMO: dict = _memo_store().setdefault("pb_sim", {})      # (속도) 새로고침해도 남는 기억


def _pb_sim_opts() -> dict:
    ss, y = st.session_state, data.now_kst().year
    pick = ss.get("pbs_start", "하반기 첫날(7/1)")
    md = PB_SIM_STARTS.get(pick)
    start = date(y, *md) if md else ss.get("pbs_date", date(y, 7, 1))
    return {"start": start, "seed": int(ss.get("pbs_seed", 10_000_000)), "risk": float(ss.get("pbs_risk", 1.5)),
            "max_pos": int(ss.get("pbs_max", 8)), "rr": float(ss.get("pbs_rr", 3.0)),
            "narr_only": bool(ss.get("pbs_narr", True)), "mkt": bool(ss.get("pbs_mkt", True))}


def _pb_sim_run(df, histories, quotes, p, fins_all):
    """눌림 타점 원칙대로 시작일부터 하루씩 사고판 1천만 원 모의 계좌."""
    if not fins_all:
        return None, "실적(영업이익) 자료를 받는 중이에요. 영업이익이 늘어난 종목만 사기 때문에 잠시 뒤 계좌가 계산돼요."
    o = _pb_sim_opts()
    story = {x["code"]: x for x in STOCKS}
    meta = {}
    for code in df.loc[df["code"].map(data.is_kr), "code"].unique():
        if o["narr_only"] and code not in story:
            continue
        g = data.op_growth(fins_all.get(code))
        if g.get("op_up") is not True:
            continue
        x = story.get(code) or {}
        meta[code] = {"op_txt": g["op_txt"], "op_g": g.get("op_g"),
                      "narr": " · ".join(t for t in [x.get("group"), x.get("desc")] if t) or "공부 자료 밖 종목"}
    if not meta:
        return None, "조건(영업이익 증가 + 공부 자료 종목)을 통과한 종목이 아직 없어요."
    now = data.now_kst()
    pk = tuple(sorted((k, v) for k, v in p.items() if isinstance(v, (int, float))))
    key = (now.date(), now.hour * 12 + now.minute // 5, len(histories), len(meta), tuple(sorted(o.items())), pk)
    res = _PB_SIM_MEMO.get(key)
    if res is None:
        ih = load_indexes()
        idx = {sym: (ih.get(sym) or (None,))[0] for sym in data.MARKETS.values()}
        with st.spinner("1천만 원 모의 계좌로 시작일부터 하루씩 눌림 타점을 매매해 보는 중…"):
            res = data.pb_sim(df, histories, quotes, o["start"], meta, idx, p,
                              {"seed": o["seed"], "risk_pct": o["risk"], "max_pos": o["max_pos"], "rr_min": o["rr"],
                               "market_filter": o["mkt"]})
        _PB_SIM_MEMO.clear()
        _PB_SIM_MEMO[key] = res
    return (res, None) if res.get("ok") else (None, "시작일 이후 일봉이 부족해요.")


def _won_c(v):
    return "" if v is None or pd.isna(v) else f"color:{UP if v > 0 else DOWN if v < 0 else '#26343E'}"


def _pb_sim_view(sim: dict | None, msg: str | None):
    o = _pb_sim_opts()
    with st.expander("⚙️ 모의 계좌 설정", expanded=False):
        c1, c2, c3, c4, c5 = st.columns(5)
        pick = c1.selectbox("시작일", list(PB_SIM_STARTS), key="pbs_start")
        if PB_SIM_STARTS[pick] is None:
            c1.date_input("직접 고른 시작일", value=o["start"], min_value=date(data.now_kst().year - 1, 1, 2),
                          max_value=data.now_kst().date(), key="pbs_date")
        c2.number_input("시드(원)", 1_000_000, 1_000_000_000, 10_000_000, 1_000_000, key="pbs_seed")
        c3.number_input("1회 위험(계좌 %)", 0.5, 3.0, 1.5, 0.5, key="pbs_risk", help="내 원칙 1.5%")
        c4.number_input("동시 보유 최대", 3, 15, 8, 1, key="pbs_max", help="내 원칙 최대 8종목")
        c5.number_input("진입 손익비 최소", 2.0, 6.0, 3.0, 0.5, key="pbs_rr", help="전고점까지 손익비. 3 = 3R 원칙")
        c6, c7 = st.columns(2)
        c6.toggle("공부 자료·리포트 종목만", value=True, key="pbs_narr",
                  help="끄면 영업이익이 늘어난 보드 전 종목에서 골라요.")
        c7.toggle("시장 60일선 아래면 신규 매수 쉬기", value=True, key="pbs_mkt", help="원칙: 코스피·코스닥 60일선 이하면 쉼")
        st.caption("눌림 판정 기준(RS·눌림 폭·지지선 거리·거래대금·시총)은 위 ⚙️ 기준 칸 값을 그대로 써요.")
    if not sim:
        st.info(msg or "계산할 수 있는 종목이 없어요.")
        return
    col = UP if sim["pnl"] > 0 else DOWN if sim["pnl"] < 0 else "#26343E"
    kr = sim.get("kospi_ret")
    k = st.columns(4)
    k[0].markdown(f'<div class="pf-k">평가금액</div><div class="pf-v">{sim["equity"]:,.0f}원</div>'
                  f'<div class="pf-s">시드 <b>{sim["seed"]:,.0f}원</b> · {sim["start"]:%Y.%m.%d}~{sim["end"]:%m.%d}</div>',
                  unsafe_allow_html=True)
    k[1].markdown(f'<div class="pf-k">수익금 · 수익률</div><div class="pf-v" style="color:{col}">{sim["pnl"]:+,.0f}원</div>'
                  f'<div class="pf-s">수익률 <b style="color:{col}">{sim["ret"]:+.2f}%</b>'
                  + (f' · 같은 기간 코스피 <b>{kr:+.2f}%</b>' if kr is not None else "") + "</div>", unsafe_allow_html=True)
    wr = f'{sim["wins"] / sim["closed"] * 100:.0f}%' if sim["closed"] else "-"
    ar = f'{sim["avg_r"]:+.2f}R' if sim.get("avg_r") is not None else "-"
    k[2].markdown(f'<div class="pf-k">승률 · 평균 R</div><div class="pf-v">{wr}</div>'
                  f'<div class="pf-s">끝난 매매 <b>{sim["closed"]}건</b> · 평균 <b>{ar}</b> · '
                  f'손익비(PF) <b>{sim["pf"]:.2f}</b></div>' if sim.get("pf") else
                  f'<div class="pf-k">승률 · 평균 R</div><div class="pf-v">{wr}</div>'
                  f'<div class="pf-s">끝난 매매 <b>{sim["closed"]}건</b> · 평균 <b>{ar}</b></div>', unsafe_allow_html=True)
    stock_v = sim["equity"] - sim["cash"]
    k[3].markdown(f'<div class="pf-k">실현손익 · 최대 낙폭</div><div class="pf-v">{sim["realized"]:+,.0f}원</div>'
                  f'<div class="pf-s">MDD <b>{sim["mdd"]:.1f}%</b> · 주식 {stock_v / sim["equity"] * 100:.0f}% · '
                  f'현금 {sim["cash"]:,.0f}원</div>', unsafe_allow_html=True)
    if sim.get("market_known") and sim.get("market_off_days"):
        st.caption(f"⛔ 기간 중 {sim['market_off_days']}거래일은 코스피·코스닥 중 60일선 아래가 있어 신규 매수를 쉬었어요(보유 종목 관리는 계속).")

    cv = sim["curve"]
    long = [cv.assign(구분="눌림 모의 계좌", 값=cv["equity"])]
    if "kospi" in cv:
        long.append(cv.assign(구분="코스피(같은 돈)", 값=cv["kospi"]))
    long = pd.concat(long).rename(columns={"date": "날짜"})
    ch = (alt.Chart(long).mark_line(interpolate="monotone").encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m.%d", labelAngle=0, grid=False)),
        y=alt.Y("값:Q", title="원", scale=alt.Scale(zero=False), axis=alt.Axis(format="~s")),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["눌림 모의 계좌", "코스피(같은 돈)"], range=["#5B47F5", "#B9B4D8"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("값:Q", format=",.0f")])
        .properties(height=260, width="container"))
    st.altair_chart(ch)

    t1, t2, t3 = st.tabs([f"📒 매매 일지 {len(sim['journal'])}", f"🧾 매매 내역 {len(sim['trades'])}",
                          f"보유 종목 {len(sim['hold'])}"])
    with t1:
        if sim["journal"]:
            jt = pd.DataFrame([{
                "상태": "🟢 보유" if j["status"] == "보유" else ("✅ 수익" if j["pnl"] > 0 else "❌ 손실"),
                "종목": f"{j['name']} · {j['code']}", "진입일": f"{j['entry_date']:%m/%d}", "매수가": j["entry_px"],
                "수량": j["qty"], "비중": j["weight"], "손절가": j["stop0"], "손절폭": j["stop_pct"], "3R": j["t3"],
                "진입 근거": f"{j['support']} 지지 · -{j['depth']:.1f}% {j['pb_days']}일 · 손익비 {j['rr']:.1f} · RS {j['rs']:.0f}"
                             + (f" · 눌림 거래량 {j['dry']:.2f}배" if j.get("dry") == j.get("dry") and j.get("dry") is not None else ""),
                "청산일": f"{j['exit_date']:%m/%d}" if j.get("exit_date") is not None else "-",
                "청산가": j.get("exit_px") if j["status"] == "청산" else j.get("cur_px"),
                "청산 이유": j.get("exit_why") or ("보유 중 · 손절가 " + f"{j.get('cur_stop', 0):,.0f}"
                                                   + (" · 절반 익절 완료" if j.get("half") else "")),
                "보유일": j.get("days"), "수익률": j.get("ret"), "R": j.get("r"), "손익": j.get("pnl"),
                "펀더멘털": j.get("op_txt", ""), "내러티브": j.get("narr", "")} for j in sim["journal"]])
            sty = jt.style.format({"매수가": "{:,.0f}", "수량": "{:,.0f}", "비중": "{:.1f}%", "손절가": "{:,.0f}",
                                   "손절폭": "-{:.1f}%", "3R": "{:,.0f}", "청산가": "{:,.0f}", "보유일": "{:.0f}일",
                                   "수익률": "{:+.2f}%", "R": "{:+.2f}R", "손익": "{:+,.0f}"}, na_rep="-") \
                .map(_won_c, subset=["수익률", "R", "손익"])
            st.dataframe(sty, hide_index=True, width="stretch", height=420,
                         column_config={"종목": st.column_config.Column(pinned=True)})
            st.download_button("📥 매매 일지 CSV", jt.to_csv(index=False).encode("utf-8-sig"),
                               f"눌림_모의_매매일지_{sim['start']:%Y%m%d}.csv", "text/csv", key="pbs_dl_j")
        else:
            st.caption("아직 매매가 없어요 — 시작일 이후 조건을 모두 통과한 눌림 자리가 없었어요.")
    with t2:
        if sim["trades"]:
            tr = pd.DataFrame([{"날짜": f"{x['date']:%Y-%m-%d}", "종목": x["name"], "구분": x["side"], "이유": x["why"],
                                "가격": x["price"], "수량": x["qty"], "금액": x["amount"], "실현손익": x["pnl"], "R": x["r"]}
                               for x in sim["trades"]])
            sty = tr.style.format({"가격": "{:,.0f}", "수량": "{:,.0f}", "금액": "{:,.0f}", "실현손익": "{:+,.0f}",
                                   "R": "{:+.2f}R"}, na_rep="") \
                .map(_won_c, subset=["실현손익", "R"]).map(lambda v: f"color:{UP if v == '매수' else DOWN}", subset=["구분"])
            st.dataframe(sty, hide_index=True, width="stretch", height=420)
            st.download_button("📥 매매 내역 CSV", tr.to_csv(index=False).encode("utf-8-sig"),
                               f"눌림_모의_매매내역_{sim['start']:%Y%m%d}.csv", "text/csv", key="pbs_dl_t")
        else:
            st.caption("아직 매매가 없어요.")
    with t3:
        if sim["hold"]:
            tb = pd.DataFrame([{"종목": f"{x['name']} · {x['code']}", "편입": f"{x['opened']:%m/%d}", "수량": x["qty"],
                                "매수가": x["avg"], "현재가": x["price"], "수익률": x["ret"], "평가손익": x["pnl"],
                                "지금 손절가": x["stop"], "3R": x["t3"], "3R 절반 익절": "완료" if x["half"] else "-",
                                "평가금액": x["value"], "비중": x["value"] / sim["equity"] * 100} for x in sim["hold"]])
            st.dataframe(tb.style.format({"수량": "{:,.0f}", "매수가": "{:,.0f}", "현재가": "{:,.0f}", "수익률": "{:+.2f}%",
                                          "평가손익": "{:+,.0f}", "지금 손절가": "{:,.0f}", "3R": "{:,.0f}",
                                          "평가금액": "{:,.0f}", "비중": "{:.1f}%"}).map(_won_c, subset=["수익률", "평가손익"]),
                         hide_index=True, width="stretch")
        else:
            st.caption("지금은 전부 현금이에요 — 조건이 모두 맞는 눌림 자리를 기다리는 중.")
    st.caption(
        f"🤖 규칙 — **진입**: 그날까지의 일봉만 보고 눌림 자리(추세·눌림 폭·지지선) + 그날 기준 RS ≥ 기준 + 손익비 ≥ {o['rr']:.1f} "
        "+ 반등 캔들(양봉·고가권 마감 또는 전일 고가 돌파)이면 **그날 종가 매수**, 같은 날 여럿이면 손익비·눌림 거래량·RS·영업이익 순. "
        f"**금액**: 계좌 × {o['risk']:.1f}% ÷ (매수가 − 손절가), 한 종목 최대 19% · 최대 {o['max_pos']}종목. "
        "**청산**: 손절가(지지선·눌림 저점 − 0.5 ATR) 터치면 전부(갭 하락은 시가) · 3R 도달 절반 익절 후 손절 본절 · "
        "+2R 본절, +4R → +1R, +5R → +2R … 손절가 끌어올림 · 나머지는 5일선이 50일선에 닿으면 종가 정리. "
        f"수수료 0.015%·거래세 0.20% 반영 · 시총 3,000억↑ · 조건 통과 {sim['universe']}종목.\n\n"
        "⚠️ 영업이익·공부 자료는 '지금' 기준이라 과거엔 몰랐던 정보가 섞여요(실제보다 좋게 나올 수 있음). 같은 날 손절·3R이 "
        "둘 다 닿으면 손절로 봐요(보수적). 오늘 칸은 장중이면 종가에 바뀔 수 있어요. 모의 결과이고 매수 권유가 아니에요.")


def render_pullback(df: pd.DataFrame, histories: dict, quotes: dict, fins_all=None):
    """신고가 돌파가 아니라, 모멘텀·실적 좋은 종목이 지지선까지 쉬어 갈 때의 손익비 좋은 자리."""
    _sec_h("🪃 눌림 타점", "모멘텀(RS)·실적(올해 영업이익↑) 종목이 지지선까지 쉬어 갈 때 — 거래량·수급·차트로 손익비 좋은 자리")
    d = data.PB_DEFAULTS
    with st.expander("⚙️ 기준 · 계좌 금액"):
        st.caption("추세: 60일선 > 120일선 · 60일선 상승 · 20일선 > 60일선  |  눌림: 최근 20일 고점에서 2일 이상, 직전 상승의 절반 이내로 되돌림  |  "
                   "지지: 10·20·50일선·돌파 자리 중 지금가 바로 아래  |  손절: 지지선·눌림 저점 아래 0.5 ATR  |  1차 목표: 전고점")
        c1, c2, c3, c4 = st.columns(4)
        rs_min = c1.number_input("RS 최소", 50, 99, d["rs_min"], key="pb_rs", help="원칙: RS 70 이상")
        rr_min = c2.number_input("손익비 최소", 1.0, 6.0, d["rr_min"], step=0.5, key="pb_rr",
                                 help="(전고점 − 지금가) ÷ (지금가 − 손절가). 3 이상이면 3R 원칙과 맞아요")
        dmin, dmax = c3.slider("고점 대비 눌림(%)", 1.0, 40.0, (d["depth_min"], d["depth_max"]), step=1.0, key="pb_depth")
        near = c4.number_input("지지선과 거리(ATR 배)", 0.3, 3.0, d["near_atr"], step=0.1, key="pb_near",
                               help="지금가가 지지선 위 이 거리 안에 있어야 '지지 근처'")
        c5, c6, c7 = st.columns(3)
        min_tv = c5.number_input("20일 평균 거래대금 최소(억)", 0.0, 2000.0, d["min_tv"] / 1e8, step=10.0, key="pb_tv")
        min_cap = c6.number_input("최소 시가총액(억)", 0.0, 100000.0, d["min_cap"], step=500.0, key="pb_cap")
        equity = c7.number_input("계좌 평가금액(원, 수량 계산용)", 0, 10_000_000_000,
                                 int(st.session_state.get("buy_equity") or 0), step=1_000_000, key="pb_equity")
        need_op = st.toggle("올해(E) 영업이익이 늘어난 종목만", value=True, key="pb_op",
                            help="원칙: 강해도 영업이익이 늘지 않으면 배제")
    p = {**d, "rs_min": int(rs_min), "rr_min": float(rr_min), "depth_min": float(dmin), "depth_max": float(dmax),
         "near_atr": float(near), "min_tv": float(min_tv) * 1e8, "min_cap": float(min_cap)}
    pb_view = st.segmented_control("보기", ["🔎 오늘 타점", "🤖 1천만원 모의 매매"], default="🔎 오늘 타점",
                                   key="pb_view", label_visibility="collapsed") or "🔎 오늘 타점"
    if pb_view == "🤖 1천만원 모의 매매":
        sim, msg = _pb_sim_run(df, histories, quotes, p, fins_all)
        _pb_sim_view(sim, msg)
        return

    hist = load_indexes()
    sigs, oks = [], []
    for name, sym in data.MARKETS.items():
        idf = hist.get(sym, (data.empty_frame(), None))[0]
        s20, s60 = data.ma_signal(idf, 20), data.ma_signal(idf, 60)
        a60 = None if not s60 else s60.get("above")
        oks.append(a60)
        sigs.append((name, s20, s60))
    market_ok = None if None in oks else all(oks)
    idx_rs = {name: data.index_rs(hist.get(sym, (data.empty_frame(), None))[0], df).get("rs")
              for name, sym in data.MARKETS.items()}

    with st.spinner("눌림 자리 찾는 중…"):
        pre = tab_memo("pb_screen", (tuple(sorted((k, v) for k, v in p.items() if isinstance(v, (int, float, str, bool)))), len(histories)), lambda: data.pullback_screen(df, histories, quotes, p))
    fins, trends = {}, {}
    if not pre.empty and "pb_rr" in pre:
        codes = tuple(pre["code"])
        with st.spinner(f"후보 {len(codes)}종목 실적·수급 확인 중…"):
            fins = data.fetch_financials_many(codes)
            trends = data.fetch_stock_trends(codes)
    rows = []
    ft = data._flow_table(trends) if trends else pd.DataFrame(columns=["code"])
    ft = ft.set_index("code") if not ft.empty else ft
    for r in (pre.to_dict("records") if "pb_rr" in pre else []):
        og = data.op_growth(fins.get(r["code"]))
        if need_op and og.get("op_up") is not True:
            continue
        f5 = ft["f5"].get(r["code"]) if "f5" in ft else np.nan
        i5 = ft["i5"].get(r["code"]) if "i5" in ft else np.nan
        f20 = ft["f20"].get(r["code"]) if "f20" in ft else np.nan
        i20 = ft["i20"].get(r["code"]) if "i20" in ft else np.nan
        fi5 = np.nansum([f5, i5]) if not (pd.isna(f5) and pd.isna(i5)) else np.nan
        fi20 = np.nansum([f20, i20]) if not (pd.isna(f20) and pd.isna(i20)) else np.nan
        flow_ok = None if pd.isna(fi20) else bool(fi20 >= 0 or (not pd.isna(fi5) and fi5 > 0))
        dry_ok = bool(r["pb_dry"] == r["pb_dry"] and r["pb_dry"] <= 0.9)
        rs_ok = all(v is None or (r.get("rs") or 0) > v for v in idx_rs.values())
        if market_ok is False:
            tier = "시장 대기"
        elif r["pb_rr"] >= 3 and r["pb_bounce"] and flow_ok is not False:
            tier = "지금 타점"
        elif r["pb_rr"] >= 3:
            tier = "반등 대기"
        else:
            tier = "손익비 2~3"
        score = (min(r["pb_rr"], 6) / 6 * 40 + (15 if dry_ok else 0) + (15 if flow_ok else 0)
                 + (10 if r["pb_bounce"] else 0) + (r.get("rs") or 0) / 99 * 10
                 + (10 if (r.get("pb_ud") or 0) >= 1.2 else 0))
        stop_pct = r["pb_stop_pct"]
        shares = amount = None
        if equity:
            risk_won = equity * 1.5 / 100
            shares = int(min(risk_won // (r["price"] * stop_pct / 100), equity * 0.19 // r["price"]))
            amount = shares * r["price"]
        rows.append({**r, "tier": tier, "score": score, "og": og, "fi5": fi5, "fi20": fi20, "f5": f5, "i5": i5,
                     "flow_ok": flow_ok, "dry_ok": dry_ok, "rs_ok": rs_ok, "shares": shares, "amount": amount})
    res = pd.DataFrame(rows)
    order = {k: i for i, k in enumerate(PB_TIER)}
    if not res.empty:
        res = res.assign(_o=res["tier"].map(order)).sort_values(["_o", "score"], ascending=[True, False]).drop(columns="_o")
    cnt = res["tier"].value_counts() if not res.empty else pd.Series(dtype=int)

    def _sg(s):
        return "?" if not s else {"G": "🟢", "Y": "🟡", "R": "🔴"}.get(s.get("code"), "?")
    mk_txt = " · ".join(f"{n} 20{_sg(s20)} 60{_sg(s60)}" for n, s20, s60 in sigs)
    tiles = [_tile(market_ok, "시장 20·60일선", mk_txt),
             _tile(True if cnt.get("지금 타점", 0) else None, "✅ 지금 타점", f"{cnt.get('지금 타점', 0)}"),
             _tile(None, "⏳ 반등 대기", f"{cnt.get('반등 대기', 0)}"),
             _tile(None, "△ 손익비 2~3", f"{cnt.get('손익비 2~3', 0)}")]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    if market_ok is False:
        st.error("⛔ 코스피·코스닥 중 60일선 아래가 있어요. 원칙상 신규 매수는 쉬는 구간 — 아래는 공부·대기용이에요.")
    if res.empty:
        st.caption("지금은 조건에 맞는 눌림 자리가 없어요. ⚙️ 기준에서 손익비·눌림 폭을 조금 풀어 볼 수 있어요.")
        return

    view = pd.DataFrame([{
        "상태": f"{PB_TIER[x['tier']][1]} {x['tier']}", "종목": x["name"], "분류": x.get("group"),
        "현재가": x["price"], "손익비": x["pb_rr"], "지지": f"{x['pb_support']} {x['pb_sup']:,.0f}",
        "손절가": x["pb_stop"], "손절폭": x["pb_stop_pct"], "1차 목표(전고점)": x["pb_hi"],
        "목표까지": (x["pb_hi"] / x["price"] - 1) * 100, "3R": x["pb_t3"],
        "눌림": f"-{x['pb_depth']:.1f}% · {x['pb_days']}일", "거래량(눌림/평균)": x["pb_dry"],
        "외+기 5일(억)": x["fi5"], "외+기 20일(억)": x["fi20"], "RS": x.get("rs"),
        "영업이익": x["og"].get("op_txt") or "-", "반등": "✓ 반등" if x["pb_bounce"] else f"대기 · {x['pb_trigger']:,.0f} 넘으면",
        **({"수량": x["shares"], "매수금액": x["amount"]} if equity else {}),
    } for x in res.to_dict("records")])
    fmt = {"현재가": "{:,.0f}", "손익비": "{:.1f} : 1", "손절가": "{:,.0f}", "손절폭": "-{:.1f}%",
           "1차 목표(전고점)": "{:,.0f}", "목표까지": "{:+.1f}%", "3R": "{:,.0f}", "거래량(눌림/평균)": "{:.2f}배",
           "외+기 5일(억)": _eok_num, "외+기 20일(억)": _eok_num, "RS": "{:.0f}", "수량": "{:,.0f}주", "매수금액": "{:,.0f}"}

    def _c_rr(v):
        return f"color:{'#1B8A4B' if v >= 3 else '#A87400'};font-weight:700" if v == v else ""

    def _c_dry(v):
        return "color:#1B8A4B" if v == v and v <= 0.9 else ("color:#C0262E" if v == v and v >= 1.3 else "")

    def _c_flow(v):
        return "" if v is None or v != v else f"color:{UP if v > 0 else DOWN}"
    sty = (view.style.format({k: v for k, v in fmt.items() if k in view.columns}, na_rep="-")
           .map(_c_rr, subset=["손익비"]).map(_c_dry, subset=["거래량(눌림/평균)"])
           .map(_c_flow, subset=["외+기 5일(억)", "외+기 20일(억)"]))
    st.dataframe(sty, hide_index=True, width="stretch", column_config={"종목": st.column_config.Column(pinned=True)})
    st.caption("✅ 지금 타점 = 손익비 3 이상 + 오늘 반등 캔들(양봉·고가권 마감 또는 전일 고가 돌파) + 수급 이탈 아님 · "
               "⏳ 반등 대기 = 손익비 3 이상, 아직 반등 전(표의 가격을 넘으면 진입 검토) · "
               "거래량(눌림/평균) 0.9배 이하 = 쉬는 동안 매물이 적음(좋음), 1.3배 이상 = 파는 힘 큼(주의). "
               "손절은 지지선 아래라 신고가 매매(8%)보다 좁아요 — 같은 1.5% 위험이면 수량이 커지니 비중 상한(19%)도 같이 봐요.")

    st.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)
    names = [f"{PB_TIER[x['tier']][1]} {x['name']} · 손익비 {x['pb_rr']:.1f}" for x in res.to_dict("records")]
    pick = st.selectbox("차트로 확인", range(len(names)), format_func=lambda i: names[i], key="pb_pick")
    x = res.iloc[int(pick)].to_dict()
    checks = [
        _tile(market_ok, "시장 60일선", "위" if market_ok else ("아래" if market_ok is False else "?")),
        _tile(x["pb_rr"] >= 3, "손익비", f"{x['pb_rr']:.1f} : 1"),
        _tile(x["dry_ok"], "눌림 거래량", f"평균의 {x['pb_dry']:.2f}배"),
        _tile(x["flow_ok"], "외+기 수급", f"5일 {_eok_num(x['fi5'])} · 20일 {_eok_num(x['fi20'])}억"),
        _tile(x["pb_bounce"], "반등 캔들", f"DCR {x['pb_dcr']:.0f}%"),
        _tile(x["og"].get("op_up"), "올해 영업이익", x["og"].get("op_txt") or "-"),
        _tile(x["rs_ok"] and (x.get("rs") or 0) >= 70, "RS", f"{x.get('rs') or 0:.0f} (지수보다 {'위' if x['rs_ok'] else '아래'})"),
        _tile((x.get("pb_ud") or 0) >= 1.0, "오른 날/내린 날 거래량", f"{x.get('pb_ud') or 0:.2f}배 (20일)"),
    ]
    st.markdown(f'<div class="tiles">{"".join(checks)}</div>', unsafe_allow_html=True)
    st.markdown(
        f"<div class='verdict'><b>{html.escape(str(x['name']))}</b> — 20일 고점 {x['pb_hi']:,.0f}에서 {x['pb_days']}일째 "
        f"-{x['pb_depth']:.1f}% 쉬는 중(직전 상승 +{x['pb_run']:.0f}%의 {x['pb_retr']:.0f}% 되돌림). "
        f"지금가 {x['price']:,.0f}는 <b>{x['pb_support']} {x['pb_sup']:,.0f}</b> 바로 위. "
        f"손절 {x['pb_stop']:,.0f}(-{x['pb_stop_pct']:.1f}%) → 전고점 {x['pb_hi']:,.0f}까지 손익비 <b>{x['pb_rr']:.1f} : 1</b>, "
        f"3R {x['pb_t3']:,.0f}." + ("" if x["pb_bounce"] else f" 아직 반등 전 — {x['pb_trigger']:,.0f} 위로 올라서면 진입 검토.")
        + "</div>", unsafe_allow_html=True)
    fig = _pb_fig(x["code"], quotes.get(x["code"]), x)
    if fig is not None:
        st.plotly_chart(fig, key="pb_fig", config={"displaylogo": False, "scrollZoom": True})
    else:
        st.info("차트를 받지 못했어요.")



# ─────────────────────────── 📣 실적·지분 추적 ───────────────────────────
TRACK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_tracker.json")


def _track_load() -> dict:
    try:
        import json as _json
        with open(TRACK_FILE, encoding="utf-8") as f:
            d = _json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _track_save(store: dict) -> str:
    """새로 찾은 서프라이즈·지분 공시를 쌓아 둬요(채널 글이 밀려 사라져도 추적이 이어지게). GitHub 토큰이 있으면 거기도."""
    import json as _json
    try:
        with open(TRACK_FILE, "w", encoding="utf-8") as f:
            _json.dump(store, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
    tok = _secret("GITHUB_TOKEN")
    if tok:
        ok, msg = reports.save_github(store, tok, _secret("GITHUB_REPO", "kimgun124-boop/very"),
                                      _secret("GITHUB_BRANCH") or None, path="user_tracker.json")
        return msg
    return "이 서버에만 저장(GITHUB_TOKEN 없음)"


@st.cache_data(ttl=1800, show_spinner=False)
def load_surprises(since_iso: str):
    return tracker.collect_surprises(date.fromisoformat(since_iso))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_stakes(key: str, days: int, only_vc: bool):
    return tracker.stake_increases(key, days, only_vc)


@st.cache_data(ttl=3600, show_spinner=False, max_entries=60)
def load_hist_one(code: str) -> pd.DataFrame:
    """보드 밖 종목(서프라이즈·지분 공시) 일봉 — 추적에 필요한 날짜·고가·종가만, 최대 60종목."""
    h = data.fetch_history(code, count=300)[1]
    keep = [c for c in ("date", "high", "close") if c in h]
    return h[keep].astype({c: "float32" for c in keep if c != "date"}) if len(h) else h


def _track_rows(items: list[dict], df: pd.DataFrame, histories: dict, quotes: dict, date_key: str = "date") -> pd.DataFrame:
    """종목별: 발표(공시)일 종가 → 지금까지 수익률, 그 뒤 최고가, 52주 신고가 상태, RS, 오늘 등락."""
    info = df.drop_duplicates("code").set_index("code")
    need = tuple(sorted({x["code"] for x in items if x["code"] not in quotes}))
    extra_q = load_quotes(need)[0] if need else {}
    out = []
    for x in items:
        c = x["code"]
        h = (histories.get(c) or (None, None, None))[1]
        if h is None or h.empty:
            h = load_hist_one(c)
        q = quotes.get(c) or extra_q.get(c) or {}
        price = q.get("price") or (float(h["close"].iloc[-1]) if h is not None and len(h) else None)
        base = hi_after = None
        h52 = None
        if h is not None and len(h) and x.get(date_key):
            dd = pd.to_datetime(h["date"]).dt.normalize()
            d0 = pd.Timestamp(x[date_key])
            on = h[dd <= d0]
            base = float(on["close"].iloc[-1]) if len(on) else None
            after = h[dd >= d0]
            hi_after = float(pd.to_numeric(after["high"], errors="coerce").max()) if len(after) else None
            h52 = float(pd.to_numeric(h["high"], errors="coerce").tail(250).max())
        if price and hi_after:
            hi_after = max(hi_after, float(q.get("high") or 0), price)
        if price and h52:
            h52 = max(h52, float(q.get("high") or 0), price)
        r = info.loc[c] if c in info.index else None
        out.append({**x, "price": price, "chg": q.get("change") if q.get("change") is not None else (r["change"] if r is not None else None),
                    "base": base, "since": (price / base - 1) * 100 if price and base else None,
                    "max_since": (hi_after / base - 1) * 100 if hi_after and base else None,
                    "to_high": (h52 / price - 1) * 100 if price and h52 else None,
                    "new_high": bool(price and h52 and float(q.get("high") or price) >= h52 * 0.999),
                    "rs": r["rs"] if r is not None and "rs" in r else None,
                    "group": r["group"] if r is not None and "group" in r else None,
                    "on_board": c in info.index})
    return pd.DataFrame(out)


def render_tracker(df: pd.DataFrame, histories: dict, quotes: dict):
    _sec_h("📣 실적·지분 추적", "텔레그램(스탁이지·AWAKE) 3분기 어닝 서프라이즈 + DART 대량보유 보고(VC·펀드 지분 확대)를 모아 주가를 따라가요")
    y = data.now_kst().year
    view = st.segmented_control("보기", ["🚀 3분기 어닝 서프라이즈", "🏦 VC·펀드 지분 확대"], default="🚀 3분기 어닝 서프라이즈",
                                key="trk_view", label_visibility="collapsed") or "🚀 3분기 어닝 서프라이즈"
    store = _track_load()
    if view == "🚀 3분기 어닝 서프라이즈":
        c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1])
        since = c1.date_input("이날부터 발표", date(y, 10, 1), key="trk_since")
        beat_min = c2.number_input("컨센 대비 최소(%)", -50.0, 200.0, 10.0, 5.0, key="trk_beat",
                                   help="예상(컨센서스)보다 영업이익이 이만큼 이상 높으면 서프라이즈")
        only_q3 = c3.toggle("3분기 실적만", value=True, key="trk_q3")
        if c4.button("🔄 채널 새로 읽기", key="trk_reload"):
            load_surprises.clear()
        with st.spinner("텔레그램 채널(스탁이지·AWAKE)에서 어닝 서프라이즈 글을 모으는 중…"):
            rows, errs = load_surprises(since.isoformat())
        old = {f"{r['code']}|{r['quarter']}": r for r in store.get("surprises", [])}
        new_keys = [k for k in (f"{r['code']}|{r['quarter']}" for r in rows) if k not in old]
        for r in rows:
            old[f"{r['code']}|{r['quarter']}"] = {**old.get(f"{r['code']}|{r['quarter']}", {}), **r}
        if new_keys:
            store["surprises"] = list(old.values())
            store["updated"] = data.now_kst().isoformat(timespec="minutes")
            _track_save(store)
        for e in errs:
            st.caption(f"⚠️ {e}")
        items = [r for r in old.values() if r.get("beat") is not None and r["beat"] >= beat_min
                 and (r.get("date") or "") >= since.isoformat()
                 and (not only_q3 or str(r.get("quarter") or "").startswith("3Q"))]
        if not items:
            st.info("조건에 맞는 어닝 서프라이즈가 아직 없어요. 채널 글을 못 읽었으면 위 경고를 확인하고 '채널 새로 읽기'를 눌러 보세요.")
            return
        t = _track_rows(items, df, histories, quotes)
        t = t.sort_values(["new_high", "since"], ascending=[False, False])
        n_new = int(t["new_high"].sum())
        n_up = int((t["since"] > 0).sum())
        tiles = [_tile(None, "서프라이즈 종목", f"{len(t)}"), _tile(True if n_new else None, "🏔️ 지금 52주 신고가", f"{n_new}"),
                 _tile(n_up >= len(t) / 2, "발표 후 오른 종목", f"{n_up}/{len(t)}"),
                 _tile(None, "발표 후 평균", f"{t['since'].mean():+.1f}%" if t["since"].notna().any() else "-")]
        st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
        view_t = pd.DataFrame([{
            "상태": "🏔️ 신고가" if x["new_high"] else ("🔥 5% 이내" if (x["to_high"] or 99) <= 5 else ""),
            "종목": f"{x['name']} · {x['code']}", "발표일": x["date"], "분기": x.get("quarter"),
            "영업이익(억)": x.get("op"), "컨센 대비": x.get("beat"), "매출(억)": x.get("sales"),
            "발표일 종가": x["base"], "현재가": x["price"], "오늘": x["chg"], "발표 후": x["since"], "발표 후 최고": x["max_since"],
            "신고가까지": x["to_high"], "RS": x["rs"], "분류": x.get("group") or ("보드 밖" if not x["on_board"] else ""),
            "출처": x["src"], "원문": (x.get("urls") or [x.get("url")])[0]} for x in t.to_dict("records")])
        sty = view_t.style.format({"영업이익(억)": "{:,.0f}", "컨센 대비": "{:+.1f}%", "매출(억)": "{:,.0f}",
                                   "발표일 종가": "{:,.0f}", "현재가": "{:,.0f}", "오늘": "{:+.2f}%", "발표 후": "{:+.1f}%",
                                   "발표 후 최고": "{:+.1f}%", "신고가까지": "{:.1f}%", "RS": "{:.0f}"}, na_rep="-") \
            .map(_won_c, subset=["오늘", "발표 후", "발표 후 최고"])
        st.dataframe(sty, hide_index=True, width="stretch", height=480,
                     column_config={"종목": st.column_config.Column(pinned=True),
                                    "원문": st.column_config.LinkColumn("원문", display_text="열기")})
        st.download_button("📥 CSV", view_t.to_csv(index=False).encode("utf-8-sig"), f"3Q_어닝서프라이즈_{data.now_kst():%Y%m%d}.csv",
                           "text/csv", key="trk_dl_s")
        st.caption("서프라이즈 = 채널 글의 '예상비/컨센 대비' 숫자(없으면 영업이익 ÷ 컨센 금액으로 계산). 분기 표기가 없으면 발표 시기로 정해요(10~11월 = 3분기). "
                   "발표일 종가 = 그날(장 마감 뒤 발표면 반응은 다음 날부터) · 발표 후 최고 = 그 뒤 최고가 기준. 한 번 찾은 종목은 쌓아 둬서 채널 글이 밀려도 계속 따라가요. "
                   "채널 자료는 자동 생성이라 틀릴 수 있어요 — 원문·DART로 확인하세요. 매수 권유가 아니에요.")
        return

    # ── VC·펀드 지분 확대 (DART)
    key = _secret("DART_KEY") or st.session_state.get("dart_key_in", "")
    if not key:
        st.info("DART 대량보유 공시를 읽으려면 무료 OpenDART 인증키가 필요해요. opendart.fss.or.kr → 인증키 신청(바로 발급) 후 "
                "Streamlit 앱 Settings → Secrets에 `DART_KEY = \"발급받은키\"` 한 줄을 넣으면 계속 써요. 지금 한 번만 쓰려면 아래에 넣어도 돼요.")
        st.text_input("OpenDART 인증키", type="password", key="dart_key_in")
        return
    c1, c2, c3 = st.columns([1, 1, 1])
    days = c1.selectbox("기간", [7, 14, 30, 60, 90], index=2, format_func=lambda d: f"최근 {d}일", key="trk_days")
    only_vc = c2.toggle("VC·펀드·운용사만", value=True, key="trk_vc", help="끄면 보고자 상관없이 지분율이 늘어난 대량보유 보고 전부")
    if c3.button("🔄 DART 새로 읽기", key="trk_dreload"):
        load_stakes.clear()
    with st.spinner("DART 대량보유 보고서에서 지분이 늘어난 종목을 찾는 중…"):
        rows, err = load_stakes(key, int(days), bool(only_vc))
    if err:
        st.caption(f"⚠️ {err}")
    old = {f"{r['code']}|{r['reporter']}|{r['date']}": r for r in store.get("stakes", [])}
    fresh = [r for r in rows if f"{r['code']}|{r['reporter']}|{r['date']}" not in old]
    if fresh:
        for r in rows:
            old[f"{r['code']}|{r['reporter']}|{r['date']}"] = r
        store["stakes"] = sorted(old.values(), key=lambda x: x["date"], reverse=True)[:500]
        _track_save(store)
    cut = (data.now_kst().date() - pd.Timedelta(days=int(days))).isoformat()
    items = [r for r in old.values() if r["date"] >= cut and (r.get("vc") or not only_vc)]
    if not items:
        st.info("기간 안에 VC·펀드가 지분을 늘린 대량보유 보고가 없어요.")
        return
    t = _track_rows(items, df, histories, quotes)
    n_codes = t["code"].nunique()
    multi = t.groupby("code")["reporter"].nunique()
    tiles = [_tile(None, "공시", f"{len(t)}건 · {n_codes}종목"),
             _tile(True if (multi >= 2).any() else None, "여러 곳이 같이 늘림", f"{int((multi >= 2).sum())}종목"),
             _tile(True if t["new_high"].any() else None, "🏔️ 지금 52주 신고가", f"{int(t['new_high'].sum())}")]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    vt = pd.DataFrame([{
        "상태": "🏔️ 신고가" if x["new_high"] else ("🔥 5% 이내" if (x["to_high"] or 99) <= 5 else ""),
        "종목": f"{x['name']} · {x['code']}", "공시일": x["date"], "보고자": x["reporter"],
        "보유비율": x.get("ratio"), "증감(%p)": x.get("ratio_chg"), "증감 주식수": x.get("shares_chg"), "사유": x.get("reason"),
        "공시일 종가": x["base"], "현재가": x["price"], "공시 후": x["since"], "신고가까지": x["to_high"], "RS": x["rs"],
        "원문": x["url"]} for x in t.sort_values(["date", "ratio_chg"], ascending=False).to_dict("records")])
    sty = vt.style.format({"보유비율": "{:.2f}%", "증감(%p)": "{:+.2f}", "증감 주식수": "{:+,.0f}", "공시일 종가": "{:,.0f}",
                           "현재가": "{:,.0f}", "공시 후": "{:+.1f}%", "신고가까지": "{:.1f}%", "RS": "{:.0f}"}, na_rep="-") \
        .map(_won_c, subset=["공시 후"])
    st.dataframe(sty, hide_index=True, width="stretch", height=480,
                 column_config={"종목": st.column_config.Column(pinned=True),
                                "원문": st.column_config.LinkColumn("원문", display_text="DART")})
    st.download_button("📥 CSV", vt.to_csv(index=False).encode("utf-8-sig"), f"VC펀드_지분확대_{data.now_kst():%Y%m%d}.csv",
                       "text/csv", key="trk_dl_v")
    st.caption("DART 지분공시 중 '주식등의대량보유상황보고서'(5% 이상 보유자, 1%p 이상 변동 시 보고)에서 보유비율이 늘어난 것만. "
               "VC·펀드 = 보고자 이름에 인베스트먼트·벤처·파트너스·캐피탈·자산운용·투자조합·펀드·사모·PE 등이 들어간 곳(증권·은행·보험·연기금 제외). "
               "단순투자·경영참가 같은 보유 목적과 자금 출처는 원문에서 확인하세요. 매수 권유가 아니에요.")


# ─────────────────────────── 📚 관점(깡토 × 너쟁이) · 🌏 글로벌 피어 · 🪜 증거 쌓기 ───────────────────────────
_PEER_MEMO: dict = _memo_store().setdefault("peer", {})      # (속도) 새로고침해도 남는 기억


def load_peers(df: pd.DataFrame, histories: dict | None = None) -> list[dict]:
    """분류별 국내 vs 해외 피어 vs 수요처(5분 기억). 일봉은 이미 받아 둔 보드 일봉을 그대로 써요(새로 받지 않음)."""
    now = data.now_kst()
    key = (now.date(), now.hour * 12 + now.minute // 5, len(df))
    if key not in _PEER_MEMO:
        h = histories if histories is not None else _hist_store()["data"]
        try:
            res = views.peer_table(df, h)
        except Exception:  # noqa: BLE001
            res = []
        _PEER_MEMO.clear()
        _PEER_MEMO[key] = res
    return _PEER_MEMO[key]


def _views_box(keys: tuple[str, ...] | None = None, title: str = "📚 관점 노트 — 깡토 × 너쟁이(추노)"):
    with st.expander(title):
        for k, t, x in views.NJ_VIEWS:
            if keys is None or k in keys:
                st.markdown(f"- **{t}** — {x}")
        st.caption(f"출처: {views.NJ_SRC} · 효석 원칙(리스크관리 > 멘탈 > 종목 > 타점)이 항상 우선이고, 이 관점은 재량 참고예요.")


def _pct(v, d=1):
    return "-" if v is None or v != v else f"{v:+.{d}f}%"


def _render_peer_leaders(df: pd.DataFrame):
    """글로벌 피어 대비 강한 한국 종목: 국내 RS 70↑ + 글로벌 RS(국내+해외 피어 무리 안 백분위) 70↑ + 3개월 피어 초과."""
    c1, c2, c3 = st.columns([1, 1, 1.2])
    rs_min = c1.number_input("국내 RS ≥", 1, 99, 70, 5, key="pl_rs", help="원칙: RS 70 이상")
    grs_min = c2.number_input("글로벌 RS ≥", 1, 99, 70, 5, key="pl_grs",
                              help="같은 분류의 한국 종목 + 해외 피어를 한 무리로 놓고 매긴 RS 백분위")
    only_ok = c3.toggle("조건 통과만", value=True, key="pl_ok")
    t = views.kr_peer_leaders(df, rs_min, grs_min)
    if t.empty:
        st.info("해외 피어 일봉이 아직 없어서 비교할 수 없어요. 잠시 뒤 다시 열어 보세요.")
        return
    hist = load_indexes()
    idx_rs = {n: data.index_rs(hist.get(sym, (data.empty_frame(), None))[0], df).get("rs") for n, sym in data.MARKETS.items()}
    known = [v for v in idx_rs.values() if v is not None]
    t["beat_idx"] = [None if not known or r != r or r is None else all(r > v for v in known) for r in t["rs"]]
    ok_n = int(t["ok"].sum())
    n_grp = t.loc[t["ok"], "group"].nunique()
    best = t[t["ok"]].head(1)
    tiles = [_tile(True if ok_n else None, "피어보다 강한 한국 종목", f"{ok_n}"),
             _tile(None, "해당 분류", f"{n_grp}/{t['group'].nunique()}"),
             _tile(None, "지수 RS", " · ".join(f"{k} {v:.0f}" for k, v in idx_rs.items() if v is not None) or "-"),
             _tile(None, "글로벌 RS 1위", f"{best['name'].iloc[0]} {best['grs'].iloc[0]:.0f}" if len(best) else "-")]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    v = t[t["ok"]] if only_ok else t
    if v.empty:
        st.info("조건을 모두 통과한 한국 종목이 없어요. '조건 통과만'을 끄거나 기준을 낮춰 보세요.")
        return
    vt = pd.DataFrame({
        "종목": v["name"] + " · " + v["code"], "차트": "?chart=" + v["code"], "분류": v["group"],
        "글로벌 RS": v["grs"], "무리 안 순위": v["g_rank"].astype(int).astype(str) + "/" + v["g_n"].astype(int).astype(str),
        "국내 RS": v["rs"], "지수보다 강함": v["beat_idx"].map({True: "✓", False: "✕", None: "-"}),
        "3개월": v["ret_3m"], "피어 3개월": v["peer_3m"], "피어 초과(3개월)": v["ex3m"],
        "1개월": v["ret_1m"], "피어 초과(1개월)": v["ex1m"],
        "섹터 순위": [("👑 1위" if (lr == 1 and lo) else (f"{int(lr)}위" if lr == lr and lr is not None else "-"))
                  for lr, lo in zip(v["lead_rank"], v["lead_ok"])],
        "현재가": v["price"], "신고가까지": v["to_high"], "피어 1위": v["peer_top"] + " " +
        pd.to_numeric(v["peer_top_ret3m"], errors="coerce").map(lambda x: "" if x != x else f"({x:+.0f}%)"),
        "해외 피어 분류": v["peer_groups"]})
    pc = ["3개월", "피어 3개월", "피어 초과(3개월)", "1개월", "피어 초과(1개월)"]
    sty = vt.style.format({**{k: "{:+.1f}%" for k in pc}, "글로벌 RS": "{:.0f}", "국내 RS": "{:.0f}",
                           "현재가": "{:,.0f}", "신고가까지": "{:.1f}%"}, na_rep="-").map(_won_c, subset=pc)
    st.dataframe(sty, hide_index=True, width="stretch", height=min(560, 40 + 35 * len(vt)),
                 column_config={"종목": st.column_config.Column(pinned=True),
                                "차트": st.column_config.LinkColumn("차트", display_text="열기")})
    st.download_button("📥 CSV", vt.drop(columns=["차트"]).to_csv(index=False).encode("utf-8-sig"),
                       f"피어대비_강한_한국종목_{data.now_kst():%Y%m%d}.csv", "text/csv", key="pl_dl")
    st.caption("글로벌 RS = 같은 분류의 한국 종목과 해외 피어(보드의 해외 분류)를 한 무리로 놓고, 보드 RS와 같은 오닐식 점수"
               "(3개월 0.4 + 6·9·12개월 각 0.2, 각 나라 통화 기준 수익률)의 백분위. 통과 = 국내 RS·글로벌 RS 기준 이상 + 3개월 수익률이 피어 중앙값보다 높음. "
               "해외는 마지막 거래일 종가(미국은 우리 시간 새벽 마감). 매수 판단은 🎯 매수 후보 탭의 원칙 점검을 같이 보세요.")


def render_peers(df: pd.DataFrame, histories: dict):
    _sec_h("🌏 글로벌 피어", "한국 밸류체인 분류 ↔ 해외 같은 업종(피어)·수요처 — 우리만 빠지는지, 글로벌로 같이 움직이는지")
    pv = st.segmented_control("보기", ["🇰🇷 피어보다 강한 한국 종목", "🌏 분류별 비교"], default="🇰🇷 피어보다 강한 한국 종목",
                              key="peer_view", label_visibility="collapsed") or "🇰🇷 피어보다 강한 한국 종목"
    if pv == "🇰🇷 피어보다 강한 한국 종목":
        _render_peer_leaders(df)
        return
    rows = load_peers(df, histories)
    if not rows:
        st.info("해외 피어 일봉을 아직 못 받았어요. 잠시 뒤 다시 열어 보세요.")
        return
    cnt = pd.Series([r["verdict"][:1] for r in rows]).value_counts()
    tiles = [_tile(True if cnt.get("🟢", 0) else None, "🟢 글로벌 섹터 액션", f"{cnt.get('🟢', 0)}"),
             _tile(None, "🟡 한국만 약함", f"{cnt.get('🟡', 0)}"),
             _tile(False if cnt.get("🔴", 0) else None, "🔴 글로벌 동반 약세", f"{cnt.get('🔴', 0)}"),
             _tile(None, "🟠 국내만 강함", f"{cnt.get('🟠', 0)}")]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    order = {"🔴": 0, "🟡": 1, "🟢": 2, "🟠": 3, "⚪": 4, "자": 5}
    rows = sorted(rows, key=lambda r: (order.get(r["verdict"][:1], 9), -(r["kr"] or {}).get("r5", 0)))
    t = pd.DataFrame([{
        "판정": r["verdict"], "분류": r["group"], "산업": r["sector"],
        "🇰🇷 5일": (r["kr"] or {}).get("r5"), "🇰🇷 20일": (r["kr"] or {}).get("r20"), "🇰🇷 20일선 위": (r["kr"] or {}).get("a20"),
        "🌏 피어 5일": (r["peer"] or {}).get("r5"), "🌏 피어 20일": (r["peer"] or {}).get("r20"),
        "🌏 20일선 위": (r["peer"] or {}).get("a20"), "🌏 신고가 5% 안": (r["peer"] or {}).get("near"),
        "🛒 수요처 20일": (r["demand"] or {}).get("r20"), "해외 피어": r["peer_names"], "수요처": r["demand_groups"],
        "해석": r["why"]} for r in rows])
    pc = ["🇰🇷 5일", "🇰🇷 20일", "🌏 피어 5일", "🌏 피어 20일", "🛒 수요처 20일"]
    sty = t.style.format({**{k: "{:+.1f}%" for k in pc}, "🇰🇷 20일선 위": "{:.0f}%", "🌏 20일선 위": "{:.0f}%",
                          "🌏 신고가 5% 안": "{:.0f}%"}, na_rep="-").map(_won_c, subset=pc)
    st.dataframe(sty, hide_index=True, width="stretch", height=520, column_config={"판정": st.column_config.Column(pinned=True)})
    st.caption("수익률 = 바구니 종목들의 중앙값 · 해외는 각 시장의 마지막 거래일 종가 기준(미국은 우리 시간 새벽 마감). "
               "🟡 한국만 약함 = 국내 바구니 5일 -2% 이하·20일선 아래가 많은데 해외 피어는 20일선 위 60%↑·20일 상승. "
               "피어 = 같은 일을 하는 해외 기업(보드의 해외 분류), 수요처 = 그 분류가 파는 곳(예: 후공정 → 엔비디아·TSMC·하이퍼스케일러). "
               "해외 피어가 없는 분류(조선·화장품·건설 등)는 빠져요.")
    _views_box(("시장", "쿠션"), "📚 너쟁이 관점 — 왜 해외 피어를 보나")


def render_ladder(df: pd.DataFrame, histories: dict, fins_all=None):
    _sec_h("🪜 증거 쌓기", "실적 서프라이즈 → 국내 섹터 액션 → 글로벌 피어 → 리포트·뉴스 — 증거가 쌓일수록 비중을 늘리는 자리(피라미딩)")
    peers = load_peers(df, histories)
    store = _track_load()
    sur = store.get("surprises", [])
    leaders = {g for g, _ in lead_groups(df)}
    lad = views.evidence_ladder(df, peers, sur, USER_STORE.get("reports", []), fins_all or {}, leaders)
    if lad.empty:
        st.info("계산할 분류가 없어요.")
        return
    c1, c2 = st.columns([1, 3])
    min_stage = c1.selectbox("보기", [2, 1, 3, 4, 0], format_func=lambda n: f"증거 {n}개 이상" if n else "전체", key="lad_min")
    show = lad[lad["stage"] >= min_stage]
    cnt = lad["stage"].value_counts()
    tiles = [_tile(True if cnt.get(4, 0) else None, "증거 4/4", f"{cnt.get(4, 0)}"), _tile(None, "증거 3", f"{cnt.get(3, 0)}"),
             _tile(None, "증거 2", f"{cnt.get(2, 0)}"),
             _tile(None, "실적 서프라이즈 기록", f"{len(sur)}종목" if sur else "📣 탭에서 모으기")]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    if show.empty:
        st.caption("이 기준에 맞는 분류가 없어요.")
        return
    mk = lambda b: "✅" if b else "·"
    t = pd.DataFrame([{
        "단계": f"{x['stage']}/4 · {x['stage_txt']}", "분류": x["group"], "산업": x["sector"],
        "① 실적": f"{mk(x['실적'])} {x['sur_txt']}".strip(), "② 섹터": f"{mk(x['섹터'])} {x['sect_txt']}",
        "③ 글로벌": f"{mk(x['글로벌'])} {x['glob_txt']}", "④ 근거": f"{mk(x['근거'])} {x['rep_txt']}".strip(),
        "대장주": x["leader"], "RS": x["lead_rs"], "신고가까지": x["lead_to_high"], "예상 PER": x["per"], "PEG": x["peg"],
        "밸류": views.value_cushion(x["per"], x["peg"])[1].split(" — ")[-1] if x["per"] else "-"} for x in show.to_dict("records")])
    sty = t.style.format({"RS": "{:.0f}", "신고가까지": "{:.1f}%", "예상 PER": "{:.1f}", "PEG": "{:.2f}"}, na_rep="-")
    st.dataframe(sty, hide_index=True, width="stretch", height=520, column_config={"분류": st.column_config.Column(pinned=True)})
    st.caption("증거 4가지 — ① 실적: 📣 탭에 쌓인 어닝 서프라이즈(컨센 상회)가 이 분류에 있음 · ② 섹터: 최근 5일 신고가 2종목↑ 또는 "
               "평소 대비 거래대금 1.5배↑ 양봉 2종목↑ 또는 주도섹터 · ③ 글로벌: 해외 피어 20일선 위 60%↑·20일 상승 · "
               "④ 근거: 최근 30일 📥 리포트에 이 분류 종목이 있음. 단계 = 증거 개수 → 원칙의 3유닛(6% → 12% → 18%, 종목당 위험 1.5%) "
               "안에서 증거가 하나 늘 때마다 한 유닛씩(너쟁이: 정보가 구체화될수록 피라미딩). 대장주 = 분류 안 RS·시총·거래대금 1위. "
               "PEG = 예상 PER ÷ 올해 EPS 증가율(1 이하면 성장 대비 싼 편). 손절·시장 60일선 원칙은 그대로예요. 매수 권유가 아니에요.")
    _views_box(("발굴", "비중", "선별", "트리거"), "📚 너쟁이 관점 — 증거가 쌓일 때마다 비중을")
    _views_box(None)


# ─────────────────────────── 🚦 시장 신호(FTD·분산일) · 🎯 초기 매수(리 태너) ───────────────────────────
_MKT_MEMO: dict = _memo_store().setdefault("mkt", {})      # (속도) 새로고침해도 남는 기억


def load_index_long(sym: str) -> pd.DataFrame:
    """지수 일봉 약 6년(1,500거래일, 1시간 기억) + 오늘 실시간 일봉(2분 기억)을 합쳐요. 지수 2개라 메모리는 1MB도 안 돼요."""
    long_ = swr(("idx_long", sym), 3600, lambda: data.fetch_history(sym, count=1500)[1], "지수 6년 일봉을 받는 중이에요.")
    short = (load_indexes().get(sym) or (None,))[0]
    parts = [x for x in (long_, short) if x is not None and len(x)]
    if not parts:
        return data.empty_frame()
    out = pd.concat(parts, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    return out.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)


def market_signals(p: dict | None = None) -> dict:
    """{코스피/코스닥: msig.analyze 결과} — 2분마다 다시 계산."""
    p = p or {}
    now = data.now_kst()
    key = (now.date(), now.hour * 30 + now.minute // 2, tuple(sorted(p.items())))
    if key not in _MKT_MEMO:
        res = {}
        for name, sym in data.MARKETS.items():
            try:
                res[name] = msig.analyze(load_index_long(sym), p)
            except Exception:  # noqa: BLE001
                res[name] = None
        _MKT_MEMO.clear()
        _MKT_MEMO[key] = res
    return _MKT_MEMO[key]


def _mkt_card(name: str, m: dict | None) -> str:
    if not m:
        return f'<div class="pf-k">{name}</div><div class="pf-v">-</div><div class="pf-s">지수 일봉을 받는 중</div>'
    sub = []
    if m["state"] == "rally":
        sub.append(f"반등 시도 <b>Day {m['day']}</b>" + (f" · 바닥 {m['low']:,.2f}" if m.get("low") else ""))
    lf = m.get("last_ftd")
    if lf:
        sub.append(f"최근 FTD <b>{pd.Timestamp(lf['date']):%y.%m.%d}</b> {lf['result']}")
    sub.append(f"분산일 <b>{m['dd']}개</b>(25일) · 평소 {m['q50']:.0f}개 · 상위 {100 - (m['dd_pct_rank'] or 0):.0f}%"
               + (" · 책 기준 경고(5개↑)" if m["book_warn"] else ""))
    sub.append(f"52주 고점 대비 <b>{m['draw']:+.1f}%</b>")
    return (f'<div class="pf-k">{name}</div><div class="pf-v">{m["em"]} {m["state_txt"]}</div>'
            f'<div class="pf-s">{"<br>".join(sub)}</div>')


FTD_RULES = pd.DataFrame([
    {"출처": "IBD(Investor's Business Daily) 현재 규칙", "랠리 1일차": "지수가 오른 날(또는 내렸어도 고가 근처 마감)",
     "리셋": "1일차 저점을 깨면 처음부터", "FTD 가장 빠른 날": "보통 4일차(4~7일차가 전형)", "상승폭": "1% 이상(때로 그 이상)",
     "거래량": "전날보다 많음"},
    {"출처": "William O'Neil + Co 연구(2025, S&P500 1970~)", "랠리 1일차": "새 저점 뒤 3일 동안 새 저점 없음",
     "리셋": "직전 저점 이탈", "FTD 가장 빠른 날": "4일차(4~7일차가 가장 강함)", "상승폭": "1.7% 이상", "거래량": "전날보다 많음"},
    {"출처": "이 보드(🚦 시장 신호 기본값)", "랠리 1일차": "바닥 뒤 처음 오른 날 또는 범위 위쪽 절반 마감",
     "리셋": "바닥(장중 저가) 이탈", "FTD 가장 빠른 날": "4일차(⚙️ 기준에서 3일차로 바꿀 수 있음)", "상승폭": "1.25% 이상(IBD)",
     "거래량": "전날보다 많음"},
    {"출처": "스탁이지 '지금 여긴'", "랠리 1일차": "공개 안 됨", "리셋": "공개 안 됨",
     "FTD 가장 빠른 날": "공개 안 됨 — 10/28 코스피 FTD로 표시(이 보드 기준 반등 2일차)", "상승폭": "공개 안 됨", "거래량": "공개 안 됨"},
])
FTD_STATS = pd.DataFrame([
    {"연구": "William O'Neil + Co (Forbes, 2025.3)", "표본": "S&P500 9%↑ 조정 35번(1970~)",
     "결과": "첫 FTD 성공 19/35(54%) · 첫 FTD 실패 후 두 번째 성공 8/35(23%) · 약세장 8/35(23%) — 약세장은 대부분 FTD 실패 4번↑"},
    {"연구": "Quantifiable Edges (2010)", "표본": "S&P500 FTD(1970~)",
     "결과": "4일차 FTD 38승 35패(52%) vs 2일차까지 허용 39승 43패(48%) — 일찍 잡으면 실패만 늘어남"},
    {"연구": "깡토 백테스트(코스피·코스닥 1990~2026)", "표본": "하락장(-20%) 바닥 25번 · 하락장 FTD 52번",
     "결과": "바닥 뒤엔 항상 FTD(25/25) · 하락장 FTD 52번 중 25번은 진짜 바닥 전에 나와 실패 → FTD는 '허락'이지 '공격'이 아님"},
])
EXPO = pd.DataFrame([
    {"단계": "조정 · 반등 시도(FTD 전)", "IBD": "0-20%", "스승님": "쉬기 → 3일차 강세면 정찰병", "내 원칙": "60일선 아래면 0-20%"},
    {"단계": "FTD 당일·직후", "IBD": "20-40% (셋업이 적으면 0-20% 유지, 아주 강하면 처음 40%)", "스승님": "정찰병 → 살아남으면 키우기",
     "내 원칙": "60일선 아래면 0-20%"},
    {"단계": "FTD 뒤 상승 지속", "IBD": "40-60%", "스승님": "성공한 종목에 피라미딩", "내 원칙": "60일선 위면 유닛 매매"},
    {"단계": "상승 추세가 자리 잡음", "IBD": "60-80% → 80-100%", "스승님": "주도주 끌고 가기", "내 원칙": "유닛 6→12→18%, 최대 8종목"},
    {"단계": "상승 둔화(분산일 증가)", "IBD": "노출 줄이기(40-60%)", "스승님": "약한 종목부터 정리", "내 원칙": "신규는 정찰병 크기"},
])


def _render_ftd_research(mn: dict):
    """FTD 기준 · 권장 노출 근거 비교(스탁이지와 숫자가 다른 이유)."""
    with st.expander("📚 FTD 기준 · 권장 노출 — 근거 비교 (스탁이지와 숫자가 다른 이유)", expanded=True):
        rows = []
        for n, x in mn["idx"].items():
            rows.append(f"**{n}** — 이 보드: {x['state']} · IBD 기준 {x['exp_ibd']}"
                        + (f" · 내 원칙 적용 {x['exp_mine']}" if x["exp_mine"] else "") + f" → **{x['exp']}**")
        st.markdown("\n".join(f"- {r}" for r in rows))
        st.markdown("**1. FTD를 언제로 보느냐가 다름**")
        st.dataframe(FTD_RULES, hide_index=True, width="stretch")
        st.markdown("**2. FTD가 나와도 바로 80-100%가 아님** — IBD는 FTD 직후 20-40%에서 시작해 상승이 이어질 때 올려요. "
                    "스탁이지는 FTD 당일 80-100%로 표시해서 IBD보다 훨씬 공격적이에요(기준 비공개).")
        st.dataframe(EXPO, hide_index=True, width="stretch")
        st.markdown("**3. FTD 성공률 — 통계로 본 근거**")
        st.dataframe(FTD_STATS, hide_index=True, width="stretch")
        st.caption("결론: FTD는 절반 가까이 실패해요(54%·52%·25/52). 그래서 IBD도 FTD 직후 20-40%로 시작하고, 실패하면 바로 줄여요. "
                   "이 보드의 권장 노출 = IBD 5단계 → 지수가 60일선 아래면 내 원칙대로 0-20%(정찰병)로 낮춰서 보여줘요. "
                   "출처: Investor's Business Daily 'How To Invest After A Follow-Through Day', 'Risk Management In The Stock Market', "
                   "'How To Spot A Stock Market Bottom' · Forbes 'Follow-Through Day Study Update'(2025.3.7) · Quantifiable Edges "
                   "'Follow Through Days Before The 4th Day'(2010.7) · 깡토 블로그 백테스트.")


def _render_health_charts(df: pd.DataFrame):
    """🚦 시장 신호 탭: 시장 체력·증시 자금 차트(코스피 겹쳐 보기)."""
    mn = market_now(df)
    h, dp = mn["health"], mn["dep"]
    st.markdown(DB_CSS + f'<div class="db">{_now_card(mn)}</div>', unsafe_allow_html=True)
    _render_ftd_research(mn)
    if h is None or h.empty:
        st.info("시장 체력을 계산할 일봉이 아직 없어요.")
        return
    k = load_index_long(data.MARKETS["코스피"])
    kp = pd.DataFrame({"date": pd.to_datetime(k["date"]), "KOSPI": pd.to_numeric(k["close"], errors="coerce")}) if len(k) else pd.DataFrame()
    h = h.copy()
    h["date"] = pd.to_datetime(h["date"])
    base = alt.Chart(kp[kp["date"] >= h["date"].min()]) if len(kp) else None

    def dual(series: pd.DataFrame, cols: dict, title: str, y_title: str, rules: list | None = None, shade=None):
        long = series.melt("date", value_vars=list(cols), var_name="k", value_name="v").dropna()
        long["구분"] = long["k"].map(cols)
        right = alt.Chart(long).mark_line(strokeWidth=1.8, interpolate="monotone").encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False)),
            y=alt.Y("v:Q", title=y_title, axis=alt.Axis(orient="right")),
            color=alt.Color("구분:N", scale=alt.Scale(domain=list(cols.values()), range=["#F59E0B", "#0F9D8A", "#E5484D", "#8B5CF6"][:len(cols)]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("v:Q", format=",.1f")])
        layers = []
        if shade is not None and len(shade):
            layers.append(alt.Chart(shade).mark_rect(color="#F8B4B8", opacity=.35).encode(x="s:T", x2="e:T"))
        if base is not None:
            layers.append(base.mark_line(color="#3B6FD8", strokeWidth=1.6, interpolate="monotone").encode(
                x="date:T", y=alt.Y("KOSPI:Q", title="KOSPI", scale=alt.Scale(zero=False), axis=alt.Axis(orient="left")),
                tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("KOSPI:Q", format=",.2f")]))
        rlay = [right] + [alt.Chart(pd.DataFrame({"y": [y]})).mark_rule(color=c, strokeDash=[4, 4]).encode(y="y:Q") for y, c in (rules or [])]
        ch = alt.layer(*layers, alt.layer(*rlay)).resolve_scale(y="independent").properties(height=280, title=title)
        st.altair_chart(ch, width="stretch")

    # 20/200일선 하락비율 — 20일선 하락비율 80%↑ 구간 음영(과매도 접근)
    hot = h["down20"] >= 80
    grp = (hot != hot.shift()).cumsum()
    shade = pd.DataFrame([{"s": g["date"].iloc[0], "e": g["date"].iloc[-1] + pd.Timedelta(days=1)} for _, g in h[hot].groupby(grp[hot])])
    dual(h[["date", "down20", "down200"]], {"down200": "200일선 하락비율", "down20": "20일선 하락비율"},
         "20일선 / 200일선 하락비율 (음영 = 20일선 하락비율 80%↑)", "%", shade=shade)
    st.caption("이평선 아래 종목 비율 — 높을수록 약세장. 20일선 90%↑는 통상 과매도권(분할 저가매수는 가치 계좌), 20%↓는 과열권.")
    if h["nh_pct"].notna().any():
        dual(h[["date", "nh_pct", "nl_pct"]], {"nh_pct": "52주 신고가 비율", "nl_pct": "52주 신저가 비율"},
             "52주 신고가 / 신저가 비율", "%")
        last = h.dropna(subset=["net"]).iloc[-1]
        st.caption(f"오늘 신고가 {last['nh']:.0f} · 신저가 {last['nl']:.0f} · 순증 {last['net']:+.0f} — 순증이 양이면 신고가 우세(돌파매매 환경), 음이면 신저가 우세.")
    if h["adr"].notna().any():
        dual(h[["date", "adr"]], {"adr": "ADR(20일)"}, "ADR — 20일 상승 종목 합 ÷ 하락 종목 합 × 100", "ADR",
             rules=[(120, "#E5484D"), (100, "#9CA3AF"), (75, "#1F66C9")])
        st.caption("100 기준선 위면 매수 우위, 120 이상 과열, 75 이하 침체.")
    if dp is not None and len(dp) >= 2:
        d2 = dp.copy()
        d2["date"] = pd.to_datetime(d2["date"])
        d2["예탁금(조)"], d2["신용잔고(조)"] = d2["deposit"] / 10000, d2["credit"] / 10000
        long = d2.melt("date", value_vars=["예탁금(조)", "신용잔고(조)"], var_name="구분", value_name="조")
        st.altair_chart(alt.Chart(long).mark_line(strokeWidth=1.8, point=True).encode(
            x=alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0)),
            y=alt.Y("조:Q", title="조원"), color=alt.Color("구분:N", scale=alt.Scale(range=["#2B7FF0", "#E5484D"]),
                                                       legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("조:Q", format=".2f")],
        ).properties(height=240, title="증시 자금 — 투자자예탁금 · 신용잔고"), width="stretch")
        st.caption("예탁금 = 주식 사려고 대기 중인 돈 · 신용잔고 = 빚내서 산 돈. 신용잔고/예탁금이 높을수록 반대매매 위험이 커요. 출처: 네이버 금융 증시자금동향.")
    st.caption(f"시장 체력은 전체 상장사가 아니라 보드에 올라온 국내 {int(h['n'].iloc[-1])}종목의 일봉으로 계산한 근사치예요 "
               "(사이드바 종목 범위를 '주요500+신고가'로 두면 시장 전체에 가까워져요).")


def render_market_signal(df: pd.DataFrame):
    _sec_h("🚦 시장 신호", "오닐의 분산일·FTD로 지금 시장이 어디쯤인지 — 상승 추세 · 상승 둔화 · 조정 국면 · 반등 시도")
    d = msig.MKT_DEFAULTS
    with st.expander("⚙️ 기준"):
        c1, c2, c3, c4 = st.columns(4)
        ftd = c1.selectbox("FTD 상승 폭", [1.25, 1.5, 2.0, 1.0], format_func=lambda x: f"+{x}%" + {1.25: " (IBD)", 2.0: " (책 개정판)",
                                                                                              1.0: " (책 초판)"}.get(x, ""), key="ms_ftd")
        day = c2.selectbox("FTD 가장 빠른 날", [4, 3], format_func=lambda x: f"Day {x}" + (" (강한 장)" if x == 3 else ""), key="ms_day")
        corr = c3.selectbox("조정 국면 기준(고점 대비)", [10.0, 8.0, 15.0, 20.0], format_func=lambda x: f"-{x:.0f}%"
                            + (" (하락장)" if x == 20 else ""), key="ms_corr")
        ddp = c4.selectbox("분산일 하락 폭", [0.2, 0.5, 1.0], format_func=lambda x: f"-{x}%" + (" (책)" if x == 0.2 else ""), key="ms_dd")
    p = {"ftd_pct": float(ftd), "ftd_day": int(day), "corr_pct": float(corr), "dd_pct": float(ddp)}
    sig = market_signals(p)
    k = st.columns(2)
    for col, (name, m) in zip(k, sig.items()):
        col.markdown(_mkt_card(name, m), unsafe_allow_html=True)
    ms = [m for m in sig.values() if m]
    if not ms:
        st.info("지수 일봉을 받는 중이에요.")
        return
    worst = max(ms, key=lambda m: {"corr": 3, "rally": 2, "press": 1, "up": 0}[m["state"]])
    head, body = msig.action_text(worst)
    st.markdown(f"<div class='verdict'><b>{head}</b><br>{body}</div>", unsafe_allow_html=True)
    try:
        _render_health_charts(df)
    except Exception as _e:  # noqa: BLE001
        st.caption(f"시장 체력 차트를 그리지 못했어요({type(_e).__name__}).")
    if not all(m["has_vol"] for m in ms):
        st.caption("⚠️ 지수 거래량을 못 받은 날이 있어 분산일·FTD 거래량 조건을 일부 건너뛰었어요.")

    pick = st.segmented_control("지수", list(sig), default=list(sig)[0], key="ms_pick", label_visibility="collapsed") or list(sig)[0]
    m = sig.get(pick)
    if m:
        sr = m["series"].tail(250).copy()
        sr["국면"] = sr["state"].map(msig.STATE_TXT)
        base = alt.Chart(sr).encode(x=alt.X("date:T", title=None, axis=alt.Axis(format="%y.%m", labelAngle=0, grid=False)))
        line = base.mark_line(color="#26343E", strokeWidth=1.6).encode(
            y=alt.Y("close:Q", title=None, scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("close:Q", format=",.2f"), "국면:N",
                     alt.Tooltip("dd:Q", title="분산일")])
        band = base.mark_rect(opacity=.13).encode(
            x2="date:T", color=alt.Color("국면:N", scale=alt.Scale(domain=list(msig.STATE_TXT.values()),
                                                                  range=["#1B8A4B", "#E0A100", "#C0262E", "#E8730C"]),
                                         legend=alt.Legend(orient="top", title=None)))
        fd = pd.DataFrame([{"date": pd.Timestamp(f["date"]), "close": float(sr.set_index("date")["close"].get(pd.Timestamp(f["date"]), np.nan)),
                            "결과": f["result"]} for f in m["ftds"] if pd.Timestamp(f["date"]) >= sr["date"].iloc[0]])
        layers = [line]
        if len(fd):
            layers.append(alt.Chart(fd).mark_point(shape="triangle-up", size=140, filled=True, color="#5B47F5").encode(
                x="date:T", y="close:Q", tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d", title="FTD"), "결과:N"]))
        dl = pd.DataFrame([{"date": pd.Timestamp(x["date"]), "close": float(sr.set_index("date")["close"].get(pd.Timestamp(x["date"]), np.nan)),
                            "종류": x["kind"]} for x in m["dd_list"]])
        if len(dl):
            layers.append(alt.Chart(dl).mark_point(shape="triangle-down", size=90, filled=True, color="#C0262E").encode(
                x="date:T", y="close:Q", tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "종류:N"]))
        st.altair_chart(alt.layer(*layers).properties(height=300, width="container"))
        st.caption("▲ 보라 = FTD · ▼ 빨강 = 지금 살아 있는 분산일 · 배경색 = 그날의 국면")
        t1, t2 = st.tabs([f"📉 분산일 {m['dd']}개", f"🚀 FTD 기록 {len(m['ftds'])}"])
        with t1:
            if m["dd_list"]:
                st.dataframe(pd.DataFrame([{"날짜": f"{pd.Timestamp(x['date']):%Y-%m-%d}", "종류": x["kind"], "등락": x["chg"],
                                            "거래량(전날 대비)": x["vol_x"], "남은 날": f"{x['left']}일"} for x in m["dd_list"]])
                             .style.format({"등락": "{:+.2f}%", "거래량(전날 대비)": "{:.2f}배"}, na_rep="-"),
                             hide_index=True, width="stretch")
            else:
                st.caption("지금 살아 있는 분산일이 없어요(또는 조정·반등 시도 국면이라 세지 않아요).")
            st.caption(f"분산일 = 전날보다 거래량이 늘며 {p['dd_pct']}% 넘게 내린 날 + 정체일(거래량은 늘었는데 0.3% 못 오르고 아래쪽 마감). "
                       "25거래일 지나거나 그 뒤 지수가 그날 종가보다 5% 넘게 오르면 지우고, FTD가 나오면 0으로. "
                       f"한국 보정: 같은 지수의 지난 상승 국면에서 25일 분산일 개수는 보통 {m['q50']:.0f}개, 상위 20%가 {m['q80']:.0f}개 — "
                       "이보다 많으면 '상승 둔화'로 봐요(깡토 백테스트: 책 기준 4~5개는 코스피에서 상승장 아무 날의 87%가 해당해 의미가 약함).")
        with t2:
            fs = msig.ftd_stats(m["ftds"])
            if fs.get("n"):
                st.markdown(f"지난 약 6년 {pick} FTD **{fs['n']}번** 중 60일 안에 랠리 저점을 안 깬 것 **{fs['ok']}번({fs['rate']:.0f}%)** · "
                            f"60일 뒤 수익률 중앙값 **{fs['r60']:+.1f}%**" if fs.get("r60") is not None else "")
            if m["ftds"]:
                st.dataframe(pd.DataFrame([{"FTD": f"{pd.Timestamp(f['date']):%Y-%m-%d}", "바닥": f"{pd.Timestamp(f['low_date']):%m/%d}",
                                            "Day": f["day"], "당일": f["chg"], "거래량": f["vol_x"], "바닥까지 하락": f["draw"],
                                            "결과": f["result"] + (f" ({pd.Timestamp(f['fail_date']):%m/%d})" if f["fail_date"] is not None else ""),
                                            "5일 뒤": f["r5"], "20일 뒤": f["r20"], "60일 뒤": f["r60"]} for f in m["ftds"]])
                             .style.format({"당일": "{:+.2f}%", "거래량": "{:.2f}배", "바닥까지 하락": "{:.1f}%", "5일 뒤": "{:+.1f}%",
                                            "20일 뒤": "{:+.1f}%", "60일 뒤": "{:+.1f}%"}, na_rep="-")
                             .map(_won_c, subset=["5일 뒤", "20일 뒤", "60일 뒤"]), hide_index=True, width="stretch", height=320)
            st.caption(f"FTD = 조정(고점 -{p['corr_pct']:.0f}%↑) 뒤 가장 낮은 저가 다음 처음 오른 날(랠리 데이) = Day 1, Day {p['ftd_day']} 이후 "
                       f"+{p['ftd_pct']}%↑ 상승 + 거래량 전날보다 증가. 실패 = 그 뒤 랠리 저점 이탈. "
                       "깡토 백테스트(1990~2026): 하락장 바닥 뒤엔 항상 FTD(코스피 25/25)였지만 하락장 FTD의 절반 가까이는 실패 — FTD는 '허락'이지 '공격'이 아님.")

    # 하락장에서 볼 종목: 견조주 vs 과대낙폭
    st.markdown("#### 🛡️ 버티는 종목 vs 무너진 종목")
    kr = df[df["code"].map(data.is_kr)].copy()
    kr["gap"] = pd.to_numeric(kr["gap"], errors="coerce")
    tv = pd.to_numeric(kr.get("tv20"), errors="coerce") if "tv20" in kr else pd.Series(np.nan, index=kr.index)
    kr = kr[(tv >= 10e8) & (pd.to_numeric(kr["price"], errors="coerce") >= 1000) & kr["gap"].notna()]
    if len(kr) >= 20:
        q90 = kr["gap"].quantile(0.9)
        strong = kr[kr["gap"] >= q90].sort_values(["gap", "rs"], ascending=[False, False]).head(30)
        crash = kr[kr["gap"] <= -60].sort_values("gap").head(30)
        a, b = st.columns(2)
        with a:
            st.markdown(f"**🛡️ 견조주 — 52주 고점에 가장 가까운 상위 10%** ({len(kr[kr['gap'] >= q90])}종목)")
            st.dataframe(strong[["name", "group", "gap", "rs", "change"]].rename(columns={"name": "종목", "group": "분류", "gap": "고점 대비",
                                                                                        "rs": "RS", "change": "오늘"})
                         .style.format({"고점 대비": "{:+.1f}%", "RS": "{:.0f}", "오늘": "{:+.2f}%"}, na_rep="-").map(_won_c, subset=["오늘"]),
                         hide_index=True, width="stretch", height=320)
        with b:
            st.markdown(f"**⚠️ 과대낙폭 — 고점 대비 -60% 넘게** ({int((kr['gap'] <= -60).sum())}종목)")
            if len(crash):
                st.dataframe(crash[["name", "group", "gap", "rs", "change"]].rename(columns={"name": "종목", "group": "분류", "gap": "고점 대비",
                                                                                           "rs": "RS", "change": "오늘"})
                             .style.format({"고점 대비": "{:+.1f}%", "RS": "{:.0f}", "오늘": "{:+.2f}%"}, na_rep="-").map(_won_c, subset=["오늘"]),
                             hide_index=True, width="stretch", height=320)
            else:
                st.caption("고점 대비 -60% 넘게 빠진 종목이 없어요.")
        st.caption("깡토 백테스트(2018.10~2026.10, 하락장 날 56번): 과대낙폭(하위 10%, 평균 -67%)은 1개월 반등이 컸지만 1년 뒤 중앙값 -19.9%·오른 비율 29%. "
                   "견조주(상위 10%, 평균 -8%)는 1년 +16.7%, FTD 뒤 반년은 17번 중 15번 과대낙폭을 앞섬. 과대낙폭을 산다면 짧게·나올 자리 먼저·물타기 금지. "
                   "거래대금 20일 평균 10억↑·주가 1,000원↑ 종목만.")
    _views_box_kt()
    _views_box_kq(("시장", "재진입", "정찰병", "핵심"))


def _views_box_kq(keys: tuple[str, ...] | None = None, title: str = "📚 관점 노트 — 깡토 급락장 Q&A(2026.10)"):
    with st.expander(title):
        for k, t, x in views.KQ_VIEWS:
            if keys is None or k in keys:
                st.markdown(f"- **{t}** — {x}")
        st.caption(f"출처: {views.KQ_SRC} · 효석 원칙(리스크관리 > 멘탈 > 종목 > 타점)이 항상 우선이고, 이 관점은 재량 참고예요.")


def _views_box_kt():
    with st.expander("📚 관점 노트 — 깡토(분산일·FTD·하락장) × 리 태너"):
        for k, t, x in views.KT_VIEWS:
            st.markdown(f"- **{t}** — {x}")
        for k, t, x in views.LT_VIEWS:
            if k in ("시장",):
                st.markdown(f"- **[리 태너] {t}** — {x}")
        st.caption(f"출처: {views.KT_SRC} · {views.LT_SRC}")


# ─────────────────────────── 🗺️ 큰 그림: AI · 비AI · 그외 → 테마 ───────────────────────────
BP_CSS = """
<style>
.bp-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.8rem;margin:.2rem 0 1rem}
.bp-card{background:#fff;border:1px solid #ECEEF5;border-radius:16px;padding:.9rem 1rem;box-shadow:0 1px 2px rgba(16,24,40,.04),0 4px 18px rgba(91,71,245,.05);min-width:0}
.bp-h{display:flex;justify-content:space-between;align-items:baseline;gap:.4rem;margin-bottom:.15rem}
.bp-h b{font-size:1.12rem;font-weight:850;color:#111827}
.bp-h span{font-size:1.02rem;font-weight:850}
.bp-s{font-size:.76rem;color:#6B7280;margin-bottom:.55rem}
.bp-t{display:grid;grid-template-columns:minmax(0,1.3fr) 64px minmax(0,1fr);gap:.5rem;align-items:center;padding:.45rem 0;border-top:1px solid #F1F3F8}
.bp-t:first-of-type{border-top:none}
.bp-t .n{font-weight:800;font-size:.9rem;color:#111827}
.bp-t .n small{display:block;font-weight:600;font-size:.7rem;color:#9CA3AF;margin-top:.05rem}
.bp-t .c{font-weight:850;font-size:.9rem;text-align:right;white-space:nowrap}
.bp-t .m{font-size:.72rem;color:#4B5563;line-height:1.35;min-width:0}
.bp-t .m i{font-style:normal;color:#9CA3AF}
.bp-bar{height:5px;border-radius:5px;background:#EEF1F6;margin-top:.25rem;overflow:hidden}
.bp-bar div{height:100%;border-radius:5px}
.bp-hot{display:inline-block;font-size:.66rem;font-weight:800;color:#C0262E;background:#FDECEC;border-radius:6px;padding:.03rem .35rem;margin-left:.25rem}
@media (max-width:900px){.bp-row{grid-template-columns:minmax(0,1fr)}}
</style>
"""


def _bp_cards(s: pd.DataFrame, top_n: int | None = None) -> str:
    """영역 3칸 카드(테마 줄마다 등락 중앙값 · 오른 비율 막대 · 신고가 · 대장)."""
    bs = bigpicture.bucket_summary(s)
    cards = []
    for b, em, themes in bigpicture.BIG:
        g = s[s["bucket"] == b].set_index("theme")
        info = bs.get(b)
        if not info:
            continue
        col = UP if (info["chg"] or 0) > 0 else (DOWN if (info["chg"] or 0) < 0 else "#6B7280")
        rows = []
        order = [t for t in themes if t in g.index]
        if top_n:
            order = list(g.loc[order].sort_values("score", ascending=False).index[:top_n])
        for t in order:
            r = g.loc[t]
            ch = r["chg"]
            chs = "-" if ch is None or ch != ch else f"{ch:+.1f}%"
            ccol = UP if (ch or 0) > 0 else (DOWN if (ch or 0) < 0 else "#6B7280")
            up = r["up"] or 0
            hot = '<span class="bp-hot">신고가 확산</span>' if r["nh5"] >= 2 else ""
            lead = f"대장 {r['leader']}" if r["leader"] else ""
            best = f"오늘 최강 {r['best']} {r['best_chg']:+.1f}%" if r["best"] and r["best_chg"] is not None else ""
            rows.append(f'<div class="bp-t"><div class="n">{html.escape(t)}{hot}<small>{int(r["n"])}종목 · 신고가 오늘 {int(r["nh0"])} · 5일 {int(r["nh5"])}'
                        + (f' · 20일선 위 {r["a20"]:.0f}%' if r["a20"] is not None else "") + '</small>'
                        f'<div class="bp-bar"><div style="width:{up:.0f}%;background:{ccol}"></div></div></div>'
                        f'<div class="c" style="color:{ccol}">{chs}</div>'
                        f'<div class="m">{html.escape(lead)}<br><i>{html.escape(best)}</i></div></div>')
        bchg = "-" if info["chg"] is None else f"{info['chg']:+.1f}%"
        cards.append(f'<div class="bp-card"><div class="bp-h"><b>{em} {b}</b><span style="color:{col}">'
                     f'{bchg}</span></div>'
                     f'<div class="bp-s">오른 테마 {info["n_up"]}/{info["n"]} · 최근 5일 신고가 {info["nh5"]} · 막대 = 테마 안 오른 종목 비율</div>'
                     + "".join(rows) + '</div>')
    return BP_CSS + f'<div class="bp-row">{"".join(cards)}</div>'


def render_bigpicture(df: pd.DataFrame):
    _sec_h("🗺️ 큰 그림", "시장을 AI · 비AI · 그외 영역으로 먼저 나누고, 그 안에서 어떤 테마에 돈이 도는지 봐요")
    c1, c2 = st.columns([1, 1])
    scope = c1.segmented_control("범위", ["국내", "국내+해외"], default="국내", key="bp_scope", label_visibility="collapsed") or "국내"
    sort = c2.segmented_control("정렬", ["분류 순서", "강한 순"], default="분류 순서", key="bp_sort", label_visibility="collapsed") or "분류 순서"
    d = df if scope == "국내+해외" else df[df["code"].map(data.is_kr)]
    s = tab_memo("bigpicture", (scope, len(d)), lambda: bigpicture.summary(d, recent_days))
    if s.empty:
        st.info("표를 계산하는 중이에요.")
        return
    st.markdown(_bp_cards(s if sort == "분류 순서" else s.sort_values("score", ascending=False)), unsafe_allow_html=True)
    top = s.sort_values("score", ascending=False).head(5)
    st.caption("강한 테마 TOP 5 (오늘 등락 + 최근 신고가 확산 + 20일선 위 비율): "
               + " · ".join(f"{r.theme}({r.bucket}) {r.chg:+.1f}%" for r in top.itertuples() if r.chg is not None)
               + " — 등락은 테마 안 종목 등락률의 중앙값, 여러 테마에 걸친 종목은 각 테마에 모두 들어가요.")

    st.markdown("##### 테마 안 종목 보기")
    th = st.selectbox("테마", bigpicture.THEME_ORDER, key="bp_theme",
                      format_func=lambda t: f"{bigpicture.BUCKET_OF[t]} · {t}")
    t = bigpicture.tag(d)
    v = t[t["themes"].map(lambda xs: th in xs)].copy()
    if v.empty:
        st.info("이 테마에 해당하는 종목이 지금 범위에 없어요.")
        return
    v = v.sort_values(["days_since_high", "rs"], ascending=[True, False], na_position="last")
    tb = pd.DataFrame({"종목": v["name"] + " · " + v["code"], "차트": "?chart=" + v["code"], "세부 분류": v["group"],
                       "현재가": pd.to_numeric(v["price"], errors="coerce"), "등락률": pd.to_numeric(v["change"], errors="coerce"),
                       "신고가까지": pd.to_numeric(v["to_high"], errors="coerce"),
                       "신고가 뒤(일)": pd.to_numeric(v["days_since_high"], errors="coerce"),
                       "RS": pd.to_numeric(v["rs"], errors="coerce"),
                       "다른 테마": v["themes"].map(lambda xs: " · ".join(x for x in xs if x != th))})
    st.dataframe(tb.style.format({"현재가": "{:,.0f}", "등락률": "{:+.2f}%", "신고가까지": "{:.1f}%", "신고가 뒤(일)": "{:.0f}", "RS": "{:.0f}"},
                                 na_rep="-").map(_won_c, subset=["등락률"]),
                 hide_index=True, width="stretch", height=min(560, 36 * (len(tb) + 1) + 4),
                 column_config={"차트": st.column_config.LinkColumn("차트", display_text="🕯️")})
    with st.expander("ℹ️ 분류 기준"):
        for b, em, themes in bigpicture.BIG:
            st.markdown(f"**{em} {b}** — " + " · ".join(themes))
        st.caption("세부 분류 → 테마 지도는 bigpicture.py. 한 종목이 여러 테마에 걸치면(예: 삼성전기 = 반도체소부장·MLCC·유리기판) 모두 셉니다.")


# ─────────────────────────── 🌙 종가배팅: 4인 관점(M · K · C · D) ───────────────────────────
ZB_CSS = """
<style>
.zb-top{display:grid;grid-template-columns:1.1fr 1fr;gap:.8rem;margin:.2rem 0 .8rem}
.zb-box{background:#fff;border:1px solid #ECEEF5;border-radius:14px;padding:.75rem .9rem}
.zb-box b{font-size:.98rem;color:#111827}.zb-box p{margin:.25rem 0 0;font-size:.86rem;color:#374151;line-height:1.55}
.zb-box.sum{border-color:#F3C77A;background:#FFFBF2}
.zb-hl{color:#B45309;font-weight:800}
.zb-g{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.6rem;margin:.2rem 0 .4rem}
.zb-c{background:#fff;border:1px solid #ECEEF5;border-radius:14px;padding:.65rem .75rem;min-width:0}
.zb-c .h{display:flex;justify-content:space-between;align-items:center;margin-bottom:.35rem}
.zb-c .h b{font-size:.95rem;color:#111827;display:flex;align-items:center;gap:.35rem}
.zb-c .h b i{font-style:normal;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;
             color:#fff;font-size:.72rem;font-weight:900}
.zb-c .h span{font-size:.72rem;font-weight:800;border-radius:6px;padding:.1rem .45rem;background:#F3F4F6}
.zb-c div.l{font-size:.82rem;line-height:1.55;color:#374151}
.zb-c div.l.ok{color:#15803D}.zb-c div.l.no{color:#C0262E}.zb-c div.l.q{color:#6B7280}
.zb-t{width:100%;border-collapse:collapse;font-size:.84rem}
.zb-t th,.zb-t td{border-bottom:1px solid #F1F3F8;padding:.5rem .55rem;text-align:left;vertical-align:top;line-height:1.5}
.zb-t th{background:#F7F7FB;color:#6B7280;font-weight:700}
.zb-t td.sec{color:#B45309;font-weight:850;background:#FFFBF2}
.zb-t td.k{color:#6B7280;font-weight:700;white-space:nowrap}
.zb-dot{display:inline-flex;width:18px;height:18px;border-radius:50%;color:#fff;font-size:.66rem;font-weight:900;align-items:center;justify-content:center;margin-right:.25rem}
@media (max-width:900px){.zb-top{grid-template-columns:1fr}.zb-g{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:520px){.zb-g{grid-template-columns:1fr}}
</style>
"""


def _zb_cards(ck: dict) -> str:
    out = []
    for k, items in ck.items():
        v = zongbae.VIEWS[k]
        lab, col = zongbae.verdict(items)
        rows = "".join(f'<div class="l {"ok" if s == "✓" else ("no" if s == "✕" else "q")}">{"✓" if s == "✓" else ("×" if s == "✕" else "?")} '
                       f'{html.escape(t)}</div>' for s, t in items)
        out.append(f'<div class="zb-c"><div class="h"><b><i style="background:{v["color"]}">{k}</i>'
                   f'{"마스터" if k == "M" else k + " 관점"}</b><span style="color:{col}">{lab}</span></div>{rows}</div>')
    return f'<div class="zb-g">{"".join(out)}</div>'


def _zb_table() -> str:
    head = "".join(f'<th><span class="zb-dot" style="background:{v["color"]}">{k}</span>{"마스터(M)" if k == "M" else k}</th>'
                   for k, v in zongbae.VIEWS.items())
    body = []
    for lab, key in zongbae.ROWS:
        if key is None:
            body.append(f'<tr><td class="sec" colspan="5">{lab}</td></tr>')
        else:
            body.append(f'<tr><td class="k">{lab}</td>' + "".join(f"<td>{html.escape(v[key])}</td>" for v in zongbae.VIEWS.values()) + "</tr>")
    return (f'<div style="overflow-x:auto"><table class="zb-t"><thead><tr><th>항목</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def render_zongbae(df: pd.DataFrame):
    _sec_h("🌙 종가배팅", "4인 관점(M 마스터 · K · C · D)으로 오늘 종목을 자동 점검 — 큰 거래대금 · 신고가 자리 · 장 막판 진입 · 다음날 짧게")
    st.markdown(ZB_CSS, unsafe_allow_html=True)
    now = data.now_kst()
    hm = now.hour * 60 + now.minute
    before_cut = now.weekday() < 5 and hm < 14 * 60 + 40
    kind, why, fit = zongbae.market_type(load_overview(), df)
    if hm < 14 * 60 + 30 and now.weekday() < 5:
        clock = f"지금 {now:%H:%M} — 아직 진입 시각 전(C 14:30 · M·K 14:40 · D 15:00). 지금은 후보를 고르는 시간"
    elif hm < 15 * 60 + 30 and now.weekday() < 5:
        clock = f"지금 {now:%H:%M} — 진입 시간대(C 14:30↑ · M·K 14:40↑ · D 15:00↑). 고가 근처에서 버티는지 확인"
    else:
        clock = f"지금 {now:%H:%M} — 장 마감 뒤. D는 애프터장 19:00~19:30, 나머지는 내일 아침 정리 계획 점검"
    st.markdown(f'<div class="zb-top"><div class="zb-box"><b>📊 오늘 장세: {html.escape(kind)}</b><p>{html.escape(why)}</p>'
                f'<p>잘 맞는 장세 — 재료·차트형(M·K) = 테마주 장세 · 수급형(C·D) = 지수 상승장</p></div>'
                f'<div class="zb-box"><b>⏰ 시간</b><p>{html.escape(clock)}</p>'
                '<p>종배는 <span class="zb-hl">종배 전용 계좌</span> — 추세 계좌와 섞지 않아요. 다음날 짧게 · 빠른 손절이 공통 원칙.</p></div></div>',
                unsafe_allow_html=True)

    c1, c2 = st.columns([1, 1])
    min_chg = c1.slider("후보: 오늘 등락 %↑", 3.0, 15.0, 5.0, 0.5, key="zb_chg")
    min_tv = c2.slider("후보: 거래대금 억↑", 100, 1500, 300, 50, key="zb_tv")
    cand = zongbae.candidates(df, min_chg, min_tv)
    t1, t2 = st.tabs(["🔎 오늘 후보 자동 점검", "📋 4인 비교표 · 두 갈래 · 공통"])
    with t1:
        if cand.empty:
            st.info("조건에 맞는 오늘 후보가 없어요. 등락·거래대금 기준을 낮춰 보세요.")
        else:
            codes = tuple(cand["code"].head(40))
            try:
                trends = {**(_TRENDS_BG.get("v") or {}), **load_trends(tuple(c for c in codes if (_TRENDS_BG.get("v") or {}).get(c) is None))}
            except Exception:  # noqa: BLE001
                trends = _TRENDS_BG.get("v") or {}
            grp_hot = cand.groupby("group").size()
            rows, cks = [], {}
            for r in cand.head(40).to_dict("records"):
                fl = data.trend_summary(trends.get(r["code"])) if trends.get(r["code"]) is not None else None
                ck = zongbae.checks(r, fl, bool(grp_hot.get(r["group"], 0) >= 2), before_cut)
                cks[r["code"]] = (r, ck)
                v = {k: zongbae.verdict(x)[0] for k, x in ck.items()}
                gp = zongbae.gap_pct(r)
                rows.append({"종목": f"{r['name']} · {r['code']}", "차트": f"?chart={r['code']}", "분류": r["group"],
                             "등락률": r["change"], "거래대금(억)": (r.get("tv_live") or 0) / 1e8, "시가갭": gp,
                             "시총(억)": (r.get("cap_krw") or 0) / 1e8, "종가 위치": r.get("dcr_today"),
                             "M": v["M"], "K": v["K"], "C": v["C"], "D": v["D"],
                             "_n": sum(1 for x in v.values() if x == "가까움")})
            tb = pd.DataFrame(rows).sort_values(["_n", "거래대금(억)"], ascending=False).drop(columns="_n")
            colmap = {"가까움": "background-color:#E9F7EE;color:#15803D;font-weight:700",
                      "수급 보면 됨": "background-color:#FFF7E8;color:#B45309;font-weight:700",
                      "하나 부족": "color:#B45309", "멀어요": "color:#9CA3AF"}
            st.dataframe(tb.style.format({"등락률": "{:+.2f}%", "거래대금(억)": "{:,.0f}", "시가갭": "{:+.1f}%", "시총(억)": "{:,.0f}",
                                          "종가 위치": "{:.0f}%"}, na_rep="-")
                         .map(lambda x: colmap.get(x, ""), subset=["M", "K", "C", "D"]).map(_won_c, subset=["등락률"]),
                         hide_index=True, width="stretch", height=min(520, 36 * (len(tb) + 1) + 4),
                         column_config={"차트": st.column_config.LinkColumn("차트", display_text="🕯️")})
            st.caption("가까움 = 안 맞는 조건(×) 없음 · 수급 보면 됨 = 수급(?)만 남음 · 하나 부족 = × 1개 · "
                       "종가 위치 = 오늘 고가~저가 중 현재가 위치(100%면 고가 마감). 프로그램 매매·분봉 조건은 보드에 없어 HTS로 확인.")
            pick = st.selectbox("종목 자세히", list(tb["종목"]), key="zb_pick")
            code = pick.split(" · ")[-1]
            r, ck = cks[code]
            st.markdown(f'<div class="zb-box sum"><b>🕒 한 줄 정리 (자동)</b><p>{html.escape(zongbae.summary_line(r, ck))}</p></div>',
                        unsafe_allow_html=True)
            st.markdown(_zb_cards(ck), unsafe_allow_html=True)
            st.caption("✓ 맞음 · × 안 맞음 · ? 자료 연결 뒤 자동(수급) 또는 HTS 확인 · 공부용 비교이며 매매 판단이 아니에요.")
    with t2:
        st.markdown(_zb_table(), unsafe_allow_html=True)
        st.markdown("".join(f'<div class="zb-box" style="margin-top:.6rem"><b>{html.escape(a)} — {html.escape(b)}</b><p>{html.escape(c)}</p></div>'
                            for a, b, c in zongbae.TWO_WAYS), unsafe_allow_html=True)
        st.markdown(f'<div class="zb-box" style="margin-top:.6rem"><b>4인 공통</b><p>{" · ".join(zongbae.COMMON)}</p></div>',
                    unsafe_allow_html=True)
        st.caption("2026-10-17 강의 정리 · 숫자는 강의 기준 '대략'이며 절대 조건이 아니에요.")


def render_early(df: pd.DataFrame, histories: dict, quotes: dict, fins_all=None):
    _sec_h("🎯 초기 매수", "리 태너 — 표준 피벗보다 싸게 사는 자리: 하락 추세선 돌파 · 손잡이 저점(조용한 거래량) · Shakeout +3")
    d = msig.EE_DEFAULTS
    with st.expander("⚙️ 기준"):
        c1, c2, c3, c4 = st.columns(4)
        rs_min = c1.number_input("RS 최소", 50, 99, 70, key="ee_rs")
        shake = c2.selectbox("Shakeout 회복 폭(첫 저점 +)", [5.0, 3.0, 10.0], format_func=lambda x: f"+{x:.0f}%", key="ee_shake",
                             help="태너: 30달러 미만 +10%, 30~60달러 +3달러(약 5~10%), 60달러 초과 +5%")
        near = c3.number_input("피벗까지 최대 거리(%)", 3.0, 30.0, d["near_pivot"], 1.0, key="ee_near")
        need_op = c4.toggle("올해 영업이익 증가만", value=True, key="ee_op")
    p = {"shake_pct": float(shake), "near_pivot": float(near)}
    sig = market_signals()
    mk = [m for m in sig.values() if m]
    states = " · ".join(f"{n} {m['em']} {m['state_txt']}" for n, m in sig.items() if m)
    bad_mkt = any(m["state"] in ("corr", "rally") for m in mk)
    today = data.now_kst().date()
    rows = []
    with st.spinner("베이스 안 초기 매수 자리 찾는 중…"):
        for r in df[df["code"].map(data.is_kr)].to_dict("records"):
            if (r.get("rs") or 0) < rs_min:
                continue
            hist = (histories.get(r["code"]) or (None, None, None))[1]
            if hist is None or len(hist) < 120:
                continue
            try:
                dd_, o_, h_, l_, c_, v_ = data._session_bars(hist, quotes.get(r["code"]), today)
                res = msig.early_entry(dd_, o_, h_, l_, c_, v_, p)
            except Exception:  # noqa: BLE001
                res = None
            if res:
                og = data.op_growth((fins_all or {}).get(r["code"]))
                if need_op and og.get("op_up") is not True:
                    continue
                rows.append({**r, **res, "op_txt": og.get("op_txt")})
    tiles = [_tile(not bad_mkt if mk else None, "시장 국면", states or "-"), _tile(True if rows else None, "초기 매수 신호", f"{len(rows)}")]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    if bad_mkt:
        st.warning("조정·반등 시도 국면이 있어요 — 태너도 FTD 전엔 새 매수를 아주 작게(또는 쉬고), 원칙상 60일선 아래면 쉬는 장이에요. 아래는 공부·대기용.")
    if not rows:
        st.caption("지금은 조건에 맞는 초기 매수 자리가 없어요.")
    else:
        t = pd.DataFrame([{"종목": f"{x['name']} · {x['code']}", "신호": x["ee_kind"], "현재가": x["price"], "표준 피벗": x["ee_pivot"],
                           "피벗보다 싸게": x["ee_below"], "손절(-7%)": x["ee_stop"], "베이스 깊이": x["ee_depth"], "베이스 길이": x["ee_len"],
                           "거래량(3일/50일)": x["ee_dry"], "RS": x.get("rs"), "영업이익": x.get("op_txt"), "분류": x.get("group"),
                           "근거": x["ee_why"]} for x in sorted(rows, key=lambda x: -(x.get("rs") or 0))])
        st.dataframe(t.style.format({"현재가": "{:,.0f}", "표준 피벗": "{:,.0f}", "피벗보다 싸게": "{:.1f}%", "손절(-7%)": "{:,.0f}",
                                     "베이스 깊이": "-{:.0f}%", "베이스 길이": "{:.0f}일", "거래량(3일/50일)": "{:.2f}배", "RS": "{:.0f}"}, na_rep="-"),
                     hide_index=True, width="stretch", height=420, column_config={"종목": st.column_config.Column(pinned=True)})
    st.caption("표준 피벗 = 베이스 왼쪽 고점(최근 15~65거래일). 초기 매수 = 그보다 낮은 가격에서 먼저 사는 자리 — 같은 7% 손절이어도 손절가가 낮아져 "
               "정상 흔들림에 덜 털려요. 태너는 돌파 거래량(+50%)을 확인하기 전에, 미리 정한 가격에 스톱 주문으로 사요. "
               "실패하면 7%까지 기다리지 않고 2~3%에서도 정리. 효석 원칙(주도섹터·RS·영업이익·시장 60일선)과 같이 보세요. 매수 권유가 아니에요.")
    with st.expander("📚 관점 노트 — 리 태너(오닐 CAN SLIM)"):
        for k, tt, x in views.LT_VIEWS:
            st.markdown(f"- **{tt}** — {x}")
        st.caption(f"출처: {views.LT_SRC}")


def render_buy(df: pd.DataFrame, quotes: dict):
    """내 매매 원칙을 모두 통과한 국내 종목. 산업·태그 필터와 관계없이 보드 전체에서 찾아요."""
    _sec_h("🎯 매수 후보", "내 원칙을 모두 통과한 국내 종목만")
    p = _buy_params()
    hist = load_indexes()
    parts, oks, up20 = [], [], {}
    for name, sym in data.MARKETS.items():
        sm = data.index_summary(hist.get(sym, (data.empty_frame(), None))[0])
        ok = None if not sm or sm.get("above60") is None else bool(sm["above60"])
        oks.append(ok)
        up20[name] = None if not sm else sm.get("above20")
        parts.append(f"{name} " + ("-" if ok is None else f"60일선 {'위' if ok else '아래'} {sm['dist60']:+.1f}%"))
    market_ok = None if None in oks else all(oks)
    idx_rs = {name: data.index_rs(hist.get(sym, (data.empty_frame(), None))[0], df).get("rs")
              for name, sym in data.MARKETS.items()}
    market_text = " · ".join(parts)
    leaders = {g for g, _ in lead_groups(df)}
    intraday = data.market_status(quotes).startswith("장중")

    pre = data.buy_screen(df, market_ok, market_text, leaders, p, fins=None, intraday=intraday, idx_rs=idx_rs)
    res = pd.DataFrame()
    if not pre.empty:
        codes = tuple(pre["code"])
        with st.spinner("후보 실적 확인 중…"):
            fins = data.fetch_financials_many(codes)
        res = data.buy_screen(df[df["code"].isin(codes)], market_ok, market_text, leaders, p, fins=fins,
                              intraday=intraday, idx_rs=idx_rs)
    counts = res["tier"].value_counts() if not res.empty else pd.Series(dtype=int)
    idx_txt = " · ".join(f"{n} {'✓' if o else ('✕' if o is False else '?')}" for n, o in zip(data.MARKETS, oks))
    tiles = [
        _tile(market_ok, "시장 60일선", idx_txt),
        _tile(bool(leaders), "주도섹터", f"{len(leaders)}개"),
        _tile(True if counts.get("매수 가능", 0) else None, "✅ 매수 가능", f"{counts.get('매수 가능', 0)}"),
        _tile(None, "⏳ 종가 확인 · ⚠️ 1개 미충족", f"{counts.get('종가 확인', 0)} · {counts.get('1개 미충족', 0)}"),
    ]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    if market_ok is False:
        st.error("⛔ 매수 쉬는 구간 — 코스피·코스닥 둘 다 60일선 위일 때만. 아래는 참고용이에요.")

    render_leaders(df, leaders)
    if res.empty:
        st.caption("원칙에 맞는 종목도, 하나만 빠지는 종목도 지금은 없어요.")
        return
    trends = data.fetch_stock_trends(tuple(res["code"]))
    labels = dict(data.BUY_RULES)
    rows = []
    for x in res.itertuples():
        r, c = x.row, x.checks
        mk_up = True if up20 and all(v is True for v in up20.values()) else None     # 코스피·코스닥 둘 다 20일선 위
        plan = data.position_plan(float(r["price"]), r.get("atr_pct"), p["equity"], {**p, "mkt_up20": mk_up})
        sm = data.trend_summary(trends.get(x.code))
        rows.append({
            "상태": f"{BUY_TIER_STYLE[x.tier][1]} {x.tier}", "종목": x.name,
            "섹터 순위": c["top"][1].split(" (")[0].split("/")[0],
            "현재가": r["price"], "신고가까지": r.get("to_high"), "돌파": _bo_full(r.to_dict()),
            "손절가": plan["stop_price"], "3R 목표가": plan["target_price"],
            "수량": plan["shares"], "매수금액": plan["amount"],
            "유닛": (f"{plan.get('unit_level')}/{plan.get('units')}" + (" (시장 +1)" if plan.get("mkt_unit") else ""))
            if plan.get("unit_level") else ("시장 +1" if plan.get("mkt_unit") else "-"),
            "미충족": ", ".join(labels[k].split("(")[0] for k in x.fails) or "-",
            "등락률": r["change"], "RS": r.get("rs"), "ADX": r.get("adx"), "20일선 이격": r.get("dist_ma20"),
            "돌파일 거래량": r.get("bo_vol_ratio"), "DCR": r.get("bo_dcr"), "손절폭": plan["stop_pct"],
            "외국인 5일(억)": sm["flow5_외국인"], "기관 5일(억)": sm["flow5_기관"], "끌고 가기": _hold_short(r),
            "분류": x.group, "메모": "HTF — 리스크 타이트" if r.get("htf") else "",
        })
    view = pd.DataFrame(rows)
    base_cols = ["상태", "종목", "섹터 순위", "현재가", "신고가까지", "돌파", "손절가", "3R 목표가"] + \
        (["수량", "매수금액", "유닛"] if p["equity"] else []) + ["미충족"]
    if not st.toggle("지표 자세히", key="buy_detail"):
        view = view[base_cols]
    elif not p["equity"]:
        view = view.drop(columns=["수량", "매수금액", "유닛"])
    fmt = {"현재가": "{:,.0f}", "등락률": "{:+.2f}%", "신고가까지": "{:+.1f}%", "RS": "{:.0f}", "ADX": "{:.0f}",
           "20일선 이격": "{:+.1f}%", "돌파일 거래량": "{:.1f}배", "DCR": "{:.0f}%", "손절가": "{:,.0f}",
           "손절폭": "{:.1f}%", "3R 목표가": "{:,.0f}", "수량": "{:,.0f}주", "매수금액": "{:,.0f}",
           "외국인 5일(억)": _eok_num, "기관 5일(억)": _eok_num}
    sty = view.style.format({k: v for k, v in fmt.items() if k in view.columns}, na_rep="-") \
        .map(lambda v: f"color: {BUY_TIER_STYLE[v.split(' ', 1)[1]][0]}; font-weight: 700", subset=["상태"]) \
        .map(_bo_color, subset=["돌파"]).map(_to_high_color, subset=["신고가까지"])
    for col in ("등락률", "외국인 5일(억)", "기관 5일(억)"):
        if col in view.columns:
            sty = sty.map(lambda v: "" if pd.isna(v) or v == 0 else f"color: {UP if v > 0 else DOWN}", subset=[col])
    st.dataframe(sty, hide_index=True, height=min(520, 36 * (len(view) + 1) + 4),
                 column_config={"종목": st.column_config.Column(pinned=True),
                                "돌파": st.column_config.Column(help=BO_FULL_HELP)})

    good = res[res["tier"].isin(["매수 가능", "종가 확인", "시장 대기"])]
    for x in good.itertuples():
        with st.expander(f"{BUY_TIER_STYLE[x.tier][1]} {x.name} · 조건 체크"):
            st.markdown('<div class="tiles">' + "".join(
                _tile(x.checks[k][0], lab.split("(")[0], x.checks[k][1][:22]) for k, lab in data.BUY_RULES) + "</div>",
                unsafe_allow_html=True)
            if x.tier == "종가 확인":
                st.caption("오늘 장중 돌파 — 종가가 직전 52주 최고가 위에서 마감하는지 확인.")
    with st.expander("ℹ️ 표 읽는 법"):
        st.markdown("- ⏳ 종가 확인 = 오늘 장중 돌파 · ⛔ 시장 대기 = 종목은 통과, 시장 미충족 · ⚠️ 1개 미충족 = 관심 종목(매수 대상 아님)\n"
                    "- 손절가 = 현재가 기준 1R(8%, ATR이 8% 이상이면 ATR) · 수량 = 계좌 1.5% 위험 안에서 3유닛 중 지금 단계(코스피·코스닥이 모두 20일선 위면 시장 유닛 +1) · 3R 목표가에서 절반 익절\n"
                    "- 해외 종목은 제외 · 종목별 수급의 연기금·투신은 기관 합계에 포함 · 마지막 확인은 HTS 차트로")


def _mem_census():
    """살아 있는 파이썬 객체를 종류별로 세고, 큰 표(DataFrame)를 찾아요."""
    import collections
    import sys as _sys
    gc.collect()
    objs = gc.get_objects()
    cnt, shallow = collections.Counter(), collections.Counter()
    frames = []
    for o in objs:
        tn = type(o).__name__
        cnt[tn] += 1
        try:
            shallow[tn] += _sys.getsizeof(o)
        except TypeError:
            pass
        if tn == "DataFrame":
            try:
                frames.append((int(o.memory_usage(deep=True).sum()), o.shape, list(o.columns[:4])))
            except Exception:
                pass
    frames.sort(key=lambda x: -x[0])
    st.caption(f"객체 {len(objs):,}개 · 파이썬 작은 블록 {_sys.getallocatedblocks():,}개 · 표 {len(frames):,}개 합계 "
               f"{sum(f[0] for f in frames) / 1e6:,.0f}MB")
    st.caption("많은 종류: " + " · ".join(f"{k} {v:,}개({shallow[k] / 1e6:,.0f}MB)" for k, v in cnt.most_common(10)))
    st.caption("큰 표: " + " · ".join(f"{b / 1e6:,.1f}MB {sh} {cols}" for b, sh, cols in frames[:8]))
    wref = collections.Counter()
    fn_names = collections.Counter()
    for o in objs:
        tn = type(o).__name__
        if tn == "ReferenceType":
            try:
                r = o()
                wref["(사라짐)" if r is None else type(r).__name__] += 1
            except Exception:
                pass
        elif tn == "function":
            fn_names[getattr(o, "__qualname__", "?")] += 1
    ths = threading.enumerate()
    tn = collections.Counter(re.sub(r"[-_ ]?\d+.*$", "", t.name) or t.name for t in ths)
    th_objs = sum(1 for o in objs if isinstance(o, threading.Thread))
    st.caption(f"살아 있는 스레드 {len(ths):,}개 · 스레드 객체 {th_objs:,}개 · " + " · ".join(f"{k} {v:,}" for k, v in tn.most_common(6)))
    dead_th = [o for o in objs if isinstance(o, threading.Thread) and not o.is_alive()]
    holders = collections.Counter()
    for t in dead_th[:20]:
        for r in gc.get_referrers(t):
            if r is objs or r is dead_th:
                continue
            if isinstance(r, dict):
                holders["dict" + str(sorted(map(str, r.keys()))[:3])] += 1
            elif isinstance(r, (list, set, tuple)):
                owners = [type(x).__name__ + (str(sorted(map(str, x.keys()))[:2]) if isinstance(x, dict) else "")
                          for x in gc.get_referrers(r) if x is not objs and x is not dead_th][:2]
                holders[f"{type(r).__name__}(len {len(r)})<-{owners}"] += 1
            else:
                holders[type(r).__name__] += 1
    if dead_th:
        st.caption(f"끝난 스레드 {len(dead_th):,}개, 이름 예: {', '.join(t.name for t in dead_th[:4])} · 붙잡는 쪽: "
                   + " · ".join(f"{k} {v}" for k, v in holders.most_common(5)))
    st.caption("약한 참조가 가리키는 것: " + " · ".join(f"{k} {v:,}" for k, v in wref.most_common(6)))
    st.caption("같은 이름 함수가 여러 벌: " + " · ".join(f"{k} ×{v:,}" for k, v in fn_names.most_common(6)))
    by_shape = collections.Counter((f[1][1], tuple(f[2])) for f in frames)
    st.caption("같은 모양 표가 많은 것: " + " · ".join(f"{n:,}개 × {c[0]}열 {list(c[1])}" for c, n in by_shape.most_common(5)))
    del objs


def render_checks(df: pd.DataFrame, quote_error: str | None, shares_store: dict | None = None):
    mb = _GC.get("mb") or rss_mb()
    if mb:
        st.caption(f"🖥️ 서버 메모리 {mb:,.0f}MB / 한도 {MEM_LIMIT:,.0f}MB"
                   + (f" · 빠듯해서(기준 {MEM_SOFT:,}MB) 뒤쪽 새로 받기를 잠시 멈췄어요" if mb > MEM_SOFT else ""))
        with st.expander("🖥️ 메모리 쓰임새(창고별)"):
            if st.toggle("창고별 크기 계산하기(몇 초 걸려요)", key="mem_rep_on"):
                st.caption(" · ".join(f"{n} {v:,.1f}MB" for n, v in mem_report() if v >= 0.3)
                           + f" · 새로 받기 멈춤 {MEM_SOFT:,}MB · 기억 비우기 {MEM_HARD:,}MB")
            ph = [(k, v) for k, v in _PHASE.items() if not k.startswith("_")]
            if ph:
                st.caption("지난 새로고침 단계별: " + " → ".join(f"{k} {b - a:+.0f}MB" for k, (a, b) in ph)
                           + f" · 정리 뒤 가장 낮았던 값 {_PHASE.get('_low', 0):,.0f}MB · 코드 {MEM_CODE_TAG}")
            if st.button("🔬 파이썬 객체 세기(2~5초, 원인 찾기용)", key="mem_census"):
                _mem_census()
            pm, mi = proc_mem(), malloc_info()
            tst = _TRIM_T[1] if isinstance(_TRIM_T[1], dict) else {}
            st.caption(f"정리 {tst.get('runs', 0):,}번 · pandas 쌓인 기록 {tst.get('pruned', 0):,}개 지움")
            st.caption(f"켜진 직후(라이브러리만) {getattr(data, '_BOOT_RSS', 0):,.0f}MB · 지금까지 최고점 {pm.get('VmHWM', 0):,.0f}MB"
                       + (f" · 할당기: 쓰는 중 {mi['used']:,.0f}MB, 비었는데 쥐고 있음 {mi['free_held']:,.0f}MB" if mi else ""))
    failed = df[df["price"].isna()]
    mismatch = df[df["name_ok"] == False]  # noqa: E712
    cap_missing = int(df["cap_krw"].isna().sum())
    fixed = df[df["h52_fixed"] == True] if "h52_fixed" in df else df.iloc[0:0]  # noqa: E712
    if not fixed.empty:
        with st.expander(f"🔧 52주 최고·최저 교정 {len(fixed)}종목 — 네이버 공식값으로 고쳐서 보여주고 있어요"):
            view = pd.DataFrame({
                "종목": fixed["name"], "코드": fixed["code"],
                "앱 계산 최고": fixed["high52_calc"], "네이버 최고": fixed["high52"],
                "차이(%)": pd.to_numeric(fixed["h52_diff"], errors="coerce").round(1),
                "앱 계산 최저": fixed["low52_calc"], "네이버 최저": fixed["low52"],
            })
            st.dataframe(view, hide_index=True)
            st.caption("일봉 데이터로 계산한 값이 네이버 공식 52주 최고·최저와 0.3% 넘게 다른 종목이에요. "
                       "주로 유·무상증자·액면분할로 과거 주가가 수정된 종목이에요. 표·괴리율·신고가까지는 공식값으로 보여주고, "
                       "일봉 기준 돌파 판정이 틀릴 수 있어서 🎯 매수 후보에서는 '판정 보류'로 빼요. 매매 전에는 HTS 차트로 확인하세요.")
    if failed.empty and mismatch.empty and not quote_error and cap_missing == 0 and not PENDING_MISS:
        return
    n_items = len(failed) + len(mismatch) + (1 if cap_missing else 0) + len(PENDING_MISS)
    with st.expander(f"데이터 점검 필요 {n_items}건"):
        for name, cands in PENDING_MISS.items():
            hint = ", ".join(f"{nm} {c}" for c, nm in cands) if cands else "후보 없음"
            st.write(f"코드 못 찾음: {name} — 네이버 검색 후보: {hint}")
        if cap_missing and shares_store:
            miss = df[df["cap_krw"].isna()]
            kr, os_ = shares_store.get("kr", {}), shares_store.get("os", {})
            by_market = miss.groupby("market")["name"].apply(list)
            parts = [f"{m} {len(v)}개: {', '.join(v[:15])}{' 외' if len(v) > 15 else ''}" for m, v in by_market.items()]
            st.write(f"시가총액 없는 종목 {cap_missing}개 — " + " / ".join(parts))
            st.caption(
                f"최근 조회 결과: 국내 {sum(v for k, v in kr.items() if k != 'total')}/{kr.get('total', 0)} "
                f"(상세API {kr.get('상세API', 0)}, 순위표 {kr.get('순위표', 0)}, 종목페이지 {kr.get('종목페이지', 0)}, 모바일 {kr.get('모바일', 0)}), "
                f"해외 {os_.get('SEC', 0) + os_.get('네이버', 0) + os_.get('야후', 0)}/{os_.get('total', 0)} "
                f"(SEC {os_.get('SEC', 0)}, 네이버 {os_.get('네이버', 0)}, 야후 {os_.get('야후', 0)}). "
                "빠진 종목은 10분마다 다시 시도해요(종목당 3번까지, 12시간마다 전체 새로 받기). "
                "대만·유럽·호주 종목은 네이버가 다루지 않고 야후가 막히면 '-'로 남을 수 있어요."
            )
        if quote_error:
            st.write(f"실시간 시세: {quote_error}. 일봉 마지막 값으로 대신 보여주고 있어요.")
        for r in failed.itertuples():
            st.write(f"조회 실패: {r.name} ({r.code}) — {r.error}")
        for r in mismatch.itertuples():
            st.write(f"이름 불일치: 목록에는 {r.name}, 네이버에는 {r.naver_name} ({r.code})")
        st.caption("코드가 틀렸거나 사명이 바뀐 경우예요. verify.bat을 실행하면 올바른 코드를 찾아줘요.")


def load_financials(codes: tuple[str, ...]) -> dict:
    """종목별 연간 영업이익·EPS. data 쪽에서 종목마다 12시간 기억해요."""
    with st.spinner(f"영업이익·EPS 실적과 전망을 불러오는 중이에요({len(codes)}종목, 처음 한 번만 오래 걸려요)."):
        return data.fetch_financials_many(codes)


def add_earnings(f: pd.DataFrame, fins: dict) -> pd.DataFrame:
    res = [data.earnings_trend(fins.get(c), earn_years) for c in f["code"]]
    keys = list(data.earnings_trend(None))
    return f.assign(**{k: [r[k] for r in res] for k in keys}) if len(f) else f.assign(**{k: [] for k in keys})


def render_earnings(code: str, currency: str):
    """종목 자세히 보기: 연간 매출·영업이익·EPS와 정배열 판정."""
    if not data.is_kr(code):
        st.caption("해외 종목은 실적 정배열 판정을 하지 않아요.")
        return
    fin = data.fetch_financials(code)
    if fin is None or fin.empty:
        st.caption("실적 자료를 불러오지 못했어요. 잠시 뒤 다시 열어 보세요.")
        return
    tr = data.earnings_trend(fin, earn_years)
    mark = {True: "✅ 정배열", False: "❌ 아님", None: "판정 불가(전망 없음/자료 부족)"}
    c1, c2, c3 = st.columns(3)
    c1.metric("영업이익·EPS 정배열", mark[tr["earn_ok"]], tr["earn_span"], delta_color="off")
    c2.metric("영업이익", mark[tr["op_ok"]])
    c3.metric("EPS", mark[tr["eps_ok"]])
    view = fin.set_index("period")[[c for c in ("sales", "op", "eps") if c in fin]].T
    view.index = [{"sales": "매출액(억원)", "op": "영업이익(억원)", "eps": "EPS(원)"}[i] for i in view.index]
    st.dataframe(view.style.format("{:,.0f}", na_rep="-"))
    st.caption("네이버 증권 기업실적분석(연결 우선) 기준, (E)는 컨센서스 전망치예요. "
               "네이버는 보통 전망을 1~2년만 줘서 3년 치 전망을 보여주는 사이트와 판정이 다를 수 있어요.")


# ─────────────────────────── 🏁 신고가 후보 ───────────────────────────
NHC_CSS = """
<style>
.nh-head { display: flex; align-items: baseline; justify-content: space-between; gap: 0.6rem; flex-wrap: wrap; margin: 0.2rem 0 0.7rem; }
.nh-head b { font-size: 1.35rem; font-weight: 800; color: #16212B; letter-spacing: -0.02em; }
.nh-head span { font-size: 0.8rem; color: #7A8A94; }
.nh-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.9rem; margin-bottom: 1.3rem; }
.nh-box { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; overflow: hidden; }
.nh-box-h { background: #F7F9FA; border-bottom: 1px solid #EEF1F3; padding: 0.75rem 1rem 0.65rem; }
.nh-box-t { display: flex; justify-content: space-between; align-items: center; font-size: 1.05rem; font-weight: 800; color: #16212B; }
.nh-box-t .cnt { font-size: 1.2rem; font-weight: 800; font-variant-numeric: tabular-nums; }
.nh-box-d { font-size: 0.78rem; color: #6B7A84; margin-top: 0.15rem; }
.nh-box-b { padding: 0.6rem 0.7rem 0.7rem; display: flex; flex-direction: column; gap: 0.5rem; max-height: 470px; overflow-y: auto; }
.nh-empty { text-align: center; color: #6B7A84; font-size: 0.85rem; padding: 1.6rem 0; }
.stApp a.nh-card { display: block; border: 1px solid #E3E8EB; border-radius: 10px; padding: 0.6rem 0.7rem 0.55rem;
  text-decoration: none; color: #16212B; background: #FFFFFF; }
.stApp a.nh-card:hover { border-color: #B9C7CF; }
.nh-c-top { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }
.nh-c-name { font-weight: 800; font-size: 1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nh-c-chg { font-weight: 800; font-variant-numeric: tabular-nums; white-space: nowrap; }
.nh-c-sub { font-size: 0.75rem; color: #6B7A84; margin: 0.1rem 0 0.45rem; }
.nh-c-keys { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.35rem; }
.nh-c-keys div { background: #F2F5F7; border-radius: 7px; padding: 0.3rem 0.5rem; font-size: 0.68rem; color: #6B7A84; min-width: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nh-c-keys b { display: block; font-size: 0.95rem; font-weight: 800; color: #16212B; white-space: nowrap; font-variant-numeric: tabular-nums; }
.nh-c-keys b small { font-size: 0.7rem; font-weight: 600; color: #6B7A84; margin-left: 0.1rem; }
.nh-pill { display: inline-flex; align-items: center; gap: 0.3rem; font-size: 0.72rem; font-weight: 700; padding: 0.15rem 0.5rem;
  border-radius: 6px; white-space: nowrap; }
.nh-pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.nh-pill.s-돌파 { background: #FCE4E5; color: #C0262E; }
.nh-pill.s-터치 { background: #FFF1C9; color: #9A6A00; }
.nh-pill.s-임박 { background: #FFE8D6; color: #C2570E; }
.nh-pill.s-근접 { background: #E3F4EA; color: #1B6B3E; }
.nh-pill.s-밀림 { background: #E4EDFA; color: #1F66C9; }
.nh-kind { display: inline-block; font-size: 0.66rem; font-weight: 700; padding: 0.05rem 0.4rem; border-radius: 5px; margin-left: 0.25rem; vertical-align: 1px; }
.nh-kind.ath { background: #FBE3E4; color: #C0313A; } .nh-kind.w52 { background: #FFF1D6; color: #A86A00; }
.nh-kind.unk { background: #EEF1F3; color: #6B7A84; } .nh-kind.lag { background: #FFF4E5; color: #B45309; }
.nh-c-why { font-size: 0.74rem; color: #3E4C55; margin: -0.25rem 0 0.45rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nh-pill.s-관찰 { background: #EEF1F3; color: #51616C; }
.nh-tbl-wrap { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; overflow-x: auto; }
table.nh-tbl { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.nh-tbl th { background: #F7F9FA; font-size: 0.78rem; font-weight: 700; color: #51616C; padding: 0.6rem 0.6rem; text-align: right;
  white-space: nowrap; border-bottom: 1px solid #E3E8EB; }
.nh-tbl td { padding: 0.55rem 0.6rem; border-top: 1px solid #F0F3F5; text-align: right; white-space: nowrap; font-size: 0.86rem; vertical-align: middle; }
.nh-tbl tr:first-child td { border-top: none; }
.nh-tbl th.l, .nh-tbl td.l { text-align: left; } .nh-tbl th.c, .nh-tbl td.c { text-align: center; }
.nh-tbl td.rk { color: #6B7A84; }
.nh-tbl td.nm a { color: #16212B; font-weight: 800; text-decoration: none; font-size: 0.93rem; }
.nh-tbl td small { display: block; font-size: 0.72rem; color: #7A8A94; font-weight: 500; margin-top: 0.05rem; }
.nh-tbl td.sp { min-width: 170px; }
.nh-tbl .sp-lab { font-size: 0.8rem; margin-bottom: 0.1rem; }
.nh-tbl .sp-lab b { font-weight: 800; } .nh-tbl .sp-lab span { color: #7A8A94; font-size: 0.74rem; margin-left: 0.15rem; }
.nh-tbl td b.px { font-weight: 800; color: #16212B; }
.nh-tbl td b.rs { font-weight: 800; font-size: 0.95rem; color: #16212B; }
.vs-tag { display: inline-block; font-size: 0.68rem; font-weight: 700; padding: 0.05rem 0.4rem; border-radius: 6px; margin-left: 0.3rem; }
.vs-tag.first { background: #FCE4E5; color: #C0262E; } .vs-tag.multi { background: #FFE8D6; color: #C2570E; }
.vs-tag.dn { background: #E4EDFA; color: #1F66C9; }
@media (max-width: 640px) {
  .nh-grid { grid-template-columns: 1fr; gap: 0.6rem; }
  .nh-box-b { max-height: 430px; }
  .nh-tbl .hide-m { display: none; }
  .nh-tbl td, .nh-tbl th { padding: 0.45rem 0.4rem; font-size: 0.8rem; }
  .nh-tbl td.sp { min-width: 130px; }
}
</style>
"""

NHC_SECTIONS = [
    ("신선 후보", "✳️", "#1B8A4B", "최근 60거래일 신고가 이력이 없고, 기준가 근처에서 거래대금·RS가 살아난 후보예요."),
    ("돌파권", "🎯", "#C27C0E", "기준가(직전 52주 고가)까지 가깝고 거래대금·RS가 받쳐 주는 종목이에요(신선 후보 제외)."),
    ("돌파 중", "📈", "#D6333B", "52주 고가를 새로 쓰고, 지금 가격(넥장 포함)도 기준가 위에 있어요(거래대금·RS 동반)."),
    ("찍고 밀림", "〰️", "#1F66C9", "최근 10거래일 안에 52주 고점을 찍고 조금(7% 안쪽) 밀린 재관찰 후보예요(거래대금·RS 동반)."),
]
NHC_LABEL = {"돌파": "돌파 유지", "터치": "오늘 찍고 밀림", "밀림": "고점 후 밀림", "임박": "임박", "근접": "근접",
             "관찰": "관찰"}
NHC_KIND_CLS = {"역사적": "ath", "52주": "w52"}


def _kind_badge(r) -> str:
    k = r.get("nh_kind") or ""
    cls = NHC_KIND_CLS.get(k, "unk")
    txt = {"역사적": "역사적 신고가", "52주": "52주 신고가"}.get(k, "52주 · 역사 확인 중")
    return f'<span class="nh-kind {cls}">{txt}</span>'


def _md(v) -> str:
    return "" if v is None or (isinstance(v, float) and v != v) or v is pd.NaT else f"{v:%m/%d}"


def _s(v) -> str:
    """None·NaN이면 빈 글자."""
    return "" if v is None or (isinstance(v, float) and v != v) else str(v)


def _spark_svg(vals, ref: float | None, up: bool, w: int = 160, hgt: int = 34) -> str:
    """종가 스파크라인 + 신고가 기준선(점선). 끝점: 돌파 초록, 아니면 빨강."""
    vals = [v for v in (vals or []) if v == v]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals + ([ref] if ref else [])), max(vals + ([ref] if ref else []))
    rng = (hi - lo) or 1.0
    pad = 3
    def y(v):
        return pad + (hgt - 2 * pad) * (1 - (v - lo) / rng)
    step = (w - 2 * pad) / (len(vals) - 1)
    pts = " ".join(f"{pad + i * step:.1f},{y(v):.1f}" for i, v in enumerate(vals))
    end_x, end_y = pad + (len(vals) - 1) * step, y(vals[-1])
    parts = [f'<svg width="{w}" height="{hgt}" viewBox="0 0 {w} {hgt}" style="display:block">']
    if ref:
        ry = y(ref)
        fill = "#E3F4EA" if up else "#FCE9EA"
        parts.append(f'<rect x="0" y="0" width="{w}" height="{max(ry, 0):.1f}" fill="{fill}" opacity="0.55"/>')
        parts.append(f'<line x1="0" x2="{w}" y1="{ry:.1f}" y2="{ry:.1f}" stroke="#E08A8F" stroke-width="1" stroke-dasharray="3 3"/>')
    parts.append(f'<polyline points="{pts}" fill="none" stroke="#5B6770" stroke-width="1.3" stroke-linejoin="round"/>')
    parts.append(f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="2.8" fill="{"#1FA75A" if up else "#D6333B"}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _chg_html(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    color = UP if v > 0 else DOWN if v < 0 else "#51616C"
    return f'<span style="color:{color}">{v:+.2f}%</span>'


def _tv_eok(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    e = v / 1e8
    return f"{e / 1e4:,.2f}조" if e >= 1e4 else f"{e:,.0f}억"


def _dist_html(r, big: bool = False) -> tuple[str, str]:
    """상태별 핵심 숫자: 돌파 = 기준가 위 몇 % · 찍고 밀림 = 고점 대비 몇 % · 나머지 = 기준가까지 남은 %."""
    s = r["nh_state"]
    if s == "돌파":
        return f'<b style="color:{UP}">+{r["nh_brk"]:.2f}%</b>', "기준 위"
    if s == "터치":     # 스탁이지처럼 '기준가까지 남은 %'(오늘 고가에서 몇 % 밀렸는지는 카드 설명에)
        return f'<b style="color:{DOWN}">{max(r["nh_dist"], 0):.2f}%</b>', "기준까지 남음"
    if s == "밀림":
        return f'<b style="color:{DOWN}">{r["peak_gap"]:.2f}%</b>', "고점 대비"
    return f'<b>{max(r["nh_dist"], 0):.2f}%</b>', "남음"


_NH_FADE = [3.0]     # 돌파 중 경고 기준(설정값을 render에서 넣어요)


def _nh_why(r) -> str:
    """카드 한 줄 근거: 기준가(날짜·출처) · 지금 가격(출처) · 확정 여부 (+ 깨진 봉을 고쳤으면 그 내용)."""
    base = _nh_why_base(r)
    fx = r.get("bar_fix")
    return f"{base} · {fx}" if isinstance(fx, str) and fx else base


def _nh_why_base(r) -> str:
    s = r["nh_state"]
    ref = f'기준 {r["ref_high"]:,.0f}' + (f'({_md(r["ref_date"])})' if _md(r.get("ref_date")) else
                                         ("(네이버 공식)" if r.get("ref_src") == "네이버 공식" else ""))
    now = f'지금 {r["nh_price"]:,.0f}' + ("" if r.get("price_src") == "KRX" else f'·{r["price_src"]}')
    if s == "돌파":
        tail = _s(r.get("confirm"))
        if r.get("fade") is not None and r["fade"] >= _NH_FADE[0]:
            tail += f' · 고가 {r["day_high"]:,.0f}서 -{r["fade"]:.1f}%'
        return f"{ref} · {now} · {tail}".strip(" ·")
    if s == "터치":
        day = "오늘" if r.get("peak_date") == data.now_kst().date() else _md(r.get("peak_date")) or "당일"
        return f'기준 {r["ref_high"]:,.0f} · {day} 고가 {r["day_high"]:,.0f} 찍고 -{r["fade"]:.1f}% · {now}' + (f' · {_s(r["confirm"])}' if _s(r.get("confirm")) else "")
    if s == "밀림":
        ago = f'{int(r["peak_ago"])}거래일 전' if r.get("peak_ago") == r.get("peak_ago") and r.get("peak_ago") else ""
        return f'{_md(r["peak_date"])} 고점 {r["peak"]:,.0f}({ago}) · {now}'
    return f"{ref} · {now}"


def _nh_card(r) -> str:
    val, lab = _dist_html(r)
    tvr = "-" if pd.isna(r["tv_ratio"]) else f"{r['tv_ratio']:.1f}배"
    rs = "-" if pd.isna(r["rs"]) else f"{r['rs']:.0f}"
    rs1 = "-" if pd.isna(r["rs_1m"]) else f"{r['rs_1m']:.0f}"
    head = {"돌파": "기준가 대비", "터치": "기준가까지", "밀림": "고점 대비"}.get(r["nh_state"], "기준가까지")
    ath = ""
    if r.get("nh_kind") == "52주" and r.get("ath_gap") == r.get("ath_gap") and r.get("ath_gap") is not None:
        ath = f' · 역사 고점까지 {r["ath_gap"]:.1f}%'
    lag = '<span class="nh-kind lag">⚠️ 일봉 지연</span>' if _is_true(r.get("data_lag")) else ""
    if _is_true(r.get("fresh")) and r["nh_state"] in ("돌파", "터치"):
        lag += '<span class="nh-kind ath">⭐ 60일 만의 첫 신고가</span>'
    return (
        f'<a class="nh-card" href="?chart={html.escape(str(r["code"]))}" target="_self">'
        f'<div class="nh-c-top"><span class="nh-c-name">{html.escape(str(r["name"]))} '
        f'<span class="nh-pill s-{r["nh_state"]}">{NHC_LABEL[r["nh_state"]]}</span></span>'
        f'<span class="nh-c-chg">{_chg_html(r["change"])}</span></div>'
        f'<div class="nh-c-sub">{r["code"]} · {html.escape(str(r["group"]))} {_kind_badge(r)}{lag}</div>'
        f'<div class="nh-c-why">{html.escape(_nh_why(r))}{html.escape(ath)}</div>'
        f'<div class="nh-c-keys"><div>{head}{val[:-4]}<small>{lab}</small></b></div>'
        f'<div>거래대금 20일 평균대비<b>{tvr}</b></div><div>RS / RS1M<b>{rs} / {rs1}</b></div></div></a>'
    )


def _nh_params() -> dict:
    d = data.NHC_DEFAULTS
    with st.expander("⚙️ 후보 기준 바꾸기"):
        c1, c2, c3 = st.columns(3)
        p = {
            "imminent": c1.slider("임박: 기준가까지 %", 0.5, 5.0, float(d["imminent"]), 0.5, key="nhc_imm"),
            "near": c2.slider("근접: 기준가까지 %", 3.0, 15.0, float(d["near"]), 0.5, key="nhc_near"),
            "watch": c3.slider("관찰: 기준가까지 %", 8.0, 30.0, float(d["watch"]), 1.0, key="nhc_watch"),
        }
        c4, c5, c6 = st.columns(3)
        p["tv_min"] = c4.slider("네 칸 공통: 거래대금 20일 평균대비(배)", 1.0, 5.0, float(d["tv_min"]), 0.1, key="nhc_tv2")
        p["rs_min"] = c5.slider("네 칸 공통: 종합 RS 이상(전 종목 기준)", 50, 99, int(d["rs_min"]), 1, key="nhc_rs2")
        p["fresh_days"] = c6.slider("신선: 최근 며칠간 신고가 없음(거래일)", 20, 120, int(d["fresh_days"]), 5, key="nhc_fresh")
        c7, c8, _ = st.columns(3)
        p["recent_peak"] = c7.slider("찍고 밀림: 고점이 최근 며칠 안(거래일)", 3, 30, int(d["recent_peak"]), 1, key="nhc_peak")
        p["fade_warn"] = c8.slider("돌파 중 경고: 오늘 고가에서 몇 % 밀리면", 1.0, 10.0, float(d["fade_warn"]), 0.5,
                                   key="nhc_fade")
        c9, c10, _ = st.columns(3)
        p["pull_max"] = c9.slider("찍고 밀림 칸: 고점에서 몇 % 안쪽까지", 2.0, 15.0, float(d["pull_max"]), 0.5, key="nhc_pull")
        p["tv20_min"] = c10.slider("네 칸 공통: 20일 평균 거래대금 최소(억, 0=끄기)", 0, 100, int(d["tv20_min"] / 1e8), 5,
                                   key="nhc_tv20") * 1e8
    return p


# ─────────────────────────── 🏔️ 신고가 (역사적 신고가 · 계속 갱신) ───────────────────────────
ATH_CSS = """<style>
.ath-head { display:flex; align-items:baseline; gap:0.6rem; margin:0.2rem 0 0.6rem; }
.ath-head b { font-size:1.25rem; color:#16212B; } .ath-head span { color:#8A979F; font-size:0.85rem; }
.ath-tbl { border:1px solid #E2E8EB; border-radius:12px; overflow:hidden; background:#fff; margin-bottom:0.9rem; }
.ath-th, .ath-row { display:grid; grid-template-columns: minmax(0,1fr) 72px 92px 46px; align-items:center; gap:0.4rem;
  padding:0.55rem 0.9rem; }
.ath-th { background:#F3F6F7; color:#51616C; font-size:0.8rem; font-weight:600; }
.ath-th span:nth-child(n+2), .ath-row > span:nth-child(n+2) { text-align:right; }
.ath-sec { display:flex; justify-content:space-between; align-items:center; padding:0.5rem 0.9rem; background:#F7F9FA;
  border-top:1px solid #E2E8EB; border-bottom:1px solid #EEF2F3; }
.ath-sec b { font-size:0.95rem; color:#16212B; } .ath-sec small { color:#8A979F; margin-left:0.35rem; }
.ath-avg { font-size:0.8rem; font-weight:700; padding:0.15rem 0.5rem; border-radius:6px; }
.ath-row { border-top:1px solid #F0F3F4; text-decoration:none !important; color:#16212B !important; }
.ath-row:hover { background:#FAFBFC; }
.ath-nm { display:flex; align-items:center; gap:0.35rem; min-width:0; }
.ath-nm b { font-weight:700; font-size:0.95rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ath-nm small { color:#8A979F; font-size:0.72rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.ath-badge { flex:none; font-size:0.7rem; font-weight:700; padding:0.1rem 0.4rem; border-radius:5px; }
.ath-badge.ath { background:#FBE3E4; color:#C0313A; } .ath-badge.wk { background:#FFF1D6; color:#A86A00; }
.ath-badge.st { background:#E3EEF9; color:#2D5FA0; }
.ath-chg { font-weight:700; font-size:0.92rem; } .ath-rs { color:#16212B; font-size:0.9rem; }
.ath-sub { display:block; color:#8A979F; font-size:0.7rem; font-weight:500; }
.ath-kpi { display:flex; gap:0.5rem; flex-wrap:wrap; margin:0 0 0.7rem; }
.ath-kpi div { flex:1; min-width:100px; background:#F3F6F7; border-radius:10px; padding:0.45rem 0.7rem; }
.ath-kpi em { display:block; font-style:normal; color:#51616C; font-size:0.75rem; } .ath-kpi b { font-size:1.15rem; }
@media (max-width: 640px) { .ath-th, .ath-row { grid-template-columns: minmax(0,1fr) 62px 70px 34px; padding:0.5rem 0.6rem; }
  .ath-nm small { display:none; } }
</style>"""


def _ath_candle(r) -> str:
    """오늘 일봉 미니 캔들(저가~고가 꼬리, 시가~현재가 몸통). 빨강 양봉 · 파랑 음봉."""
    lo, hi, op, px = (pd.to_numeric(r.get(k), errors="coerce") for k in ("day_low", "day_high", "day_open", "price"))
    if not (pd.notna(lo) and pd.notna(hi) and pd.notna(px)) or hi <= lo:
        return ""
    op = op if pd.notna(op) else (pd.to_numeric(r.get("prev"), errors="coerce") if pd.notna(r.get("prev")) else px)
    x = lambda v: 4 + (float(v) - lo) / (hi - lo) * 80  # noqa: E731
    col = "#D9444C" if px >= op else "#3B6FD8"
    a, b = sorted((x(op), x(px)))
    return (f'<svg width="88" height="14" viewBox="0 0 88 14"><line x1="4" y1="7" x2="84" y2="7" stroke="{col}" stroke-width="1.2"/>'
            f'<rect x="{a:.1f}" y="2" width="{max(b - a, 2.5):.1f}" height="10" rx="1.5" fill="{col}"/></svg>')


def _is_true(v) -> bool:
    """True / numpy True만 참(None·NaN·False는 거짓). 표의 dtype이 바뀌어도 같은 결과가 나오게."""
    return v is True or (isinstance(v, np.bool_) and bool(v))


def _ath_row(r, sub: str = "") -> str:
    badges = ""
    if _is_true(r.get("ath_d")):
        badges += '<span class="ath-badge ath">역사</span>'
    elif _is_true(r.get("ath_w")) or _is_true(r.get("ath_m")):
        badges += '<span class="ath-badge wk">역사(' + ("주" if _is_true(r.get("ath_w")) else "월") + ')</span>'
    stk = pd.to_numeric(r.get("nh_streak"), errors="coerce")
    if pd.notna(stk) and stk >= 2:
        badges += f'<span class="ath-badge st">{int(stk)}연속</span>'
    chg = pd.to_numeric(r.get("change"), errors="coerce")
    rs = pd.to_numeric(r.get("rs"), errors="coerce")
    chg_txt = f"{chg:+.1f}%" if pd.notna(chg) else "-"
    sub_html = f'<small>{html.escape(sub)}</small>' if sub else ""
    return (f'<a class="ath-row" target="_self" href="?chart={html.escape(str(r["code"]))}">'
            f'<span class="ath-nm"><b>{html.escape(str(r["name"]))}</b>{badges}{sub_html}</span>'
            f'<span class="ath-chg" style="color:{_sign_color(chg)}">{chg_txt}</span>'
            f'<span>{_ath_candle(r)}</span><span class="ath-rs">{"" if pd.isna(rs) else int(rs)}</span></a>')


def _ath_table(f: pd.DataFrame, sub_fn=None, by_sector: bool = True, sort_col: str = "change") -> str:
    head = '<div class="ath-th"><span>종목/섹터</span><span>등락률</span><span>오늘 봉</span><span>RS</span></div>'
    if f.empty:
        return f'<div class="ath-tbl">{head}<div class="ath-row" style="color:#8A979F !important">해당 종목이 없어요.</div></div>'
    f = f.assign(_k=pd.to_numeric(f[sort_col], errors="coerce")).sort_values("_k", ascending=False, na_position="last")
    body = ""
    if by_sector:
        f = f.assign(_sec=f["sector"].map(lambda x: SECTOR_SHORT.get(x, x)))
        order = (f.groupby("_sec")["change"].agg(lambda v: pd.to_numeric(v, errors="coerce").mean())
                 .sort_values(ascending=False))
        cnt = f.groupby("_sec").size()
        for sec in sorted(order.index, key=lambda s_: (-cnt[s_], -(order[s_] if pd.notna(order[s_]) else -99))):
            g = f[f["_sec"] == sec]
            avg = order[sec]
            col = _sign_color(avg)
            bg = "#FBE3E4" if (avg or 0) > 0 else ("#E3EEF9" if (avg or 0) < 0 else "#EEF2F3")
            body += (f'<div class="ath-sec"><span><b>{html.escape(str(sec))}</b><small>{len(g)}종목</small></span>'
                     f'<span class="ath-avg" style="color:{col};background:{bg}">평균 {avg:+.1f}%</span></div>'
                     if pd.notna(avg) else f'<div class="ath-sec"><span><b>{html.escape(str(sec))}</b><small>{len(g)}종목</small></span></div>')
            body += "".join(_ath_row(r, sub_fn(r) if sub_fn else r.get("group", "")) for _, r in g.iterrows())
    else:
        body = "".join(_ath_row(r, sub_fn(r) if sub_fn else r.get("group", "")) for _, r in f.iterrows())
    return f'<div class="ath-tbl">{head}{body}</div>'


@st.fragment
def render_ath(df: pd.DataFrame, quotes: dict):
    """🏔️ 신고가: 오늘 52주 신고가 · 역사적(상장 이후) 신고가 · 신고가를 계속 갱신 중인 종목."""
    st.markdown(ATH_CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="ath-head"><b>🏔️ 신고가 주요종목</b><span>{data.now_kst():%m/%d %H:%M} · '
                f'{data.market_status(quotes)} · 보드 종목 기준(누르면 차트)</span></div>', unsafe_allow_html=True)
    c1, c2 = st.columns([3, 1.2])
    view = c1.segmented_control("보기", ["오늘 52주 신고가", "역사적 신고가", "계속 갱신 중"], default="오늘 52주 신고가",
                                required=True, key="ath_view")
    kr_only = c2.toggle("국내만", value=True, key="ath_kr")
    f = df[df["price"].notna()].copy()
    if kr_only:
        f = f[f["code"].map(data.is_kr)]
    for k in ("nh52_d", "ath_d", "ath_w", "ath_m"):
        f[k] = f[k].map(_is_true)
    for k in ("nh_cnt20", "nh_cnt60", "nh_streak"):
        f[k] = pd.to_numeric(f.get(k), errors="coerce")
    today_nh = f[f["nh52_d"]]
    ath_today = f[f["ath_d"]]
    ath_period = f[(f["ath_w"] | f["ath_m"]) & ~f["ath_d"]]
    renew = f[f["nh_cnt20"] >= 3]
    st.markdown(
        '<div class="ath-kpi">'
        f'<div><em>오늘 52주 신고가</em><b>{len(today_nh)}</b></div>'
        f'<div><em>오늘 역사적 신고가</em><b style="color:#C0313A">{len(ath_today)}</b></div>'
        f'<div><em>이번 주·달 역사 신고가</em><b>{len(ath_period)}</b></div>'
        f'<div><em>20일 중 3회↑ 갱신</em><b style="color:#2D5FA0">{len(renew)}</b></div></div>', unsafe_allow_html=True)

    if view == "오늘 52주 신고가":
        st.caption("오늘 장중 고가가 직전 52주 최고가를 넘은 종목을 섹터별로 묶었어요. '역사' = 상장 이후 최고가, 'N연속' = N거래일 연속 신고가.")
        st.markdown(_ath_table(today_nh), unsafe_allow_html=True)
    elif view == "역사적 신고가":
        st.caption("상장 이후 전체(월봉) 최고가를 넘은 종목이에요. 위에 저항이 없는 자리라 추세가 가장 강한 구간이에요.")
        st.markdown("**오늘 역사적 신고가**")
        st.markdown(_ath_table(ath_today), unsafe_allow_html=True)
        st.markdown("**이번 주·이번 달 역사적 신고가** (오늘은 아님)")
        st.markdown(_ath_table(ath_period), unsafe_allow_html=True)
        ap = pd.to_numeric(f["ath_price"], errors="coerce")
        near = f[(~f["ath_d"]) & (~f["ath_w"]) & (~f["ath_m"]) & f["ath_ok"].map(_is_true) & ap.notna()
                 & (f["price"] / ap >= 0.95)].copy()
        near["_gap"] = (near["price"] / pd.to_numeric(near["ath_price"], errors="coerce") - 1) * 100
        st.markdown("**역사적 신고가까지 5% 이내**")
        st.markdown(_ath_table(near, sub_fn=lambda r: f"역사 고점까지 {-r['_gap']:.1f}%", by_sector=False, sort_col="_gap"),
                    unsafe_allow_html=True)
        st.caption("역사적 신고가는 월봉을 받아 둔 종목(공부 자료 종목 + 작은 신고가 종목)만 계산해요.")
    else:
        st.caption("최근 20거래일 중 52주 신고가를 3번 이상 쓴 종목 — 신고가를 '계속 갱신하는' 주도주 후보예요. 갱신 횟수 많은 순.")
        sub = lambda r: (f"20일 중 {int(r['nh_cnt20'])}회 · 60일 {int(r['nh_cnt60']) if pd.notna(r['nh_cnt60']) else '-'}회"  # noqa: E731
                         + (f" · {int(r['nh_streak'])}일 연속" if pd.notna(r['nh_streak']) and r['nh_streak'] >= 1 else ""))
        renew = renew.assign(_k=renew["nh_cnt20"] * 1000 + renew["nh_streak"].fillna(0) * 10
                             + pd.to_numeric(renew["rs"], errors="coerce").fillna(0) / 10)
        st.markdown(_ath_table(renew, sub_fn=sub, by_sector=False, sort_col="_k"), unsafe_allow_html=True)


@st.cache_data(ttl=15, show_spinner=False, max_entries=20)
def load_nxt_quotes(codes: tuple[str, ...]):
    return data.fetch_nxt_quotes(codes)


# ── 🏁 신고가 후보 전용: 코스피·코스닥 전 종목 일봉(가벼운 배열)을 뒤에서 받아 두는 곳 ──
SCAN_CHUNK = 60
# 전 종목 배열은 종목당 약 10KB(전체 25MB 안쪽)라 기억 비우기 기준(MEM_HARD)까지는 받아요.
SCAN_MEM_MAX = int(os.environ.get("SCAN_MEM_MB") or MEM_HARD)


@st.cache_resource
def _scan_store() -> dict:
    """{arr: {코드: 배열}, thru: {코드: 이 날짜까지는 장 마감 뒤 받은 완성 봉}, running, done, total}"""
    return {"arr": {}, "thru": {}, "lock": threading.Lock(), "running": False, "done": 0, "total": 0, "err": 0}


def _scan_bg(codes: tuple[str, ...]):
    """없는 종목은 300일, 지난 세션 완성 봉이 없는 종목은 최근 10일만 받아 붙여요. 한 번에 하나의 작업만, 화면은 안 기다려요.
    (메모리 여유 기준이 보드보다 느슨해요: 이 배열은 종목당 약 10KB라 전 종목이어도 30MB 안쪽이에요.)"""
    S = _scan_store()
    exp = data.last_session_day()
    now = data.now_kst()
    done_today = now.weekday() >= 5 or now.time() >= dtime(15, 40) or now.time() < dtime(9, 0)
    complete = exp if done_today else np.busday_offset(exp, -1, roll="forward")
    with S["lock"]:
        if S["running"]:
            return
        full = [c for c in codes if c not in S["arr"]]
        tail = [c for c in codes if c in S["arr"] and S["thru"].get(c, np.datetime64("1970-01-01")) < complete]
        if not (full or tail) or rss_mb() > SCAN_MEM_MAX:
            return
        S.update(running=True, done=0, total=len(full) + len(tail))

    def work():
        try:
            for kind, todo in (("full", full), ("tail", tail)):
                cnt = data.SCAN_BARS if kind == "full" else 10
                for i in range(0, len(todo), SCAN_CHUNK):
                    part = todo[i:i + SCAN_CHUNK]
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=6) as pool:
                        got = list(pool.map(lambda c: (c, data.fetch_history(c, cnt)[1]), part))
                    miss = [c for c, h in got if h is None or h.empty]
                    if miss and len(miss) < len(part):          # 네이버가 잠깐 막으면 천천히 한 번 더
                        time.sleep(2)
                        got += [(c, data.fetch_history(c, cnt)[1]) for c in miss]
                    with S["lock"]:
                        for c, h in got:
                            a = data.bars_to_arr(h)
                            if a is None:
                                S["err"] += 1
                                continue
                            S["arr"][c] = data.merge_arr(S["arr"].get(c), a) if kind == "tail" else a
                            S["thru"][c] = complete
                        S["done"] += len(part)
                    del got
                    trim_maybe(20)
                    if rss_mb() > SCAN_MEM_MAX:              # 받는 중에 메모리가 넘치면 멈추고 다음 새로고침에 이어서
                        break
                    time.sleep(0.3)
        finally:
            S["running"] = False

    threading.Thread(target=work, daemon=True).start()


@st.cache_data(ttl=30, show_spinner=False, max_entries=4)
def load_scan_quotes(codes: tuple[str, ...]) -> dict:
    """전 종목 실시간 시세(40종목씩 묶어 요청, 30초 기억)."""
    return data.fetch_quotes(codes)[0]


def load_cand_monthlies(codes: tuple[str, ...]) -> dict:
    """후보 종목만 월봉을 바로 받아요(역사적/52주 신고가 구분용, 종목별로 6시간 기억 — 보드 월봉과 같은 창고).
    (메모리) 예전엔 후보 목록(튜플)마다 따로 40개까지 기억해서, 후보가 조금씩 바뀔 때마다 같은 월봉이 겹겹이 쌓였어요."""
    ms = _monthly_store()
    now = time.time()
    with ms["lock"]:
        need = [c for c in codes if c not in ms["items"] or now - ms["items"][c][0] > 21600]
    if need:
        got = data.fetch_monthlies(tuple(need), workers=8)
        t = time.time()
        with ms["lock"]:
            for c in need:
                df_ = _slim_monthly(got.get(c))
                if df_ is not None:
                    ms["items"][c] = (t, df_)
                elif c not in ms["items"]:
                    ms["items"][c] = (t - 21600 + 1800, None)
    with ms["lock"]:
        return {c: ms["items"][c][1] for c in codes if c in ms["items"] and ms["items"][c][1] is not None}


def _nh_universe(df: pd.DataFrame, histories: dict, quotes: dict):
    """신고가 후보를 고를 표·일봉·시세. 사이드바 범위(내 보드 등)와 상관없이 코스피·코스닥 전 종목이에요.
    전 종목 일봉을 아직 다 못 받았으면 받은 만큼 + 보드 종목으로 먼저 보여줘요."""
    uni = {u["code"]: u for u in (load_universe() or [])}
    if not uni:
        return df, histories, quotes, None
    codes = tuple(sorted(uni))
    _scan_bg(codes)
    S = _scan_store()
    with S["lock"]:
        arrs = dict(S["arr"])
        prog = {"have": len(arrs), "total": len(codes), "running": S["running"], "done": S["done"], "todo": S["total"]}
    q_all = {**load_scan_quotes(codes), **{k: v for k, v in quotes.items() if v}}
    sf = data.scan_frame({c: a for c, a in arrs.items() if c in uni}, q_all, uni, df)
    missing_board = [c for c in df["code"] if data.is_kr(c) and c not in arrs] if len(df) else []
    if missing_board:                           # 아직 전 종목을 못 받았을 때: 보드 종목은 보드 값으로
        extra = df[df["code"].isin(missing_board)].copy()
        extra["in_board"] = True
        sf = pd.concat([sf, extra[[c for c in sf.columns if c in extra.columns]]], ignore_index=True) if len(sf) else extra
    return sf, (arrs, histories), q_all, prog


def _nh_hist_for(codes, src) -> dict:
    """후보 계산할 종목만 일봉 표로 만들어요(전 종목 배열 → 표, 없으면 보드 일봉)."""
    if not isinstance(src, tuple):
        return src
    arrs, board_hist = src
    out = {}
    for c in codes:
        if c in arrs:
            out[c] = (None, data.arr_df(arrs[c]), None)
        elif c in board_hist:
            out[c] = board_hist[c]
    return out


@st.cache_data(ttl=600, show_spinner=False, max_entries=20)
def load_official_52w_cand(codes: tuple[str, ...]) -> dict:
    return data.fetch_official_52w(codes)


def _ago_txt(r) -> str:
    a = r.get("peak_ago")
    if a is None or a != a:
        return ""
    if int(a) == 0:
        pd_ = r.get("peak_date")
        return " 오늘" if pd_ is not None and pd_ == data.now_kst().date() else " · 최근 거래일"
    return f" · {int(a)}일 전"


def _nxt_status(nxt: dict) -> str:
    """넥장 상태 한 줄: 받은 시세 중 가장 최근 체결 시각과 세션."""
    ts = [(v.get("nxt_time"), v.get("nxt_session"), v.get("nxt_status")) for v in nxt.values() if v.get("nxt_time")]
    if not ts:
        return "넥장 시세 없음(KRX 기준)"
    t, sess, stt = max(ts, key=lambda x: x[0])
    name = {"PRE_MARKET": "프리마켓", "AFTER_MARKET": "애프터마켓"}.get(str(sess).upper(), "넥장")
    return f"넥장 {name} {'진행 중' if str(stt).upper() == 'OPEN' else '마감'} · 최근 체결 {t:%m/%d %H:%M}"


def _nh_spike_dates(hist: pd.DataFrame | None, dates) -> list[str]:
    """기준가·고점이 된 날 중, 그날 고가가 앞뒤 사흘 종가보다 5% 넘게 튀어나온 날(깨진 봉일 수 있는 날)."""
    if hist is None or hist.empty:
        return []
    ds = pd.to_datetime(hist["date"]).dt.strftime("%Y-%m-%d").to_numpy()
    hi = pd.to_numeric(hist["high"], errors="coerce").to_numpy(dtype=float)
    cl = pd.to_numeric(hist["close"], errors="coerce").to_numpy(dtype=float)
    out = []
    for d in {str(x)[:10] for x in dates if x is not None and str(x) not in ("NaT", "nan", "None")}:
        idx = np.flatnonzero(ds == d)
        if not len(idx):
            continue
        i = int(idx[-1])
        near = cl[max(0, i - 1):i + 2]
        near = near[np.isfinite(near)]
        if len(near) and hi[i] > near.max() * 1.05:
            out.append(d)
    return out


def _nh_fix_bars(cand: pd.DataFrame, histories: dict) -> dict:
    """후보의 기준가·고점 봉이 네이버 데이터 오류(깨진 봉)인지 확인하고, 맞으면 고쳐서 다시 계산하게 해요.
    {코드: {날짜: 고친 값}}. 고친 봉은 전 종목 창고·보드 일봉에도 반영돼요."""
    jobs = []
    for _, r in cand.head(250).iterrows():
        hist = (histories.get(r["code"]) or (None, None, None))[1]
        dd = _nh_spike_dates(hist, [r.get("ref_date"), r.get("peak_date")])
        if dd:
            jobs.append((r["code"], dd, hist))
    if not jobs:
        return {}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=6) as pool:
        found = list(pool.map(lambda j: (j[0], data.check_bars(j[0], j[1]), j[2]), jobs))
    fixed = {}
    for code, fx, hist in found:
        ok = data.confirm_bar_fix(code, hist, fx) if fx else {}
        if ok:
            fixed[code] = ok
    if not fixed:
        return {}
    S, SS = _hist_store(), _scan_store()
    for code in fixed:
        h = histories.get(code)
        if h and h[1] is not None:
            histories[code] = (h[0], data.apply_bar_fix(code, h[1]), h[2])
        with S["lock"]:
            b = S["data"].get(code)
            if b and b[1] is not None:
                S["data"][code] = (b[0], data.apply_bar_fix(code, b[1]), b[2])
        with SS["lock"]:
            a = SS["arr"].get(code)
            if a is not None:
                SS["arr"][code] = data.bars_to_arr(data.apply_bar_fix(code, data.arr_df(a)))
    return fixed


def _fix_txt(fx: dict | None) -> str:
    if not fx:
        return ""
    d, f = sorted(fx.items())[-1]
    return (f"⚠️ 네이버 {d[5:].replace('-', '/')} 일봉 오류(고가 {f['was_high']:,.0f}·종가 {f['was_close']:,.0f}, "
            f"다음 날 기준 종가는 {f['close']:,.0f}) — 고쳐서 계산")


def render_nh_candidates(df: pd.DataFrame, histories: dict, quotes: dict):
    st.markdown(NHC_CSS, unsafe_allow_html=True)
    p = _nh_params()
    _NH_FADE[0] = p["fade_warn"]
    df, hsrc, quotes, prog = _nh_universe(df, histories, quotes)
    codes = data.nh_prefilter(df, p)
    ctup = tuple(sorted(codes))
    histories = _nh_hist_for(ctup, hsrc)
    nxt, nxt_err = load_nxt_quotes(ctup) if ctup else ({}, None)
    official = load_official_52w_cand(ctup) if ctup else {}
    if prog is None:
        scope_txt = f"보드 {len(df):,}종목(전 종목 목록을 못 받아 보드로만)"
    elif prog["have"] >= prog["total"] * 0.97:
        scope_txt = f"코스피·코스닥 전 종목 {prog['have']:,}개"
    else:
        scope_txt = (f"전 종목 일봉 받는 중 {prog['have']:,}/{prog['total']:,} — 받은 종목 + 보드 종목으로 먼저 보여줘요"
                     + (" (1분마다 늘어나요)" if prog["running"] else
                        f" (서버 메모리 {rss_mb():.0f}MB라 잠시 멈춤)" if rss_mb() > SCAN_MEM_MAX else ""))
    st.markdown(f'<div class="nh-head"><b>🏁 신고가 후보</b><span>기준: {data.now_kst():%y.%m.%d %H:%M} · '
                f'KRX {data.market_status(quotes)} · {_nxt_status(nxt)} · {scope_txt}</span></div>',
                unsafe_allow_html=True)
    with st.expander("읽는 법 · 판정 기준"):
        st.markdown(
            "- **기준가** = 오늘을 뺀 직전 52주(250거래일) 장중 최고가. 네이버 공식 52주 최고가가 더 높으면(오늘 쓴 값 제외) 공식값을 써요.\n"
            "- **지금 가격** = KRX 실시간. 장 마감 뒤 넥스트레이드 애프터마켓(15:40~20:00)·개장 전 프리마켓(08:00~08:50) 체결이 "
            "더 최근이면 **넥장 가격**으로 판정해요. 오늘 고가에도 넥장 고가를 넣어요.\n"
            "- **돌파 유지** = 오늘 고가가 기준가를 넘었고 지금 가격도 위. 장 마감 뒤 KRX 종가도 위면 **종가 확정**, 장중이면 **미확정**. "
            "오늘 고가에서 많이 밀리면 카드에 '고가서 -x%'가 붙어요.\n"
            "- **오늘 찍고 밀림** = 오늘 고가는 넘었지만 지금은 기준가 아래(종가는 돌파했는데 넥장에서 밀린 경우 포함).\n"
            "- **고점 후 밀림** = 최근 10거래일 안에 52주 고점을 찍었고 지금은 그 고점 아래. 다시 넘어야 진짜 돌파예요.\n"
            "- **임박/근접/관찰** = 기준가까지 남은 % (기본 2% / 7% / 15%).\n"
            "- **역사적 신고가** = 기준가가 상장 이후 최고가(월봉+일봉)와 같음 → 위에 매물대가 없는 자리. "
            "**52주 신고가** = 1년 고점이지만 더 위에 예전 고점이 있음(카드에 '역사 고점까지 %'). "
            "월봉을 아직 못 받은 종목은 '역사 확인 중'.\n"
            "- **⚠️ 일봉 지연** = 시세의 전일 종가가 일봉과 안 맞아 하루 넘게 늦은 일봉 → 판정을 믿을 수 없어 위 네 칸에서 뺐어요.\n"
            "- **네 칸 공통 조건** = 거래대금이 20일 평균의 1.5배↑ + 종합 RS 70↑(코스피·코스닥 전 종목 안 백분위) + 20일 평균 거래대금 10억↑. "
            "조건이 안 되는 종목은 아래 상세 리스트에만 나와요.\n"
            "- **신선 후보** = 임박·근접 + 최근 60거래일 신고가 없음 · **돌파권** = 임박·근접 중 신선 후보가 아닌 것 · "
            "**찍고 밀림** = 오늘 찍고 밀렸거나 최근 10거래일 고점에서 7% 안쪽 · 한 종목은 한 칸에만(돌파 중 > 찍고 밀림 > 신선 > 돌파권).\n"
            "- **🩹 깨진 봉 고치기** = 기준가·고점이 된 봉이 앞뒤보다 유난히 튀면, 다음 날 '전일 대비'로 거꾸로 계산한 진짜 종가와 맞춰 봐요. "
            "2% 넘게 다르면 네이버 데이터 오류로 보고 그 봉의 고가·종가를 고쳐서 계산해요(카드에 ⚠️로 표시). 예: 에스원 10/14 고가 106,500 → 86,300.\n"
            "- **대상** = 사이드바 범위와 상관없이 코스피·코스닥 전 종목(스팩·ETF·우선주 제외). 전 종목 일봉은 뒤에서 받아 두고 매일 새 봉만 붙여요.\n"
            "- 카드를 누르면 앱 안 차트가 열려요. 매수 판단은 🎯 매수 후보 탭의 원칙으로 한 번 더 걸러요.")
    if nxt_err:
        st.caption(f"⚠️ {nxt_err} — 이번엔 KRX 시세로만 판정했어요.")
    def _nh_calc():
        cand = data.nh_candidates(df, histories, quotes, p, nxt=nxt, official=official, codes=codes)
        bar_fixed = _nh_fix_bars(cand, histories) if not cand.empty else {}
        if bar_fixed:                          # 깨진 봉을 고쳤으면 그 종목은 다시 계산
            cand = data.nh_candidates(df, histories, quotes, p, nxt=nxt, official=official, codes=codes)
        if not cand.empty:                     # 후보만 월봉을 받아 역사적/52주 신고가를 가려요
            mcodes = tuple(sorted(cand["code"].head(250)))
            with st.spinner("후보 종목의 상장 이후 월봉을 확인하는 중이에요(역사적 신고가 구분)."):
                monthlies = load_cand_monthlies(mcodes)
            if monthlies:
                cand = data.nh_candidates(df, histories, quotes, p, nxt=nxt, official=official, monthlies=monthlies,
                                          codes=list(cand["code"]))
        return cand
    _pk = tuple(sorted((k, v) for k, v in p.items() if isinstance(v, (int, float, str, bool))))
    cand = tab_memo("nh_cand", (_pk, len(codes) if codes is not None else -1, len(histories), len(nxt or {})), _nh_calc).copy()
    if cand.empty:
        st.info("지금 조건에 맞는 신고가 후보가 없어요.")
        return
    all_fix = {c: data.BAR_FIX.get(c) for c in cand["code"] if data.BAR_FIX.get(c)}
    cand["bar_fix"] = cand["code"].map(lambda c: _fix_txt(all_fix.get(c)))

    masks = {sec: cand["section"] == sec for sec in ("신선 후보", "돌파권", "돌파 중", "찍고 밀림")}
    boxes = []
    for sec, icon, color, desc in NHC_SECTIONS:
        sub = cand[masks[sec]]
        body = "".join(_nh_card(r) for _, r in sub.head(12).iterrows()) or '<div class="nh-empty">해당 종목이 없습니다.</div>'
        more = f'<div class="nh-empty" style="padding:0.3rem 0">외 {len(sub) - 12}개는 아래 리스트에서</div>' if len(sub) > 12 else ""
        boxes.append(
            f'<div class="nh-box"><div class="nh-box-h"><div class="nh-box-t"><span>{icon} {sec}</span>'
            f'<span class="cnt" style="color:{color}">{len(sub)}</span></div><div class="nh-box-d">{desc}</div></div>'
            f'<div class="nh-box-b">{body}{more}</div></div>')
    st.markdown(f'<div class="nh-grid">{"".join(boxes)}</div>', unsafe_allow_html=True)
    fixed_rows = cand[cand["bar_fix"].astype(bool)]
    if len(fixed_rows):
        st.caption("🩹 네이버 일봉이 앞뒤와 안 맞는 깨진 봉을 고쳐서 계산한 종목: "
                   + " · ".join(f"{n}({t.split('(')[0].replace('⚠️ 네이버 ', '').replace(' 일봉 오류', '')})"
                                for n, t in zip(fixed_rows["name"], fixed_rows["bar_fix"]))
                   + " — 다음 날 '전일 대비'로 거꾸로 계산한 진짜 종가와 2% 넘게 다르고, 앞뒤보다 고가만 튀어나온 봉이에요.")
    lag = cand[cand["data_lag"].map(_is_true)]
    if len(lag):
        st.caption(f"⚠️ 일봉이 늦게 들어온 {len(lag)}종목은 판정에서 뺐어요: "
                   + ", ".join(lag["name"].astype(str).head(15)) + (" …" if len(lag) > 15 else "")
                   + " (다음 새로고침에 일봉을 다시 받아요)")

    # ── 상세 후보 리스트 ──
    st.markdown('<div class="nh-head"><b style="font-size:1.15rem">상세 후보 리스트</b></div>', unsafe_allow_html=True)
    counts = cand["nh_state"].value_counts()
    opts = ["전체"] + list(data.NHC_STATES)
    c1, c2, c3, c4 = st.columns([4.2, 1.3, 1.2, 1.4])
    pick = c1.segmented_control("상태", opts, default="전체", key="nhc_pick2", label_visibility="collapsed",
                                format_func=lambda s: f"{NHC_LABEL.get(s, s)} {len(cand) if s == '전체' else int(counts.get(s, 0))}")
    kind = c2.selectbox("구분", ["전체", "역사적", "52주"], key="nhc_kind", label_visibility="collapsed",
                        format_func=lambda s: {"전체": "신고가 구분 전체", "역사적": "역사적 신고가만", "52주": "52주 신고가만"}[s])
    sort_by = c3.selectbox("정렬", ["상태순", "등락률", "거래대금 배수", "RS", "ATR%"], key="nhc_sort2",
                           label_visibility="collapsed")
    q = c4.text_input("종목·코드 검색", key="nhc_q", placeholder="🔍 종목·코드 검색", label_visibility="collapsed")
    v = cand if pick in (None, "전체") else cand[cand["nh_state"] == pick]
    if kind == "역사적":
        v = v[v["nh_kind"] == "역사적"]
    elif kind == "52주":
        v = v[v["nh_kind"] != "역사적"]
    if q:
        v = v[v["name"].str.contains(q, case=False, na=False, regex=False) | v["code"].str.contains(q, na=False, regex=False)]
    key = {"등락률": "change", "거래대금 배수": "tv_ratio", "RS": "rs", "ATR%": "atr_pct"}.get(sort_by)
    if key:
        v = v.sort_values(key, ascending=False, na_position="last")
    if v.empty:
        st.info("해당 종목이 없어요.")
        return
    rows = []
    for i, (_, r) in enumerate(v.head(150).iterrows(), 1):
        val, lab = _dist_html(r)
        brk = r["nh_state"] == "돌파"
        lag_b = '<span class="nh-kind lag">⚠️ 지연</span>' if _is_true(r.get("data_lag")) else ""
        src = "" if r.get("price_src") == "KRX" else f'<small>{html.escape(str(r["price_src"]))}</small>'
        ref_d = _md(r.get("ref_date")) if _md(r.get("ref_date")) else ("공식" if r.get("ref_src") == "네이버 공식" else "")
        rows.append(
            f'<tr><td class="c rk hide-m">{i}</td>'
            f'<td class="l nm"><a href="?chart={html.escape(str(r["code"]))}" target="_self">{html.escape(str(r["name"]))}</a>'
            f'<small>{r["code"]} · {html.escape(str(r["group"]))}</small></td>'
            f'<td class="c"><span class="nh-pill s-{r["nh_state"]}">{NHC_LABEL[r["nh_state"]]}</span>'
            f'<small>{html.escape(_s(r.get("confirm")))}</small></td>'
            f'<td class="c">{_kind_badge(r)}{lag_b}</td>'
            f'<td class="sp"><div class="sp-lab">{val}<span>{lab}</span></div>'
            f'{_spark_svg(r["spark"], r["ref_high"], brk)}</td>'
            f'<td><b class="px">{r["nh_price"]:,.0f}원</b>{src}</td>'
            f'<td><b class="px">{r["ref_high"]:,.0f}원</b><small>{ref_d}</small></td>'
            f'<td class="hide-m"><b class="px">{r["peak"]:,.0f}원</b><small>{_md(r["peak_date"])}'
            f'{_ago_txt(r)}</small></td>'
            f'<td><b>{_chg_html(r["change"])}</b></td>'
            f'<td class="hide-m"><b class="px">{_tv_eok(r["tv_today"])}</b><small>20평균 '
            f'{"-" if pd.isna(r["tv_ratio"]) else format(r["tv_ratio"], ".1f") + "배"}</small></td>'
            f'<td><b class="rs">{_n(r["rs"])}</b><small>1M {_n(r["rs_1m"])}</small></td></tr>')
    head = ('<tr><th class="c hide-m">순위</th><th class="l">종목</th><th class="c">상태</th><th class="c">신고가 구분</th>'
            '<th>기준·고점 대비</th><th>지금 가격</th><th>기준가(직전 52주)</th><th class="hide-m">52주 고점</th>'
            '<th>등락률</th><th class="hide-m">거래대금</th><th>RS</th></tr>')
    st.markdown(f'<div class="nh-tbl-wrap"><table class="nh-tbl">{head}{"".join(rows)}</table></div>',
                unsafe_allow_html=True)
    if len(v) > 150:
        st.caption(f"앞 150개만 보여줘요(전체 {len(v)}개). 상태 필터나 검색으로 좁혀 보세요.")
    st.caption("기준가 = 오늘을 뺀 직전 250거래일 장중 최고가(돌파 판정선) · 52주 고점 = 오늘 포함 · "
               "스파크라인은 최근 120거래일 종가, 점선이 기준가 · 등락률은 지금 가격(넥장 포함) 기준")


# ─────────────────────────── 💥 거래량 폭발 ───────────────────────────
def _vol_svg(vol, close, split: int | None, recent: int, mult_line: float | None = None, w: int = 220, hgt: int = 44) -> str:
    """거래량 막대(최근 구간 빨강, 조용한 구간 회색 배경) + 종가 선."""
    vol = [x or 0 for x in (vol or [])]
    close = [x for x in (close or [])]
    n = len(vol)
    if n < 10:
        return ""
    vmax = max(vol) or 1
    bw = w / n
    parts = [f'<svg width="{w}" height="{hgt}" viewBox="0 0 {w} {hgt}" style="display:block">']
    if split is not None:
        s = max(0, split)
        parts.append(f'<rect x="{s * bw:.1f}" y="0" width="{(n - recent - s) * bw:.1f}" height="{hgt}" fill="#EEF1F3"/>')
    for i, x in enumerate(vol):
        bh = (hgt - 2) * x / vmax
        col = "#D6333B" if i >= n - recent else "#9AA9B3"
        parts.append(f'<rect x="{i * bw:.2f}" y="{hgt - bh:.1f}" width="{max(bw - 0.4, 0.6):.2f}" height="{bh:.1f}" fill="{col}"/>')
    cl = [c for c in close if c == c]
    if len(cl) >= 2:
        lo, hi = min(cl), max(cl)
        rng = (hi - lo) or 1
        pts = " ".join(f"{(i + 0.5) * bw:.1f},{2 + (hgt * 0.55) * (1 - (c - lo) / rng):.1f}"
                       for i, c in enumerate(close) if c == c)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#16212B" stroke-width="1.1" opacity="0.8"/>')
    parts.append("</svg>")
    return "".join(parts)


def _vs_chart(hist: pd.DataFrame, name: str, recent: int, quiet: int):
    """일진전기 화면처럼: 위 캔들 + 이동평균, 아래 거래량(상승 빨강·하락 파랑)."""
    h = hist.tail(250).copy()
    h["date"] = pd.to_datetime(h["date"])
    for n_ in (5, 20, 60, 120):
        h[f"MA{n_}"] = hist["close"].rolling(n_).mean().tail(250).values
    h["up"] = h["close"] >= h["open"]
    base = alt.Chart(h).encode(x=alt.X("date:T", title=None, axis=alt.Axis(format="%y.%m", labelAngle=0, grid=False)))
    color = alt.condition("datum.up", alt.value(UP), alt.value(DOWN))
    wick = base.mark_rule().encode(y=alt.Y("low:Q", title=None, scale=alt.Scale(zero=False)), y2="high:Q", color=color)
    body = base.mark_bar(width=2.4).encode(y="open:Q", y2="close:Q", color=color,
                                           tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("close:Q", format=",.0f"),
                                                    alt.Tooltip("volume:Q", format=",.0f")])
    ma = alt.Chart(h.melt("date", value_vars=["MA5", "MA20", "MA60", "MA120"], var_name="선", value_name="값").dropna()) \
        .mark_line(strokeWidth=1.2).encode(
            x="date:T", y="값:Q",
            color=alt.Color("선:N", scale=alt.Scale(domain=["MA5", "MA20", "MA60", "MA120"],
                                                    range=["#1FA75A", "#D6333B", "#F29A18", "#8E5CC4"]),
                            legend=alt.Legend(orient="top", title=None)))
    top = (wick + body + ma).properties(height=240, width="container", title=name)
    qstart = h["date"].iloc[max(0, len(h) - recent - quiet)]
    rstart = h["date"].iloc[max(0, len(h) - recent)]
    band = alt.Chart(pd.DataFrame({"s": [qstart], "e": [rstart]})).mark_rect(color="#EEF1F3", opacity=0.9) \
        .encode(x="s:T", x2="e:T")
    vol = base.mark_bar(width=2.4).encode(y=alt.Y("volume:Q", title=None, axis=alt.Axis(format="~s")), color=color)
    bottom = (band + vol).properties(height=110, width="container")
    st.altair_chart(top)
    st.altair_chart(bottom)


def render_volume_surge(df: pd.DataFrame, histories: dict, quotes: dict):
    st.markdown(NHC_CSS, unsafe_allow_html=True)
    st.markdown('<div class="nh-head"><b>💥 거래량 폭발</b><span>몇 달 조용하던 거래량이 갑자기 터진 국내 종목</span></div>',
                unsafe_allow_html=True)
    d = data.VS_DEFAULTS
    c1, c2, c3, c4 = st.columns(4)
    quiet = c1.segmented_control("조용했던 기간", [60, 90, 120], default=int(d["quiet"]), key="vs_quiet",
                                 format_func=lambda x: f"{x}일")
    recent = c2.segmented_control("최근 며칠 안에 터졌나", [1, 3, 5, 10], default=int(d["recent"]), key="vs_recent",
                                  format_func=lambda x: "오늘" if x == 1 else f"{x}일")
    mult = c3.slider("폭발 배수(평소 거래량의 몇 배)", 2.0, 10.0, float(d["mult"]), 0.5, key="vs_mult")
    min_tv = c4.number_input("폭발일 거래대금 최소(억)", 0, 5000, int(d["min_tv"] / 1e8), 5, key="vs_mintv")
    with st.expander("⚙️ 조용함 기준 · 추가 필터"):
        e1, e2, e3 = st.columns(3)
        quiet_cap = e1.slider("조용함: 5일 평균 거래량이 평소의 몇 배를 안 넘었나", 1.3, 4.0, float(d["quiet_cap"]), 0.1,
                              key="vs_qcap", help="낮출수록 더 '말라 있던' 종목만 남아요. 하루 이틀 튄 날은 5일 평균이라 괜찮아요.")
        only_quiet = e1.toggle("조용함 조건 적용", value=True, key="vs_onlyq")
        only_up = e2.toggle("폭발일이 양봉인 종목만", value=True, key="vs_up",
                            help="거래량이 터진 날 종가가 시가 위(빨간 봉)인 경우만. 대량 음봉(투매)은 빼요.")
        only_above = e2.toggle("최근 수익률 플러스만", value=False, key="vs_pos")
        max_to_high = e3.slider("신고가까지 % 이내만 (100=전체)", 5, 100, 100, 5, key="vs_toh")
    quiet, recent = quiet or int(d["quiet"]), recent or int(d["recent"])
    p = {"quiet": quiet, "recent": recent, "mult": mult, "quiet_cap": quiet_cap, "min_tv": min_tv * 1e8}
    res = tab_memo("vs", (tuple(sorted((k, v) for k, v in p.items() if isinstance(v, (int, float, str, bool)))), only_quiet, len(histories)), lambda: data.volume_surges(df, histories, quotes, p, only_quiet=only_quiet))
    if not res.empty:
        if only_up:
            res = res[res["vs_peak_up"] == True]  # noqa: E712
        if only_above:
            res = res[pd.to_numeric(res["vs_ret"], errors="coerce") > 0]
        if max_to_high < 100:
            res = res[pd.to_numeric(res["to_high"], errors="coerce") <= max_to_high]
    if res.empty:
        st.info("지금 조건에 맞는 종목이 없어요. 폭발 배수를 낮추거나 기간을 늘려 보세요.")
        return
    today = data.now_kst().date()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("찾은 종목", f"{len(res)}")
    k2.metric("오늘 터짐", f"{int((res['vs_since'] == 0).sum())}")
    k3.metric("2일 이상 연속·반복", f"{int((res['vs_days'] >= 2).sum())}")
    k4.metric("신고가 10% 이내", f"{int((pd.to_numeric(res['to_high'], errors='coerce') <= 10).sum())}")

    sort_by = st.segmented_control("정렬", ["폭발 배수", "최근 수익률", "말라 있던 정도", "RS", "신고가까지"],
                                   default="폭발 배수", key="vs_sort", label_visibility="collapsed")
    key, asc = {"최근 수익률": ("vs_ret", False), "말라 있던 정도": ("vs_dry", True), "RS": ("rs", False),
                "신고가까지": ("to_high", True)}.get(sort_by or "", ("vs_mult", False))
    res = res.sort_values(key, ascending=asc, na_position="last").reset_index(drop=True)

    rows = []
    for i, r in res.head(120).iterrows():
        tag = ""
        if r["vs_days"] >= 2:
            tag = f'<span class="vs-tag multi">{int(r["vs_days"])}일 폭발</span>'
        elif r["vs_days"] == 1:
            tag = '<span class="vs-tag first">1회 폭발</span>'
        if r["vs_peak_up"] is False:
            tag += '<span class="vs-tag dn">음봉</span>'
        pk = r["vs_peak_date"]
        pk_txt = "오늘" if pk == today else f"{pk:%m/%d}"
        dry = "-" if pd.isna(r["vs_dry"]) else f"{r['vs_dry']:.2f}"
        rows.append(
            f'<tr><td class="c rk hide-m">{i + 1}</td>'
            f'<td class="l nm"><a href="?chart={html.escape(str(r["code"]))}" target="_self">{html.escape(str(r["name"]))}</a>{tag}'
            f'<small>{r["code"]} · {html.escape(str(r["group"]))}</small></td>'
            f'<td class="sp">{_vol_svg(r["vs_vol"], r["vs_close"], r["vs_split"], recent)}</td>'
            f'<td><b class="rs" style="color:{UP}">{r["vs_mult"]:.1f}배</b><small>{pk_txt} · 평균 {r["vs_avg_mult"]:.1f}배</small></td>'
            f'<td><b class="px">{_tv_eok(r["vs_peak_tv"])}</b><small>폭발일 거래대금</small></td>'
            f'<td>{_chg_html(r["vs_ret"])}<small>오늘 {_chg_html(r["change"])}</small></td>'
            f'<td class="hide-m"><b>{dry}</b><small>횡보폭 {_pct(r["vs_range"])}</small></td>'
            f'<td><b>{"-" if pd.isna(r["to_high"]) else format(r["to_high"], ".1f") + "%"}</b>'
            f'<small>{"정배열" if r.get("aligned") == True else ""}</small></td>'  # noqa: E712
            f'<td><b class="rs">{_n(r["rs"])}</b><small>1M {_n(r["rs_1m"])}</small></td></tr>')
    head = ('<tr><th class="c hide-m">순위</th><th class="l">종목</th>'
            f'<th class="l">거래량 {len(res.iloc[0]["vs_vol"] or [])}일 (회색=조용한 {quiet}일)</th>'
            f'<th>폭발 배수</th><th>거래대금</th><th>최근 {recent}일</th><th class="hide-m">거래 수준</th>'
            '<th>신고가까지</th><th>RS</th></tr>')
    st.markdown(f'<div class="nh-tbl-wrap"><table class="nh-tbl">{head}{"".join(rows)}</table></div>',
                unsafe_allow_html=True)
    st.caption(f"폭발 배수 = 최근 {recent}거래일 중 가장 큰 거래량 ÷ 그 앞 {quiet}거래일 거래량 중앙값 · "
               "평균 = 최근 구간 평균 배수 · 거래 수준 = 조용한 기간 중앙값 ÷ 그 앞 1년 중앙값(1보다 작을수록 평소보다 말라 있었음) · "
               "횡보폭 = 조용한 기간 종가 최고/최저 차이")

    labels = {r.code: f"{r.name} ({r.code}) · {r.vs_mult:.1f}배" for r in res.head(120).itertuples()}
    code = st.selectbox("차트로 보기", list(labels), format_func=labels.get, key="vs_chart_code")
    hist = (histories.get(code) or (None, data.empty_frame(), None))[1]
    if hist is not None and not hist.empty:
        _vs_chart(hist, labels[code], recent, quiet)
    with st.expander("ℹ️ 이 탭을 쓰는 법"):
        st.markdown(
            "- 일진전기처럼 **몇 달 동안 거래량이 말라 있다가** 최근 며칠 사이 평소의 몇 배가 터진 종목을 찾아요.\n"
            "- 조용함 조건: 조용한 기간 동안 **5일 평균 거래량**이 평소(중앙값)의 N배를 넘은 적이 없어야 해요. "
            "하루 튄 날은 괜찮지만, 며칠씩 거래가 붙었던 종목은 빠져요.\n"
            "- 거래량 폭발 자체는 매수 신호가 아니에요. 폭발 뒤 **고가 근처에서 버티는지(눌림 거래량 감소)**, "
            "신고가 돌파로 이어지는지를 🏁 신고가 후보 · 🎯 매수 후보 탭과 같이 보세요.\n"
            "- 보드에 넣은 종목(stocks.py) 안에서만 찾아요. 오늘 봉은 실시간 누적 거래량이라 장중엔 계속 커져요.")


IA_CSS = """
<style>
.ia-zone { border-radius: 12px; padding: 0.8rem 1rem; margin: 0.3rem 0 0.8rem; border: 1px solid #E3E8EB; background: #FFFFFF; }
.ia-zone b.z { font-size: 1.15rem; font-weight: 800; }
.ia-zone .d { font-size: 0.82rem; color: #3E4C55; margin-top: 0.2rem; }
.ia-zone.z-과매도권 { background: #FDECEC; border-color: #F3B9BC; } .ia-zone.z-과매도권 b.z { color: #C0262E; }
.ia-zone.z-과매도 { background: #FFF4E5; border-color: #F6D4A8; } .ia-zone.z-과매도 b.z { color: #B45309; }
.ia-zone.z-약세 { background: #EEF4FC; border-color: #C9DAF2; } .ia-zone.z-약세 b.z { color: #1F66C9; }
.ia-zone.z-과열권 { background: #E9F6EE; border-color: #BFE3CC; } .ia-zone.z-과열권 b.z { color: #1B6B3E; }
.ia-units { display: flex; gap: 0.4rem; flex-wrap: wrap; margin: 0.3rem 0 0.2rem; }
.ia-unit { flex: 1 1 70px; text-align: center; border-radius: 9px; padding: 0.45rem 0.3rem; font-size: 0.75rem; color: #6B7A84;
  background: #F2F5F7; border: 1px dashed #D3DCE1; }
.ia-unit b { display: block; font-size: 0.95rem; color: #16212B; font-weight: 800; }
.ia-unit.on { background: #FCE4E5; border: 1px solid #F3B9BC; color: #C0262E; }
.ia-sec { display: inline-block; font-size: 0.68rem; font-weight: 700; padding: 0.05rem 0.4rem; border-radius: 6px; margin-left: 0.3rem;
  background: #EEF1F3; color: #51616C; }
.ia-sec.lead { background: #FCE4E5; color: #C0262E; }
.ia-tier { display: inline-block; font-size: 0.72rem; font-weight: 800; padding: 0.12rem 0.5rem; border-radius: 6px; white-space: nowrap; }
.ia-tier.t0 { background: #FCE4E5; color: #C0262E; } .ia-tier.t1 { background: #FFF1C9; color: #9A6A00; }
.ia-tier.t2 { background: #EEF1F3; color: #51616C; }
.ia-chk { font-size: 0.7rem; color: #6B7A84; white-space: normal; line-height: 1.35; max-width: 260px; }
.ia-chk .x { color: #1F66C9; font-weight: 700; }
</style>
"""


def _ia_breadth_chart(bs: pd.DataFrame, idx: pd.DataFrame | None):
    """코스피(왼쪽 축)와 20일선·200일선 아래 종목 비율(오른쪽 축, %). 20 DOWN 80%↑ 구간은 빨간 띠."""
    b = bs.copy()
    b["date"] = pd.to_datetime(b["date"])
    long = b.melt("date", value_vars=["down20", "down200"], var_name="k", value_name="v")
    long["k"] = long["k"].map({"down20": "20일선 아래 비율", "down200": "200일선 아래 비율"})
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False))
    hot = b[b["down20"] >= 80].assign(e=lambda t: t["date"] + pd.Timedelta(days=1))
    band = alt.Chart(hot).mark_rect(color="#F3B9BC", opacity=0.45).encode(x=x, x2="e:T")
    rules = alt.Chart(pd.DataFrame({"y": [90, 80, 20]})).mark_rule(strokeDash=[4, 3], color="#B0BCC4").encode(
        y=alt.Y("y:Q", scale=alt.Scale(domain=[0, 100]), axis=None))
    lines = alt.Chart(long).mark_line(strokeWidth=1.8).encode(
        x=x, y=alt.Y("v:Q", title="%", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(orient="right")),
        color=alt.Color("k:N", scale=alt.Scale(domain=["20일선 아래 비율", "200일선 아래 비율", "코스피"],
                                               range=["#1FA37A", "#F29A18", "#3B5BDB"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), "k:N", alt.Tooltip("v:Q", format=".1f")])
    right = band + rules + lines
    if idx is not None and not idx.empty:
        k = idx[["date", "close"]].copy()
        k["date"] = pd.to_datetime(k["date"])
        k = k[k["date"] >= b["date"].min()].assign(k="코스피")
        left = alt.Chart(k).mark_line(strokeWidth=1.6).encode(
            x=x, y=alt.Y("close:Q", title=None, scale=alt.Scale(zero=False), axis=alt.Axis(orient="left", format=",.0f")),
            color=alt.Color("k:N", scale=alt.Scale(domain=["20일선 아래 비율", "200일선 아래 비율", "코스피"],
                                                   range=["#1FA37A", "#F29A18", "#3B5BDB"]), legend=None),
            tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("close:Q", format=",.2f", title="코스피")])
        chart = alt.layer(left, right).resolve_scale(y="independent")
    else:
        chart = right
    st.altair_chart(chart.properties(height=260, width="container"))


def _ia_breadth(histories: dict, quotes: dict):
    """시장 과매도 체크(20 DOWN) + 저가 매수 유닛 분할."""
    _sec_h("📉 시장 과매도 체크 (20 DOWN)", "보드 종목 중 20일선 아래 비율 — 90%↑ 통상 과매도권")
    bs = data.breadth_series(histories, quotes)
    if bs.empty or len(bs) < 5:
        st.caption("일봉이 아직 다 안 들어와서 비율을 계산하지 못했어요. 잠시 뒤 다시 볼게요.")
        return None
    last = bs.iloc[-1]
    d20, d200 = float(last["down20"]), (None if pd.isna(last["down200"]) else float(last["down200"]))
    d20_5 = float(bs["down20"].iloc[-6]) if len(bs) >= 6 else None
    zone, desc = data.breadth_zone(d20)
    zcls = "과매도" if zone == "과매도 접근" else zone
    # 과매도권(80%↑) 연속 일수
    streak = 0
    for v in bs["down20"].iloc[::-1]:
        if v >= 80:
            streak += 1
        else:
            break
    peak = float(bs["down20"].tail(20).max())
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("20 DOWN (20일선 아래)", f"{d20:.1f}%", None if d20_5 is None else f"{d20 - d20_5:+.1f}%p (5일 전 대비)",
              delta_color="inverse")
    k2.metric("200일선 아래", "-" if d200 is None else f"{d200:.1f}%")
    k3.metric("80%↑ 연속", f"{streak}일")
    k4.metric("최근 20일 최고", f"{peak:.1f}%")
    st.markdown(f'<div class="ia-zone z-{zcls}"><b class="z">{zone}</b> <span style="color:#7A8A94;font-size:.8rem">'
                f'{pd.Timestamp(last["date"]):%m/%d} 기준 · {int(last["n20"])}종목</span><div class="d">{html.escape(desc)}</div></div>',
                unsafe_allow_html=True)
    _ia_breadth_chart(bs, (load_indexes().get("KOSPI") or (None,))[0])

    with st.expander("🧺 저가 매수 유닛 나누기 (과매도 구간용)", expanded=d20 >= 80):
        c1, c2, c3 = st.columns(3)
        units = c1.segmented_control("유닛 수", [3, 4, 5], default=5, key="ia_units", format_func=lambda x: f"{x}유닛") or 5
        start = c2.number_input("1차 투입 기준(20 DOWN %)", 60, 95, 85, 1, key="ia_start",
                                help="이 비율 이상이면 1차. 이후 과매도가 이어지는 동안 지표 보며 하루 1유닛씩 더 넣는 흐름이에요.")
        money = c3.number_input("저가 매수에 쓸 금액(원)", 0, 10_000_000_000, 0, step=1_000_000, key="ia_money",
                                help="저장되지 않아요. 0이면 금액은 빼고 보여줘요.")
        st.caption("원칙: 시장이 어떻게 될지 모르니 한 번에 다 사지 않고 3~5유닛으로 나눠 분할 투입 · "
                   "반등이 나오면 바로 비중을 다시 줄이기 · 단기 반등 수익은 펀더멘털(투자포인트)이 그대로인 가치투자 보유 종목의 수량 늘리기에만.")
        # 투입 가능 차수: 기준 이상인 날이 연속된 일수(오늘 포함) — 하루 1유닛
        on_days = 0
        for v in bs["down20"].iloc[::-1]:
            if v >= start:
                on_days += 1
            else:
                break
        n_on = min(on_days, units)
        per = money / units if money else None
        cells = "".join(
            f'<div class="ia-unit{" on" if i < n_on else ""}"><b>{i + 1}차</b>'
            f'{"투입 구간" if i < n_on else "대기"}{f"<br>{per / 1e4:,.0f}만원" if per else ""}</div>' for i in range(units))
        st.markdown(f'<div class="ia-units">{cells}</div>', unsafe_allow_html=True)
        if n_on:
            st.markdown(f"**지금 {n_on}차까지 투입 구간** — 20 DOWN {start}%↑가 {on_days}일째예요. "
                        "지표가 더 나빠지거나 버티면 다음 차수, 반등(20 DOWN이 빠르게 내려옴)하면 추가 매수를 멈추고 비중 축소.")
        elif streak == 0 and peak >= start and d20 < peak - 15:
            st.markdown(f"**반등 구간** — 최근 20일 최고 {peak:.0f}%에서 {d20:.0f}%로 내려왔어요. "
                        "과매도 때 산 물량은 반등에서 비중을 줄이는 원칙이에요.")
        else:
            st.caption(f"지금은 1차 기준({start}%) 아래라 저가 매수 대기예요.")
    if d20 >= 60:
        st.warning("⚠️ 절반 넘는 종목이 20일선 아래예요. 이런 장에선 신고가 돌파·인더스트리 액션도 실패가 잦아요 — 정찰병 비중으로만.")
    return d20


def _ia_params() -> dict:
    d = data.IA_DEFAULTS
    with st.expander("⚙️ 인더스트리 액션 기준"):
        c1, c2, c3, c4 = st.columns(4)
        vol = c1.number_input("거래량(평소 20일 평균의 몇 배)", 1.0, 5.0, d["vol_mult"], 0.1, key="ia_vol")
        chg = c2.number_input("장대양봉 최소 등락률(%)", 0.0, 15.0, d["min_chg"], 0.5, key="ia_chg")
        mn = c3.number_input("섹터 안 같은 날 몇 종목↑", 1, 10, int(d["min_n"]), 1, key="ia_minn")
        near = c4.number_input("저항(직전 52주 고가)까지 % 이내", 1.0, 20.0, d["near_pct"], 0.5, key="ia_near")
        c5, c6, c7, c8 = st.columns(4)
        dcr = c5.number_input("종가 강도 최소(%)", 0.0, 100.0, d["dcr_min"], 5.0, key="ia_dcr",
                              help="그날 고가~저가 사이에서 종가 위치. 100%면 고가 마감(꼬리 없음)")
        cap = c6.number_input("최소 시가총액(억원)", 0.0, 100000.0, d["min_cap"], 500.0, key="ia_cap")
        ext = c7.number_input("20일선 이격 최대(%)", 5.0, 60.0, d["max_ext"], 1.0, key="ia_ext")
        equity = c8.number_input("계좌 평가금액(원, 수량 계산)", 0, 10_000_000_000, 0, step=1_000_000, key="ia_equity")
        st.caption("RS 70↑ & 코스피·코스닥 RS보다 위는 원칙이라 고정 · 장중 거래량 배수는 지금 시각까지로 환산(아침엔 크게 나올 수 있어요)")
    return {**d, "vol_mult": float(vol), "min_chg": float(chg), "min_n": int(mn), "near_pct": float(near),
            "dcr_min": float(dcr), "min_cap": float(cap), "max_ext": float(ext), "equity": float(equity or 0)}


def render_industry_action(df: pd.DataFrame, histories: dict, quotes: dict):
    st.markdown(NHC_CSS + IA_CSS, unsafe_allow_html=True)
    st.markdown('<div class="nh-head"><b>🏭 인더스트리 액션</b><span>주도섹터 후보에서 여러 종목이 같은 날 거래량 1.5배↑ + 장대양봉</span></div>',
                unsafe_allow_html=True)
    d20 = _ia_breadth(histories, quotes)

    _sec_h("🏭 섹터 인더스트리 액션", "섹터 안 거래량이 같이 붙는 날 → 그중 저항·신고가를 넘는 종목")
    p = _ia_params()
    leaders = {g for g, _ in lead_groups(df)}
    pot = data.potential_groups(df, leaders)
    scan = tab_memo("ia_scan", (tuple(sorted((k, v) for k, v in p.items() if isinstance(v, (int, float, str, bool)))), len(histories)), lambda: data.industry_action_scan(df, histories, quotes, p))
    if scan.empty:
        st.info("일봉이 아직 부족해요. 잠시 뒤 다시 볼게요.")
        return
    dates = sorted(scan["date"].unique(), reverse=True)
    c1, c2 = st.columns([1.4, 1])
    scope = c1.segmented_control("섹터 범위", ["주도섹터 가능성", "모든 섹터"], default="주도섹터 가능성", key="ia_scope")
    today = data.now_kst().date()
    day = c2.selectbox("날짜", dates, key="ia_day",
                       format_func=lambda x: ("오늘 " if x == today else "") + f"{x:%m/%d} ({'월화수목금토일'[x.weekday()]})")
    secs = data.industry_action_sectors(scan, day, p)
    if scope != "모든 섹터":
        secs = secs[secs["group"].isin(list(pot))]
    ia = secs[secs["ia"]]
    intraday = data.market_status(quotes).startswith("장중") and day == today

    hist_idx = load_indexes()
    idx_rs = {name: data.index_rs(hist_idx.get(sym, (data.empty_frame(), None))[0], df).get("rs")
              for name, sym in data.MARKETS.items()}
    oks = []
    for name, sym in data.MARKETS.items():
        sm = data.index_summary(hist_idx.get(sym, (data.empty_frame(), None))[0])
        oks.append(None if not sm or sm.get("above60") is None else bool(sm["above60"]))
    market_ok = None if None in oks else all(oks)
    cand = data.ia_candidates(df, scan, day, set(ia["group"]), p, idx_rs) if not ia.empty else pd.DataFrame()
    if not cand.empty:            # 원칙: 영업이익이 안 느는 종목은 배제 → 후보만 실적을 받아 다시 판정
        with st.spinner("후보 실적 확인 중…"):
            _ia_fins = load_financials(tuple(cand["code"]))
        cand = data.ia_candidates(df, scan, day, set(ia["group"]), {**p, "earn_years": earn_years}, idx_rs, _ia_fins)
    n_buy = int((cand["tier"] == "매수 가능").sum()) if not cand.empty else 0
    tiles = [
        _tile(market_ok, "시장 60일선", " · ".join(f"{n} {'✓' if o else ('✕' if o is False else '?')}" for n, o in zip(data.MARKETS, oks))),
        _tile(None if d20 is None else d20 < 60, "20 DOWN", "-" if d20 is None else f"{d20:.0f}%"),
        _tile(True if len(ia) else None, "액션 섹터", f"{len(ia)} / {len(secs)}"),
        _tile(True if n_buy else None, "✅ 매수 가능", f"{n_buy}"),
    ]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)
    if market_ok is False:
        st.error("⛔ 코스피·코스닥이 60일선 아래 — 원칙상 돌파매매 쉬는 구간이에요. 아래는 참고용.")

    # 섹터 표
    if secs.empty:
        st.caption("이 범위에 섹터가 없어요. '모든 섹터'로 넓혀 보세요.")
    else:
        names = scan[(scan["date"] == day) & scan["act"]].merge(df[["code", "name"]], on="code", how="left")
        acts = {g: " · ".join(f"{r.name}({r.chg:+.1f}%, {r.vx:.1f}배)" for r in sub.sort_values("vx", ascending=False).head(5).itertuples())
                for g, sub in names.groupby("group")}
        rows = []
        for r in secs.head(40).itertuples():
            lab = pot.get(r.group, "")
            tag = f'<span class="ia-sec{" lead" if lab == "주도섹터" else ""}">{html.escape(lab)}</span>' if lab else ""
            mark = '<b style="color:#C0262E">🏭 액션</b>' if r.ia else '<span style="color:#9AA9B3">-</span>'
            rows.append(
                f'<tr><td class="c">{mark}</td><td class="l nm"><b>{html.escape(str(r.group))}</b>{tag}'
                f'<small>{html.escape(acts.get(r.group, "액션 종목 없음"))}</small></td>'
                f'<td><b class="rs" style="color:{UP if r.n_act else "#16212B"}">{r.n_act}</b><small>/ {r.n}종목</small></td>'
                f'<td><b>{r.n_vol}</b><small>거래량 {p["vol_mult"]:.1f}배↑</small></td>'
                f'<td><b>{"-" if r.sec_vx is None else f"{r.sec_vx:.1f}배"}</b><small>섹터 거래대금</small></td>'
                f'<td>{_chg_html(r.chg)}<small>평균 등락</small></td></tr>')
        head = ('<tr><th class="c">판정</th><th class="l">섹터 · 액션 종목</th><th>장대양봉+거래량</th>'
                '<th>거래량 증가</th><th>섹터 배수</th><th>등락</th></tr>')
        st.markdown(f'<div class="nh-tbl-wrap"><table class="nh-tbl">{head}{"".join(rows)}</table></div>', unsafe_allow_html=True)
        st.caption(f"🏭 액션 = 같은 날 '거래량 평소 {p['vol_mult']:.1f}배↑ + 양봉 {p['min_chg']:.0f}%↑' 종목이 {p['min_n']}개↑ · "
                   "섹터 배수 = 섹터 거래대금 합 ÷ 평소(20일 평균) 합 · 주도섹터 가능성 = 주도섹터(최근 신고가 2개↑) + 52주 고가 10% 이내 2개↑")

    # 매수 후보
    _sec_h("🎯 액션 섹터 안 매수 후보", "거래량이 평소보다 높게 터진 종목 → 돌파·저항 근처·종가 강도·RS·시총·이격")
    if cand.empty:
        st.caption("이날은 인더스트리 액션 섹터가 없어요. 다른 날짜를 골라 보거나 기준을 넓혀 보세요.")
    else:
        bp = {**data.BUY_DEFAULTS}
        rows = []
        for r in cand.head(60).itertuples():
            tcls = {"매수 가능": "t0", "1개 미충족": "t1"}.get(r.tier, "t2")
            tier = "종가 확인" if (r.tier == "매수 가능" and intraday) else r.tier
            plan = data.position_plan(float(r.close), r.atr_pct, p["equity"] or None, bp)
            chk = " · ".join((f'<span class="x">✕ {html.escape(t)}</span>' if ok is False else
                              (f'<span class="x">? {html.escape(t)}</span>' if ok is None else html.escape(t)))
                             for ok, t in r.checks.values())
            lead = ""
            if r.lead_ok is True and r.lead_rank == r.lead_rank and r.lead_rank is not None:
                lead = '<span class="vs-tag first">👑 대장주</span>' if int(r.lead_rank) == 1 else f'<span class="ia-sec">섹터 {int(r.lead_rank)}위</span>'
            first = '<span class="vs-tag multi">⭐ 첫 돌파</span>' if (r.brk_close and r.bo_nth == 1) else ""
            qty = (f'<small>{plan["shares"]:,}주 · {plan["weight"]:.0f}%</small>' if plan.get("shares") else "")
            rows.append(
                f'<tr><td class="c"><span class="ia-tier {tcls}">{tier}</span></td>'
                f'<td class="l nm"><a href="?chart={html.escape(str(r.code))}" target="_self">{html.escape(str(r.name))}</a>{lead}{first}'
                f'<small>{r.code} · {html.escape(str(r.group))}</small></td>'
                f'<td><b class="rs" style="color:{UP}">{r.vx:.1f}배</b><small>거래대금 {_tv_eok(r.tv)}</small></td>'
                f'<td>{_chg_html(r.chg)}<small>종가 강도 {r.dcr:.0f}%</small></td>'
                f'<td><b>{"돌파" if r.brk_close else ("-" if r.to_res is None else f"{r.to_res:.1f}%")}</b><small>저항(52주 고가)</small></td>'
                f'<td><b class="px">{plan["stop_price"]:,.0f}</b><small>손절 -{plan["stop_pct"]:.1f}%</small></td>'
                f'<td><b class="px">{plan["target_price"]:,.0f}</b><small>3R +{3 * plan["stop_pct"]:.0f}%</small>{qty}</td>'
                f'<td class="l hide-m"><div class="ia-chk">{chk}</div></td></tr>')
        head = ('<tr><th class="c">판정</th><th class="l">종목</th><th>거래량</th><th>등락</th><th>저항</th>'
                '<th>손절가</th><th>3R 목표</th><th class="l hide-m">조건</th></tr>')
        st.markdown(f'<div class="nh-tbl-wrap"><table class="nh-tbl">{head}{"".join(rows)}</table></div>', unsafe_allow_html=True)
        st.caption("손절·3R은 그날 종가 기준(1R = 8%, ATR이 8%↑면 ATR) · 수량은 계좌 1.5% 위험 ÷ 3유닛 중 1유닛 · "
                   "'종가 확인' = 장중이라 종가까지 액션(양봉·거래량)이 유지되는지 봐야 해요")
        with st.expander("📌 매수 · 관리 순서 (매매 복기에서 정리)"):
            st.markdown(
                "1. **섹터 확인** — 주도섹터 가능성 있는 섹터에서 여러 종목이 같은 날 거래량 1.5배↑ 장대양봉(인더스트리 액션)\n"
                "2. **종목 고르기** — 그중 직전 고점(52주 고가) 근처·돌파, 종가 강하게(꼬리 짧게), 대장주·첫 돌파 우선\n"
                "3. **진입** — 아침 1차 + 종가 2차로 나눠 사기(양념반·후라이드반). 고가 돌파가 애매하면 관종(포켓피봇)만\n"
                "4. **손절** — -8%(1R), ATR이 더 크면 ATR\n"
                "5. **3R(+24%) 도달** — 1/3 익절해서 수익 쿠션 → 손절을 본절(+1%)로\n"
                "6. **나머지** — 본절 또는 20일선 종가 이탈 시 정리. 매도 후 다시 신고가 근처에서 트리거가 나오면 재진입")

    # 최근 N일 히트맵
    _sec_h(f"🗓️ 최근 {len(dates)}거래일 인더스트리 액션 기록", "칸 숫자 = 그날 장대양봉+거래량 종목 수 · 빨간 칸 = 액션")
    hm = scan[scan["act"]].groupby(["group", "date"]).size().reset_index(name="n")
    if scope != "모든 섹터":
        hm = hm[hm["group"].isin(list(pot))]
    hot_groups = hm[hm["n"] >= p["min_n"]]["group"].unique()
    hm = hm[hm["group"].isin(hot_groups)]
    if hm.empty:
        st.caption("최근에 인더스트리 액션이 나온 섹터가 없어요.")
    else:
        order = hm.groupby("group")["n"].sum().sort_values(ascending=False).index.tolist()[:25]
        hm = hm[hm["group"].isin(order)].copy()
        hm["d"] = pd.to_datetime(hm["date"]).dt.strftime("%m/%d")
        hm["ia"] = hm["n"] >= p["min_n"]
        dom = [pd.Timestamp(x).strftime("%m/%d") for x in sorted(dates)]
        base = alt.Chart(hm).encode(x=alt.X("d:O", title=None, sort=dom, scale=alt.Scale(domain=dom), axis=alt.Axis(labelAngle=0)),
                                    y=alt.Y("group:N", title=None, sort=order))
        rect = base.mark_rect(cornerRadius=3).encode(
            color=alt.condition("datum.ia", alt.Color("n:Q", scale=alt.Scale(scheme="reds", domain=[1, 6]), legend=None),
                                alt.value("#F6E3E4")),
            tooltip=["group:N", "d:O", alt.Tooltip("n:Q", title="액션 종목")])
        txt = base.mark_text(fontSize=11, fontWeight="bold").encode(
            text="n:Q", color=alt.condition("datum.n >= 3", alt.value("white"), alt.value("#16212B")))
        st.altair_chart((rect + txt).properties(height=max(120, 26 * len(order)), width="container"))

    # 차트
    if not cand.empty:
        labels = {r.code: f"{r.name} ({r.code}) · {r.group} · {r.vx:.1f}배" for r in cand.head(60).itertuples()}
        code = st.selectbox("차트로 보기", list(labels), format_func=labels.get, key="ia_chart_code")
        h = (histories.get(code) or (None, data.empty_frame(), None))[1]
        if h is not None and not h.empty:
            _vs_chart(h, labels[code], 1, 20)
    with st.expander("ℹ️ 이 탭을 쓰는 법"):
        st.markdown(
            "- **위(20 DOWN)**: 시장 전체가 얼마나 무너졌는지. 90%↑는 통상 과매도권 → 저가 매수는 3~5유닛 분할, 반등 시 비중 축소. "
            "보드에 올라온 종목 기준이라 전체 상장사 수치와 조금 다를 수 있어요.\n"
            "- **가운데(인더스트리 액션)**: 한 종목만 튀는 게 아니라 **섹터가 같이 거래량을 동반해 오르는 날**을 찾아요. "
            "그 섹터 안에서 거래량이 평소보다 높고, 저항(직전 52주 고가)을 넘거나 바로 아래인 종목이 매수 후보예요.\n"
            "- 날짜를 바꾸면 9/10·9/22처럼 지난 액션 날에 어떤 종목이 후보였는지 복기할 수 있어요.\n"
            "- 거래량 배수는 직전 20거래일 평균 대비, 장중엔 지금 시각까지로 환산해요. 장 초반은 과대평가될 수 있어요.")



# ─────────────────────────── 🎯 대시보드(10/28 시안): 지금 살 종목 · 왜 · 보유 대응 ───────────────────────────
NOW_CSS = """
.db-now{margin-bottom:1rem}
.db-nwg{display:grid;grid-template-columns:1fr 1fr;gap:.8rem}
.db-nw{border:1px solid var(--ln);border-radius:14px;padding:.75rem .85rem}
.db-nw .h{display:flex;justify-content:space-between;align-items:baseline}
.db-nw .h b{font-size:1.05rem;font-weight:850;color:#111827}.db-nw .h span{font-size:.86rem;font-weight:800}
.db-nw .ex{border-radius:12px;text-align:center;padding:.6rem .4rem;margin:.5rem 0 .25rem}
.db-nw .ex b{display:block;font-size:1.7rem;font-weight:900;line-height:1.15}
.db-nw .ex small{font-size:.78rem;font-weight:700;color:#4B5563}
.db-nw .ex-w{font-size:.78rem;color:#6B7280;text-align:center;margin-bottom:.35rem;line-height:1.5}
.db-nw .ex-w b{color:#111827}
.db-nw .kv{display:flex;justify-content:space-between;border-top:1px solid #F1F3F8;padding:.45rem .1rem;font-size:.88rem}
.db-nw .kv span{color:#4B5563;font-weight:600}.db-nw .kv b{color:#111827;font-weight:850}
.db-mtg{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem;margin-top:.9rem}
.db-mth{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:.35rem}
.db-mth b{font-size:.98rem;font-weight:850;color:#111827}.db-mth small{font-size:.74rem;color:#9CA3AF}
.db-mts{display:grid;grid-template-columns:1fr 1fr;gap:.5rem}
.db-mts .mt{background:#F7F9FC;border-radius:12px;padding:.55rem .7rem}
.db-mts .mt small{display:block;font-size:.76rem;color:#6B7280}
.db-mts .mt b{display:block;font-size:1.25rem;font-weight:900;color:#111827;margin:.1rem 0}
.db-mts .mt i{font-style:normal;font-size:.74rem;font-weight:800}
.db-mts .mt em{display:block;font-style:normal;font-size:.7rem;color:#9CA3AF;margin-top:.05rem}
@media (max-width:900px){.db-nwg,.db-mtg{grid-template-columns:1fr}}
"""
DB_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/npm/@sun-typeface/suit@2.0.5/fonts/variable/woff2/SUIT-Variable.css');
.db{--b:#2B7FF0;--bs:#EAF2FE;--ink:#111827;--sub:#6B7280;--mut:#9CA3AF;--ln:#ECEEF5;--card:#fff;--up:#E5484D;
    --amb:#F59E0B;--ambs:#FFF7E8;--grn:#16A34A;--grns:#E9F7EE}
.db,.db *{box-sizing:border-box;font-family:'SUIT Variable','Pretendard Variable',Pretendard,'Malgun Gothic',sans-serif!important;letter-spacing:-.01em}
.db-card{background:var(--card);border:1px solid var(--ln);border-radius:18px;box-shadow:0 1px 2px rgba(16,24,40,.04),0 6px 22px rgba(43,127,240,.05);
         padding:1.05rem 1.15rem}
.db-hero{display:flex;gap:1rem;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:1rem}
.db-hl{display:flex;gap:.9rem;align-items:flex-start;min-width:280px;flex:1 1 420px}
.db-ic{flex:0 0 auto;width:48px;height:48px;border-radius:14px;background:var(--bs);display:flex;align-items:center;justify-content:center}
.db-k{font-size:0.93rem;font-weight:700;color:#374151}
.db-hl h2{margin:.1rem 0 .25rem!important;padding:0!important;font-size:1.81rem!important;font-weight:850!important;color:var(--ink)!important;
          letter-spacing:-.035em;line-height:1.25!important}
.db-hl p{margin:0;font-size:1.02rem;color:var(--sub);line-height:1.5}
.db-mode{display:inline-block;margin-top:.45rem;font-size:0.86rem;font-weight:800;border-radius:999px;padding:.18rem .65rem}
.db-stats{display:grid;grid-template-columns:repeat(3,minmax(150px,190px));gap:.6rem;flex:0 1 auto}
.db-st{border:1px solid var(--ln);border-radius:14px;padding:.7rem .8rem;background:#fff;min-width:0}
.db-st .t{display:flex;align-items:center;gap:.45rem;font-size:0.84rem;font-weight:800;color:#374151;letter-spacing:.02em}
.db-st .t span{width:26px;height:26px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;font-size:0.99rem;font-weight:900}
.db-st b{display:block;font-size:1.26rem;font-weight:850;color:var(--ink);margin:.35rem 0 .1rem;white-space:nowrap}
.db-st small{font-size:0.81rem;color:var(--mut)}
.db-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1rem;margin-bottom:1rem}
.db-hd{display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem;margin-bottom:.7rem}
.db-hd h3{margin:0!important;padding:0!important;font-size:1.34rem!important;font-weight:850!important;color:var(--ink)!important;letter-spacing:-.03em}
.db-hd p{margin:.15rem 0 0;font-size:0.90rem;color:var(--sub)}
.db-pill{font-size:0.84rem;font-weight:800;border-radius:999px;padding:.25rem .65rem;white-space:nowrap;text-decoration:none!important}
.db-pill.b{background:var(--bs);color:#1A62CC}.db-pill.r{background:#FDECEC;color:#C0262E}.db-pill.g{background:#F3F4F6;color:#4B5563}
.db-tbl{width:100%;border-collapse:separate;border-spacing:0}
.db-tbl th{background:#F5F8FD;font-size:0.84rem;font-weight:700;color:var(--mut);text-align:left;padding:.5rem .55rem}
.db-tbl th:first-child{border-radius:10px 0 0 10px}.db-tbl th:last-child{border-radius:0 10px 10px 0}
.db-tbl td{padding:.62rem .55rem;border-bottom:1px solid #F1F3F8;vertical-align:middle}
.db-tbl tr:last-child td{border-bottom:none}
.db-rk{width:30px;height:30px;border-radius:9px;display:inline-flex;align-items:center;justify-content:center;font-weight:850;font-size:0.99rem;
       background:#F1F5F9;color:#64748B}
.db-rk.on{background:var(--b);color:#fff}
.db-ar{width:30px;height:30px;border-radius:9px;display:inline-flex;align-items:center;justify-content:center;background:var(--bs);color:var(--b);
       font-weight:900;font-size:0.99rem}
.db-ar.off{background:#F8FAFC;color:#CBD5E1}
.db-nm a{font-weight:850;font-size:1.08rem;color:var(--ink)!important;text-decoration:none!important}
.db-nm small{display:block;font-size:0.84rem;color:var(--sub);margin-top:.1rem;line-height:1.35}
.db-nm em{display:block;font-style:normal;font-size:0.84rem;color:#1A62CC;font-weight:700;margin-top:.1rem}
.db-px{font-weight:800;font-size:0.99rem;color:var(--ink);white-space:nowrap}
.db-ch b{font-size:1.01rem;font-weight:850;white-space:nowrap}
.db-ch small{display:block;font-size:0.81rem;font-weight:700;margin-top:.1rem}
.db-empty{font-size:0.99rem;color:#4B5563;background:#F9FAFB;border:1px dashed #E5E7EB;border-radius:12px;padding:.8rem .9rem}
.db-modebox{display:flex;gap:.8rem;align-items:flex-start;border-radius:14px;padding:.85rem .95rem;margin-bottom:.75rem;border:1px solid}
.db-modebox .i{flex:0 0 auto;width:38px;height:38px;border-radius:11px;background:#fff;display:flex;align-items:center;justify-content:center;
               font-weight:900;font-size:1.16rem}
.db-modebox small{font-size:0.84rem;font-weight:800}
.db-modebox b{display:block;font-size:1.26rem;font-weight:850;color:var(--ink);margin:.1rem 0}
.db-modebox p{margin:0;font-size:0.93rem;color:#4B5563;line-height:1.45}
.db-flt{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;background:#F5F8FD;border-radius:12px;padding:.6rem .75rem;margin-bottom:.85rem}
.db-flt small{font-size:0.84rem;color:var(--mut);font-weight:700;margin-right:.2rem}
.db-chip{font-size:0.84rem;font-weight:800;border-radius:999px;padding:.22rem .6rem;background:#fff;border:1px solid var(--ln);color:#374151}
.db-chip.a{background:var(--ambs);border-color:#FBE3B8;color:#B45309}.db-chip.b{background:var(--bs);border-color:#CFE2FC;color:#1A62CC}
.db-chip.g{background:var(--grns);border-color:#C9EBD5;color:#15803D}
.db-ckh{display:flex;align-items:baseline;gap:.7rem;margin:.1rem 0 .45rem}
.db-ckh b{font-size:1.04rem;font-weight:850;color:var(--ink)}.db-ckh small{font-size:0.84rem;color:var(--sub)}
.db-ck{display:grid;grid-template-columns:auto 110px 1fr auto;align-items:center;gap:.7rem;border:1px solid var(--ln);border-radius:12px;
       padding:.55rem .75rem;margin-bottom:.45rem}
.db-ck .n{width:28px;height:28px;border-radius:8px;background:var(--bs);color:#1A62CC;font-weight:850;font-size:0.86rem;display:flex;align-items:center;justify-content:center}
.db-ck b{font-size:0.99rem;font-weight:850;color:var(--ink)}
.db-ck .d{font-size:0.88rem;color:var(--sub)}
.db-ck .d em{font-style:normal;color:#111827;font-weight:700}
.db-ck i{width:20px;height:20px;border-radius:50%;border:2px solid #CBD5E1;display:inline-flex;align-items:center;justify-content:center;
         font-style:normal;font-size:0.81rem;font-weight:900;color:#fff}
.db-ck i.on{background:var(--b);border-color:var(--b)}
.db-bar{height:9px;border-radius:9px;background:#EEF2F8;margin-top:.55rem;overflow:hidden}
.db-bar div{height:100%;border-radius:9px;background:var(--b)}
.db-barl{font-size:0.81rem;color:var(--mut);margin-top:.25rem}
.db-sig{display:grid;grid-template-columns:auto 64px 1fr;gap:.7rem;align-items:center;background:#F7F9FC;border-radius:12px;padding:.6rem .75rem;margin-bottom:.45rem}
.db-sig .n{width:26px;height:26px;border-radius:8px;color:#fff;font-weight:850;font-size:0.81rem;display:flex;align-items:center;justify-content:center}
.db-sig b{font-size:1.00rem;font-weight:850;color:var(--ink)}
.db-sig .d{font-size:0.90rem;color:#4B5563;line-height:1.45}
.db-two{display:grid;grid-template-columns:1fr 1fr;gap:.7rem}
.db-box{background:#F7F9FC;border-radius:14px;padding:.85rem .9rem;min-width:0}
.db-box h4{margin:0 0 .5rem!important;padding:0!important;font-size:1.04rem!important;font-weight:850!important;color:var(--ink)!important;display:flex;align-items:center;gap:.45rem}
.db-box h4 span{width:30px;height:30px;border-radius:9px;background:#E7F6EC;color:#16A34A;display:inline-flex;align-items:center;justify-content:center;font-weight:900}
.db-hold{font-size:0.93rem;padding:.4rem 0;border-top:1px solid #E9EDF4;line-height:1.4}
.db-hold:first-of-type{border-top:none}
.db-hold b{font-size:1.00rem}.db-hold small{display:block;color:var(--sub);font-size:0.84rem}
.db-box p{margin:.2rem 0;font-size:0.93rem;color:#4B5563;line-height:1.5}
.db-box ul{margin:0;padding-left:0;list-style:none}
.db-box li{font-size:0.93rem;color:#374151;margin:.35rem 0;padding-left:1.1rem;position:relative;line-height:1.45}
.db-box li::before{content:"";position:absolute;left:0;top:.42rem;width:8px;height:8px;border-radius:50%;background:var(--b)}
.db-foot{display:flex;justify-content:space-between;gap:.5rem;font-size:0.81rem;color:var(--mut);border-top:1px solid var(--ln);padding:.6rem .1rem 0;margin-top:.2rem}
.db-foot b{letter-spacing:.08em;font-weight:800}
.db-bds{display:flex;flex-wrap:wrap;gap:.25rem;margin-top:.25rem}
.db-bd{font-size:0.79rem;font-weight:700;color:#1A62CC;background:#EAF2FE;border-radius:6px;padding:.08rem .4rem;white-space:nowrap}
.db-step{display:grid;grid-template-columns:auto 1fr auto;gap:.7rem;align-items:center;border:1px solid var(--ln);border-radius:12px;
         padding:.55rem .75rem;margin-bottom:.4rem;position:relative}
.db-step .n{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:850;font-size:0.93rem;
            background:#F1F5F9;color:#64748B}
.db-step b{display:block;font-size:1.02rem;font-weight:850;color:var(--ink)}
.db-step .d{font-size:0.88rem;color:var(--sub)}
.db-step .r{font-size:0.86rem;font-weight:800;border-radius:999px;padding:.2rem .6rem;white-space:nowrap;background:#F3F4F6;color:#6B7280}
.db-step.ok .n{background:var(--b);color:#fff}.db-step.ok .r{background:var(--grns);color:#15803D}
.db-step.no .n{background:#FDECEC;color:#C0262E}.db-step.no .r{background:#FDECEC;color:#C0262E}
.db-concl{border:1.5px solid;border-radius:14px;padding:.7rem .9rem;margin-top:.7rem}
.db-concl small{font-size:0.84rem;font-weight:800}
.db-concl b{display:block;font-size:1.43rem;font-weight:900;margin:.05rem 0}
.db-concl p{margin:0;font-size:0.97rem;color:#374151;font-weight:600}
.db-rule{display:flex;align-items:center;gap:.6rem;font-size:1.00rem;color:#1F2937;font-weight:600;background:#F7F9FC;border-radius:12px;
         padding:.6rem .75rem;margin-bottom:.45rem}
.db-rule > span{flex:0 0 auto;width:26px;height:26px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;font-weight:900}
.db-quote{font-size:0.97rem;color:#374151;border-left:3px solid var(--b);padding:.35rem .75rem;margin-top:.6rem;line-height:1.5}
.db-quote small{display:block;font-size:0.84rem;color:var(--mut);margin-top:.15rem}
.db-hl p b{color:#111827}
@media (max-width:520px){.db-step{grid-template-columns:auto 1fr}.db-step .r{grid-column:2;justify-self:start;margin-top:.1rem}}
.db .ln{display:block;margin:.14rem 0;line-height:1.55}
.db .ln.lead{font-weight:800;color:#111827}
.db-nt{font-size:0.88rem;font-weight:850;color:#111827;margin-bottom:.3rem}
.db-dl{display:grid;grid-template-columns:78px 1fr;gap:.7rem;padding:.45rem 0;border-top:1px solid #E6EAF2}
.db-dl:first-of-type{border-top:none}.db-dl b{font-size:0.86rem;font-weight:850}
.db-cmr{display:grid;grid-template-columns:118px 1fr;gap:.8rem;padding:.6rem 0;border-top:1px solid #F1F3F8;font-size:0.92rem;color:#374151}
.db-cmr:first-of-type{border-top:none}
.db-cmr b{align-self:start;font-size:0.82rem;font-weight:850;color:#1A62CC;background:#EAF2FE;border-radius:8px;padding:.2rem .5rem;text-align:center}
.db-sig{align-items:start}.db-sig .n{margin-top:.1rem}.db-sig b{margin-top:.15rem}
.db-step .x .ln{margin:.1rem 0}
@media (max-width:520px){.db-dl{grid-template-columns:1fr;gap:.15rem}.db-cmr{grid-template-columns:1fr;gap:.3rem}.db-cmr b{justify-self:start}}
.db-bp{margin-bottom:1rem}
.db-bpg{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem}
.db-bpi{background:#F7F9FC;border-radius:14px;padding:.7rem .85rem;min-width:0}
.db-bpi .h{display:flex;justify-content:space-between;align-items:baseline}
.db-bpi .h b{font-size:1.05rem;font-weight:850;color:#111827}.db-bpi .h span{font-size:1.05rem;font-weight:850}
.db-bpi small{display:block;font-size:.78rem;color:#6B7280;margin:.15rem 0 .4rem}
.db-bpc{display:inline-block;font-size:.82rem;font-weight:800;background:#fff;border:1px solid #ECEEF5;border-radius:999px;padding:.15rem .55rem;margin:0 .3rem .3rem 0}
@media (max-width:900px){.db-bpg{grid-template-columns:minmax(0,1fr)}}
.db-fx{display:grid;grid-template-columns:38px 1fr;gap:.45rem;margin-top:.3rem;font-size:0.86rem;color:#374151}
.db-fx em{font-style:normal;font-size:0.72rem;font-weight:800;color:#64748B;background:#F1F5F9;border-radius:6px;text-align:center;
          padding:.1rem 0;align-self:start;margin-top:.15rem}
.db-fx.rl em{color:#7C5CF0;background:#F1EEFE}.db-fx.gd em{color:#15803D;background:#E9F7EE}
.db-concl .db-fx em{background:#fff}
.db-rule b{font-weight:850;color:#111827}
""" + NOW_CSS + """
.db .db-a{font-family:'Pretendard Variable',Pretendard,'Malgun Gothic','Apple SD Gothic Neo',sans-serif!important}
.db-step .x{margin:.25rem 0 0;font-size:0.88rem;color:#4B5563;line-height:1.5}
.db-step{align-items:start}.db-step .r{margin-top:.15rem}
.db-concl .x{margin-top:.3rem!important;font-size:0.93rem!important;color:#4B5563!important;font-weight:500!important;line-height:1.5}
.db-note{font-size:0.89rem;color:#4B5563;line-height:1.6;background:#F7F9FC;border-radius:12px;padding:.6rem .75rem;margin-top:.7rem}
.db-note b{color:#111827}
.db-rule{align-items:flex-start}.db-rule small{display:block;margin-top:.2rem!important;font-size:0.88rem;font-weight:500;color:#6B7280;margin-top:.15rem;line-height:1.5}
.db-cm{margin:0;padding-left:1.1rem;font-size:0.95rem;color:#374151;line-height:1.65}
.db-cm li{margin:.2rem 0}.db-cm b{color:#111827}
@media (max-width:1100px){.db-stats{grid-template-columns:repeat(3,minmax(0,1fr));flex:1 1 100%}}
@media (max-width:900px){.db-grid,.db-two{grid-template-columns:minmax(0,1fr)}.db-hl h2{font-size:1.43rem!important}
  .db-ck{grid-template-columns:auto 1fr auto;row-gap:.15rem}.db-ck .n{grid-row:1/3;grid-column:1}.db-ck b{grid-column:2;grid-row:1}.db-ck .d{grid-column:2;grid-row:2}.db-ck i{grid-row:1/3;grid-column:3}}
@media (max-width:520px){.db-card{padding:.85rem .8rem}.db-stats{grid-template-columns:repeat(3,minmax(0,1fr));gap:.4rem}
  .db-st{padding:.5rem .55rem}.db-st b{font-size:1.01rem;white-space:normal}.db-st small{display:none}
  .db-tbl th:nth-child(2),.db-tbl td:nth-child(2){display:none}.db-tbl td{padding:.5rem .35rem}.db-sig{grid-template-columns:auto 1fr;row-gap:.3rem}
  .db-sig .d{grid-column:1/3}}
</style>
"""
_DB_SHORT = {
    "corr": "반등을 예상해 미리 사지 않고, 하락장에서 버티는 견조주 목록만 준비합니다.",
    "rally12": "정방향 신호(3일차 강세·FTD) 전까지 기다리고, 신고가·거래량·주도 섹터를 함께 확인합니다.",
    "day3": "3일차 강세 확인 — 견조주 중 오늘 신고가 가는 선도주에 정찰병을 작게 넣습니다.",
    "susp_ftd": "의심스러운 FTD — 의심의 벽을 타는 자리. 인더스트리 액션 섹터 선도주에 정찰병을 넣습니다.",
    "ftd": "FTD 확인 — 정찰병 중 살아서 오르는 종목에 점진적으로 비중을 늘립니다.",
    "press": "분산일이 평소보다 많아요. 신규는 정찰병 크기로, 약한 종목부터 정리합니다.",
    "up": "상승 추세 — 주도섹터 대장주의 신고가 돌파를 원칙대로 삽니다.",
}
_IDX_EN = {"코스피": "KOSPI", "코스닥": "KOSDAQ"}
_CLOCK = ('<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2B7FF0" stroke-width="2.2" stroke-linecap="round">'
          '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>')


def load_health(df: pd.DataFrame) -> pd.DataFrame:
    """시장 체력(20/200일선 하락비율 · 52주 신고가/신저가 · ADR) — 보드 국내 일봉으로 계산, 5분 기억."""
    return data.market_health(_hist_store()["data"], _QUOTES)


def load_deposits() -> pd.DataFrame:
    t = swr(("deposits", DATA_VERSION), 3600, lambda: data.fetch_deposits(), "증시 자금(예탁금·신용잔고)을 불러오는 중이에요.")
    if t is None or len(t) == 0:            # 실패는 오래 기억하지 않고 10분 뒤 다시 시도
        t = swr(("deposits_retry", DATA_VERSION, data.now_kst().strftime("%H%M")[:3]), 600, lambda: data.fetch_deposits(),
                "증시 자금을 다시 불러오는 중이에요.")
    return t


def _hl_tone(kind: str, v) -> tuple[str, str]:
    """지표 값 → (색, 한 줄 해석). 기준은 스탁이지 읽는 법 · 내 원칙과 같은 숫자."""
    if v is None or v != v:
        return "#9CA3AF", "-"
    if kind == "down20":
        return ("#1F66C9", "과매도권(90%↑)") if v >= 90 else (("#1F66C9", "과매도 접근(80%↑)") if v >= 80 else
                (("#B45309", "약세(60%↑)") if v >= 60 else (("#E5484D", "과열권(20%↓)") if v <= 20 else ("#6B7280", "중립"))))
    if kind == "down200":
        return ("#1F66C9", "장기 약세(70%↑)") if v >= 70 else (("#15803D", "장기 강세(30%↓)") if v <= 30 else ("#6B7280", "중립"))
    if kind == "net":
        return ("#15803D", "신고가 우세") if v > 0 else (("#B45309", "신저가 우세") if v < 0 else ("#6B7280", "균형"))
    if kind == "adr":
        return ("#E5484D", "과열(120↑)") if v >= 120 else (("#15803D", "매수 우위(100↑)") if v >= 100 else
                (("#1F66C9", "침체(75↓)") if v <= 75 else ("#6B7280", "매도 우위(100↓)")))
    if kind == "credit_osc":
        return ("#E5484D", "빚투 증가") if v > 1 else (("#1F66C9", "빚투 감소") if v < -1 else ("#6B7280", "보합"))
    if kind == "credit_ratio":
        return ("#E5484D", "레버리지 높음(35%↑)") if v >= 35 else ("#6B7280", "보통")
    return "#6B7280", ""


def market_now(df: pd.DataFrame) -> dict:
    """'지금 여긴' 숫자 모음 — 판단 보드 · 시장 신호 탭이 같이 써요."""
    msigs = market_signals()
    hist_idx = load_indexes()
    idx = {}
    for name, sym in data.MARKETS.items():
        m = msigs.get(name)
        sm = data.index_summary(hist_idx.get(sym, (data.empty_frame(), None))[0]) or {}
        ph = scout.index_phase(m)
        ex = data.exposure_for(ph["phase"] if ph else None, sm.get("above60"), (ph or {}).get("n_after"),
                               (m or {}).get("dd"), (m or {}).get("q50"))
        lf = (m or {}).get("last_ftd")
        idx[name] = {"state": (m or {}).get("state_txt", "-"), "em": (m or {}).get("em", "⚪"), "phase": ph,
                     "exp": ex["final"], "exp_ibd": ex["ibd"], "exp_why": ex["ibd_why"], "exp_mine": ex["mine"],
                     "dd": (m or {}).get("dd"), "q50": (m or {}).get("q50"),
                     "ftd": pd.Timestamp(lf["date"]) if lf else None, "ftd_res": lf["result"] if lf else None,
                     "above60": sm.get("above60"), "dist60": sm.get("dist60"), "last": sm.get("last"), "chg": sm.get("change")}
    try:
        h = load_health(df)
    except Exception:  # noqa: BLE001
        h = pd.DataFrame()
    hl = h.dropna(subset=["down20"]).iloc[-1] if len(h) else None
    hn = h.dropna(subset=["net"]).iloc[-1] if len(h) and h["net"].notna().any() else None
    try:
        dp = load_deposits()
    except Exception:  # noqa: BLE001
        dp = pd.DataFrame()
    dl = dp.iloc[-1] if len(dp) else None
    return {"idx": idx, "health": h, "h_last": hl, "h_net": hn, "dep": dp, "d_last": dl}


def _now_card(mn: dict) -> str:
    """판단 보드 '지금 여긴' — 지수별 국면·권장 노출·분산일·마지막 FTD + 시장 체력 + 증시 자금(모두 숫자)."""
    esc = html.escape
    cols = []
    for name, x in mn["idx"].items():
        ok = x["state"] in ("상승 추세",)
        col, bg = ("#15803D", "#E9F7EE") if ok else (("#B45309", "#FFF7E8") if x["state"] in ("상승 둔화", "반등 시도") else ("#C0262E", "#FDECEC"))
        res_ = x["ftd_res"]
        if res_ == "⏳ 진행 중" and x["state"] in ("조정 국면", "반등 시도"):
            res_ = "이후 조정"
        ftd = (f"{x['ftd']:%y.%m.%d}" + (f" · {res_}" if res_ else "")) if x["ftd"] is not None else "-"
        dd = "-" if x["dd"] is None else f"{x['dd']}개" + (f" (평소 {x['q50']:.0f})" if x["q50"] is not None else "")
        cols.append(f'<div class="db-nw"><div class="h"><b>{esc(name)}</b><span style="color:{col}">● {esc(x["state"])}</span></div>'
                    f'<div class="ex" style="background:{bg};color:{col}"><b>{esc(x["exp"])}</b><small>권장 노출</small></div>'
                    f'<div class="ex-w">IBD 기준 <b>{esc(x["exp_ibd"])}</b> — {esc(x["exp_why"])}'
                    + (f'<br>내 원칙: 60일선 아래 → <b>{esc(x["exp_mine"])}</b>(정찰병만)' if x["exp_mine"] else "") + '</div>'
                    f'<div class="kv"><span>60일선 대비</span><b>{"-" if x["dist60"] is None else format(x["dist60"], "+.1f") + "%"}</b></div>'
                    f'<div class="kv"><span>Distribution Day (25일)</span><b>{esc(dd)}</b></div>'
                    f'<div class="kv"><span>Last Follow-Through Day</span><b>{esc(ftd)}</b></div></div>')
    hl, hn, dl = mn["h_last"], mn["h_net"], mn["d_last"]

    def met(label, v, fmt, kind, sub=""):
        c, t = _hl_tone(kind, v)
        vs = "-" if v is None or v != v else fmt.format(v)
        return (f'<div class="mt"><small>{esc(label)}</small><b>{vs}</b><i style="color:{c}">{esc(t)}</i>'
                + (f'<em>{esc(sub)}</em>' if sub else "") + '</div>')
    n_used = int(hl["n"]) if hl is not None else 0
    health = "".join([
        met("20일선 하락비율", None if hl is None else hl["down20"], "{:.1f}%", "down20"),
        met("200일선 하락비율", None if hl is None else hl["down200"], "{:.1f}%", "down200"),
        met("신고가-신저가 순증", None if hn is None else hn["net"], "{:+.0f}", "net",
            "" if hn is None else f"신고가 {hn['nh']:.0f} · 신저가 {hn['nl']:.0f}"),
        met("ADR (20일)", None if hl is None else hl["adr"], "{:.1f}", "adr")])
    money = "".join([
        met("투자자예탁금", None if dl is None else dl["deposit"] / 10000, "{:.1f}조", ""),
        met("신용잔고", None if dl is None else dl["credit"] / 10000, "{:.1f}조", ""),
        met("신용잔고/예탁금", None if dl is None else dl["credit_ratio"], "{:.1f}%", "credit_ratio"),
        met("신용잔고 오실레이터", None if dl is None else dl["credit_osc"], "{:+.1f}%", "credit_osc", "5일÷20일 평균 −1")])
    hdate = f"{pd.Timestamp(hl['date']):%y.%m.%d}" if hl is not None else "-"
    _dg = getattr(data, "DEPOSIT_DIAG", {}) or {}
    ddate = f"{pd.Timestamp(dl['date']):%y.%m.%d}" if dl is not None else "불러오지 못함 — 네이버 페이지 형식이 바뀌어 다른 출처 확인 중"
    link = "?tab=" + quote_plus("🚦 시장 신호")
    return ('<div class="db-card db-now"><div class="db-hd"><div><h3>📍 지금 여긴</h3>'
            '<p>지수 국면 · 권장 노출(IBD 5단계 + 내 60일선 원칙) · 분산일 · FTD(IBD: Day 4 이후 +1.25%↑ · 거래량 증가)</p></div>'
            f'<a class="db-pill b" href="{link}" target="_self">차트로 보기 →</a></div>'
            f'<div class="db-nwg">{"".join(cols)}</div>'
            f'<div class="db-mtg"><div><div class="db-mth"><b>시장 체력</b><small>기준 {hdate} · 보드 국내 {n_used}종목</small></div>'
            f'<div class="db-mts">{health}</div></div>'
            f'<div><div class="db-mth"><b>증시 자금</b><small>기준 {ddate} · 네이버 증시자금동향</small></div>'
            f'<div class="db-mts">{money}</div></div></div></div>')





def _db_lines(text, by_mid: bool = False, lead: bool = False) -> str:
    """긴 설명을 한 줄에 한 가지씩: 문장 끝(. )마다, by_mid면 ' · '마다 줄을 나눠요."""
    parts = [str(text or "").strip()]
    parts = [q for x in parts for q in re.split(r"(?<=[.])\s+(?=[^\d\s])", x)]
    if by_mid:
        parts = [q for x in parts for q in x.split(" · ")]
    parts = [q.strip() for q in parts if q and q.strip()]
    return "".join(f'<span class="ln{" lead" if lead and i == 0 else ""}">{html.escape(q)}</span>' for i, q in enumerate(parts))


def _db_note_row(x: str) -> str:
    """해설 한 항목('<b>제목</b>: 내용') → 제목 칸 + 줄 나눈 내용."""
    m = re.match(r"<b>(.*?)</b>\s*:?\s*(.*)", x, flags=re.S)
    label, body = (m.group(1), m.group(2)) if m else ("", x)
    body = re.sub(r"</?b>", "", body)
    return f'<div class="db-cmr"><b>{html.escape(label)}</b><div>{_db_lines(body, by_mid=True)}</div></div>'


def _db_bigpicture(df: pd.DataFrame) -> str:
    """판단 보드 띠: 큰 그림(AI · 비AI · 그외) — 영역 등락과 강한 테마 3개."""
    try:
        kr = df[df["code"].map(data.is_kr)]
        s_ = tab_memo("bigpicture", ("국내", len(kr)), lambda: bigpicture.summary(kr, recent_days))
        bs = bigpicture.bucket_summary(s_)
    except Exception:  # noqa: BLE001
        return ""
    cells = []
    for b, em, _ in bigpicture.BIG:
        x = bs.get(b)
        if not x:
            continue
        col = "#E5484D" if (x["chg"] or 0) > 0 else ("#1F66C9" if (x["chg"] or 0) < 0 else "#6B7280")
        chips = "".join(f'<span class="db-bpc" style="color:{"#E5484D" if (c or 0) > 0 else ("#1F66C9" if (c or 0) < 0 else "#6B7280")}">'
                        f'{html.escape(t)} {"-" if c is None or c != c else format(c, "+.1f") + "%"}</span>' for t, c in x["top"])
        bchg = "-" if x["chg"] is None else format(x["chg"], "+.1f") + "%"
        cells.append(f'<div class="db-bpi"><div class="h"><b>{em} {b}</b><span style="color:{col}">{bchg}</span></div>'
                     f'<small>오른 테마 {x["n_up"]}/{x["n"]} · 5일 신고가 {x["nh5"]} · 강한 테마</small><div>{chips}</div></div>')
    link = "?tab=" + quote_plus("🗺️ 큰 그림")
    return ('<div class="db-card db-bp"><div class="db-hd"><div><h3>🗺️ 큰 그림</h3>'
            '<p>AI · 비AI · 그외 영역으로 먼저 나눠 보기 — 숫자는 테마 등락 중앙값</p></div>'
            f'<a class="db-pill b" href="{link}" target="_self">테마 전체 →</a></div><div class="db-bpg">{"".join(cells)}</div></div>')


def _tb_holdings(df: pd.DataFrame, phase: str | None) -> tuple[list, str | None, int]:
    """보유 종목 매도·대응 요약(잠금이 풀렸을 때만). (행 [(이모지, 이름, 손익, 할 일)], 안내문, 급한 수)"""
    ss = st.session_state
    try:
        locked = holdings.has_password(holdings.load_settings()) and not ss.get("hold_unlocked")
    except Exception:  # noqa: BLE001
        locked = True
    if locked:
        return [], "🔒 잠겨 있어요 — 💼 내 보유에서 비밀번호로 열면 여기에 손절·익절·본절 신호가 떠요.", 0
    hs = [h for h in (holdings.load().get("holdings") or []) if h.get("account", "추세") == "추세"]
    if not hs:
        return [], "표시할 보유 종목이 없습니다. 💼 내 보유에 넣으면 여기서 매도 신호를 봐요.", 0
    px = dict(zip(df["code"], pd.to_numeric(df["price"], errors="coerce")))
    atr = dict(zip(df["code"], pd.to_numeric(df["atr_pct"], errors="coerce"))) if "atr_pct" in df else {}
    rows, urgent = [], 0
    order = {"🔴": 0, "🔵": 1, "🟠": 2, "🟣": 3, "🟢": 4, "⚪": 5}
    for h in hs:
        p, avg = px.get(h["code"]), holdings._f(h.get("avg"))
        if not p or p != p or not avg:
            rows.append(("⚪", h["name"], "시세 없음", "보드에 없는 종목 — 💼 내 보유에서 점검"))
            continue
        a = atr.get(h["code"])
        sp = holdings._f(h.get("stop_pct")) or max(8.0, a if a == a and a else 0.0)
        pnl = (p / avg - 1) * 100
        r = pnl / sp if sp else 0
        stop, t3 = avg * (1 - sp / 100), avg * (1 + 3 * sp / 100)
        if p <= stop:
            em, act = "🔴", f"손절선 {stop:,.0f} 이탈 — 원칙대로 정리"
        elif r >= 3 and not h.get("half_taken"):
            em, act = "🔵", f"3R {t3:,.0f} 도달 — 절반 익절, 나머지는 5일선이 50일선에 닿을 때까지"
        elif r >= 2:
            em, act = "🟣", f"+{r:.1f}R — 손절선을 본전({avg:,.0f}) 이상으로 올리기"
        elif phase in ("corr", "rally12") and r < 1:
            em, act = "🟠", f"쉬는 장 · 쿠션 없음 — 정찰병 크기인지 확인, 손절 {stop:,.0f} 넓히지 않기"
        else:
            em, act = "🟢", f"보유 유지 — 손절 {stop:,.0f} · 3R {t3:,.0f}"
        urgent += em in ("🔴", "🔵")
        rows.append((em, h["name"], f"{pnl:+.1f}% ({r:+.1f}R)", act))
    rows.sort(key=lambda x: order.get(x[0], 9))
    return rows, None, urgent


def render_trade_board(df: pd.DataFrame, histories: dict, quotes: dict):
    """🎯 대시보드: 오늘 판정 + 지수·진입 신호 → 준비/살 종목 TOP 6 · 오늘의 매매 판단 → 시장 흐름 · 보유·리스크."""
    try:
        sig = _ia_sig(df, histories, quotes)
    except Exception:  # noqa: BLE001
        sig = {"signals": [], "watch": [], "scan": None, "ia_all": [], "market_ok": None, "d20": None, "market_text": ""}
    msigs = market_signals()
    phases = {n: scout.index_phase(m) for n, m in msigs.items()}
    phase, ph_txt = scout.combine(phases)
    best = max(((n, v) for n, v in phases.items() if v), key=lambda kv: kv[1]["rank"], default=(None, None))[0]
    m_best = msigs.get(best) if best else None
    low, peak = (m_best or {}).get("low_date"), (m_best or {}).get("peak_date")
    res = tab_memo("scout_res", (str(low), str(peak), len(df)), lambda: scout.resilient(df, histories, low, peak))
    n_res = int(res["resilient"].sum()) if res is not None else None
    ia_secs = list(sig.get("ia_all") or [])
    cands = tab_memo("scout_cands", (str(low), tuple(ia_secs), len(df)),
                     lambda: scout.candidates(df, sig.get("scan"), res, set(ia_secs)))
    allowed = scout.scout_allowed(phase)
    market_ok = sig.get("market_ok")
    pb = scout.PLAYBOOK.get(phase or "corr")
    ph_key = phase or "corr"
    esc = html.escape

    # ── 사실(숫자) 모으기
    hist_idx = load_indexes()
    idx_sm = {n: (data.index_summary(hist_idx.get(sym, (data.empty_frame(), None))[0]) or {}) for n, sym in data.MARKETS.items()}
    kr = df[df["code"].map(data.is_kr)]
    n_nh = int((pd.to_numeric(kr["days_since_high"], errors="coerce") == 0).sum()) if "days_since_high" in kr else 0
    leaders = lead_groups(df)
    n_lead, lead_names = len(leaders), [g for g, _ in leaders][:3]
    n_main = len(sig.get("signals") or [])
    eb = tab_memo("early_buys", (tuple(ia_secs), len(df)),
                  lambda: scout.early_buys(df, sig.get("scan"), histories, quotes, set(ia_secs)))
    n_eb = len(eb)
    n_go = int((cands["tier"] == "투입").sum()) if len(cands) else 0
    hrows, hmsg, urgent = _tb_holdings(df, phase)
    d20 = sig.get("d20")
    W = scout.SCOUT_WEIGHT

    # ── 판단 회로: 단계마다 사실 → 원칙 → 판정
    mn = market_now(df)
    _hl, _hn, _dl = mn["h_last"], mn["h_net"], mn["d_last"]
    hfacts = []
    if _hl is not None:
        hfacts.append(f"20일선 하락비율 {_hl['down20']:.1f}% ({_hl_tone('down20', _hl['down20'])[1]}) · "
                      f"200일선 {_hl['down200']:.1f}%" + (f" · ADR {_hl['adr']:.1f} ({_hl_tone('adr', _hl['adr'])[1]})" if _hl['adr'] == _hl['adr'] else ""))
    if _hn is not None:
        hfacts.append(f"52주 신고가 {_hn['nh']:.0f} · 신저가 {_hn['nl']:.0f} → 순증 {_hn['net']:+.0f} ({_hl_tone('net', _hn['net'])[1]})")
    hfacts.append("권장 노출 " + " · ".join(f"{n} {x['exp']}" for n, x in mn["idx"].items()))
    s1 = {"t": "시장 국면", "ok": allowed,
          "facts": [f"{n}: {scout.PLAYBOOK[v['phase']]['title'].split(' — ')[0]} — {v['why']}" for n, v in phases.items() if v] + hfacts,
          "rule": "스승님: 조정 중엔 사지 않는다. 반등 3일차 강세 · FTD가 나와야 정찰병부터 (FTD는 '허락'이지 '공격'이 아님)",
          "v": "정찰병 허락" if allowed else "매수 보류 — 반등 확인 전"}
    s2 = {"t": "지수 60일선", "ok": market_ok,
          "facts": [f"{n} 60일선 대비 {sm['dist60']:+.1f}% ({'위' if sm.get('above60') else '아래'})" for n, sm in idx_sm.items() if sm.get("dist60") is not None],
          "rule": "내 원칙: 코스피·코스닥 둘 다 60일선 위여야 본매매(유닛). 하나라도 아래면 정찰병 크기만",
          "v": "본매매 가능" if market_ok else ("정찰병 크기만" if market_ok is False else "확인 중")}
    s3 = {"t": "주도 · 액션 섹터", "ok": bool(n_lead or ia_secs),
          "facts": [f"주도섹터(5일 안 신고가 2종목↑) {n_lead}개" + (f": {', '.join(lead_names)}" if lead_names else ""),
                    f"오늘 인더스트리 액션 {len(ia_secs)}개" + (f": {', '.join(ia_secs[:3])}" if ia_secs else "")],
          "rule": "내 원칙: 주도섹터가 없으면 돌파매매 안 함 · 스승님: 그날 액션 섹터의 선도주가 1순위",
          "v": "매수할 섹터 있음" if (n_lead or ia_secs) else "섹터 없음 — 돌파매매 쉼"}
    s4 = {"t": "종목 조건", "ok": bool(n_main or n_eb or n_go),
          "facts": [f"원칙 전부 통과(52주 신고가 돌파) {n_main}개",
                    f"선매입(매물대 돌파 + 거래량 1.5배↑ + 액션 섹터) {n_eb}개",
                    f"견조주 중 거래량 실은 신고가 {n_go}개" + (f" (견조주 {n_res}개 중)" if n_res is not None else "")],
          "rule": "RS 70↑·지수보다 강함 · 거래량 실은 장대양봉 · 52주 신고가 또는 큰 매물대 돌파 · 영업이익 증가",
          "v": "살 종목 있음" if (n_main or n_eb or n_go) else "조건 맞는 종목 없음"}
    s5 = {"t": "리스크", "ok": urgent == 0,
          "facts": [f"정찰병 {W:.0f}% × 손절 8% = 종목당 계좌 위험 약 {W * 0.08:.2f}%",
                    "1유닛 6% × 8% ≈ 0.48% · 한도 종목당 1.5%, 최대 8종목"]
          + ([f"보유 중 손절·익절 처리할 종목 {urgent}개"] if urgent else []),
          "rule": "리스크관리 > 멘탈 > 종목 > 타점 — 보유 종목의 손절·익절이 새 매수보다 먼저",
          "v": "새 매수 여유 있음" if urgent == 0 else "보유 정리 먼저"}
    steps = [s1, s2, s3, s4, s5]

    # ── 결론: 위에서부터 막힌 단계가 있으면 거기서 멈춤
    if not s1["ok"]:
        decision = "쉬기"
    elif not s3["ok"]:
        decision = "쉬기"
    elif urgent:
        decision = "보유 정리 먼저"
    elif not s4["ok"]:
        decision = "대기"
    elif market_ok is not True:
        decision = "정찰병만"
    else:
        decision = "매수"
    can_buy = decision in ("매수", "정찰병만")
    size = "1유닛(계좌 약 6%)" if decision == "매수" else f"계좌 약 {W:.0f}%"
    stop_reasons = [f"{s_['t']} — {s_['v']}" for s_ in steps if s_["ok"] is False]
    go_reasons = [f"{s_['t']} — {s_['v']}" for s_ in steps if s_["ok"]]
    ok_n = sum(1 for s_ in steps if s_["ok"])

    # ── TOP 6: 원칙 매수 → 선매입 → 정찰병 → (남는 칸) 준비 종목
    rows, seen = [], set()
    for x in sig.get("signals") or []:
        seen.add(x["code"])
        why = " · ".join(t for t in (f"거래량 {x['vx']:.1f}배", x["lead"], "⭐ 첫 돌파" if x["first"] else None,
                                     "52주 신고가 돌파" if x["brk"] else f"저항까지 {x['to_res']:.1f}%", "원칙 전부 통과") if t)
        rows.append({"kind": "main" if can_buy else "watch", "tag": "원칙 매수" if can_buy else "원칙 통과(대기)", "code": x["code"],
                     "name": x["name"], "group": x["group"], "price": x["price"], "chg": x["chg"], "why": why,
                     "plan": f"손절 {x['stop']:,.0f} · 3R {x['target']:,.0f} · {size}" if can_buy else None})
    for r in eb.head(6).itertuples():
        if r.code in seen:
            continue
        seen.add(r.code)
        rows.append({"kind": "early" if can_buy else "watch", "tag": "선매입" if can_buy else "선매입 후보(대기)", "code": r.code,
                     "name": r.name, "group": r.group, "price": r.price, "chg": r.chg, "why": r.why,
                     "plan": (f"손절 {r.stop:,.0f}(-{r.stop_pct:.1f}%, 매물대 아래) · 3R {r.target:,.0f} · {size}" if can_buy
                              else f"매물대 {r.node_lo:,.0f}~{r.node_hi:,.0f} 위 유지 확인")})
    if can_buy and phase != "up":
        for r in scout.pick(cands, n=6, per_group=2).itertuples() if len(cands) else []:
            if r.code in seen:
                continue
            seen.add(r.code)
            rows.append({"kind": "scout", "tag": "정찰병", "code": r.code, "name": r.name, "group": r.group, "price": r.price,
                         "chg": r.chg, "why": r.why, "plan": f"손절 {r.stop:,.0f} · 3R {r.target:,.0f} · 계좌 {W:.0f}%"})
    n_buy = sum(1 for r in rows if r["kind"] in ("main", "early", "scout"))
    if len(rows) < 6 and len(cands):
        pool = pd.concat([scout.pick(cands, 12, 2, "투입"), scout.pick(cands, 12, 2, "대기")])
        for r in pool.itertuples():
            if len(rows) >= 6:
                break
            if r.code in seen:
                continue
            seen.add(r.code)
            rows.append({"kind": "watch", "tag": "우선 검토" if r.tier == "투입" else "관찰 후보", "code": r.code, "name": r.name,
                         "group": r.group, "price": r.price, "chg": r.chg, "why": r.why, "plan": None})
    for x in (sig.get("signals") or []):
        if x["code"] not in st.session_state.setdefault("ia_toasted", set()):
            st.session_state["ia_toasted"].add(x["code"])
            st.toast(f"🔔 매수하세요: {x['name']} {x['chg']:+.1f}% · 거래량 {x['vx']:.1f}배", icon="🏭")

    # ── 모드 · 한 줄 판정 (결론을 그대로 따름)
    n_e = sum(1 for r in rows if r["kind"] == "early")
    n_s = sum(1 for r in rows if r["kind"] == "scout")
    n_m = sum(1 for r in rows if r["kind"] == "main")
    if decision == "매수":
        mode, mcol, mbg, micon, mtitle = "매수 모드", "#15803D", "#E9F7EE", "✓", "매수 · 원칙 진입"
        action = f"매수 {n_buy}종목"
    elif decision == "정찰병만":
        mode, mcol, mbg, micon, mtitle = "정찰병 모드", "#1A62CC", "#EAF2FE", "↗", "소량 진입 · 정찰병 크기"
        action = f"소량 진입 {n_buy}종목"
    elif decision == "대기":
        mode, mcol, mbg, micon, mtitle = "대기 모드", "#1A62CC", "#EAF2FE", "…", "대기 · 종목 조건 미충족"
        action = "조건 맞는 종목 대기"
    elif decision == "보유 정리 먼저":
        mode, mcol, mbg, micon, mtitle = "정리 모드", "#C0262E", "#FDECEC", "!", "보유 정리 먼저"
        action = "보유 정리 먼저"
    else:
        mode, mcol, mbg, micon, mtitle = "관찰 모드", "#B45309", "#FFF7E8", "!", "관찰 · 신규 매수 멈춤"
        action = "신규 매수 멈춤"
    headline = f"{pb['title'].split(' — ')[0]} · {action}"
    mdesc = {"매수": f"원칙 매수 {n_m} · 선매입 {n_e} · 정찰병 {n_s} — 아침·종가로 나눠 사고, 손절선을 먼저 정해 둡니다.",
             "정찰병만": f"선매입 {n_e} · 정찰병 {n_s} · 원칙 매수 {n_m} — 종목당 계좌 약 {W:.0f}%, 손절 1R 그대로. 지수가 60일선을 회복하면 살아남은 종목부터 1유닛.",
             "대기": "국면과 섹터는 허락했지만 거래량·신고가·매물대 조건을 채운 종목이 없어요. 억지로 사지 않습니다.",
             "보유 정리 먼저": "보유 종목 중 손절선 이탈·3R 도달이 있어요. 새 매수보다 그 처리가 먼저입니다.",
             "쉬기": "막힌 단계가 풀릴 때까지 사지 않습니다. 대신 버티는 견조주·매물대 근처 종목을 목록으로 준비합니다."}[decision]
    concl_act = {"매수": f"종목당 {size} · 손절 -8%(선매입은 매물대 아래) · 3R에서 절반",
                 "정찰병만": f"종목당 계좌 약 {W:.0f}% · 손절 -8%(선매입은 매물대 아래) · 같은 섹터 2개까지",
                 "대기": "사지 않기 — 준비 목록에서 매물대 돌파·신고가가 나오는지 지켜보기",
                 "보유 정리 먼저": "💼 내 보유에서 손절·익절부터 처리",
                 "쉬기": "사지 않는 게 매매 — 견조주·매물대 근처 종목 목록만 준비"}[decision]
    why_bits = (["멈추는 이유: " + " · ".join(r.split(" — ")[0] + "(" + r.split(" — ")[1] + ")" for r in stop_reasons[:2])] if stop_reasons
                else []) + (["허락 근거: " + " · ".join(r.split(" — ")[0] for r in go_reasons[:4])] if go_reasons and can_buy else [])

    # ── 지수 · 진입 신호 카드
    stats = []
    for name, sym in data.MARKETS.items():
        sm = idx_sm.get(name) or {}
        ab = sm.get("above60")
        ic, icol, ibg = ("↑", "#16A34A", "#E7F6EC") if ab else (("↓", "#D97706", "#FEF3E2") if ab is False else ("·", "#94A3B8", "#F1F5F9"))
        ph = phases.get(name)
        note = (scout.PLAYBOOK[ph["phase"]]["title"].split(" — ")[0] if ph else "-") + \
            (f" · {sm['dist60']:+.1f}%" if sm.get("dist60") is not None else "")
        stats.append(f'<div class="db-st"><div class="t"><span style="background:{ibg};color:{icol}">{ic}</span>{esc(_IDX_EN.get(name, name))}</div>'
                     f'<b>60일선 {"위" if ab else ("아래" if ab is False else "-")}</b><small>{esc(note)}</small></div>')
    sv = action if len(action) <= 9 else decision
    sic, scol, sbg = (("✓", "#16A34A", "#E7F6EC") if decision == "매수" else (("↗", "#1A62CC", "#EAF2FE") if can_buy else ("—", "#B45309", "#FFF7E8")))
    stats.append(f'<div class="db-st"><div class="t"><span style="background:{sbg};color:{scol}">{sic}</span>진입 신호</div>'
                 f'<b>{esc(sv)}</b><small>{esc(("막힘: " + stop_reasons[0].split(" — ")[0]) if stop_reasons else "막힌 단계 없음")}</small></div>')

    # ── HTML
    o = [
         f'<div class="db-card db-hero"><div class="db-hl"><div class="db-ic">{_CLOCK}</div><div>'
         f'<div class="db-k">오늘의 매매 보드</div><h2>{esc(headline)}</h2><p><b>왜?</b> {esc(" / ".join(why_bits) or "막힌 단계 없음")}</p><p><b>할 일</b> {esc(concl_act)}</p>'
         f'<span class="db-mode" style="background:{mbg};color:{mcol}">{mode}</span></div></div>'
         f'<div class="db-stats">{"".join(stats)}</div></div>',
         "§now§", _now_card(mn),
         "§big§", _db_bigpicture(df),
         "§main§", '<div class="db-grid">']
    # 왼쪽: TOP 6 — 종목마다 '왜'는 배지 3개, '어떻게'는 손절·3R 한 줄
    if n_buy:
        t_title, t_sub = "지금 살 종목 TOP 6", "원칙 매수 → 선매입(매물대 돌파) → 정찰병 순 · 이름을 누르면 차트"
    else:
        t_title, t_sub = "준비 종목 TOP 6", f"지금은 '{decision}' — 막힌 단계가 풀리면 먼저 볼 종목"
    link = "?tab=" + quote_plus("🏁 신고가 후보")
    o.append(f'<div class="db-card"><div class="db-hd"><div><h3>{t_title}</h3><p>{t_sub}</p></div>'
             f'<a class="db-pill b" href="{link}" target="_self">전체 보기 →</a></div>')
    _short = (("52주 신고가 종가 돌파", "신고가 돌파"), ("신고가 돌파", "신고가 돌파"), ("오늘 인더스트리 액션 섹터", "액션 섹터"),
              ("섹터에서 먼저 신고가", "섹터 첫 신고가"), ("⭐ 첫 돌파", "첫 돌파"), ("👑 대장주", "대장주"))

    def badges(why: str) -> str:
        out = []
        for w in (why or "").split(" · "):
            if w.startswith("매물대 "):
                w = "매물대 돌파"
            if w.startswith("조정 바닥"):
                m_ = re.search(r"상위 (\d+)%", w)
                w = f"견조 상위 {m_.group(1)}%" if m_ else "견조주"
            for a, b in _short:
                if w == a:
                    w = b
            if w and w not in out:
                out.append(w)
        return "".join(f'<span class="db-bd">{esc(x)}</span>' for x in out[:4])

    if rows:
        trs = []
        for i, r in enumerate(rows[:6], 1):
            chg = r["chg"]
            if chg is None or chg != chg:
                continue
            on = r["kind"] in ("main", "early", "scout") or r["tag"] in ("우선 검토", "선매입 후보(대기)")
            ccol = "#E5484D" if chg > 0 else ("#1F66C9" if chg < 0 else "#6B7280")
            tcol = {"main": "#15803D", "early": "#C2410C", "scout": "#1A62CC"}.get(r["kind"], "#1A62CC" if r["tag"] in ("우선 검토", "선매입 후보(대기)") else "#9CA3AF")
            trs.append(f'<tr><td><span class="db-rk{" on" if on else ""}">{i}</span></td>'
                       f'<td><span class="db-ar{"" if on else " off"}">{"↗" if on else "·"}</span></td>'
                       f'<td class="db-nm"><a href="?chart={esc(r["code"])}" target="_self">{esc(str(r["name"]))}</a>'
                       f'<small>{esc(str(r["group"]))}</small><div class="db-bds">{badges(r["why"])}</div>'
                       + (f'<em>{esc(r["plan"])}</em>' if r["plan"] else "") + '</td>'
                       f'<td class="db-px">{r["price"]:,.0f}원</td>'
                       f'<td class="db-ch"><b style="color:{ccol}">{chg:+.1f}%</b><small style="color:{tcol}">{esc(r["tag"])}</small></td></tr>')
        o.append('<table class="db-tbl"><thead><tr><th>순위</th><th></th><th>종목 / 왜</th><th>현재가</th><th>등락률</th></tr></thead>'
                 f'<tbody>{"".join(trs)}</tbody></table>')
        legend = [("#15803D", "원칙 매수", ["오늘 인더스트리 액션 섹터 + 내 원칙 전부 통과",
                                           "지수 60일선 위 · RS 70↑ · 거래량 · 장대양봉 · 저항 돌파 · 실적",
                                           "→ 1유닛, 아침·종가 나눠 사기"]),
                  ("#C2410C", "선매입", ["52주 신고가 전이라도: 오늘 인더스트리 액션 섹터 + 거래량 1.5배↑ 양봉",
                                       "+ 1년 매물대(가격대별 거래량) 중 큰 덩어리를 종가로 돌파 + RS 70↑",
                                       "→ 손절은 뚫은 매물대 아래(최대 -8%), 비중은 결론(매수/정찰병)에 따라"]),
                  ("#1A62CC", "정찰병", ["조정 때 버틴 견조주 중 오늘 거래량 실어 52주 신고가",
                                       f"→ 계좌 약 {scout.SCOUT_WEIGHT:.0f}% · 손절 -8% · 같은 섹터 2개까지",
                                       "살아서 3R 쪽으로 가면 그때 키우기"]),
                  ("#111827", "우선 검토", ["신고가는 갔지만 국면이 아직 허락 전"]),
                  ("#6B7280", "관찰 후보", ["신고가까지 3% 안 — 지켜보기"])]
        o.append('<div class="db-note"><div class="db-nt">읽는 법</div>' + "".join(
            f'<div class="db-dl"><b style="color:{c}">{t}</b><div>' + "".join(f'<span class="ln">{esc(x)}</span>' for x in ls) + '</div></div>'
            for c, t, ls in legend) + '</div>')
    else:
        o.append('<div class="db-empty">조건에 맞는 견조주 후보가 아직 없어요 — 📉 하락장 점검에서 버티는 종목부터 확인.</div>')
    o.append('</div>')
    # 오른쪽: 판단 회로 — 단계마다 사실 → 원칙 → 판정, 맨 아래 결론
    live = data.market_status(quotes).startswith("장중")
    o.append(f'<div class="db-card"><div class="db-hd"><div><h3>판단 회로</h3><p>사실(숫자) → 원칙 → 판정 · 위에서부터 막히면 그 단계에서 멈춤</p></div>'
             f'<span class="db-pill {"r" if live else "g"}">{"LIVE" if live else "장 마감"}</span></div>')
    for i, s_ in enumerate(steps, 1):
        ok = s_["ok"]
        cls = "ok" if ok else ("no" if ok is False else "na")
        facts = "".join(f'<span class="ln">{esc(x)}</span>' for x in s_["facts"])
        o.append(f'<div class="db-step {cls}"><span class="n">{i}</span><div><b>{s_["t"]}</b>'
                 f'<div class="db-fx"><em>사실</em><div>{facts}</div></div>'
                 f'<div class="db-fx rl"><em>원칙</em><div><span class="ln">{esc(s_["rule"])}</span></div></div></div>'
                 f'<span class="r">{"✓" if ok else ("✕" if ok is False else "?")} {esc(s_["v"])}</span></div>')
    o.append(f'<div class="db-bar"><div style="width:{ok_n / len(steps) * 100:.0f}%"></div></div>'
             f'<div class="db-barl">통과 {ok_n}/{len(steps)}</div>'
             f'<div class="db-concl" style="background:{mbg};border-color:{mcol}55"><small style="color:{mcol}">결론</small>'
             f'<b style="color:{mcol}">{micon} {esc(decision)}</b><p>{esc(concl_act)}</p>'
             + (f'<div class="db-fx"><em>멈춤</em><div>' + "".join(f'<span class="ln">{esc(x)}</span>' for x in stop_reasons) + '</div></div>'
                if stop_reasons else "")
             + (f'<div class="db-fx gd"><em>근거</em><div>' + "".join(f'<span class="ln">{esc(x)}</span>' for x in go_reasons) + '</div></div>'
                if go_reasons else "")
             + f'<div class="x">{_db_lines(mdesc)}</div></div></div></div>')

    # 아래 1줄: 근거 데이터 | 보유 → 매도 판단
    idx_lines = []
    for n, m in msigs.items():
        sm = idx_sm.get(n) or {}
        if m:
            idx_lines.append(f"{n}: {m['state_txt']}" + (f" Day {m['day']}" if m.get("state") == "rally" else "")
                             + f" · 고점 대비 {m['draw']:+.1f}%"
                             + (f" · 60일선 {sm['dist60']:+.1f}%" if sm.get("dist60") is not None else "")
                             + f" · 분산일 {m['dd']}개(평소 {m['q50']:.0f})")
    breadth = hfacts[:2] + [f"오늘 52주 신고가 {n_nh}개 (국내 보드 종목)"]
    if d20 is not None:
        breadth.append(f"20 DOWN {d20:.0f}% — 20일선 아래 종목 비율" + (" · 90%↑ 과매도권" if d20 >= 90 else (" · 80%↑ 과매도 접근" if d20 >= 80 else "")))
    eb_lines = [f"{r.name} — 매물대 {r.node_lo:,.0f}~{r.node_hi:,.0f} 돌파 · 거래량 {r.vx:.1f}배 · 위 매물 {r.above:.0f}%"
                for r in eb.head(3).itertuples()]
    sigs = [("01", "#2B7FF0", "지수", "".join(f'<span class="ln">{esc(x)}</span>' for x in idx_lines)),
            ("02", "#7C5CF0", "시장 폭", "".join(f'<span class="ln">{esc(x)}</span>' for x in breadth)),
            ("03", "#16A34A", "섹터", "".join(f'<span class="ln">{esc(x)}</span>' for x in s3["facts"])),
            ("04", "#F59E0B", "선매입", "".join(f'<span class="ln">{esc(x)}</span>' for x in eb_lines)
             or '<span class="ln">오늘 매물대를 거래량 실어 뚫은 액션 섹터 종목 없음</span>')]
    hold_html = ("".join(f'<div class="db-hold">{em} <b>{esc(n)}</b> {esc(v)}<small>{esc(a)}</small></div>' for em, n, v, a in hrows[:6])
                 if hrows else f'<div class="db-empty">{esc(hmsg or "")}</div>')
    o.append("§data§")
    o.append('<div class="db-grid"><div class="db-card"><div class="db-hd"><div><h3>근거 데이터</h3>'
             '<p>판단에 쓴 숫자를 그대로 — 지수 · 시장 폭 · 섹터 · 선매입</p></div><span class="db-pill g">오늘 기준</span></div>'
             + "".join(f'<div class="db-sig"><span class="n" style="background:{c}">{n}</span><b>{t}</b><span class="d">{x}</span></div>'
                       for n, c, t, x in sigs) + '</div>')
    o.append('<div class="db-card"><div class="db-hd"><div><h3>보유 → 매도 판단</h3>'
             f'<p>{"⚠️ 지금 처리할 종목 " + str(urgent) + "개" if urgent else "🔴 손절 · 🔵 3R 절반 · 🟣 본절 올리기 · 🟢 유지"}</p></div>'
             f'<a class="db-pill b" href="?tab={quote_plus("💼 내 보유")}" target="_self">내 보유 →</a></div>{hold_html}'
             '<div class="db-note"><div class="db-nt">매도 기준</div>'
             '<span class="ln">🔴 손절선 이탈 → 예외 없이 정리 (평단 -8%, ATR이 8%보다 크면 ATR · 선매입은 뚫은 매물대 아래)</span>'
             '<span class="ln">🔵 3R(+24%) → 절반 익절, 나머지는 일봉 5일선이 50일선에 닿을 때</span>'
             '<span class="ln">🟣 +2R → 손절선을 본전으로, +4R → +1R로 올리기</span>'
             '<span class="ln">➕ 불타기 때 3R 익절가는 처음 가격 그대로</span>'
             f'<div class="db-nt" style="margin-top:.55rem">지금 장</div>{_db_lines(pb["hold"], by_mid=True)}</div></div></div>')

    # 아래 2줄: 사는 방법 | 원칙 출처
    how = [("1", "#16A34A", "원칙 매수", "52주 신고가 돌파 + 인더스트리 액션 + 원칙 전부 통과",
            "지수 60일선 위일 때만 · 1유닛 · 아침 1차 · 종가 2차"),
           ("2", "#C2410C", "선매입", "신고가 전이라도 큰 매물대를 거래량 1.5배↑로 뚫고, 그 섹터에 인더스트리 액션",
            "손절 = 뚫은 매물대 아래(최대 -8%) · 위 매물 비율이 낮을수록 가벼움 · 다음 매물대까지 여유 확인"),
           ("3", "#1A62CC", "정찰병", "조정 때 버틴 견조주가 거래량 실어 52주 신고가",
            f"계좌 약 {W:.0f}% · 손절 -8% · 같은 섹터 2개까지 · 살아서 3R 쪽이면 키우기"),
           ("✕", "#E5484D", "사지 않기", "물타기 · 손절 넓히기 · 같은 섹터 우르르 · 막힌 단계가 있는데 사기",
            "변동성이 커도 손절을 넓히면 손실만 커져요")]
    srcs = [("FTD", "깡토 백테스트(코스피 1990~2026): 하락장 바닥 뒤엔 항상 FTD가 나왔지만 하락장 FTD 52번 중 25번은 실패 → FTD는 허락이지 공격이 아님, 점진적으로"),
            ("견조주", "깡토 백테스트(2018.10~2026.10 하락장 56일): 견조주(고점 -8%)는 1년 +16.7%, 과대낙폭(-67%)은 1년 중앙값 -19.9% → 버틴 종목을 산다"),
            ("신고가", "한국 연구: 52주 신고가 하나만으로는 예측력이 약하다는 결과도 있음 → 섹터 액션 · 거래량 · 매물대 · 실적과 함께 볼 때 의미"),
            ("매물대", "위에 쌓인 거래량 = 본전 오면 팔려는 물량. 큰 매물대를 거래량 실어 뚫었다 = 그 물량을 소화 → 위 매물이 적을수록 오를 길이 가벼움"),
            ("손익비", "승률 30%면 2.33R가 손익분기 → 3R가 최소 목표, 그래서 손절선(1R)을 먼저 정하고 들어감"),
            ("60일선", "내 원칙: 지수가 60일선 아래면 돌파매매는 쉬는 장 — 스승님 방식의 정찰병만 허용")]
    o.append("§how§")
    o.append('<div class="db-grid"><div class="db-card"><div class="db-hd"><div><h3>사는 방법</h3><p>신고가만 기다리지 않고 매물대 돌파로 먼저 사는 길까지</p></div>'
             '<span class="db-pill g">원칙</span></div>'
             + "".join(f'<div class="db-rule"><span style="color:{c};background:{c}14">{m}</span><div><b>{esc(t)}</b> — {esc(x)}<small>{_db_lines(y)}</small></div></div>'
                       for m, c, t, x, y in how)
             + f'<div class="db-quote">“{esc(pb["mentor"])}”<small>— 스승님 · {esc(pb["title"])}</small></div></div>')
    o.append('<div class="db-card"><div class="db-hd"><div><h3>원칙 출처 — 왜 이 규칙인가</h3><p>판단에 쓰는 규칙의 근거(백테스트 · 연구 · 논리)</p></div></div>'
             + "".join(f'<div class="db-cmr"><b>{esc(k)}</b><div>{_db_lines(v)}</div></div>' for k, v in srcs) + '</div></div>')
    o.append("§foot§")
    o.append('<div class="db-foot"><span>밸류체인 보드 · 확인 후 진입, 위험 관리를 최우선으로 · 매매 추천이 아니라 원칙 확인용</span><b>TREND BOARD</b></div>')
    out = "".join(o)
    for ch in "→↑↓←↗":            # SUIT는 화살표를 꺾쇠(> ^)처럼 그려서, 화살표만 원래 글꼴로
        out = out.replace(ch, f'<span class="db-a">{ch}</span>')
    parts = re.split(r"§(\w+)§", out)          # [판정, 이름, 내용, 이름, 내용, ...]
    st.markdown(DB_CSS + f'<div class="db">{parts[0]}</div>', unsafe_allow_html=True)

    # 접고 여는 칸 — key를 고정해서 1분마다 새로 그려도 열어 둔 칸은 그대로 열려 있어요
    try:
        _bs = bigpicture.bucket_summary(tab_memo("bigpicture", ("국내", int(df["code"].map(data.is_kr).sum())),
                                                 lambda: bigpicture.summary(df[df["code"].map(data.is_kr)], recent_days)))
        big_lab = " · ".join(f"{b} {x['chg']:+.1f}%" for b, x in _bs.items() if x and x.get("chg") is not None)
    except Exception:  # noqa: BLE001
        big_lab = ""
    now_lab = " · ".join(f"{n} {x['state']} {x['exp']}" for n, x in mn["idx"].items())
    if _hl is not None and _hl["adr"] == _hl["adr"]:
        now_lab += f" · ADR {_hl['adr']:.0f}"
    labels = {
        "now": (f"📍 지금 여긴 — {now_lab}", False),
        "big": ("🗺️ 큰 그림 — " + (big_lab or "AI · 비AI · 그외"), False),
        "main": (f"🛒 살 종목 · 판단 회로 — 결론: {decision}" + (f" · {action}" if action != decision else ""), True),
        "data": ("📊 근거 데이터 · 보유 → 매도 판단" + (f" — ⚠️ 처리할 보유 {urgent}개" if urgent else ""), bool(urgent)),
        "how": ("📘 사는 방법 · 원칙 출처", False),
    }
    for name, body in zip(parts[1::2], parts[2::2]):
        html_ = f'<div class="db">{body}</div>'
        if name == "foot":
            st.markdown(html_, unsafe_allow_html=True)
            continue
        lab, opened = labels.get(name, (name, False))
        with st.expander(lab, expanded=opened, key=f"db_sec_{name}"):
            st.markdown(html_, unsafe_allow_html=True)

    with st.expander("📖 스승님 국면별 플레이북 — 이런 장에선 이렇게"):
        tb = [{"단계": f"{v['em']} {v['title']}", "비중": v["size"], "매수": v["buy"], "보유·매도": v["hold"], "스승님": v["mentor"],
               "지금": "👉" if k == phase else ""} for k, v in ((k, scout.PLAYBOOK[k]) for k in scout.PHASE_ORDER)]
        st.dataframe(pd.DataFrame(tb), hide_index=True, width="stretch",
                     column_config={"매수": st.column_config.TextColumn(width="large"),
                                    "보유·매도": st.column_config.TextColumn(width="large"),
                                    "스승님": st.column_config.TextColumn(width="medium")})
        for n, v in phases.items():
            if v:
                st.caption(f"{n}: {v['em']} {v['title']} — {v['why']}")
        notes = scout.commentary(phase, ph_txt, market_ok, sig.get("d20"), cands, ia_secs, n_res, n_lead)
        st.markdown("**🧭 해설**\n" + "\n".join("- " + re.sub(r"</?b>", "**", x) for x in notes))
        st.caption(f"출처: {scout.MENTOR_SRC} · 정찰병 = 1유닛(계좌 6% 안팎)보다 작게, 약 {scout.SCOUT_WEIGHT:.0f}%. "
                   "견조주 = 지수 조정 바닥날 52주 고점에 가장 가까웠던 상위 20%. 효석 원칙(리스크관리 우선, 60일선 아래 본매매 휴식)이 항상 먼저예요.")


def _ia_sig(df: pd.DataFrame, histories: dict, quotes: dict) -> dict:
    """🔔 매수하세요 판정(인더스트리 액션 + 원칙 전부 통과) — 메인 매매 보드·알림이 같이 써요."""
    d = data.IA_DEFAULTS
    ss = st.session_state
    p = {"vol_mult": ss.get("ia_vol", d["vol_mult"]), "min_chg": ss.get("ia_chg", d["min_chg"]),
         "min_n": ss.get("ia_minn", d["min_n"]), "near_pct": ss.get("ia_near", d["near_pct"]),
         "dcr_min": ss.get("ia_dcr", d["dcr_min"]), "min_cap": ss.get("ia_cap", d["min_cap"]),
         "max_ext": ss.get("ia_ext", d["max_ext"])}
    return tab_memo("ia_buy", (tuple(sorted(p.items())), recent_days, min_count),
                    lambda: data.ia_buy_signals(df, histories, quotes, load_indexes(), p, recent_days, min_count))


def render_buy_alert(df: pd.DataFrame, histories: dict, quotes: dict):
    """🔔 매수하세요: 오늘 인더스트리 액션 섹터에서 매수 조건을 모두 통과 + 시장 60일선 위인 종목. 어느 탭에서든 보여요."""
    try:
        sig = _ia_sig(df, histories, quotes)
    except Exception as exc:  # noqa: BLE001 — 알림 계산이 실패해도 보드는 떠야 해요
        st.caption(f"매수 알림 계산 실패: {exc.__class__.__name__}")
        return
    if not sig["signals"]:
        return
    now = data.now_kst()
    close = now.weekday() < 5 and (now.hour, now.minute) >= (15, 0)
    title = "종가 매수하세요 — 2차" if close else "매수하세요 — 1차(장중)"
    items = []
    for x in sig["signals"][:8]:
        tag = " · ".join(t for t in (x["lead"], "⭐ 첫 돌파" if x["first"] else None,
                                     "돌파" if x["brk"] else f"저항 {x['to_res']:.1f}%") if t)
        items.append(f'<div class="ia-al-i"><a href="?chart={html.escape(x["code"])}" target="_self"><b>{html.escape(x["name"])}</b></a> '
                     f'{x["price"]:,.0f}원 <span style="color:{UP}">{x["chg"]:+.1f}%</span> · 거래량 {x["vx"]:.1f}배 · {html.escape(tag)}'
                     f'<small>{html.escape(x["group"])} · 손절 {x["stop"]:,.0f} · 3R {x["target"]:,.0f}</small></div>')
    more = len(sig["signals"]) - 8
    warn = ('<div class="ia-al-w">⚠️ 20일선 아래 종목이 절반 넘어요 — 정찰병 비중으로만</div>'
            if (sig["d20"] or 0) >= 60 else "")
    st.markdown(
        '<style>.ia-al{background:#FDECEC;border:1.5px solid #E8858B;border-radius:12px;padding:.75rem 1rem;margin:.2rem 0 .8rem}'
        '.ia-al h4{margin:0 0 .35rem;color:#C0262E;font-size:1.1rem;font-weight:800}'
        '.ia-al-i{font-size:.9rem;padding:.25rem 0;border-top:1px solid #F6CACD}.ia-al-i:first-of-type{border-top:none}'
        '.ia-al-i a{color:#16212B;text-decoration:none}.ia-al-i small{display:block;color:#7A5A5C;font-size:.74rem}'
        '.ia-al-w{font-size:.8rem;color:#B45309;margin-top:.3rem}.ia-al-f{font-size:.74rem;color:#7A5A5C;margin-top:.35rem}</style>'
        f'<div class="ia-al"><h4>🔔 {title} · {len(sig["signals"])}종목</h4>{"".join(items)}'
        + (f'<div class="ia-al-f">…외 {more}종목은 🏭 인더스트리 액션 탭에서</div>' if more > 0 else "")
        + warn + '<div class="ia-al-f">인더스트리 액션 섹터 · 거래량 평소 1.5배↑ 장대양봉 · 저항 근처/돌파 · RS 70↑ · '
        '시장 60일선 위 모두 통과 · 아침·종가 나눠 사기 · 3R에서 1/3 익절 → 본절</div></div>', unsafe_allow_html=True)
    seen = ss.setdefault("ia_toasted", set())
    for x in sig["signals"]:
        if x["code"] not in seen:
            seen.add(x["code"])
            st.toast(f"🔔 매수하세요: {x['name']} {x['chg']:+.1f}% · 거래량 {x['vx']:.1f}배", icon="🏭")


T126_CSS = """
<style>
.t126-rule { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.6rem; margin: 0.2rem 0 0.6rem; }
.t126-step { background: #FFFFFF; border: 1px solid #E3E8EB; border-radius: 12px; padding: 0.7rem 0.85rem; }
.t126-step .w { font-size: 1.6rem; font-weight: 900; color: #C0262E; line-height: 1; }
.t126-step .w small { font-size: 0.8rem; color: #7A8A94; font-weight: 700; margin-left: 0.3rem; }
.t126-step .t { font-weight: 800; font-size: 0.92rem; margin: 0.3rem 0 0.15rem; color: #16212B; }
.t126-step .d { font-size: 0.76rem; color: #51616C; line-height: 1.4; }
.t126-step .x { font-size: 0.72rem; color: #1F66C9; margin-top: 0.35rem; }
.t126-note { background: #FFF8E6; border: 1px solid #F3DFA8; border-radius: 10px; padding: 0.55rem 0.8rem; font-size: 0.82rem; color: #5C4A12; margin-bottom: 0.8rem; }
.t126-dots { display: inline-flex; gap: 3px; vertical-align: middle; }
.t126-dots i { display: inline-block; height: 9px; border-radius: 3px; background: #E3E8EB; }
.t126-dots i.on { background: #D6333B; } .t126-dots i.half { background: #F3B9BC; }
.t126-ev { display: inline-block; font-size: 0.72rem; font-weight: 800; padding: 0.12rem 0.5rem; border-radius: 6px; white-space: nowrap; }
.t126-ev.buy { background: #FCE4E5; color: #C0262E; } .t126-ev.sell { background: #E4EDFA; color: #1F66C9; }
.t126-ev.warn { background: #FFF1C9; color: #9A6A00; } .t126-ev.wait { background: #EEF1F3; color: #51616C; }
.t126-next { font-size: 0.74rem; color: #3E4C55; white-space: normal; max-width: 280px; line-height: 1.35; }
@media (max-width: 640px) { .t126-rule { grid-template-columns: 1fr; } }
</style>
"""

_T126_MEMO: dict = _memo_store().setdefault("t126", {})      # (속도) 새로고침해도 남는 기억


def _t126_dots(stage: int, exits) -> str:
    """1·2·6 진행 막대(폭도 1:2:6). 정리한 칸은 연하게."""
    on = {0: 0, 1: 1, 2: 2, 3: 2, 4: 3}.get(int(stage), 0)
    sold = len(exits or [])
    parts = []
    for k, w in enumerate((1, 2, 6)):
        cls = "on" if k < on else ""
        if stage == 4 and k < sold:
            cls = "half"
        parts.append(f'<i class="{cls}" style="width:{w * 7}px"></i>')
    return f'<span class="t126-dots">{"".join(parts)}</span>'


def _t126_next(r, p: dict) -> str:
    st_ = int(r["stage"])
    if st_ == 0:
        if r["os_now"]:
            return f"RSI {r['rsi']:.0f} — 과매도 중. <b>30 위로 다시 올라오는 날 1 매수</b> (30 아래에서 미리 사지 않기)"
        return "증거 없음 — 지켜보기"
    if st_ == 1:
        t = "MACD가 시그널 위로 올라오면(골든크로스) <b>+2</b>"
        return t + (" · <b>임박</b>(히스토그램 3일 연속 개선)" if r["near_gc"] else "")
    if st_ == 2:
        nc = r["near_cloud"]
        return ("구름대(천장) 돌파 기다리기" + ("" if nc is None else f" · 윗선까지 {nc:+.1f}%")
                + " — 돌파해도 바로 6 넣지 않기")
    if st_ == 3:
        return "구름대까지 다시 내려와도 무너지지 않고 <b>버티다 위로 돌면 +6</b> (구름 아래로 마감하면 가짜 돌파)"
    done = set(r["exits"] or [])
    todo = [t for k, t in (("s1", "MACD 데드크로스 → 1 정리"), ("s2", f"RSI {p['rsi_weak']:.0f} 아래 → 2 정리")) if k not in done]
    return "보유 9 — " + " · ".join(todo + ["구름대 아래 마감 → 남은 6 정리"])


def _t126_ev_html(r) -> str:
    k = r["ev_kind"]
    if not isinstance(k, str) or pd.isna(r["ev_ago"]):
        return '<span class="t126-ev wait">-</span>'
    cls = {"buy1": "buy", "buy2": "buy", "buy6": "buy", "sell1": "sell", "sell2": "sell", "sell6": "sell",
           "fail": "sell", "fake": "warn", "break": "warn"}.get(k, "wait")
    short = {"buy1": "1 매수", "buy2": "+2 추가", "buy6": "+6 추가", "sell1": "1 정리", "sell2": "2 정리",
             "sell6": "6 정리", "fail": "손절", "fake": "가짜 돌파", "break": "구름 돌파"}.get(k, k)
    ago = r["ev_ago"]
    when = "오늘" if ago == 0 else f"{int(ago)}일 전"
    return f'<span class="t126-ev {cls}">{short}</span><small>{when} · {r["ev_date"]:%m/%d}</small>'


def _t126_table(res: pd.DataFrame, p: dict, equity: float):
    w_of = {"buy1": 1, "buy2": 2, "buy6": 6}
    rows = []
    for _, r in res.iterrows():
        stg = int(r["stage"])
        # 이번에 넣을 비중: 오늘 매수 신호면 그 비중, 아니면 다음 단계 비중
        w = w_of.get(r["ev_kind"]) if r["ev_ago"] == 0 and isinstance(r["ev_kind"], str) and r["ev_kind"] in w_of else {0: 1, 1: 2, 2: 6, 3: 6}.get(stg)
        stop = r["stop"] if r["stop"] == r["stop"] else None
        if stg == 0 and r["os_now"]:
            stop = None
        sz = t126_size_safe(equity, r["price"], stop, p["risk_pct"], w or 0)
        amt = (f'<b class="px">{sz["amount"] / 1e4:,.0f}만</b><small>{sz["shares"]:,}주 · {w}/9</small>'
               if sz.get("amount") and w else (f'<b>{w}/9</b><small>계좌 금액 넣으면 계산</small>' if w else "-"))
        rsi_t = "-" if r["rsi"] is None or r["rsi"] != r["rsi"] else format(r["rsi"], ".0f")
        nc = r["near_cloud"]
        nc_t = "-" if nc is None or nc != nc else (format(nc, "+.1f") + "%" if nc > 0 else "위")
        stop_t = "-" if stop is None else format(stop, ",.0f")
        sp_t = "" if not sz.get("stop_pct") else "-" + format(sz["stop_pct"], ".1f") + "%"
        code_e, name_e, grp_e = html.escape(str(r["code"])), html.escape(str(r["name"])), html.escape(str(r["group"]))
        rows.append(
            f'<tr><td class="l nm"><a href="?chart={code_e}" target="_self">{name_e}</a>'
            f'<small>{code_e} · {grp_e}</small></td>'
            f'<td class="c">{_t126_dots(stg, r["exits"])}<small>{T126_STAGE_TXT.get(stg, "")}</small></td>'
            f'<td>{_t126_ev_html(r)}</td>'
            f'<td><b class="px">{r["price"]:,.0f}</b><small>{_chg_html(r["change"])}</small></td>'
            f'<td><b>{rsi_t}</b><small>RSI</small></td>'
            f'<td class="hide-m"><b>{nc_t}</b><small>구름 윗선</small></td>'
            f'<td><b class="px">{stop_t}</b><small>{sp_t}</small></td>'
            f'<td>{amt}</td>'
            f'<td class="l hide-m"><div class="t126-next">{_t126_next(r, p)}</div></td></tr>')
    head = ('<tr><th class="l">종목</th><th class="c">1·2·6</th><th>최근 신호</th><th>현재가</th><th>RSI</th>'
            '<th class="hide-m">구름까지</th><th>손절 기준</th><th>이번 금액</th><th class="l hide-m">다음 할 일</th></tr>')
    st.markdown(f'<div class="nh-tbl-wrap"><table class="nh-tbl">{head}{"".join(rows)}</table></div>', unsafe_allow_html=True)


T126_STAGE_TXT = {0: "과매도 대기", 1: "1 보유", 2: "3 보유", 3: "3 보유 · 눌림 대기", 4: "9 보유"}


def t126_size_safe(equity, price, stop, risk_pct, weight) -> dict:
    try:
        return data.t126_size(equity or None, float(price) if price == price else None, stop, risk_pct, weight)
    except (TypeError, ValueError):
        return {}


def _t126_chart(ind: pd.DataFrame, events: list, name: str):
    """위: 캔들 + 일목 구름대 + 1·2·6 표시 / 가운데: RSI(30) / 아래: MACD·시그널."""
    h = ind.tail(160).copy()
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d", labelAngle=0, grid=False))
    base = alt.Chart(h).encode(x=x)
    color = alt.condition("datum.close >= datum.open", alt.value(UP), alt.value(DOWN))
    cloud = base.mark_area(opacity=0.22, color="#8E9AA6").encode(y=alt.Y("cloud_bot:Q", title=None, scale=alt.Scale(zero=False)),
                                                                  y2="cloud_top:Q")
    wick = base.mark_rule().encode(y="low:Q", y2="high:Q", color=color)
    body = base.mark_bar(width=2.6).encode(y="open:Q", y2="close:Q", color=color,
                                           tooltip=[alt.Tooltip("date:T", format="%Y-%m-%d"), alt.Tooltip("close:Q", format=",.0f"),
                                                    alt.Tooltip("rsi:Q", format=".0f")])
    lines = alt.Chart(h.melt("date", value_vars=["tenkan", "kijun"], var_name="선", value_name="v").dropna()).mark_line(
        strokeWidth=1).encode(x="date:T", y="v:Q", color=alt.Color("선:N", scale=alt.Scale(domain=["tenkan", "kijun"],
                              range=["#F29A18", "#8E5CC4"]), legend=alt.Legend(orient="top", title=None,
                              labelExpr="datum.label == 'tenkan' ? '전환선' : '기준선'")))
    ev = pd.DataFrame([{"date": pd.Timestamp(e["date"]), "price": e["price"], "lab": {"buy1": "1", "buy2": "+2", "buy6": "+6",
                        "sell1": "-1", "sell2": "-2", "sell6": "-6", "fail": "손절", "fake": "가짜", "break": "돌파"}.get(e["kind"], ""),
                        "buy": e["kind"].startswith("buy"), "label": e["label"]}
                       for e in events if pd.Timestamp(e["date"]) >= h["date"].iloc[0]])
    layers = [cloud, wick, body, lines]
    if not ev.empty:
        pts = alt.Chart(ev).mark_text(fontSize=12, fontWeight="bold", dy=-14).encode(
            x="date:T", y="price:Q", text="lab:N",
            color=alt.condition("datum.buy", alt.value("#C0262E"), alt.value("#1F66C9")), tooltip=["label:N"])
        tri = alt.Chart(ev).mark_point(shape="triangle-up", filled=True, size=60).encode(
            x="date:T", y="price:Q", color=alt.condition("datum.buy", alt.value("#C0262E"), alt.value("#1F66C9")))
        layers += [tri, pts]
    top = alt.layer(*layers).properties(height=280, width="container", title=name)
    rsi = alt.layer(
        base.mark_line(color="#3B5BDB", strokeWidth=1.4).encode(y=alt.Y("rsi:Q", title="RSI", scale=alt.Scale(domain=[0, 100]))),
        alt.Chart(pd.DataFrame({"y": [30, 50, 70]})).mark_rule(strokeDash=[4, 3], color="#B0BCC4").encode(y="y:Q"),
    ).properties(height=90, width="container")
    h["hist"] = h["macd"] - h["signal"]
    macd = alt.layer(
        base.mark_bar(width=2.4).encode(y=alt.Y("hist:Q", title="MACD"),
                                        color=alt.condition("datum.hist >= 0", alt.value("#F3B9BC"), alt.value("#C9DAF2"))),
        base.mark_line(color="#16212B", strokeWidth=1.2).encode(y="macd:Q"),
        base.mark_line(color="#F29A18", strokeWidth=1.2).encode(y="signal:Q"),
    ).properties(height=100, width="container")
    st.altair_chart(top)
    st.altair_chart(rsi)
    st.altair_chart(macd)


T126_SIM_STARTS = {"올해 첫 거래일(1/2)": (1, 2), "4월 1일": (4, 1), "7월 1일": (7, 1), "직접 고르기": None}
_T126_SIM_MEMO: dict = _memo_store().setdefault("t126_sim", {})      # (속도) 새로고침해도 남는 기억


def _t126_sim_opts() -> dict:
    """모의 계좌 설정(화면 아래 설정 칸과 같은 값). 타일도 같은 값으로 계산해요."""
    ss, y = st.session_state, data.now_kst().year
    pick = ss.get("t126s_start", "올해 첫 거래일(1/2)")
    md = T126_SIM_STARTS.get(pick)
    start = date(y, *md) if md else ss.get("t126s_date", date(y, 1, 2))
    return {"start": start, "seed": int(ss.get("t126s_seed", 10_000_000)), "risk": float(ss.get("t126s_risk", 1.5)),
            "max_pos": int(ss.get("t126s_max", 8)), "narr_only": bool(ss.get("t126s_narr", True))}


def _t126_sim_run(df, histories, quotes, p, fins_all):
    """원칙(1:2:6 순서·위험 한도) + 펀더멘털(영업이익 증가) + 내러티브(공부 자료·리포트 종목)로 스스로 매매한 결과."""
    if not fins_all:
        return None, "실적(영업이익) 자료를 받는 중이에요. 펀더멘털을 확인한 종목만 사기 때문에 잠시 뒤 계좌가 계산돼요."
    o = _t126_sim_opts()
    story = {x["code"]: x for x in STOCKS}
    meta = {}
    for code in df.loc[df["code"].map(data.is_kr), "code"].unique():
        if o["narr_only"] and code not in story:
            continue
        g = data.op_growth(fins_all.get(code))
        if g.get("op_up") is not True:
            continue
        x = story.get(code) or {}
        narr = " · ".join(t for t in [x.get("group"), x.get("desc")] if t) or "공부 자료 밖 종목"
        meta[code] = {"op_txt": g["op_txt"], "op_g": g.get("op_g"), "narr": narr}
    if not meta:
        return None, "조건(영업이익 증가 + 공부 자료 종목)을 통과한 종목이 아직 없어요."
    now = data.now_kst()
    key = (now.date(), now.hour * 12 + now.minute // 5, len(histories), len(meta), tuple(sorted(o.items())),
           p["retest_tol"], p["rsi_weak"])
    res = _T126_SIM_MEMO.get(key)
    if res is None:
        kospi = (load_indexes().get("KOSPI") or (None,))[0]
        with st.spinner("1천만 원 모의 계좌를 시작일부터 하루씩 매매해 보는 중…"):
            res = data.t126_sim(df, histories, quotes, o["start"], meta, kospi, p,
                                {"seed": o["seed"], "risk_pct": o["risk"], "max_pos": o["max_pos"]})
        _T126_SIM_MEMO.clear()
        _T126_SIM_MEMO[key] = res
    return (res, None) if res.get("ok") else (None, "시작일 이후 일봉이 부족해요.")


def _t126_sim_view(sim: dict | None, msg: str | None):
    o = _t126_sim_opts()
    with st.expander("⚙️ 모의 계좌 설정", expanded=False):
        c1, c2, c3, c4, c5 = st.columns(5)
        pick = c1.selectbox("시작일", list(T126_SIM_STARTS), key="t126s_start")
        if T126_SIM_STARTS[pick] is None:
            c1.date_input("직접 고른 시작일", value=o["start"], min_value=date(data.now_kst().year - 1, 1, 2),
                          max_value=data.now_kst().date(), key="t126s_date")
        c2.number_input("시드(원)", 1_000_000, 1_000_000_000, 10_000_000, 1_000_000, key="t126s_seed")
        c3.number_input("1회 위험(계좌 %)", 0.5, 3.0, 1.5, 0.5, key="t126s_risk", help="내 원칙 1.5% (영상 예시는 2%)")
        c4.number_input("동시 보유 최대", 3, 15, 8, 1, key="t126s_max", help="내 원칙 최대 8종목")
        c5.toggle("공부 자료·리포트 종목만", value=True, key="t126s_narr",
                  help="끄면 영업이익이 늘어난 종목이면 시장 전체에서 골라요(내러티브 조건 해제).")
    if not sim:
        st.info(msg or "계산할 수 있는 종목이 없어요.")
        return
    seed = sim["seed"]
    col = UP if sim["pnl"] > 0 else DOWN if sim["pnl"] < 0 else "#26343E"
    kr = sim.get("kospi_ret")
    k = st.columns(4)
    k[0].markdown(f'<div class="pf-k">평가금액</div><div class="pf-v">{sim["equity"]:,.0f}원</div>'
                  f'<div class="pf-s">시드 <b>{seed:,.0f}원</b> · {sim["start"]:%Y.%m.%d}부터</div>', unsafe_allow_html=True)
    k[1].markdown(f'<div class="pf-k">수익금 · 수익률</div><div class="pf-v" style="color:{col}">{sim["pnl"]:+,.0f}원</div>'
                  f'<div class="pf-s">수익률 <b style="color:{col}">{sim["ret"]:+.2f}%</b>'
                  + (f' · 같은 기간 코스피 <b>{kr:+.2f}%</b>' if kr is not None else "") + "</div>", unsafe_allow_html=True)
    stock_v = sim["equity"] - sim["cash"]
    k[2].markdown(f'<div class="pf-k">계좌 구성</div><div class="pf-v">{stock_v / sim["equity"] * 100:.0f}% 주식</div>'
                  f'<div class="pf-s">주식 <b>{stock_v:,.0f}원</b> · 현금 <b>{sim["cash"]:,.0f}원</b></div>', unsafe_allow_html=True)
    wr = f'{sim["wins"] / sim["closed"] * 100:.0f}%' if sim["closed"] else "-"
    k[3].markdown(f'<div class="pf-k">실현손익 · 승률</div><div class="pf-v">{sim["realized"]:+,.0f}원</div>'
                  f'<div class="pf-s">끝난 매매 <b>{sim["closed"]}건</b> · 승률 <b>{wr}</b> · 최대 낙폭 <b>{sim["mdd"]:.1f}%</b></div>',
                  unsafe_allow_html=True)

    a, b = st.columns([3, 2])
    with a:
        st.markdown("**계좌 흐름**")
        cv = sim["curve"]
        long = [cv.assign(구분="모의 계좌", 값=cv["equity"])]
        if "kospi" in cv:
            long.append(cv.assign(구분="코스피(같은 돈)", 값=cv["kospi"]))
        long = pd.concat(long).rename(columns={"date": "날짜"})
        ch = (alt.Chart(long).mark_line(interpolate="monotone").encode(
            x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m.%d", labelAngle=0, grid=False)),
            y=alt.Y("값:Q", title="원", scale=alt.Scale(zero=False), axis=alt.Axis(format="~s")),
            color=alt.Color("구분:N", scale=alt.Scale(domain=["모의 계좌", "코스피(같은 돈)"], range=["#5B47F5", "#B9B4D8"]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "구분:N", alt.Tooltip("값:Q", format=",.0f")])
            .properties(height=280, width="container"))
        st.altair_chart(ch)
    with b:
        st.markdown("**지금 계좌 구성**")
        parts = [(x["name"], x["value"]) for x in sim["hold"]] + ([("현금", sim["cash"])] if sim["cash"] > 0 else [])
        pdf = pd.DataFrame(parts, columns=["종목", "금액"])
        pdf["비중"] = pdf["금액"] / pdf["금액"].sum() * 100
        pie = (alt.Chart(pdf).mark_arc(innerRadius=55, outerRadius=100).encode(
            theta="금액:Q", color=alt.Color("종목:N", sort=list(pdf["종목"]), legend=alt.Legend(orient="bottom", title=None, columns=2)),
            tooltip=["종목:N", alt.Tooltip("금액:Q", format=",.0f"), alt.Tooltip("비중:Q", format=".1f")])
            .properties(height=300, width="container"))
        st.altair_chart(pie)

    t1, t2 = st.tabs([f"보유 종목 {len(sim['hold'])}", f"매매 일지 {len(sim['trades'])}"])
    with t1:
        if sim["hold"]:
            stage = {1: "1 (탐색)", 3: "3 (MACD 확인)", 9: "9 (구름대 확인)"}
            tb = pd.DataFrame([{
                "종목": f"{x['name']} · {x['code']}", "단계": stage.get(x["units"], str(x["units"])), "수량": x["qty"],
                "평단": x["avg"], "현재가": x["price"], "평가금액": x["value"], "비중": x["value"] / sim["equity"] * 100,
                "수익률": x["ret"], "평가손익": x["pnl"], "편입": f"{x['opened']:%m/%d}",
                "펀더멘털": x.get("op_txt", ""), "내러티브": x.get("narr", "")} for x in sim["hold"]])
            sty = tb.style.format({"수량": "{:,.0f}", "평단": "{:,.0f}", "현재가": "{:,.0f}", "평가금액": "{:,.0f}",
                                   "비중": "{:.1f}%", "수익률": "{:+.2f}%", "평가손익": "{:+,.0f}"}) \
                .map(lambda v: f"color:{UP if v > 0 else DOWN if v < 0 else '#26343E'}", subset=["수익률", "평가손익"])
            st.dataframe(sty, hide_index=True, width="stretch")
        else:
            st.caption("지금은 들고 있는 종목 없이 전부 현금이에요 — 1:2:6 신호와 조건이 같이 맞는 종목을 기다리는 중.")
    with t2:
        if sim["trades"]:
            tr = pd.DataFrame([{"날짜": f"{x['date']:%Y-%m-%d}", "종목": x["name"], "구분": x["side"], "이유": x["why"],
                                "가격": x["price"], "수량": x["qty"], "금액": x["amount"], "실현손익": x["pnl"]}
                               for x in sim["trades"]])
            sty = tr.style.format({"가격": "{:,.0f}", "수량": "{:,.0f}", "금액": "{:,.0f}",
                                   "실현손익": lambda v: "" if v is None or pd.isna(v) else f"{v:+,.0f}"}) \
                .map(lambda v: "" if v is None or pd.isna(v) else f"color:{UP if v > 0 else DOWN if v < 0 else '#26343E'}",
                     subset=["실현손익"]) \
                .map(lambda v: f"color:{UP if v == '매수' else DOWN}", subset=["구분"])
            st.dataframe(sty, hide_index=True, width="stretch", height=380)
        else:
            st.caption("아직 매매가 없어요.")
    st.caption(
        f"🤖 스스로 매매하는 규칙 — **원칙**: 1 → +2 → +6 순서로만 늘리고 가격이 내렸다고 더 사지 않음 · 1회 위험 계좌의 {o['risk']:.1f}% "
        f"(손절 = 과매도 저점) · 한 종목 최대 19% · 동시 보유 최대 {o['max_pos']}종목 · 정리도 1:2:6, 과매도 저점 깨지면 전부 손절. "
        "**펀더멘털**: 올해 영업이익이 작년보다 늘어난 종목만. **내러티브**: 공부 자료·리포트에 투자 이야기가 있는 종목만, 같은 날 신호가 겹치면 "
        f"영업이익 증가율·시총 큰 순. 시총 3,000억 이상 · 신호 날 종가 체결 · 수수료 0.015%·거래세 0.20% 반영 · 조건 통과 {sim['universe']}종목에서 골라요.\n\n"
        "⚠️ 영업이익·공부 자료는 '지금' 기준이라 과거 그날엔 몰랐던 정보가 섞여 있어요(실제보다 좋게 나올 수 있음). 오늘 신호는 장중이면 종가에 바뀔 수 있어요. "
        "모의 결과이고 매수 권유가 아니에요.")


def render_126(df: pd.DataFrame, histories: dict, quotes: dict, fins_all=None):
    st.markdown(NHC_CSS + T126_CSS, unsafe_allow_html=True)
    st.markdown('<div class="nh-head"><b>⚖️ 1:2:6 매매</b><span>후지모토 시게루 방식 — 가격이 아니라 증거가 쌓일 때 비중을 늘린다</span></div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="t126-rule">'
        '<div class="t126-step"><div class="w">1<small>탐색전</small></div><div class="t">RSI 30 아래 → 다시 30 위</div>'
        '<div class="d">30 아래로 내려간 순간이 아니라, 내려갔다가 <b>다시 올라오는 순간</b>. 매도세가 처음 약해진 자리.</div>'
        '<div class="x">정리 1 · MACD 데드크로스(경고등 하나)</div></div>'
        '<div class="t126-step"><div class="w">+2<small>누적 3</small></div><div class="t">MACD 골든크로스</div>'
        '<div class="d">MACD선이 시그널선을 아래→위로. 처음보다 가격이 올라 있어도 <b>맞을 확률이 높아졌으니</b> 더 산다.</div>'
        '<div class="x">정리 2 · RSI까지 약해짐</div></div>'
        '<div class="t126-step"><div class="w">+6<small>누적 9</small></div><div class="t">구름대 돌파 → 눌림에서 버팀</div>'
        '<div class="d">돌파만으로는 안 넣음(가짜 돌파 많음). 다시 구름대까지 와도 <b>천장이 바닥이 되어 받치고</b> 위로 돌 때.</div>'
        '<div class="x">정리 6 · 구름대 아래로 무너짐(구조 깨짐)</div></div></div>'
        '<div class="t126-note">⚠️ 가격이 떨어져서 더 사면 1:2:6이 아니라 <b>물타기</b>예요. 새 증거가 생겼을 때만 더 넣기 · '
        '"얼마 벌까"보다 <b>"틀리면 얼마 잃나"</b>를 먼저 — 손실 한도 안에서 금액을 정해요.</div>', unsafe_allow_html=True)

    d = data.T126_DEFAULTS
    with st.expander("⚙️ 계좌 · 기준"):
        c1, c2, c3, c4 = st.columns(4)
        equity = c1.number_input("계좌 평가금액(원)", 0, 10_000_000_000, 0, step=1_000_000, key="t126_eq",
                                 help="저장되지 않아요. 넣으면 단계별 금액·수량을 계산해요.")
        risk = c2.number_input("한 번 거래 최대 손실(계좌 %)", 0.5, 5.0, float(d["risk_pct"]), 0.5, key="t126_risk",
                               help="영상 예시는 2% (1천만 원이면 20만 원). 내 원칙은 1.5%.")
        mincap = c3.number_input("최소 시가총액(억원)", 0, 100000, 3000, 500, key="t126_cap")
        tol = c4.number_input("구름대 눌림 인정(윗선 + %)", 0.0, 10.0, float(d["retest_tol"]), 0.5, key="t126_tol")
        c5, c6, c7 = st.columns(3)
        weak = c5.number_input("RSI '약해짐' 기준(정리 2)", 30.0, 70.0, float(d["rsi_weak"]), 5.0, key="t126_weak",
                               help="영상은 'RSI까지 약해지면'이라고만 해서 50 아래로 내려오는 것을 기본으로 잡았어요.")
        only_op = c6.toggle("올해 영업이익 늘어난 종목만", value=False, key="t126_op",
                            help="바닥에서 잡는 매매라 펀더멘털 확인용. 실적 자료를 다 받은 뒤부터 걸러져요.")
        st.caption("RSI 14일 · MACD 12/26/9 · 일목 9/26/52(구름대 26일 앞으로) — 영상과 같은 기본값. 손절 기준: 1·2단계는 과매도 구간 최저가, "
                   "6 이후는 구름대 아랫선. 금액 = 계좌 × 손실% ÷ 손절까지 거리 → 그 금액을 1:2:6으로 나눠요.")
    p = {**d, "risk_pct": float(risk), "retest_tol": float(tol), "rsi_weak": float(weak)}

    now = data.now_kst()
    key = (now.date(), now.hour * 12 + now.minute // 5, len(histories), p["retest_tol"], p["rsi_weak"])
    res = _T126_MEMO.get(key)
    if res is None:
        with st.spinner("RSI · MACD · 일목 계산 중…"):
            res = data.t126_screen(df, histories, quotes, p)
        _T126_MEMO.clear()
        _T126_MEMO[key] = res
    if res.empty:
        st.info("일봉이 아직 부족해요. 잠시 뒤 다시 볼게요.")
        return
    res = res[pd.to_numeric(res["cap_krw"], errors="coerce").fillna(0) >= mincap * 1e8]
    if only_op and fins_all:
        ok = {c for c in res["code"] if data.op_growth(fins_all.get(c)).get("op_up") is True}
        res = res[res["code"].isin(ok)]

    today_ev = res[(res["ev_ago"] == 0) & res["ev_kind"].notna() & (res["ev_kind"] != "break")]
    prog = res[res["stage"].between(1, 4)]
    cand = res[((res["stage"] == 0) & res["os_now"]) | ((res["stage"] == 1) & res["near_gc"])
               | ((res["stage"] == 3))]
    tiles = [
        _tile(True if len(today_ev) else None, "🔔 오늘 신호", f"{len(today_ev)}"),
        _tile(None, "진행 중 1 · 3 · 눌림 · 9",
              " · ".join(str(int((res["stage"] == s).sum())) for s in (1, 2, 3, 4))),
        _tile(None, "과매도 대기(RSI<30)", f"{int(((res['stage'] == 0) & res['os_now']).sum())}"),
        _tile(None, "+2 임박 · +6 대기", f"{int(((res['stage'] == 1) & res['near_gc']).sum())} · {int((res['stage'] == 3).sum())}"),
    ]
    sim, sim_msg = _t126_sim_run(df, histories, quotes, p, fins_all)
    if sim and sim.get("ok"):
        tiles.insert(0, _tile(sim["pnl"] > 0 if sim["pnl"] else None, "🤖 1천만원 모의 계좌",
                              f"{sim['equity']:,.0f}원 · {sim['ret']:+.2f}%"))
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)

    view = st.segmented_control("보기", ["🤖 모의 계좌", "🔔 오늘 신호", "📈 진행 중", "👀 후보"], default="🔔 오늘 신호",
                                key="t126_view", label_visibility="collapsed") or "🔔 오늘 신호"
    if view == "🤖 모의 계좌":
        _t126_sim_view(sim, sim_msg)
        show, empty, cap = pd.DataFrame(), None, ""
    elif view == "🔔 오늘 신호":
        show = today_ev.copy()
        order = {"buy6": 0, "buy2": 1, "buy1": 2, "sell6": 3, "sell2": 4, "sell1": 5, "fail": 6, "fake": 7}
        show = show.assign(_o=show["ev_kind"].map(order)).sort_values(["_o", "cap_krw"], ascending=[True, False])
        empty = "오늘 새로 나온 1·2·6 매수/정리 신호가 없어요. '진행 중'·'후보'를 보세요."
        cap = "오늘 봉 기준(장중이면 지금 값이라 종가에 바뀔 수 있어요) · 매수 신호는 그 단계 비중만큼만 · 정리 신호는 그 비중만큼만 팔기"
    elif view == "📈 진행 중":
        show = prog.sort_values(["stage", "ev_ago"], ascending=[False, True])
        empty = "지금 1:2:6을 진행 중인 종목이 없어요."
        cap = "단계는 반드시 1 → 2 → 6 순서. 1·2단계 중 과매도 저점 아래로 마감하면 손절(판단이 틀림)로 처음부터 다시."
    else:
        show = cand.assign(_o=cand["stage"].map({3: 0, 1: 1, 0: 2})).sort_values(["_o", "cap_krw"], ascending=[True, False])
        empty = "후보가 없어요. 시가총액 기준을 낮춰 보세요."
        cap = ("후보 = ① RSI 30 아래(다시 올라오면 1) ② 1 보유 중 MACD 골든크로스 임박(+2) ③ 구름대 돌파 뒤 눌림 확인 대기(+6). "
               "30 아래에서 미리 사거나, 내렸다고 더 사지 않기")
    if show.empty:
        if empty:
            st.caption(empty)
    else:
        _t126_table(show.head(80), p, float(equity or 0))
        st.caption(cap)
        labels = {r.code: f"{r.name} ({r.code}) · {T126_STAGE_TXT.get(int(r.stage), '')}" for r in show.head(80).itertuples()}
        code = st.selectbox("차트로 보기 (구름대 · RSI · MACD와 1·2·6 표시)", list(labels), format_func=labels.get, key="t126_chart")
        hist = (histories.get(code) or (None, data.empty_frame(), None))[1]
        if hist is not None and not hist.empty:
            ind = data.t126_indicators(hist, quotes.get(code), data.now_kst().date(), p)
            ev = show.loc[show["code"] == code, "events"].iloc[0]
            _t126_chart(ind, ev, labels[code])
            with st.expander("이 종목 신호 기록"):
                st.markdown("\n".join(f"- {e['date']:%m/%d} · {e['label']} · {e['price']:,.0f}원" for e in ev) or "기록 없음")
    with st.expander("ℹ️ 1:2:6 매매란 (영상 요약)"):
        st.markdown(
            "- 후지모토 시게루는 버블 때 100억까지 불렸다가 20억으로 줄었고, 그 뒤 **돈을 넣는 순서**만 바꿔 220억 이상으로 다시 키웠어요. "
            "시장이 벌어 준 돈을 실력으로 착각한 게 실패 원인이었고, 그래서 '처음부터 크게'가 아니라 **시장이 확인해 줄 때마다** 늘리는 방식으로 바꿨어요.\n"
            "- **1 → +2 → +6**: RSI 30 재돌파(탐색) → MACD 골든크로스 → 구름대 돌파 후 눌림에서 버티고 다시 상승. 증거가 하나씩 늘 때마다 비중도 늘려요.\n"
            "- **정리도 1:2:6**: MACD 데드크로스에 1 → RSI까지 약해지면 2 → 구름대 아래로 무너지면 남은 6. 한 번에 다 사고 다 팔지 않아요.\n"
            "- **흔한 실수**: 1만 샀는데 바로 크게 오르면 '처음부터 크게 살걸' → 다음엔 RSI 하나만 보고 크게 들어갔다가 틀려서 큰 손실. "
            "1을 샀는데 내리면 '더 사면 평단 낮아지는데' → MACD·일목 확인 전에 넣으면 그냥 물타기.\n"
            "- **금액**: 먼저 '틀리면 얼마 잃나'를 정해요. 1천만 원 · 손실 2%(20만 원) · 손절까지 4%면 이번 거래는 최대 500만 원 → 1:2:6으로 나눠 넣기.\n"
            "- 지표(RSI·MACD·일목)는 바꿀 수 있어도 **순서**(작게 → 확인되면 늘리기 → 이유가 사라지면 줄이기)가 핵심이에요.")


_QUOTES: dict = {}
VIEW_CODES: dict = {"kr": (), "all": (), "heavy": ()}
_GC = _memo_store().setdefault("gc", {"n": 0, "mb": 0.0})   # 새로고침해도 세는 수가 이어지게


MEM_CODE_TAG = "10/23-a"
_STORE_ROOTS[:] = [lambda: _hist_store()["data"], lambda: _monthly_store()["items"], lambda: _bg_store()["items"],
                   lambda: getattr(data, "_trend_cache", {}), lambda: getattr(data, "_fin_cache", {}),
                   lambda: getattr(data, "_metrics_memo", {}), lambda: _memo_store()]
_PHASE: dict = data.__dict__.setdefault("_PHASE_MEM", {})   # (원인 찾기) 마지막 새로고침의 단계별 메모리 {단계: (전, 후)}


def _ph(name: str, t0: float):
    _PHASE[name] = (t0, rss_mb())
    return rss_mb()


def _nxt_hours(now=None) -> bool:
    """넥스트레이드 프리(08:00~08:50)·애프터(15:30~20:00) 시간 — KRX 시세가 멈춰 있는 동안."""
    now = now or data.now_kst()
    t = now.time()
    return now.weekday() < 5 and (dtime(8, 0) <= t < dtime(8, 50) or dtime(15, 30) < t <= dtime(20, 0))


def _nxt_live_highs(df: pd.DataFrame, quotes: dict) -> pd.DataFrame:
    """(실시간 신고가) 넥장 시간엔 KRX 시세가 멈춰서 애프터·프리에서 52주 고가를 넘어도 보드에 안 떴어요(🏁 탭만 넥장 반영).
    52주 고가 5% 안 국내 종목만 넥장 시세를 받아, 넥장이 더 최근이면 현재가·52주 고가·신고가 경과일을 고쳐요."""
    if df.empty or not _nxt_hours() or "to_high" not in df:
        return df
    near = df[df["code"].map(data.is_kr) & (pd.to_numeric(df["to_high"], errors="coerce") <= 5)]
    codes = tuple(sorted(near["code"].unique()))[:400]
    if not codes:
        return df
    try:
        nxt, _ = load_nxt_quotes(codes)
    except Exception:  # noqa: BLE001
        return df
    today = data.now_kst().date()
    df = df.copy()
    hit = 0
    for i in near.index:
        c = df.at[i, "code"]
        live, src, nhi = data._live_price(quotes.get(c), nxt.get(c), today)
        if not live or src == "KRX":
            continue
        hi52 = float(df.at[i, "high52"] or 0)
        top = max(float(nhi or 0), float(live))
        df.at[i, "price"] = float(live)
        prev = df.at[i, "prev"] if "prev" in df else None
        if prev:
            df.at[i, "change"] = (float(live) / float(prev) - 1) * 100
        if top > hi52:
            df.at[i, "high52"] = top
            df.at[i, "days_since_high"] = 0
            hi52 = top
            hit += 1
        df.at[i, "gap"] = (float(live) / hi52 - 1) * 100
        df.at[i, "to_high"] = (hi52 / float(live) - 1) * 100
        if "source" in df:
            df.at[i, "source"] = src
    NXT_LIVE["hits"], NXT_LIVE["checked"], NXT_LIVE["t"] = hit, len(codes), data.now_kst()
    return df


NXT_LIVE: dict = {}


def _mkt_status_txt() -> str:
    """상태 줄에 붙이는 시장 국면(🚦 탭과 같은 계산)."""
    try:
        sig = market_signals()
        return " · 🚦 " + " · ".join(f"{n} {m['em']} {m['state_txt']}" + (f" D{m['day']}" if m["state"] == "rally" else "")
                                    for n, m in sig.items() if m)
    except Exception:  # noqa: BLE001
        return ""


# ─────────────────────────── 💵 현금창출 기업 ───────────────────────────
@st.cache_data(show_spinner=False, max_entries=2)
def _cf_metrics(mtime: float) -> tuple[list[dict], str | None, str]:
    """cashflow_screen.json(분기마다 GitHub Actions가 갱신) → 종목별 지표. 파일이 바뀔 때만 다시 계산."""
    raw = cashflow.load()
    out = []
    for code, rec in (raw.get("items") or {}).items():
        try:
            m = cashflow.metrics(code, rec)
        except Exception:  # noqa: BLE001
            m = None
        if m:
            out.append(m)
    return out, raw.get("updated"), raw.get("source") or cashflow.SRC


def _cf_eok(v):
    return "-" if v is None or v != v else f"{v:,.0f}"


def render_cashflow(df: pd.DataFrame):
    _sec_h("💵 현금창출 기업", "적게 투자하면서도 성장하고, 번 이익이 꾸준히 현금(FCF)으로 쌓이는 기업 — 고유가·고금리 장기화 대비")
    try:
        mtime = os.path.getmtime(cashflow.OUT_FILE)
    except OSError:
        mtime = 0.0
    mets, updated, src = _cf_metrics(mtime)
    if not mets:
        st.info("아직 재무 자료 파일(cashflow_screen.json)이 없어요. GitHub → Actions → '💵 현금창출 기업 재무 갱신' → Run workflow 를 "
                "한 번 누르면 10분쯤 뒤에 채워져요(그 뒤로는 매달 6·21일 자동).")
        return

    with st.expander("⚙️ 기준 바꾸기", expanded=False):
        c1, c2, c3, c4, c5 = st.columns(5)
        th = {
            "cagr": c1.number_input("매출 연평균 성장 ≥ (%)", 0.0, 50.0, cashflow.DEFAULTS["cagr"], 1.0, key="cf_cagr"),
            "capex_s": c2.number_input("CAPEX/매출 ≤ (%)", 1.0, 30.0, cashflow.DEFAULTS["capex_s"], 1.0, key="cf_cs",
                                       help="4년 합산 CAPEX(유형+무형자산 취득) ÷ 4년 합산 매출"),
            "capex_ocf": c3.number_input("CAPEX/영업현금 ≤ (%)", 10.0, 100.0, cashflow.DEFAULTS["capex_ocf"], 5.0, key="cf_co",
                                         help="번 현금 중 설비에 다시 들어간 비율(4년 합산)"),
            "de": c4.number_input("차입금/자본 ≤ (%)", 0.0, 200.0, cashflow.DEFAULTS["de"], 5.0, key="cf_de",
                                  help="순현금(현금 > 차입금)이면 이 기준과 상관없이 통과"),
            "icr": c5.number_input("이자보상배율 ≥ (배)", 0.0, 100.0, cashflow.DEFAULTS["icr"], 1.0, key="cf_icr",
                                   help="최근 연도 영업이익 ÷ 이자비용"),
        }
        inc_fin = st.toggle("금융·지주도 포함", value=False, key="cf_fin",
                            help="은행·증권·보험·지주는 영업현금흐름과 차입금의 의미가 달라서 기본은 빼요")

    board = df.drop_duplicates("code").set_index("code") if len(df) else pd.DataFrame()
    rows = []
    for m in mets:
        if m["fin"] and not inc_fin:
            continue
        g, n_ok, chk = cashflow.grade(m, th)
        b = board.loc[m["code"]] if m["code"] in board.index else None
        rows.append({**m, "grade": g, "n_ok": n_ok, "chk": chk,
                     "price": None if b is None else b.get("price"), "to_high": None if b is None else b.get("to_high"),
                     "chg": None if b is None else b.get("change")})
    if not rows:
        st.info("조건에 맞는 종목이 없어요.")
        return
    cnt = {k: sum(1 for r in rows if r["grade"] == k) for k in ("1군", "2군", "함정")}
    tiles = [_tile(True if cnt["1군"] else None, "🥇 1군 — 5개 기준 모두 통과", f"{cnt['1군']}"),
             _tile(None, "🥈 2군 — 하나만 아쉬움", f"{cnt['2군']}"),
             _tile(False if cnt["함정"] else None, "⚠️ 성장은 좋은데 현금이 안 남음", f"{cnt['함정']}"),
             _tile(None, "분석 종목", f"{len(rows)}")]
    st.markdown(f'<div class="tiles" style="margin-bottom:.6rem">{"".join(tiles)}</div>', unsafe_allow_html=True)

    views_ = {"🥇 1군": ["1군"], "🥈 2군": ["2군"], "⚠️ 함정": ["함정"], "전체": ["1군", "2군", "함정", "-"]}
    c1, c2 = st.columns([2.2, 1])
    pick = c1.segmented_control("보기", list(views_), default="🥇 1군", key="cf_view", label_visibility="collapsed") or "🥇 1군"
    sort_by = c2.selectbox("정렬", ["FCF 증가폭", "매출 성장률", "FCF 마진", "순현금", "CAPEX 적은 순"], key="cf_sort",
                           label_visibility="collapsed")
    sel = [r for r in rows if r["grade"] in views_[pick]]
    keyf = {"FCF 증가폭": lambda r: -(r["fcf_trend"] or -1e9), "매출 성장률": lambda r: -(r["cagr"] or -1e9),
            "FCF 마진": lambda r: -(r["fcf_margin"] or -1e9), "순현금": lambda r: -(r["net_cash"] or -1e12),
            "CAPEX 적은 순": lambda r: (r["capex_s"] if r["capex_s"] is not None else 1e9)}[sort_by]
    sel.sort(key=lambda r: (-r["n_ok"], keyf(r)) if pick == "전체" else keyf(r))
    if not sel:
        st.info("이 묶음에 해당하는 종목이 없어요. 위 '기준 바꾸기'에서 기준을 조정해 보세요.")
        return

    mark = lambda v: "✅" if v is True else ("❌" if v is False else "·")  # noqa: E731
    yrs = sel[0]["years"]
    tbl = pd.DataFrame([{
        "종목": f"{r['name']} · {r['code']}", "분류": r["group"], "등급": r["grade"],
        "성장": mark(r["chk"]["성장"]), "저CAPEX": mark(r["chk"]["저CAPEX"]), "FCF+": mark(r["chk"]["FCF 연속+"]),
        "FCF↑": mark(r["chk"]["FCF 증가"]), "재무": mark(r["chk"]["재무"]),
        "매출 성장(연)": r["cagr"], f"{r['years'][-1]} 매출(억)": r["sales"][-1],
        "FCF 추이(억)": [v if v is not None else 0 for v in r["fcf"]],
        f"{r['years'][-1]} FCF(억)": r["fcf"][-1], "FCF 마진": r["fcf_margin"], "CAPEX/매출": r["capex_s"],
        "순현금(억)": r["net_cash"], "이자보상(배)": None if r["icr"] is None else min(r["icr"], 999),
        "올해 성장(연환산)": r["p_growth"], "올해 FCF(억)": r["p_fcf"],
        "현재가": r["price"], "신고가까지": r["to_high"],
    } for r in sel])
    pct = lambda lab: st.column_config.NumberColumn(lab, format="%.1f%%")  # noqa: E731
    eok = lambda lab: st.column_config.NumberColumn(lab, format="%,.0f")  # noqa: E731
    st.dataframe(tbl, hide_index=True, width="stretch", height=min(560, 40 + 35 * len(tbl)),
                 column_config={
                     "종목": st.column_config.Column(pinned=True),
                     "FCF 추이(억)": st.column_config.BarChartColumn(f"FCF 추이 {yrs[0]}→{yrs[-1]}", y_min=None),
                     "매출 성장(연)": pct("매출 성장(연)"), "FCF 마진": pct("FCF 마진"), "CAPEX/매출": pct("CAPEX/매출"),
                     "올해 성장(연환산)": pct("올해 성장(연환산)"), "신고가까지": pct("신고가까지"),
                     f"{yrs[-1]} 매출(억)": eok(f"{yrs[-1]} 매출(억)"), f"{yrs[-1]} FCF(억)": eok(f"{yrs[-1]} FCF(억)"),
                     "순현금(억)": eok("순현금(억)"), "올해 FCF(억)": eok("올해 FCF(억)"), "현재가": eok("현재가"),
                     "이자보상(배)": st.column_config.NumberColumn("이자보상(배)", format="%.0f"),
                 })
    st.download_button("📥 CSV", tbl.drop(columns=["FCF 추이(억)"]).to_csv(index=False).encode("utf-8-sig"),
                       f"현금창출기업_{pick[2:]}_{data.now_kst():%Y%m%d}.csv", "text/csv", key="cf_dl")

    # ── 종목 자세히
    names = {f"{r['name']} · {r['code']}": r for r in sel}
    choice = st.selectbox("🔎 종목 자세히", list(names), key="cf_pick")
    r = names[choice]
    long = []
    for i, y in enumerate(r["years"]):
        for lab, arr in (("영업현금흐름", r["ocf"]), ("CAPEX", r["capex"]), ("FCF", r["fcf"])):
            if arr[i] is not None:
                long.append({"연도": y, "항목": lab, "억원": arr[i]})
    cl, cr = st.columns([1.5, 1])
    with cl:
        if long:
            ch = alt.Chart(pd.DataFrame(long)).mark_bar().encode(
                x=alt.X("연도:N", title=None), xOffset=alt.XOffset("항목:N", sort=["영업현금흐름", "CAPEX", "FCF"]),
                y=alt.Y("억원:Q", title="억원"),
                color=alt.Color("항목:N", sort=["영업현금흐름", "CAPEX", "FCF"],
                                scale=alt.Scale(range=["#8A979F", "#E8A33D", "#6C5CE7"]), legend=alt.Legend(orient="top", title=None)),
                tooltip=["연도", "항목", alt.Tooltip("억원:Q", format=",.0f")]).properties(height=260)
            st.altair_chart(ch, width="stretch")
    with cr:
        c = r["chk"]
        lines = [
            f"{mark(c['성장'])} **성장** 매출 {_cf_eok(r['sales'][0])} → {_cf_eok(r['sales'][-1])}억 "
            f"(연 {r['cagr']:+.1f}%)" if r["cagr"] is not None else f"{mark(c['성장'])} **성장** 자료 부족",
            f"{mark(c['저CAPEX'])} **저CAPEX** 매출의 {r['capex_s']:.1f}% · 영업현금의 "
            f"{(r['capex_ocf'] or 0):.0f}%" if r["capex_s"] is not None else f"{mark(c['저CAPEX'])} **저CAPEX** 자료 부족",
            f"{mark(c['FCF 연속+'])} **FCF 연속 플러스** {r['fcf_pos']}/{r['fcf_n']}년",
            f"{mark(c['FCF 증가'])} **FCF 증가** " + " → ".join(_cf_eok(v) for v in r["fcf"]) + "억",
            f"{mark(c['재무'])} **재무** 현금 {_cf_eok(r['cash'])} · 차입금 {_cf_eok(r['debt'])}억"
            + (f" · 이자보상 {min(r['icr'], 999):.0f}배" if r["icr"] is not None else ""),
        ]
        if r["part"]:
            lines.append(f"📅 **{r['part']} 누적** 매출 {_cf_eok(r['p_sales'])} · 영업이익 {_cf_eok(r['p_op'])} · "
                         f"FCF {_cf_eok(r['p_fcf'])}억" + (f" (매출 연환산 {r['p_growth']:+.0f}%)" if r["p_growth"] is not None else ""))
        st.markdown("\n\n".join(lines))
        st.caption(f"{'연결' if r['mc'] == 'C' else '개별'} 기준 · 재무상태 {r['bs_at']}")
    st.caption(f"자료: {src}" + (f" · 갱신 {updated[:16].replace('T', ' ')}" if updated else "")
               + " · FCF = 영업활동현금흐름 − 유형·무형자산 취득 · 성장/CAPEX/FCF는 최근 4개 연도 기준, '올해'는 반기·분기 누적. "
               "1군 = 5개 기준 모두 통과 · 2군 = 4개 통과 + 최근 FCF 플러스 · 함정 = 매출은 크는데 FCF가 마이너스거나 투자 부담이 큼. "
               "조선처럼 선수금으로 현금이 들어오는 업종은 숫자가 좋게 나올 수 있으니 사업 구조를 같이 보세요. 매수 권유가 아니에요.")


# ─────────────────────────── 📉 하락장 점검(제주바람 섹터 점검 자료) ───────────────────────────
def _bc_pick() -> dict | None:
    """저장된 점검 + 이번 접속에 올린 점검 중 고른 날짜의 자료."""
    extra = st.session_state.setdefault("bc_extra", {})
    dates = sorted(set(bear_check.list_dates()) | set(extra), reverse=True)
    if not dates:
        return None
    pick = dates[0]
    if len(dates) > 1:
        pick = st.selectbox("점검 날짜", dates, key="bc_date",
                            format_func=lambda x: f"{x} (최신)" if x == dates[0] else x)
    return extra.get(pick) or bear_check.load(pick)


def _bc_upload():
    with st.expander("📤 새 점검 자료 넣기(HTML)"):
        up = st.file_uploader("'하락장 섹터 점검' HTML 파일", type=["html", "htm"], key="bc_up")
        if not up:
            st.caption("새 날짜의 점검 HTML을 넣으면 그 날짜가 위 '점검 날짜'에 추가돼요. GITHUB_TOKEN이 있으면 저장소에도 저장돼요.")
            return
        d = bear_check.parse_html(up.getvalue())
        if not d:
            st.error("이 파일에서 점검 데이터를 찾지 못했어요. 같은 형식(하락장 섹터 점검)의 HTML인지 확인해 주세요.")
            return
        st.session_state.setdefault("bc_extra", {})[d["base"]] = d
        try:
            bear_check.save_local(d)
        except OSError:
            pass
        token = _secret("GITHUB_TOKEN")
        if token and st.session_state.get("bc_saved") != d["base"]:
            ok, msg = reports.save_github(d, token, _secret("GITHUB_REPO", "kimgun124-boop/very"),
                                          path=bear_check.repo_path(d))
            if ok:
                st.session_state["bc_saved"] = d["base"]
            st.success(f"{d['base']} 점검을 넣었어요. {msg}") if ok else st.warning(msg)
        else:
            st.success(f"{d['base']} 점검을 넣었어요" + ("" if token else " (GITHUB_TOKEN이 없어 이번 접속에서만 보여요)") + ".")


def _bc_idx_card(name: str, m: dict) -> None:
    c = lambda v: UP if v > 0 else DOWN if v < 0 else "#26343E"
    d = lambda s: (s or "-")[2:].replace("-", ".")
    ftd = (m.get("ftds") or [None])[-1]
    cells = [
        ("종가", f"{m['close']:,.2f}", f"<span style='color:{c(m['chg_1d'])}'>{m['chg_1d']:+.2f}%</span>"),
        ("52주 고점 대비", f"<span style='color:{c(m['dd'])}'>{m['dd']:+.1f}%</span>", f"고점 {d(m['high_date'])}"),
        ("고점 후", f"{m['days_since_high']}거래일", f"{m.get('calendar_days_since_high', '-')}일"),
    ]
    top = "".join(f"<div style='flex:1;min-width:0'><div style='font-size:.78rem;color:#51616C;font-weight:700'>{k}</div>"
                  f"<div style='font-size:1.25rem;font-weight:900;letter-spacing:-.02em;white-space:nowrap'>{v}</div>"
                  f"<div style='font-size:.78rem;color:#7A8A94'>{s}</div></div>" for k, v, s in cells)
    ma = " · ".join(f"{n} <b style='color:{c(m[k])}'>{m[k]:+.1f}%</b>" for n, k in
                    (("20일선", "ma20_gap"), ("50일선", "ma50_gap"), ("200일선", "ma200_gap")))
    lines = [
        ma,
        f"분산일 <b>{m['dist_days']}개</b> <span style='color:#7A8A94'>({', '.join(m.get('dist_dates') or [])})</span>",
        (f"최근 FTD <b>{d(ftd['date'])}</b> (Day {ftd['day']}, +{ftd['gain']:.1f}%)" if ftd else "FTD 없음")
        + (f" · 반등 시도 <b>Day {m['rally_day']}</b>" if m.get("rally_day") else " · 반등 시도 없음"),
        f"저점 {m['low_close']:,.2f} ({d(m['low_date'])}, {m['low_dd']:+.1f}%) · 첫 하락장 진입 {d(m.get('first_bear_date'))}",
    ]
    st.markdown(
        f"<div style='background:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;padding:.8rem 1rem;margin-bottom:.6rem'>"
        f"<div style='font-weight:800;margin-bottom:.4rem'>{html.escape(name)} {'🐻 하락장' if m.get('bear') else ''}</div>"
        f"<div style='display:flex;gap:.6rem;margin-bottom:.5rem'>{top}</div>"
        f"<div style='font-size:.84rem;line-height:1.6;color:#26343E'>{'<br>'.join(lines)}</div></div>",
        unsafe_allow_html=True)


def render_bear_check(df: pd.DataFrame):
    _sec_h("📉 하락장 점검", "하락장 과대낙폭 vs 버티는 종목 — 섹터별로 어디가 무너지고 어디가 버티는지")
    D = _bc_pick()
    _bc_upload()
    if not D:
        st.info("저장된 점검 자료가 없어요. 위 '새 점검 자료 넣기'로 HTML을 넣어 주세요.")
        return
    st.caption(f"기준일 **{D['base']}** · 대상 {D.get('N', '-')}종목 · 출처: {bear_check.SRC} (copyright by 제주바람)")

    # 01 시장 위치
    st.markdown("##### 01 시장 위치")
    st.caption("종목보다 시장이 먼저. 분산일·FTD는 자료 작성자의 자체 계산이에요.")
    mk = D.get("market") or {}
    cols = st.columns(max(1, len(mk)))
    for col, (name, m) in zip(cols, mk.items()):
        with col:
            _bc_idx_card(name, m)
    br = D.get("breadth") or {}
    if br:
        a, b, c = st.columns(3)
        a.metric("20일선 위 종목", f"{br.get('above_ma20', 0):.1f}%")
        b.metric("50일선 위 종목", f"{br.get('above_ma50', 0):.1f}%")
        c.metric("200일선 위 종목", f"{br.get('above_ma200', 0):.1f}%")
    l, r = st.columns(2)
    with l:
        mon = [{"지수": n, "월": x[0], "수익률": x[1]} for n, m in mk.items() for x in (m.get("monthly") or [])]
        if mon:
            md = pd.DataFrame(mon)
            st.altair_chart(alt.Chart(md).mark_bar(cornerRadius=2).encode(
                x=alt.X("월:N", title=None, axis=alt.Axis(labelAngle=0)), xOffset="지수:N",
                y=alt.Y("수익률:Q", title="월간 %"),
                color=alt.condition("datum.수익률 > 0", alt.value(UP), alt.value(DOWN)),
                opacity=alt.Opacity("지수:N", scale=alt.Scale(range=[1.0, 0.55]), legend=alt.Legend(orient="top", title=None)),
                tooltip=["지수", "월", alt.Tooltip("수익률:Q", format="+.1f")],
            ).properties(height=220, title="월간 등락"), width="stretch")
    with r:
        hl = D.get("hl") or {}
        if hl.get("dates"):
            hd = pd.DataFrame({"날짜": hl["dates"], "신고가": hl["hi"], "신저가": [-x for x in hl["lo"]]}) \
                .melt("날짜", var_name="구분", value_name="종목 수")
            st.altair_chart(alt.Chart(hd).mark_bar(cornerRadius=2).encode(
                x=alt.X("날짜:N", title=None, sort=None, axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("종목 수:Q", title="신고가(+) / 신저가(−)"),
                color=alt.Color("구분:N", scale=alt.Scale(domain=["신고가", "신저가"], range=[UP, DOWN]),
                                legend=alt.Legend(orient="top", title=None)),
                tooltip=["날짜", "구분", alt.Tooltip("종목 수:Q", format="d")],
            ).properties(height=220, title="52주 신고가·신저가 종목 수"), width="stretch")

    # 02 그룹 경계값
    st.markdown("##### 02 그룹 경계값")
    bd = D.get("bounds") or {}
    if bd:
        a, b, c, e = st.columns(4)
        a.metric(f"과대낙폭 경계 (하위 {bd.get('n_crash', '-')}종목)", f"{bd['crash_cut']:+.1f}%", f"평균 {bd['crash_avg']:+.1f}%", delta_color="off")
        b.metric(f"견조 경계 (상위 {bd.get('n_strong', '-')}종목)", f"{bd['strong_cut']:+.1f}%", f"평균 {bd['strong_avg']:+.1f}%", delta_color="off")
        c.metric("전체 낙폭 중앙값", f"{bd['all_med']:+.1f}%")
        bt = D.get("backtest") or {}
        e.metric("깡토 백테스트 평균", f"{bt.get('crash_avg_dd', -67):+.0f}% / {bt.get('strong_avg_dd', -8):+.0f}%", "과대낙폭 / 견조", delta_color="off")
    if D.get("boundsText"):
        st.caption(D["boundsText"])

    sec = pd.DataFrame(D.get("sectors") or [])
    if len(sec):
        sec = sec.sort_values("버팀지수", ascending=False)
        # 03 섹터 지도
        st.markdown("##### 03 섹터 지도")
        st.caption("가로 = 버팀지수(섹터 안 견조 비율 − 과대낙폭 비율, %p) · 세로 = 섹터 낙폭 중앙값 · 크기 = 섹터 시가총액 합. 오른쪽 위일수록 버티는 섹터.")
        base = alt.Chart(sec).encode(
            x=alt.X("버팀지수:Q", title="버팀지수(%p)"), y=alt.Y("낙폭중앙값:Q", title="낙폭 중앙값(%)", scale=alt.Scale(zero=False)))
        pts = base.mark_circle(opacity=0.65, stroke="#FFFFFF", strokeWidth=1).encode(
            size=alt.Size("시총합:Q", legend=None, scale=alt.Scale(range=[60, 2200])),
            color=alt.Color("버팀지수:Q", scale=alt.Scale(scheme="redblue", domainMid=0, reverse=True), legend=None),
            tooltip=["대분류", "종목수", alt.Tooltip("버팀지수:Q", format="+.1f"), alt.Tooltip("낙폭중앙값:Q", format="+.1f"),
                     "과대낙폭수", "견조수", alt.Tooltip("시총합:Q", format=",.0f", title="시총합(억)")])
        txt = base.mark_text(dy=-11, fontSize=11, fontWeight=600, color="#26343E").encode(text="대분류:N")
        zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color="#9CA3AF", strokeDash=[3, 3]).encode(x="x:Q")
        st.altair_chart((zero + pts + txt).properties(height=420), width="stretch")

        # 04 점유율
        st.markdown("##### 04 과대낙폭 점유율 vs 견조 점유율")
        st.caption("과대낙폭(또는 견조) 그룹 전체에서 그 섹터가 차지하는 비중. 버팀지수 순.")
        sh = sec[["대분류", "과대낙폭점유율", "견조점유율"]].rename(columns={"과대낙폭점유율": "과대낙폭", "견조점유율": "견조"}) \
            .melt("대분류", var_name="그룹", value_name="점유율")
        st.altair_chart(alt.Chart(sh).mark_bar(cornerRadius=2).encode(
            y=alt.Y("대분류:N", sort=list(sec["대분류"]), title=None), yOffset="그룹:N",
            x=alt.X("점유율:Q", title="그룹 안 비중(%)"),
            color=alt.Color("그룹:N", scale=alt.Scale(domain=["과대낙폭", "견조"], range=[DOWN, UP]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["대분류", "그룹", alt.Tooltip("점유율:Q", format=".1f")],
        ).properties(height=max(300, 26 * len(sec))), width="stretch")

        # 05 섹터 집계표
        st.markdown("##### 05 섹터 집계표")
        cols5 = ["대분류", "종목수", "버팀지수", "과대낙폭수", "과대낙폭비율", "견조수", "견조비율", "낙폭중앙값", "낙폭평균",
                 "시총가중낙폭", "꼬리비율", "인기주붕괴수", "신고가후보수", "RS중앙값", "200일선위비율", "거래대금변화", "고점일중앙값", "시총합"]
        t5 = sec[[c for c in cols5 if c in sec.columns]].rename(columns={"시총합": "시총합(억)"})
        pc = ["과대낙폭비율", "견조비율", "낙폭중앙값", "낙폭평균", "시총가중낙폭", "꼬리비율", "200일선위비율"]
        st.dataframe(t5.style.format({**{c: "{:.1f}%" for c in pc}, "버팀지수": "{:+.1f}", "RS중앙값": "{:.0f}",
                                      "거래대금변화": "{:.2f}", "시총합(억)": "{:,.0f}"}, na_rep="-")
                     .map(_won_c, subset=["버팀지수"]), hide_index=True, width="stretch",
                     height=min(760, 36 * (len(t5) + 1) + 4))
        st.caption("머리글을 누르면 정렬돼요. 거래대금변화 = 최근 거래대금 ÷ 평소(1보다 작으면 거래가 말라감).")

    board = df.drop_duplicates("code").set_index("code") if len(df) and "code" in df.columns else pd.DataFrame()

    # 06 섹터별 대표 종목
    reps_ = pd.DataFrame(D.get("reps") or [])
    if len(reps_):
        st.markdown("##### 06 섹터별 대표 종목")
        st.caption("각 섹터의 과대낙폭·견조 그룹에서 20일 평균 거래대금 상위 3종목씩 기계적으로 뽑음(결과를 보고 고르지 않음).")
        order = list(sec["대분류"]) if len(sec) else sorted(reps_["대분류"].unique())
        rows = []
        for s_ in order:
            sub = reps_[reps_["대분류"] == s_]
            if not len(sub):
                continue
            fmt_ = lambda g: " · ".join(f"{r['종목명']}({r['dd_52w']:+.0f}%)"
                                        for _, r in sub[sub["그룹"] == g].sort_values("순위").iterrows()) or "-"
            rows.append({"섹터": s_, "🛡️ 견조": fmt_("견조"), "⚠️ 과대낙폭": fmt_("과대낙폭")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(760, 36 * (len(rows) + 1) + 4),
                     column_config={"🛡️ 견조": st.column_config.TextColumn(width="large"),
                                    "⚠️ 과대낙폭": st.column_config.TextColumn(width="large")})

    # 07 전 종목
    stx = pd.DataFrame(D.get("stocks") or [])
    if len(stx):
        st.markdown("##### 07 과대낙폭 · 견조 전 종목")
        c1, c2 = st.columns([1, 2])
        g = c1.segmented_control("그룹", ["과대낙폭", "견조"], default="과대낙폭", key="bc_g", label_visibility="collapsed") or "과대낙폭"
        secs = sorted(stx["sec"].dropna().unique(), key=str)
        sf = c2.selectbox("섹터", ["전체 섹터"] + secs, key="bc_sec", label_visibility="collapsed")
        v = stx[stx["g"] == g]
        if sf != "전체 섹터":
            v = v[v["sec"] == sf]
        v = v.sort_values("dd", ascending=(g == "과대낙폭"))
        has_gap = len(board) and "gap" in board.columns
        now_gap = [float(pd.to_numeric(board.at[c, "gap"], errors="coerce")) if has_gap and c in board.index else float("nan")
                   for c in v["code"]]
        t7 = pd.DataFrame({
            "종목": v["name"], "차트": [f"?chart={c}" for c in v["code"]], "코드": v["code"],
            "시장": v["mkt"].map({"KOSPI": "코스피", "KOSDAQ": "코스닥"}).fillna(v["mkt"]),
            "대분류": v["sec"], "소분류": v["sub"], "뱃지": v["tags"].map(bear_check.tag_label),
            "고점 대비": v["dd"], "지금 고점 대비": pd.Series(now_gap, index=v.index, dtype="float64"),
            "고점일": v["hd"], "고점후 거래일": v["dsh"], "코스피 고점후": v["ddk"], "20일": v["r20"], "50일": v["r50"],
            "RS": v["rs"], "200일선 위": v["ma200"].map(lambda x: "●" if x else "·"),
            "거래대금(억)": v["to"], "거래대금 변화": v["tor"], "시총(억)": v["cap"],
        })
        pcs = ["고점 대비", "지금 고점 대비", "코스피 고점후", "20일", "50일"]
        st.dataframe(t7.style.format({**{c: "{:+.1f}%" for c in pcs}, "RS": "{:.0f}", "거래대금(억)": "{:,.0f}",
                                      "거래대금 변화": "{:.2f}", "시총(억)": "{:,.0f}"}, na_rep="-")
                     .map(_won_c, subset=["코스피 고점후", "20일", "50일"]),
                     hide_index=True, width="stretch", height=min(640, 36 * (len(t7) + 1) + 4),
                     column_config={"차트": st.column_config.LinkColumn("차트", display_text="🕯️")})
        n_in = int(t7["지금 고점 대비"].notna().sum())
        st.caption(f"{len(t7)}종목 · 보드에 있는 종목 {n_in}개는 '지금 고점 대비'에 보드 실시간 값을 같이 보여줘요(점검일 이후 변화 확인용). "
                   "뱃지: " + " · ".join(f"**{a}** {b}" for a, b in bear_check.TAG_TXT.values()))

    st.caption(bear_check.NOTE + " 효석 원칙(시장 → 주도섹터 → 종목, 리스크관리 우선)이 항상 먼저예요.")
    _views_box_kq(("하락장", "재진입", "손절", "타점"), "📚 깡토 급락장 Q&A — 버틴 종목 · 다시 들어갈 자리")


# ─────────────────────────── 메인 메뉴: 묶음(1줄) → 탭(2줄) ───────────────────────────
NAV_GROUPS = [
    ("📋 기본", ["📋 리스트", "⭐ 내 종목", "🕯️ 차트", "📥 리포트"]),
    ("💼 내 계좌", ["💼 내 보유", "👀 보유 살피기"]),
    ("🏔️ 신고가·돌파", ["🏁 신고가 후보", "🏔️ 신고가", "💥 거래량 폭발", "🏭 인더스트리 액션", "🎯 매수 후보"]),
    ("🎯 타점·매매", ["🎯 초기 매수", "🪃 눌림 타점", "⚖️ 1:2:6 매매", "🌙 종가배팅"]),
    ("🔭 섹터 흐름", ["🗺️ 큰 그림", "💰 거래대금", "🚀 급상승", "🔭 차기 주도", "🌱 회복 리더", "🪜 증거 쌓기", "🌏 글로벌 피어"]),
    ("🚦 시장·이벤트", ["🚦 시장 신호", "📉 하락장 점검", "🧭 시나리오", "📣 실적·지분", "💵 현금창출"]),
]
NAV_DESC = {
    "📋 리스트": "공부 자료 속 종목 전체 — 현재가·신고가 거리·수급", "⭐ 내 종목": "내(친구) 관심종목과 포트폴리오",
    "🕯️ 차트": "종목 검색 → 일·주·월봉 차트", "📥 리포트": "리포트를 넣으면 종목이 보드에 반영",
    "💼 내 보유": "보유 종목을 내 원칙으로 점검", "👀 보유 살피기": "보유 종목 실시간 수급·뉴스 + AI 판단",
    "🏁 신고가 후보": "넥장까지 반영한 진짜 돌파·찍고 밀림", "🏔️ 신고가": "역사적·52주 신고가를 섹터별로",
    "💥 거래량 폭발": "조용하다가 거래량이 터진 종목", "🏭 인더스트리 액션": "섹터 동반 거래량 1.5배↑ 매수 후보",
    "🎯 매수 후보": "내 원칙을 모두 통과한 종목", "🎯 초기 매수": "리 태너 — 표준 피벗보다 싸게 사는 자리",
    "🪃 눌림 타점": "지지선까지 쉬어 갈 때 + 1천만원 모의 매매", "🌙 종가배팅": "4인 관점(M·K·C·D) 오늘 후보 자동 점검 + 비교표", "⚖️ 1:2:6 매매": "후지모토 1:2:6 + 모의 계좌",
    "🗺️ 큰 그림": "AI · 비AI · 그외 → 테마별 등락·신고가 확산·대장주", "💰 거래대금": "섹터별 거래대금 쏠림", "🚀 급상승": "섹터 안에서 가장 빠르게 치고 오르는 종목",
    "🔭 차기 주도": "이익·수급·신고가로 본 다음 주도섹터 후보", "🌱 회복 리더": "조정 뒤 먼저 회복하는 종목",
    "🪜 증거 쌓기": "실적 → 섹터 → 글로벌 → 근거, 증거만큼 비중", "🌏 글로벌 피어": "한국 분류 vs 해외 피어·수요처",
    "🚦 시장 신호": "FTD·분산일로 본 시장 국면 + 견조주", "📉 하락장 점검": "과대낙폭 vs 버티는 종목 — 섹터 점검(제주바람)", "🧭 시나리오": "시장 시나리오별 대응", "📣 실적·지분": "3분기 어닝 서프라이즈 · VC·펀드 지분 확대",
    "💵 현금창출": "저CAPEX·매출 성장·FCF 증가·낮은 부채 — 스스로 번 현금으로 크는 기업",
}
_NAV_OF = {t: gi for gi, (_, ts) in enumerate(NAV_GROUPS) for t in ts}


def _nav_pick_group():
    ss = st.session_state
    gi = [g for g, _ in NAV_GROUPS].index(ss.get("nav_group") or NAV_GROUPS[0][0])
    last = ss.setdefault("nav_last", {})
    ss["main_tab_sel"] = last.get(gi) or NAV_GROUPS[gi][1][0]


def _nav_pick_tab(gi: int):
    ss = st.session_state
    t = ss.get(f"nav_tab_{gi}")
    if t:
        ss["main_tab_sel"] = t
        ss.setdefault("nav_last", {})[gi] = t


def main_nav() -> str:
    """1줄: 6개 묶음 카드 · 2줄: 고른 묶음의 탭 알약 · 3줄: 지금 탭 한 줄 설명. 보고 있는 탭만 계산해요."""
    ss = st.session_state
    try:                          # 매매 보드 안 링크(?tab=탭 이름)로 탭 이동
        qt = st.query_params.get("tab")
        if qt:
            if qt in _NAV_OF:
                ss["main_tab_sel"] = qt
            del st.query_params["tab"]
    except Exception:  # noqa: BLE001
        pass
    cur = ss.get("main_tab_sel")
    if cur not in _NAV_OF:
        cur = NAV_GROUPS[0][1][0]
        ss["main_tab_sel"] = cur
    gi = _NAV_OF[cur]
    ss["nav_group"] = NAV_GROUPS[gi][0]               # 다른 곳(차트 링크 등)에서 탭을 바꿔도 묶음이 따라가요
    ss[f"nav_tab_{gi}"] = cur
    ss.setdefault("nav_last", {})[gi] = cur
    with st.container(key="main_nav"):
        try:
            st.segmented_control("묶음", [g for g, _ in NAV_GROUPS], key="nav_group", on_change=_nav_pick_group,
                                 label_visibility="collapsed", required=True, width="stretch")
        except TypeError:          # 예전 Streamlit(width 없음)
            st.segmented_control("묶음", [g for g, _ in NAV_GROUPS], key="nav_group", on_change=_nav_pick_group,
                                 label_visibility="collapsed", required=True)
        st.segmented_control("탭", NAV_GROUPS[gi][1], key=f"nav_tab_{gi}", on_change=_nav_pick_tab, args=(gi,),
                             label_visibility="collapsed", required=True)
        st.markdown(f'<div class="nav-desc"><b>{html.escape(cur)}</b> — {html.escape(NAV_DESC.get(cur, ""))}</div>',
                    unsafe_allow_html=True)
    return cur


def render_board():
    _m0 = rss_mb()
    quotes, quote_error = load_quotes(KR_CODES + EXTRA_CODES)
    _m0 = _ph("시세", _m0)
    _QUOTES.clear()
    _QUOTES.update(quotes)
    shares_store = load_shares()
    core_info = None
    small_hi_set = set()
    if scope_core:
        core, small_hi, core_info = core_view(quotes, shares_store["data"])
        small_hi_set = set(small_hi)
        keep = core | small_hi | set(OS_CODES)
        stocks_use = []
        for x in STOCKS_VIEW:
            if x["code"] in keep:
                if x["code"] in small_hi and x["code"] not in core:
                    x = dict(x, tags=list(x.get("tags") or []) + ["🆕 소형 신고가"])
                stocks_use.append(x)
        core_info.update(core=len(core), small_hi=len(small_hi))
    else:
        stocks_use = STOCKS_VIEW
    view_kr = tuple(sorted({x["code"] for x in stocks_use if data.is_kr(x["code"])}))
    view_os = tuple(sorted({x["code"] for x in stocks_use if not data.is_kr(x["code"])}))
    histories = get_histories(view_kr + view_os, wait=True,
                              label="일봉을 불러오는 중이에요. 처음 켤 때 한 번만 조금 걸리고, 이후엔 바뀐 종목만 받아요.")
    if scope_core:
        _hist_drop(set(view_kr + view_os) | set(KR_CODES))
    VIEW_CODES["kr"], VIEW_CODES["all"] = view_kr, view_kr + view_os
    kr_board = tuple(c for c in view_kr if c in set(KR_CODES))
    # (메모리) 역대 신고가용 월봉·수급·실적은 공부 자료 종목 + 작은 신고가 종목만(전체 1,000종목이면 서버 메모리 초과)
    heavy = tuple(sorted(set(kr_board) | (small_hi_set if scope_core else set()))) + view_os
    VIEW_CODES["heavy"] = tuple(c for c in heavy if data.is_kr(c))
    _m0 = _ph("일봉", _m0)
    df = data.build_table(stocks_use, histories, quotes, shares_store["data"], load_fx(), bo_mode=bo_mode,
                          monthlies=load_monthlies(heavy), official52=load_official_52w(kr_board))
    df = mark_sector_first(df, histories)
    df = _nxt_live_highs(df, quotes)
    set_data_ver(df, REFRESH.get(refresh_label))
    _GC["n"] += 1
    if _GC["n"] % 3 == 0:          # (메모리) 세 번에 한 번 쓰지 않는 메모리 정리
        gc.collect()
    _GC["mb"] = mem_guard(set(view_kr + view_os) | set(KR_CODES))
    _m0 = _ph("표 계산", _m0)
    _PHASE["_tab0"] = _m0
    _mem_log(len(df))
    if os.environ.get("MEM_REPORT_FILE"):          # (테스트용) 창고별 크기를 파일로
        try:
            import json as _json
            with open(os.environ["MEM_REPORT_FILE"], "w", encoding="utf-8") as _f:
                _json.dump({"rss": rss_mb(), "items": mem_report()}, _f, ensure_ascii=False)
        except Exception:
            pass
    if core_info:
        bg = core_info["bg"]
        st.caption(f"⚡ 빠른 화면: 시가총액 3,000억↑ 주요 {core_info['core']}종목 + 시총이 작아도 최근 신고가 "
                   f"{core_info['small_hi']}종목"
                   + (f" · 작은 종목 일봉 뒤에서 받는 중 {bg['done']:,}/{bg['total']:,}" if bg.get("running") else
                      f" · 작은 종목 {core_info['loaded']:,}/{core_info['rest']:,} 확인 완료")
                   + ("" if IS_STREAMLIT_CLOUD else " · 모두 보려면 사이드바 '전체'"))
    df["cap_krw"] = pd.to_numeric(df["cap_krw"], errors="coerce")
    df["cap_local"] = pd.to_numeric(df["cap_local"], errors="coerce")

    interval = REFRESH[refresh_label]
    refresh_text = f"{refresh_label}마다 새로 불러와요." if interval else "자동 새로고침은 꺼져 있어요."
    st.markdown(
        f'<div class="status">{data.now_kst():%m/%d %H:%M:%S} · {data.market_status(quotes)} · {refresh_text}'
        + (f' · 넥장 반영(52주 고가 5% 안 {NXT_LIVE.get("checked", 0)}종목, 넥장 신고가 {NXT_LIVE.get("hits", 0)})'
           if _nxt_hours() and NXT_LIVE.get("t") else "") + _mkt_status_txt() + '</div>',
        unsafe_allow_html=True,
    )
    if data.MOCK:
        st.info("가짜 데이터로 보여주는 테스트 모드예요. 실제 시세를 보려면 STOCK_MOCK 설정 없이 실행하세요.")

    leaders_now = {g for g, _ in lead_groups(df)}
    trends_all, fins_all = load_trends_all(), load_fins_all()
    _TRENDS_BG["v"] = trends_all
    try:
        render_trade_board(df, histories, quotes)          # 🎯 이 앱의 목적: 지금 살 종목·왜·보유 대응
    except Exception as _e:  # noqa: BLE001 — 매매 보드가 실패해도 나머지 보드는 계속
        st.caption(f"매매 보드를 그리지 못했어요({type(_e).__name__}: {_e}).")
        render_buy_alert(df, histories, quotes)
    render_market(df)
    try:
        render_sector_movers(df, quotes)
    except Exception as _e:  # noqa: BLE001 — 첫 화면 카드가 실패해도 나머지 보드는 계속
        st.caption(f"섹터 강세 카드를 그리지 못했어요({type(_e).__name__}).")
    render_radar(df[df["code"].map(_mkt) == market])
    jobs = {
        "📋 리스트": lambda: render_list(df, histories, quote_error, shares_store, fins_all),
        "⭐ 내 종목": lambda: render_watch_tab(df, quotes, histories),
        "💼 내 보유": lambda: render_holdings_tab(df, quotes),
        "👀 보유 살피기": lambda: render_monitor_tab(df, quotes),
        "🕯️ 차트": lambda: render_chart_tab(df, quotes),
        "📥 리포트": lambda: render_reports_tab(),
        "🏔️ 신고가": lambda: render_ath(df, quotes),
        "🏁 신고가 후보": lambda: render_nh_candidates(df, histories, quotes),
        "💥 거래량 폭발": lambda: render_volume_surge(df, histories, quotes),
        "🏭 인더스트리 액션": lambda: render_industry_action(df, histories, quotes),
        "⚖️ 1:2:6 매매": lambda: render_126(df, histories, quotes, fins_all),
        "🎯 매수 후보": lambda: render_buy(df, quotes),
        "🗺️ 큰 그림": lambda: render_bigpicture(df),
        "💰 거래대금": lambda: render_money(df),
        "🚀 급상승": lambda: render_engine(df, trends_all, leaders_now, fins_all),
        "🔭 차기 주도": lambda: render_next(df, fins_all, trends_all, leaders_now),
        "🌱 회복 리더": lambda: render_recovery(df, histories, trends_all),
        "🧭 시나리오": lambda: render_scenario(df, histories, trends_all, leaders_now, fins_all),
        "🪃 눌림 타점": lambda: render_pullback(df, histories, quotes, fins_all),
        "📣 실적·지분": lambda: render_tracker(df, histories, quotes),
        "💵 현금창출": lambda: render_cashflow(df),
        "🪜 증거 쌓기": lambda: render_ladder(df, histories, fins_all),
        "🌏 글로벌 피어": lambda: render_peers(df, histories),
        "🚦 시장 신호": lambda: render_market_signal(df),
        "📉 하락장 점검": lambda: render_bear_check(df),
        "🎯 초기 매수": lambda: render_early(df, histories, quotes, fins_all),
        "🌙 종가배팅": lambda: render_zongbae(df),
    }
    MAIN_TABS_LAZY["v"] = True
    cur = main_nav()
    with st.container(key="main_body"):
        _t0 = rss_mb()
        jobs.get(cur, jobs["📋 리스트"])()
        _PHASE["탭"] = (_t0, rss_mb())
    _t0 = rss_mb()
    _TRIM_T[0] = 0.0 if rss_mb() - (_PHASE.get("_low") or 1e9) > 150 else _TRIM_T[0]   # 많이 늘었으면 바로 정리
    trim_maybe()
    _PHASE["정리(gc·trim)"] = (_t0, rss_mb())
    _PHASE["_low"] = min(_PHASE.get("_low") or 1e9, rss_mb())


def _attach_flows(f: pd.DataFrame) -> pd.DataFrame:
    """지금 화면에 보이는 종목(쪽)의 수급 열을 채워요. 뒤에서 받아 둔 전 종목 수급이 있으면 그걸 먼저 써요."""
    flow_keys = list(data.trend_summary(None))
    if not len(f):
        return f.assign(**{k: [] for k in flow_keys})
    trends = {}
    if show_flow:
        bg = _TRENDS_BG.get("v") or {}
        need = tuple(c for c in f["code"] if data.is_kr(c) and (bg.get(c) is None or bg[c].empty))
        trends = {**bg, **(load_trends(need) if need else {})}
    _TRENDS_BG["page"] = trends
    summ = [data.trend_summary(trends.get(c)) for c in f["code"]]
    return f.assign(**{k: [x[k] for x in summ] for k in flow_keys})


_TRENDS_BG: dict = {}


def render_list(df, histories, quote_error, shares_store, fins_all=None):
    f = apply_filters(df)
    if only_earn:
        fins = load_financials(tuple(c for c in f["code"] if data.is_kr(c)))
        f = add_earnings(f, fins)
        f = f[f["earn_ok"] == True]  # noqa: E712
    n_hot = int((f["days_since_high"] <= recent_days).sum())
    n_near = int((f["to_high"] <= 10).sum())
    n_aligned = int((f["aligned"] == True).sum())  # noqa: E712
    n_holding = int((f["bo_status"] == "유지").sum())
    n_first = int(((f["bo_status"] == "유지") & (pd.to_numeric(f["bo_nth"], errors="coerce") == 1)).sum())

    with st.container(key="desk_kpis"):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("종목", f"{len(f)}")
        c2.metric(f"최근 {recent_days}일 신고가", f"{n_hot}")
        c3.metric("돌파 유지", f"{n_holding}", f"⭐ 첫 돌파 {n_first}", delta_color="off")
        c4.metric("신고가까지 10% 이내", f"{n_near}")
        c5.metric("정배열", f"{n_aligned}")

    if f.empty:
        st.info("조건에 맞는 종목이 없어요. 왼쪽 필터를 넓혀 보세요.")
    else:
        if len(f) > 300:        # (속도) 전체 종목이면 표를 나눠서 그려요
            c1, c2, c3 = st.columns([1.3, 1, 2])
            sort_by = c1.selectbox("정렬", ["신고가 가까운 순", "등락률 높은 순", "거래대금 많은 순", "평소 대비 거래 많은 순",
                                          "RS 높은 순", "시가총액 큰 순"], key="list_sort")
            size = c2.selectbox("한 번에", [100, 200, 300], index=0, key="list_size")
            col, asc = {"신고가 가까운 순": ("to_high", True), "등락률 높은 순": ("change", False),
                        "거래대금 많은 순": ("tv_live", False), "평소 대비 거래 많은 순": ("tv_x", False),
                        "RS 높은 순": ("rs", False), "시가총액 큰 순": ("cap_krw", False)}[sort_by]
            f = f.assign(_k=pd.to_numeric(f[col], errors="coerce")).sort_values("_k", ascending=asc,
                                                                                 na_position="last").drop(columns="_k")
            pages = max(1, -(-len(f) // size))
            page = c3.number_input(f"쪽 (전체 {len(f):,}종목 · {pages}쪽)", 1, pages, 1, key="list_page")
            f = f.iloc[(page - 1) * size: page * size]
        f = _attach_flows(f)
        with st.container(key="desk_table"):
            render_table(f)
            st.caption("노란 줄 = 최근 신고가 · 신고가까지 빨강 3% 이내, 주황 10% 이내 · 수급은 억원(추정) · 해외는 현지 통화, 15분 지연")
        with st.container(key="mobile_view"):
            render_cards(f, n_hot, n_near, n_aligned, fins_all)
        render_detail(f, histories, _TRENDS_BG.get("page") or _TRENDS_BG.get("v") or {})
    render_checks(df, quote_error, shares_store)


theme.header("밸류체인 신고가 보드", "공부 자료 속 종목 · 52주 신고가 · 거래대금 흐름을 한눈에", "📈 실시간")
st.fragment(render_board, run_every=REFRESH[refresh_label])()
