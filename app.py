"""내 계좌 — 넣고, 고치고, 원칙대로 판단하기"""
import hmac
import io
import json
from datetime import date, datetime
from html import escape

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import core

# app.py와 core.py 버전이 어긋나면(한쪽만 올렸거나, 서버가 옛 core를 기억하는 경우) 먼저 다시 불러오고,
# 그래도 옛 core면 무엇을 해야 하는지 알려준다.
NEED_CORE = 11
if getattr(core, "STORE_VERSION", 0) < NEED_CORE:
    import importlib
    core = importlib.reload(core)
if getattr(core, "STORE_VERSION", 0) < NEED_CORE:
    st.error("core.py가 예전 버전이에요. GitHub에 app.py와 core.py를 **같이** 올린 뒤, "
             "Streamlit 앱 메뉴(⋮) → Reboot app을 눌러 주세요.")
    st.stop()

st.set_page_config(page_title="내 계좌", page_icon=":material/account_balance_wallet:",
                   layout="wide", initial_sidebar_state="collapsed")

# ─────────────────────────── 스타일 ───────────────────────────
st.markdown("""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css');
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800&display=swap');
:root{
  --ink:#0f172a; --ink2:#334155; --muted:#64748b; --faint:#94a3b8;
  --line:#e8ebf0; --surface:#f7f8fa; --bg:#ffffff; --accent:#2563eb;
  --up:#e5484d; --down:#2f6fed;
  --sell-bg:#fef1f1; --sell:#c42b2b; --take-bg:#ecfdf3; --take:#0f7b45;
  --add-bg:#eef4ff; --add:#1d4ed8; --warn-bg:#fff7e6; --warn:#a15c00;
  --hold-bg:#f1f3f6; --hold:#475569;
}
html, body, .stApp, [data-testid="stAppViewContainer"]{background:var(--bg)!important; color:var(--ink);}
html, body, .stApp, .stApp p, .stApp div, .stApp label, .stApp input, .stApp textarea, .stApp button, .stApp li, .stApp h1, .stApp h2, .stApp h3{
  font-family:"Pretendard Variable",Pretendard,"Noto Sans KR",-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif!important;
  letter-spacing:-0.01em; -webkit-font-smoothing:antialiased;}
[data-testid="stHeader"]{background:transparent;}
.block-container{padding-top:2.2rem; padding-bottom:4rem; max-width:1180px;}
h1,h2,h3,h4{letter-spacing:-0.02em; color:var(--ink);}

/* 탭 */
.stTabs [data-baseweb="tab-list"]{gap:4px; border-bottom:1px solid var(--line);}
.stTabs [data-baseweb="tab"]{padding:10px 14px; font-weight:600; color:var(--muted);}
.stTabs [aria-selected="true"]{color:var(--ink)!important;}
.stTabs [data-baseweb="tab-highlight"]{background:var(--ink)!important; height:2px;}
.stTabs [data-baseweb="tab-border"]{display:none;}

/* 입력 */
[data-baseweb="input"], [data-baseweb="select"]>div, [data-baseweb="textarea"]{
  background:#fff!important; border-radius:10px!important;}
[data-testid="stForm"], [data-testid="stVerticalBlockBorderWrapper"]{border-radius:14px;}
.stButton>button, .stFormSubmitButton>button, .stDownloadButton>button, .stLinkButton>a{
  border-radius:10px; font-weight:600;}

/* 공통 컴포넌트 */
.hd{display:flex; align-items:flex-end; justify-content:space-between; margin-bottom:4px;}
.hd .t{font-size:1.65rem; font-weight:800; letter-spacing:-0.03em; color:var(--ink);}
.hd .s{font-size:.85rem; color:var(--muted);}
.sec{font-size:1.05rem; font-weight:700; color:var(--ink); margin:18px 0 10px;}
.sub{font-size:.82rem; color:var(--muted); margin:-6px 0 10px;}

.chips{display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:14px 0 6px;}
.chip{border:1px solid var(--line); border-radius:12px; padding:10px 12px; background:#fff;}
.chip .n{font-size:.75rem; color:var(--muted); font-weight:600; display:flex; align-items:center; gap:6px;}
.chip .v{font-size:1.02rem; font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums;}
.chip .g{font-size:.75rem; margin-top:1px; font-variant-numeric:tabular-nums;}
.dot{width:7px; height:7px; border-radius:50%; display:inline-block;}
.on{background:#16a34a;} .off{background:#dc2626;}

.kpis{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:12px 0 4px;}
.kpis.k3{grid-template-columns:repeat(3,1fr);} .kpis.k2{grid-template-columns:repeat(2,1fr); margin-top:0;}
.kpi{background:var(--surface); border-radius:14px; padding:14px 16px;}
.kpi .l{font-size:.78rem; color:var(--muted); font-weight:600;}
.kpi .v{font-size:1.28rem; font-weight:800; margin-top:4px; letter-spacing:-0.02em; font-variant-numeric:tabular-nums;}
.kpi .d{font-size:.78rem; margin-top:2px; color:var(--muted); font-variant-numeric:tabular-nums;}

.up{color:var(--up)!important;} .down{color:var(--down)!important;} .mut{color:var(--muted);}

.notice{border-radius:12px; padding:12px 16px; font-size:.9rem; line-height:1.6; margin:10px 0;}
.notice.warn{background:var(--warn-bg); color:var(--warn);}
.notice.info{background:var(--surface); color:var(--ink2);}

.badge{display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px;
  font-size:.78rem; font-weight:700; white-space:nowrap;}
.badge::before{content:""; width:6px; height:6px; border-radius:50%; background:currentColor;}
.b-sell{background:var(--sell-bg); color:var(--sell);} .b-take{background:var(--take-bg); color:var(--take);}
.b-add{background:var(--add-bg); color:var(--add);} .b-warn{background:var(--warn-bg); color:var(--warn);}
.b-hold{background:var(--hold-bg); color:var(--hold);}

.list{border:1px solid var(--line); border-radius:14px; overflow:hidden; background:#fff; margin-top:8px;}
.row{padding:14px 16px; border-top:1px solid var(--line);}
.row:first-child{border-top:none;}
.row .top{display:flex; justify-content:space-between; align-items:center; gap:10px;}
.row .nm{font-weight:700; font-size:1rem; color:var(--ink);}
.row .cd{font-size:.75rem; color:var(--faint); margin-left:6px; font-weight:500;}
.row .nums{display:grid; grid-template-columns:repeat(4,1fr); gap:6px; margin-top:10px;}
.row .nums div{font-size:.72rem; color:var(--muted);}
.row .nums b{display:block; font-size:.95rem; color:var(--ink); font-weight:700; margin-top:1px; font-variant-numeric:tabular-nums;}
.row .why{font-size:.8rem; color:var(--muted); margin-top:8px;}

.title2{display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-top:6px;}
.title2 .nm{font-size:1.45rem; font-weight:800; letter-spacing:-0.03em;}
.meta{font-size:.8rem; color:var(--muted); margin:4px 0 6px;}

.callout{border-radius:14px; padding:14px 16px; margin:14px 0; background:var(--surface);}
.callout .h{font-size:.78rem; font-weight:700; color:var(--muted); margin-bottom:4px;}
.callout .b{font-size:.93rem; color:var(--ink); line-height:1.55;}
.callout ul{margin:4px 0 0 0; padding-left:18px;} .callout li{margin:2px 0;}

.checks{border:1px solid var(--line); border-radius:14px; overflow:hidden;}
.ck{display:grid; grid-template-columns:28px 120px 1fr; align-items:center; padding:10px 14px; border-top:1px solid var(--line); font-size:.88rem;}
.ck:first-child{border-top:none;}
.ck .k{font-weight:600; color:var(--ink2);} .ck .t{color:var(--muted); font-variant-numeric:tabular-nums;}
.mk{width:18px; height:18px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center; font-size:11px; font-weight:800;}
.mk.y{background:#dcfce7; color:#15803d;} .mk.n{background:#fee2e2; color:#b91c1c;} .mk.z{background:#f1f5f9; color:#94a3b8;}

.info2{display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:6px;}
.box{border:1px solid var(--line); border-radius:14px; padding:12px 14px; font-size:.86rem; color:var(--ink2); line-height:1.6;}
.box .h{font-size:.78rem; font-weight:700; color:var(--muted); margin-bottom:4px;}

/* 계좌 히어로 */
.hero{display:flex; flex-wrap:wrap; align-items:flex-end; gap:6px 28px; margin:16px 0 4px;}
.hero .big{font-size:2.1rem; font-weight:800; letter-spacing:-0.035em; font-variant-numeric:tabular-nums; line-height:1.1;}
.hero .lab{font-size:.8rem; color:var(--muted); font-weight:600; margin-bottom:4px;}
.hero .ret{font-size:1.25rem; font-weight:800; font-variant-numeric:tabular-nums;}
.hero .sm{font-size:.85rem; color:var(--muted); font-variant-numeric:tabular-nums;}

/* 판단 보드 */
.row{border-left:4px solid transparent;}
.row.l-sell{border-left-color:var(--sell); background:linear-gradient(90deg,#fff6f6,#fff 40%);}
.row.l-take{border-left-color:var(--take); background:linear-gradient(90deg,#f3fdf7,#fff 40%);}
.row.l-add{border-left-color:var(--add);} .row.l-warn{border-left-color:#f5a524;} .row.l-hold{border-left-color:#e2e8f0;}
.row .rule{margin-top:10px; padding:10px 12px; border-radius:10px; background:#fff; border:1px solid var(--line); font-size:.82rem; color:var(--ink2); line-height:1.55;}
.row .rule b{color:var(--ink);}
.badge.lg{font-size:.9rem; padding:6px 14px;}
.pill.need{background:#fff4e5; color:#b45309; box-shadow:inset 0 0 0 1px #fcd9a6;}
.pill{display:inline-block; font-size:.72rem; font-weight:700; color:var(--ink2); background:var(--surface); border-radius:6px; padding:2px 7px; margin-left:6px; vertical-align:2px;}

/* 판정 배너 */
.verdict{border-radius:18px; padding:20px 22px; margin:10px 0 14px; display:flex; gap:18px; align-items:center;}
.verdict .word{font-size:2rem; font-weight:900; letter-spacing:-0.04em; white-space:nowrap;}
.verdict .one{font-size:.95rem; line-height:1.55;}
.verdict .one .nm{font-weight:800; font-size:1.05rem;}
.v-sell{background:var(--sell-bg); color:var(--sell);} .v-take{background:var(--take-bg); color:var(--take);}
.v-add{background:var(--add-bg); color:var(--add);} .v-warn{background:var(--warn-bg); color:var(--warn);}
.v-hold{background:var(--hold-bg); color:var(--hold);}
.verdict .one > span{color:var(--ink2);}
.verdict .one .act{color:#fff;}

.rules{display:flex; flex-direction:column; gap:10px; margin-top:6px;}
.rc{border:1px solid var(--line); border-radius:14px; padding:14px 16px; background:#fff; border-left:4px solid var(--line);}
.rc.l-sell{border-left-color:var(--sell);} .rc.l-take{border-left-color:var(--take);} .rc.l-add{border-left-color:var(--add);}
.rc.l-warn{border-left-color:#f5a524;} .rc.l-hold{border-left-color:#cbd5e1;}
.rc .t{font-weight:800; font-size:.95rem; color:var(--ink);}
.rc .p{margin-top:6px; font-size:.86rem; color:var(--muted); line-height:1.6; padding-left:10px; border-left:2px solid var(--line);}
.rc .f{margin-top:8px; font-size:.9rem; color:var(--ink); line-height:1.55;}
.rc .f::before{content:"지금 "; font-weight:700; color:var(--muted); font-size:.78rem;}
.acts{margin-top:10px; display:flex; flex-wrap:wrap; gap:6px;}
.act{background:var(--ink); color:#fff; border-radius:8px; padding:6px 10px; font-size:.82rem; font-weight:600;}

/* 판단 보드 카드 */
.cards{display:flex; flex-direction:column; gap:12px; margin-top:8px;}
.card{background:#fff; border:1px solid var(--line); border-radius:16px; padding:18px 20px; border-left:5px solid #e2e8f0;}
.card.l-sell{border-left-color:#e5484d;} .card.l-take{border-left-color:#16a34a;} .card.l-add{border-left-color:#2563eb;}
.card.l-warn{border-left-color:#f59e0b;} .card.l-hold{border-left-color:#cbd5e1;}
.c-head{display:flex; justify-content:space-between; align-items:flex-start; gap:12px;}
.c-head .nm{font-size:1.15rem; font-weight:800; color:var(--ink); letter-spacing:-0.02em;}
.c-head .cd{font-size:.78rem; color:var(--faint); margin-left:8px; font-weight:500;}
.c-tags{margin-top:6px; display:flex; gap:6px; flex-wrap:wrap;}
.tag{font-size:.75rem; font-weight:700; padding:3px 9px; border-radius:7px; background:#f1f5f9; color:#475569;}
.tag.ok{background:#f0fdf4; color:#15803d;} .tag.need{background:#fff7ed; color:#c2410c;}
.sigbig{font-size:1rem; font-weight:800; padding:7px 16px; border-radius:999px; white-space:nowrap;}
.c-grid{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:16px;}
.c-grid label{display:block; font-size:.75rem; color:var(--muted); font-weight:600; margin-bottom:3px;}
.c-grid b{display:block; font-size:1.12rem; font-weight:800; color:var(--ink); font-variant-numeric:tabular-nums; letter-spacing:-0.02em;}
.c-grid small{display:block; font-size:.75rem; color:var(--muted); margin-top:1px; font-variant-numeric:tabular-nums;}
.rng{position:relative; height:34px; margin:16px 4px 2px;}
.rng .bar{position:absolute; top:13px; left:0; right:0; height:6px; border-radius:6px;
  background:linear-gradient(90deg,#fecaca 0%,#e2e8f0 var(--avg),#bbf7d0 100%);}
.rng .tk{position:absolute; top:24px; transform:translateX(-50%); font-size:.68rem; color:var(--muted); white-space:nowrap;}
.rng .tk.l{transform:none;} .rng .tk.r{transform:translateX(-100%);}
.rng .now{position:absolute; top:7px; width:18px; height:18px; border-radius:50%; background:var(--ink); border:3px solid #fff;
  box-shadow:0 0 0 1px var(--ink); transform:translateX(-50%);}
.rng .avg{position:absolute; top:9px; width:2px; height:14px; background:#64748b; transform:translateX(-50%);}
.c-why{margin-top:16px; padding:12px 14px; border-radius:12px; background:#f8fafc; font-size:.88rem; line-height:1.6; color:var(--ink2);}
.c-why .t{font-weight:800; color:var(--ink);}
.c-why .f{display:block; color:var(--muted); font-size:.82rem; margin-top:2px;}
@media (max-width:640px){
  .card{padding:16px;} .c-grid{grid-template-columns:repeat(2,1fr); gap:12px;}
  .c-head .nm{font-size:1.05rem;} .sigbig{font-size:.92rem; padding:6px 13px;}
}

/* 압축 판단 보드 (한 종목 한 줄) */
.board{border:1px solid var(--line); border-radius:14px; overflow:hidden; background:#fff; margin-top:8px;}
.brow{display:grid; grid-template-columns:4px 146px 84px 88px 108px minmax(150px,1fr) 186px 136px; column-gap:10px;
  align-items:center; gap:10px; padding:9px 14px 9px 0; border-top:1px solid var(--line); font-size:.88rem;}
.brow:first-child{border-top:none;}
.brow.bhead{font-size:.72rem; color:var(--muted); font-weight:700; background:#f8fafc; padding-top:7px; padding-bottom:7px;}
.brow .bar{align-self:stretch; margin:-9px 0; background:#e2e8f0;}
.brow.l-sell .bar{background:#e5484d;} .brow.l-take .bar{background:#16a34a;} .brow.l-add .bar{background:#2563eb;}
.brow.l-warn .bar{background:#f59e0b;}
.brow.l-sell{background:#fffafa;}
.bn b{font-weight:800; color:var(--ink); font-size:.95rem;}
.bn small{display:block; color:var(--faint); font-size:.7rem; margin-top:-1px;}
.bsig{justify-self:center; width:84px; text-align:center; box-sizing:border-box; font-size:.8rem; font-weight:800;
  padding:5px 6px; border-radius:999px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.bv{font-weight:700; color:var(--ink); font-variant-numeric:tabular-nums; white-space:nowrap;}
.bv small{display:block; font-size:.7rem; font-weight:600; color:var(--muted);}
.bv.up, .bv.down{color:inherit;} .bv.up{color:var(--up);} .bv.down{color:var(--down);}
.bv i{font-style:normal; color:#c2410c; margin-left:3px;}
.bchips{display:flex; flex-wrap:nowrap; gap:3px;}
.bchips.ma{display:grid; grid-template-columns:42px 40px 46px 52px; gap:2px;}
.bchips.fl{display:grid; grid-template-columns:1fr 1fr; gap:4px;}
.bchips .mchip, .bchips .fchip{text-align:center; display:block;}
.mslot{display:block;}
.bmeta{display:contents;}
.mchip, .fchip{font-size:.68rem; font-weight:700; padding:2px 5px; border-radius:6px; white-space:nowrap; font-variant-numeric:tabular-nums;}
.mchip.ok{background:#ecfdf3; color:#15803d;} .mchip.no{background:#fef2f2; color:#b91c1c;}
.mchip.strong{background:#15803d; color:#fff;}
.fchip{background:#f1f5f9; color:var(--ink2);} .fchip.up{background:#fef2f2; color:#c42b2b;} .fchip.down{background:#eff6ff; color:#1d4ed8;}
.mini{display:inline-block; white-space:nowrap; font-size:.66rem; font-weight:800; padding:1px 6px; border-radius:5px; margin-top:3px;}
.mini.ok{background:#f0fdf4; color:#15803d;}
.mini.r3{background:#16a34a; color:#fff;} .mini.r3past{background:#ecfdf3; color:#15803d;}
.brow.r3row{background:linear-gradient(90deg,#f0fdf4,#fff 60%);} .brow.r3row .bar{background:#16a34a;}
.rr-bot small.r3txt{color:#15803d; font-weight:800;}
.mini.scout{background:#f5f3ff; color:#6d28d9;} .mini.full{background:#eef2ff; color:#3730a3;}
.bn .tags{display:flex; flex-wrap:wrap; gap:3px; margin-top:3px;} .bn .tags .mini{margin-top:0;}
.bn{min-width:0;} .bn b{display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.mini.need{background:#fff7ed; color:#c2410c;}
.bnote{grid-column:2 / -1; font-size:.78rem; color:var(--ink2); margin-top:-2px;}
@media (min-width:761px) and (max-width:1120px){
  .brow{grid-template-columns:4px 140px 78px 82px 102px minmax(140px,1fr) 92px 84px;}
  .bchips.ma{grid-template-columns:44px 46px; row-gap:3px;}
  .bchips.fl{grid-template-columns:1fr; row-gap:3px;}
}
@media (max-width:760px){
  .brow.bhead{display:none;}
  .brow{grid-template-columns:4px 1fr auto; row-gap:6px; padding:10px 12px 10px 0;}
  .brow .bar{grid-row:1 / span 4;}
  .brow .bn{grid-column:2;} .brow .bsig{grid-column:3; justify-self:end;}
  .brow .bv{grid-column:auto; display:inline-block;}
  .brow > .bv{display:none;}
  .brow .bnote{grid-column:2 / 4;} .brow .nf{display:none;}
  .bmeta{display:grid; grid-template-columns:1fr; gap:6px; grid-column:2 / 4;}
  .bchips.ma{grid-template-columns:44px 42px 46px 52px;} .bchips.fl{grid-template-columns:1fr 1fr; max-width:240px;}
  .mrow{grid-column:2 / 4; display:grid; grid-template-columns:1fr 1fr; gap:8px 10px;}
  .mrow .rr{grid-column:1 / 3;}
  .mrow .rr-top b:first-child::before{content:"−1R 손절 "; font-size:.68rem; color:var(--muted); font-weight:700;}
  .mrow .rr-top b:last-child::before{content:"3R 익절 "; font-size:.68rem; color:var(--muted); font-weight:700;}
  .brow > .rr{display:none;}
}
.mrow{display:none;}
/* −1R ↔ 3R 칸 */
.rr{display:block; min-width:0;}
.rr-top, .rr-bot{display:flex; justify-content:space-between; gap:8px; font-variant-numeric:tabular-nums;}
.rr-top b{font-weight:800; color:var(--ink); font-size:.92rem;}
.rr-bot small{font-size:.7rem; font-weight:700; white-space:nowrap;}
.rr-bot small.down{color:var(--down);} .rr-bot small.up{color:var(--up);}
.rr-bot i{font-style:normal; color:#c2410c; margin-left:2px;}
.rr-bot b.lock{margin-left:4px; font-size:.68rem; font-weight:800; color:#15803d; background:#ecfdf3; padding:0 5px; border-radius:4px;}
.rr-bar{position:relative; display:block; height:6px; margin:5px 0 4px; border-radius:6px;
  background:linear-gradient(90deg,#bfdbfe,#e2e8f0 45%,#fecaca);}
.rr-bar i{position:absolute; top:50%; width:12px; height:12px; border-radius:50%; background:var(--ink);
  border:2px solid #fff; box-shadow:0 0 0 1px var(--ink); transform:translate(-50%,-50%);}
.rr-bar em{position:absolute; top:-3px; width:2px; height:12px; background:#64748b; transform:translateX(-50%);}
.rrh{display:flex; justify-content:space-between; gap:6px; white-space:nowrap;}
.bv small b{font-weight:800;} .bv small b.up{color:var(--up);} .bv small b.down{color:var(--down);}
@media (max-width:760px){ .mrow{display:grid;} }
.mrow .bv{font-size:.85rem;} .mrow .bv::before{content:attr(data-l); display:block; font-size:.66rem; color:var(--muted); font-weight:600;}

.upmark{font-style:normal; color:#c42b2b!important; font-weight:800;}
.qt-pv{display:flex; flex-wrap:wrap; gap:8px 22px; padding:12px 14px; margin:6px 0 8px; border-radius:12px; background:#f8fafc;
  font-size:.9rem; font-variant-numeric:tabular-nums; color:var(--ink2);}
.qt-pv label{display:block; font-size:.7rem; color:var(--muted); font-weight:700;}
.qt-pv b{color:var(--ink);} .qt-pv b.up{color:var(--up);} .qt-pv b.down{color:var(--down);}

/* 매매 미리보기 */
.pv{border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin:8px 0 10px; background:#fff;}
.pv-h{font-size:.8rem; font-weight:700; color:var(--muted); margin-bottom:6px;}
.pv table{width:100%; border-collapse:collapse; font-size:.9rem; font-variant-numeric:tabular-nums;}
.pv table, .pv th, .pv td{border:none!important;}
.pv th, .pv td{padding:7px 4px; border-top:1px solid var(--line)!important; text-align:right; background:none!important;}
.pv tr:first-child th{border-top:none; color:var(--muted); font-size:.78rem;}
.pv th:first-child{text-align:left; color:var(--ink2); font-weight:600;}

/* 사진 끌어다 놓기 칸 크게 */
.dropnote{font-size:.85rem; color:var(--ink2); margin:2px 0 8px;}
[data-testid="stFileUploaderDropzone"]{min-height:150px; border:2px dashed #cbd5e1!important; border-radius:14px!important;
  background:#f8fafc!important; display:flex; align-items:center; justify-content:center;}
[data-testid="stFileUploaderDropzone"]:hover{border-color:#2563eb!important; background:#eff6ff!important;}

.login{max-width:380px; margin:12vh auto 0;}
.login .t{font-size:1.6rem; font-weight:800; letter-spacing:-0.03em;}
.login .s{color:var(--muted); font-size:.9rem; margin:4px 0 18px;}

@media (max-width:640px){
  .block-container{padding-left:1rem; padding-right:1rem; padding-top:1.4rem;}
  .chips{grid-template-columns:repeat(2,1fr);} .kpis{grid-template-columns:repeat(2,1fr);}
  .row .nums{grid-template-columns:repeat(2,1fr);} .info2{grid-template-columns:1fr;}
  .ck{grid-template-columns:26px 100px 1fr;}
  .kpis.k3 .kpi{padding:12px;} .kpis.k3 .v{font-size:1.05rem;}
  .hd .t{font-size:1.4rem;}
  .hero .big{font-size:1.7rem;}
  .verdict{flex-direction:column; align-items:flex-start; gap:6px; padding:16px;}
  .verdict .word{font-size:1.6rem;}
}
</style>
""", unsafe_allow_html=True)

LEVEL_ORDER = {"sell": 0, "take": 1, "warn": 2, "add": 3, "hold": 4}
INDEX_SYMBOL = {"KOSPI": ("KR", "KOSPI"), "KOSDAQ": ("KR", "KOSDAQ"),
                "S&P500": ("US", "^GSPC"), "NASDAQ": ("US", "^IXIC")}


def html(s: str):
    st.markdown(s, unsafe_allow_html=True)


def badge(level: str, text: str) -> str:
    return f"<span class='badge b-{level}'>{escape(text)}</span>"


def sign_cls(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)) or v == 0:
        return ""
    return "up" if v > 0 else "down"


# ─────────────────────────── 잠금 ───────────────────────────
def device_key(pw: str) -> str:
    """비밀번호에서 만든 기기 열쇠. 비밀번호를 바꾸면 예전 열쇠는 자동으로 무효."""
    import hashlib
    return hashlib.sha256(("portfolio-device|" + pw).encode()).hexdigest()[:24]


def gate():
    pw = st.secrets.get("APP_PASSWORD", "")
    if not pw:
        st.error("Streamlit Secrets에 APP_PASSWORD가 없어요. 앱 Settings → Secrets에 넣어 주세요.")
        st.stop()
    if st.session_state.get("authed"):
        return
    # 휴대폰 홈 화면용: 주소에 기기 열쇠(?k=)가 있으면 비밀번호 없이 바로 열기
    k = st.query_params.get("k", "")
    if k and hmac.compare_digest(k, device_key(str(pw))):
        st.session_state.authed = True
        return
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        html("<div class='login'><div class='t'>내 계좌</div><div class='s'>보유 종목을 원칙대로 판단합니다</div></div>")
        if st.session_state.get("tries", 0) >= 5:
            st.error("비밀번호를 5번 틀렸어요. 잠시 뒤 새로 열어 주세요.")
            st.stop()
        with st.form("login", border=False):
            typed = st.text_input("비밀번호", type="password", label_visibility="collapsed",
                                  placeholder="비밀번호")
            remember = st.checkbox("이 기기에서 바로 열기 (홈 화면 바로가기용)", value=True)
            if st.form_submit_button("열기", type="primary", width="stretch"):
                if hmac.compare_digest(typed.encode(), str(pw).encode()):
                    st.session_state.authed = True
                    if remember:
                        st.query_params["k"] = device_key(str(pw))
                    st.rerun()
                st.session_state.tries = st.session_state.get("tries", 0) + 1
                st.error("비밀번호가 달라요.")
    st.stop()


gate()


# ─────────────────────────── 저장소 ───────────────────────────
# 저장소 객체는 접속(세션)마다 만들고, core.py가 바뀌면 새로 만든다.
# (예전엔 cache_resource로 서버 전체에 한 번만 만들어서, 코드를 고쳐 올려도 옛 저장 코드가 계속 돌았음)
if st.session_state.get("store_v") != core.STORE_VERSION:
    gh = dict(st.secrets["github"]) if "github" in st.secrets else None
    st.session_state.store = core.Store(gh)
    st.session_state.store_v = core.STORE_VERSION
    for k in ("save_err", "store_status"):
        st.session_state.pop(k, None)
store = st.session_state.store
AI = {"anthropic_key": st.secrets.get("ANTHROPIC_API_KEY", ""), "gemini_key": st.secrets.get("GEMINI_API_KEY", ""),
      "model": st.secrets.get("AI_MODEL", "")}
if "book" not in st.session_state:
    try:
        st.session_state.book = store.load()
    except Exception as e:
        st.error(f"저장된 데이터를 못 불러왔어요: {e}")
        st.stop()
book = st.session_state.book
S = book["settings"]

if "store_status" not in st.session_state:
    st.session_state.store_status = store.check()
STORE_OK, STORE_MSG = st.session_state.store_status


def save(msg: str, allow_empty: bool = False):
    try:
        store.save(book, msg, allow_empty=allow_empty)
        st.session_state.pop("save_err", None)
        if store.info.get("verified") is False:
            st.session_state.save_err = "저장은 했는데 다시 읽어 보니 종목 수가 달라요. 설정 탭 '저장 상태'를 확인해 주세요."
        st.toast("저장했어요", icon=":material/check_circle:")
    except Exception as e:
        st.session_state.save_err = f"{e}"   # 새로고침(rerun) 뒤에도 보이도록 남겨 둠


# ─────────────────────────── 데이터 (캐시) ───────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def hist(code: str, market: str):
    try:
        return core.fetch_kr(code) if market == "KR" else core.fetch_us(code)
    except Exception:
        return pd.DataFrame(), ""


@st.cache_data(ttl=300, show_spinner=False)
def regime(index_name: str):
    mkt, sym = INDEX_SYMBOL[index_name]
    df, _ = hist(sym, mkt)
    if df.empty or len(df) < 61:
        return None
    return core.market_regime(df, index_name)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fund(code: str, market: str):
    return core.fundamentals_kr(code) if market == "KR" else core.fundamentals_us(code)


@st.cache_data(ttl=1800, show_spinner=False)
def flows(code: str, market: str):
    return core.flows_kr(code) if market == "KR" else None


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def auto_sector(code: str, market: str):
    if market != "KR":
        return "미국 주식"
    return core.sector_kr(code)


@st.cache_data(ttl=1800, show_spinner=False)
def reports(code: str):
    return core.reports_kr(code)


@st.cache_data(ttl=600, show_spinner=False)
def news(code: str):
    return core.news_kr(code)


@st.cache_data(ttl=3600, show_spinner=False)
def fx_rate():
    return core.fetch_fx()


@st.cache_data(ttl=600, show_spinner=False)
def search(q: str):
    return core.search_kr(q)


@st.cache_data(ttl=300, show_spinner=False)
def equity_curve_cached(book_json: str, fx: float):
    b = json.loads(book_json)
    try:
        return core.equity_curve(b, hist, fx)
    except Exception:
        return None


def fmt(v, market="KR"):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{v:,.0f}" if market == "KR" else f"{v:,.2f}"


def won(v):
    return f"{v:,.0f}원"


def prefetch(open_h):
    """시세·실적·수급·지수를 종목별로 동시에 받아 캐시에 채워 둔다 (한 종목씩 받던 것보다 몇 배 빠름)."""
    from concurrent.futures import ThreadPoolExecutor
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
    ctx = get_script_run_ctx()
    jobs = [(fx_rate,)] + [(regime, n) for n in ("KOSPI", "KOSDAQ", "S&P500", "NASDAQ")]
    for h in open_h:
        jobs += [(hist, h["code"], h["market"]), (fund, h["code"], h["market"]), (flows, h["code"], h["market"])]

    def run(job):
        add_script_run_ctx(threading.current_thread(), ctx)
        try:
            job[0](*job[1:])
        except Exception:
            pass
    import threading
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(run, jobs))


def analyze():
    open_h = [h for h in book["holdings"] if core.position(h).qty > 0]
    if not st.session_state.get("_prefetched_at") or \
            (datetime.now() - st.session_state["_prefetched_at"]).seconds > 50:
        prefetch(open_h)
        st.session_state["_prefetched_at"] = datetime.now()
    FX = fx_rate() or S["fx"]
    realized_all = sum(core.position(h).realized * (FX if h["market"] == "US" else 1.0) for h in book["holdings"])
    pre = []
    unreal = 0.0
    for h in open_h:
        pos = core.position(h)
        df, _ = hist(h["code"], h["market"])
        fxm = FX if h["market"] == "US" else 1.0
        if not df.empty and len(df) >= 30:
            unreal += (float(df["Close"].iloc[-1]) - pos.avg) * pos.qty * fxm
        pre.append((h, pos, df, fxm))
    account = float(S["equity"]) + realized_all + unreal
    rows = []
    be_hits = []
    for h, pos, df, fxm in pre:
        if df.empty or len(df) < 30:
            rows.append({"h": h, "pos": pos, "ind": None, "j": None, "fx": fxm, "df": df})
            continue
        ind = core.indicators(df)
        hit3 = core.check_3r(h, df, S)               # 3R 익절가에 처음 닿은 날 기록
        if hit3:
            be_hits.append(f"🎯 {h['name']} {S['target_r']:g}R 달성 ({hit3[5:].replace('-', '.')})")
        be = core.check_breakeven(h, df, S)          # +2R 닿으면 손절가 → 본절 (자동, 한 번만)
        if be:
            be_hits.append(f"{h['name']} +{be['hit_r']}R 도달 · 손절가 {fmt(be['from'], h['market'])} → "
                           f"{be['label']} {fmt(be['to'], h['market'])}")
        reg = regime(h.get("index", "KOSPI"))
        f = fund(h["code"], h["market"])
        fund_ok = h["fund_override"] if h.get("fund_override") is not None else f.get("ok")
        fl = flows(h["code"], h["market"])
        j = core.judge(h, pos, ind, S, reg, fund_ok, fl, len(open_h), account)
        rows.append({"h": h, "pos": pos, "ind": ind, "j": j, "fx": fxm, "df": df,
                     "fund": f, "flows": fl, "reg": reg})
    if be_hits:
        save("breakeven: " + ", ".join(be_hits))
        st.session_state["flash"] = "자동 반영 — " + " · ".join(be_hits)
    return rows, FX, {"account": account, "realized": realized_all, "unreal": unreal}


def curve_chart(cv: pd.DataFrame, bench: pd.Series | None):
    fig = go.Figure()
    x, y = cv.index, cv["ret"]
    pos_y, neg_y = y.clip(lower=0), y.clip(upper=0)
    fig.add_trace(go.Scatter(x=x, y=pos_y, fill="tozeroy", mode="none", fillcolor="rgba(229,72,77,0.10)",
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=x, y=neg_y, fill="tozeroy", mode="none", fillcolor="rgba(47,111,237,0.10)",
                             hoverinfo="skip", showlegend=False))
    if bench is not None and len(bench):
        fig.add_trace(go.Scatter(x=bench.index, y=bench, mode="lines", name="KOSPI",
                                 line=dict(color="#94a3b8", width=1.5, dash="dot"),
                                 hovertemplate="KOSPI %{y:+.2f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="내 계좌", line=dict(color="#0f172a", width=2.2),
                             customdata=np.stack([cv["equity"], cv["dd"]], axis=1),
                             hovertemplate="내 계좌 %{y:+.2f}%<br>%{customdata[0]:,.0f}원 · 고점 대비 %{customdata[1]:.1f}%<extra></extra>"))
    last = y.iloc[-1]
    fig.add_annotation(x=x[-1], y=last, text=f"<b>{last:+.2f}%</b>", showarrow=False, xanchor="left", xshift=6,
                       font=dict(size=12, color="#e5484d" if last > 0 else "#2f6fed" if last < 0 else "#0f172a"))
    fig.add_hline(y=0, line_color="#cbd5e1", line_width=1)
    fig.update_layout(template="plotly_white", height=260, margin=dict(l=4, r=48, t=8, b=4),
                      hovermode="x unified", dragmode=False, paper_bgcolor="#fff", plot_bgcolor="#fff",
                      font=dict(family="Pretendard Variable, Pretendard, sans-serif", size=11, color="#64748b"),
                      legend=dict(orientation="h", y=1.12, x=0, font=dict(size=11)))
    fig.update_xaxes(showgrid=False, linecolor="#e8ebf0", tickformat="%m.%d")
    fig.update_yaxes(gridcolor="#f1f3f6", ticksuffix="%", side="right", zeroline=False)
    return fig


# ─────────────────────────── 머리 ───────────────────────────
top_l, top_r = st.columns([4, 1], vertical_alignment="bottom")
with top_l:
    html(f"<div class='hd'><div><div class='t'>내 계좌</div>"
         f"<div class='s'>{datetime.now():%Y.%m.%d %H:%M} 기준</div></div></div>")
with top_r:
    if st.button("새로고침", icon=":material/refresh:", width="stretch"):
        st.cache_data.clear()
        st.session_state.book = store.load()
        st.rerun()

if st.session_state.get("save_err"):
    html("<div class='notice warn' style='background:#fef1f1;color:#b42318'><b>마지막 저장이 실패했어요.</b>&nbsp;"
         f"{escape(st.session_state.save_err)} — 이 상태로는 앱을 다시 켜면 방금 기록이 사라져요.</div>")
if not STORE_OK:
    html(f"<div class='notice warn' style='background:#fef1f1;color:#b42318'><b>기록이 안전하게 저장되지 않고 있어요.</b>&nbsp;"
         f"{escape(STORE_MSG)} 설정 탭 맨 아래 '저장 상태'를 확인해 주세요.</div>")

tab_sum, tab_edit, tab_judge, tab_calc, tab_set = st.tabs(["요약", "기록", "판단", "계산기", "설정"])

with st.spinner("시세 불러오는 중..."):
    ROWS, FX, ACC = analyze()


# ─────────────────────────── 요약 ───────────────────────────
def atr_editor(rows):
    """종목마다 내가 직접 ATR%(또는 손절가)를 넣어야 '적용'으로 인정. 자동으로 채우지 않는다."""
    key = "atr_ed"
    status_lab = {"applied": "✓ 적용", "check": "⚠ 확인 필요", "missing": "⚠ 입력 필요"}
    base = []
    for r in rows:
        h, pos, ind = r["h"], r["pos"], r["ind"]
        stt = core.atr_status(h)
        ref = core.auto_atr(h, r["df"]) if r["df"] is not None and not r["df"].empty else None
        base.append({"id": h["id"], "상태": status_lab[stt], "종목": h["name"], "평단": round(pos.avg, 2),
                     "내 ATR %": float(h["atr_pct"]) if h.get("atr_pct") and stt != "missing" else None,
                     "직접 손절가": float(h["stop"]) if h.get("stop") else None,
                     "익절 기준가": float(h.get("target_base") or 0) or core.position(h).first_buy,
                     "적용": stt == "applied",
                     "참고: 진입일 ATR(20)": ref,
                     "참고: 지금 ATR(20)": round(ind["atr_pct"], 1) if ind and not np.isnan(ind["atr_pct"]) else None})
    df = pd.DataFrame(base)
    for c_ in ("평단", "내 ATR %", "직접 손절가", "익절 기준가", "참고: 진입일 ATR(20)", "참고: 지금 ATR(20)"):
        df[c_] = pd.to_numeric(df[c_], errors="coerce").astype(float)     # 빈칸이 'None' 대신 비어 보이게
    edits = (st.session_state.get(key) or {}).get("edited_rows", {})
    for i, ch in edits.items():
        for col, v in ch.items():
            if col in ("내 ATR %", "직접 손절가", "적용", "익절 기준가"):
                df.at[int(i), col] = v
            if col in ("내 ATR %", "직접 손절가") and v not in (None, ""):
                df.at[int(i), "적용"] = True           # 값을 직접 넣으면 적용으로 체크
    out = []
    for i, row in df.iterrows():
        h = next(r["h"] for r in rows if r["h"]["id"] == row["id"])
        atr = None if pd.isna(row["내 ATR %"]) else row["내 ATR %"]
        manual = None if pd.isna(row["직접 손절가"]) or not row["직접 손절가"] else float(row["직접 손절가"])
        changed = str(i) in edits or i in edits
        h_prev = {**h, "target_fixed": None, "stop_floor": None,
                  "target_base": row["익절 기준가"] if not pd.isna(row["익절 기준가"]) else None} if changed else h
        lv = core.levels(h_prev, row["평단"], S, atr, manual)
        if lv["implied_atr"] is not None:
            row = {**row, "내 ATR %": lv["implied_atr"]}
        applied = bool(row["적용"]) and (atr is not None or manual is not None)
        out.append({**row, "상태": status_lab["applied"] if applied else row["상태"],
                    "손절률": f"−{lv['stop_pct']:g}%" + ("" if applied else " (임시)"),
                    "손절가": lv["stop"], f"{S['target_r']:g}R 목표가": lv["target"]})
    view = pd.DataFrame(out)
    for c_ in ("내 ATR %", "직접 손절가", "손절가", f"{S['target_r']:g}R 목표가"):
        view[c_] = pd.to_numeric(view[c_], errors="coerce").astype(float)
    ed = st.data_editor(
        view, key=key, hide_index=True, width="stretch",
        column_order=["상태", "종목", "평단", "내 ATR %", "직접 손절가", "적용", "손절률", "손절가",
                      "익절 기준가", f"{S['target_r']:g}R 목표가", "참고: 진입일 ATR(20)", "참고: 지금 ATR(20)"],
        disabled=["상태", "종목", "평단", "손절률", "손절가", f"{S['target_r']:g}R 목표가",
                  "참고: 진입일 ATR(20)", "참고: 지금 ATR(20)"],
        column_config={
            "평단": st.column_config.NumberColumn(format="%,.0f"),
            "내 ATR %": st.column_config.NumberColumn(min_value=0.0, max_value=50.0, step=0.5, format="%.1f%%",
                                                     help=f"직접 입력. 손절률 = MAX({S['stop_pct']:g}%, 이 값)"),
            "직접 손절가": st.column_config.NumberColumn(min_value=0.0, format="%,.0f",
                                                     help="적으면 (평단−손절가)/평단 = ATR로 계산. 평단 이상이면 수익 보호용"),
            "적용": st.column_config.CheckboxColumn(help="'확인 필요' 종목은 값이 맞으면 체크하고 저장"),
            "익절 기준가": st.column_config.NumberColumn(min_value=0.0, format="%,.0f",
                                                     help="익절가 = 이 가격 × (1 + 3 × 손절률). 실제 첫 매수가로 고치면 익절가가 다시 정해져요"),
            "손절가": st.column_config.NumberColumn(format="%,.0f"),
            f"{S['target_r']:g}R 목표가": st.column_config.NumberColumn(format="%,.0f"),
            "참고: 진입일 ATR(20)": st.column_config.NumberColumn(format="%.1f%%", help="참고용 — 자동으로 적용되지 않아요"),
            "참고: 지금 ATR(20)": st.column_config.NumberColumn(format="%.1f%%", help="참고용 — 자동으로 적용되지 않아요"),
        })
    if st.button("ATR·손절가 저장", type="primary", icon=":material/save:", width="stretch"):
        for i, row in ed.iterrows():
            h = next(r["h"] for r in rows if r["h"]["id"] == row["id"])
            old = (h.get("atr_pct"), h.get("stop"), h.get("atr_src"), h.get("target_base"))
            nb = None if pd.isna(row["익절 기준가"]) or not row["익절 기준가"] else float(row["익절 기준가"])
            h["target_base"] = nb if nb and abs(nb - (core.position(h).first_buy or 0)) >= 0.5 else None
            new_stop = None if pd.isna(row["직접 손절가"]) or not row["직접 손절가"] else float(row["직접 손절가"])
            new_atr = None if pd.isna(row["내 ATR %"]) else round(float(row["내 ATR %"]), 2)
            imp = core.implied_atr(core.position(h).avg, new_stop)
            h["stop"] = new_stop
            h["atr_pct"] = imp if imp is not None else new_atr
            if bool(row["적용"]) and (h["atr_pct"] or new_stop):
                h["atr_src"] = "stop" if imp is not None else "manual"
            elif not h["atr_pct"] and not new_stop:
                h["atr_src"] = None
            if old != (h.get("atr_pct"), h.get("stop"), h.get("atr_src"), h.get("target_base")):
                h["stop_floor"] = None
                core.reset_target(h, S)
        st.session_state.pop(key, None)
        save("atr settings")
        st.rerun()


SHORT = {
    "stop": "손절가 이탈 → 전량 매도 (손실은 짧게)",
    "exit_rest": "절반 익절 후 5일선이 50일선에 닿음 → 나머지 매도",
    "take_half": "3R 익절가 도달 → 절반 매도, 손절가는 본전으로",
    "climax": "신고가 대량 윗꼬리 · 고점 −10% 대량 음봉 → 비중 축소",
    "trend": "속도에 맞는 이평선 이탈 → 종가로 2~3일 확인",
    "breakeven": "+2R 본절 · +4R → +1R · +5R → +2R 잠금",
    "protect": "올려 둔 손절가(수익 잠금) 이탈 → 남은 물량 정리",
    "add": "수익 중 신고가 돌파 + 거래량 → 1유닛 추가 (물타기 금지)",
    "market": "지수가 60일선 아래 → 신규·추가 매수 쉬기",
    "fund": "영업이익이 늘지 않으면 배제",
    "rs": "종목 RS가 지수보다 위여야 함",
    "hold": "손절가 위 · 추세 유지 → 그대로 보유",
}


def range_bar(price, avg, stop, target, mk) -> str:
    lo, hi = min(stop, price), max(target, price)
    if hi <= lo:
        return ""
    pos = lambda v: max(0.0, min(100.0, (v - lo) / (hi - lo) * 100))
    return (f"<div class='rng' style='--avg:{pos(avg):.1f}%'><div class='bar'></div>"
            f"<div class='avg' style='left:{pos(avg):.1f}%'></div>"
            f"<div class='now' style='left:{pos(price):.1f}%' title='현재가'></div>"
            f"<div class='tk l' style='left:0'>손절 {fmt(stop, mk)}</div>"
            f"<div class='tk' style='left:{pos(avg):.1f}%'>평단</div>"
            f"<div class='tk r' style='left:100%'>익절 {fmt(target, mk)}</div></div>")


SECTOR_ALIAS = {   # 네이버 업종명을 짧게
    "반도체와반도체장비": "반도체", "전자장비와기기": "전자장비", "디스플레이장비및부품": "디스플레이",
    "IT서비스": "IT서비스", "전기장비": "전기장비", "전기제품": "전기제품", "우주항공과국방": "방산·우주",
    "화장품": "화장품", "건강관리장비와용품": "의료기기", "생물공학": "바이오", "제약": "제약",
    "기계": "기계", "조선": "조선", "건설": "건설", "화학": "화학", "철강": "철강",
    "에너지장비및서비스": "에너지장비", "석유와가스": "정유", "자동차부품": "자동차부품", "자동차": "자동차",
    "소프트웨어": "소프트웨어", "통신장비": "통신장비", "핸드셋": "휴대폰", "식품": "식품", "음료": "음료",
}


def sector_of(h) -> str:
    if h.get("sector"):
        return h["sector"]
    s_ = auto_sector(h["code"], h["market"])
    return SECTOR_ALIAS.get(s_, s_) if s_ else "미분류"


def sector_section(rows):
    """계좌 안 섹터 비중 — 평가금액 기준 가로 막대."""
    rows = [r for r in rows if r["ind"]]
    if not rows:
        return
    agg = {}
    for r in rows:
        v = r["ind"]["price"] * r["pos"].qty * r["fx"]
        sec = sector_of(r["h"])
        a = agg.setdefault(sec, {"v": 0.0, "pl": 0.0, "names": []})
        a["v"] += v
        a["pl"] += (r["ind"]["price"] - r["pos"].avg) * r["pos"].qty * r["fx"]
        a["names"].append((r["h"]["name"], v))
    total = sum(a["v"] for a in agg.values())
    acc = ACC["account"]
    items = sorted(agg.items(), key=lambda kv: kv[1]["v"])          # 가로 막대: 큰 게 위로
    html("<div class='sec'>섹터 비중</div>"
         f"<div class='sub'>보유 종목 평가금액 기준 · 주식 {won(total)} = 계좌의 {total / acc * 100:.0f}%</div>")
    top_sec, top = items[-1]
    html("<div class='kpis k3'>"
         f"<div class='kpi'><div class='l'>가장 큰 섹터</div><div class='v'>{escape(top_sec)}</div>"
         f"<div class='d'>주식의 {top['v'] / total * 100:.1f}% · 계좌의 {top['v'] / acc * 100:.1f}%</div></div>"
         f"<div class='kpi'><div class='l'>섹터 수</div><div class='v'>{len(agg)}개</div>"
         f"<div class='d'>{len(rows)}종목</div></div>"
         f"<div class='kpi'><div class='l'>상위 2개 섹터 합</div>"
         f"<div class='v'>{sum(a['v'] for _, a in items[-2:]) / total * 100:.0f}%</div><div class='d'>주식 중 비중</div></div>"
         "</div>")
    ys = [k for k, _ in items]
    xs = [a["v"] / total * 100 for _, a in items]
    cols = ["#2563eb" if i == len(items) - 1 else "#93c5fd" if i >= len(items) - 3 else "#cbd5e1" for i in range(len(items))]
    hover = []
    for k, a in items:
        lst = "<br>".join(f"· {n} {v / total * 100:.1f}%" for n, v in sorted(a["names"], key=lambda x: -x[1]))
        hover.append(f"<b>{k}</b> {a['v'] / total * 100:.1f}% ({a['v'] / 1e4:,.0f}만원)<br>평가손익 {a['pl']:+,.0f}원<br>{lst}")
    labels = [f"{x:.1f}%  ·  {len(a['names'])}종목" for x, (_, a) in zip(xs, items)]
    fig = go.Figure(go.Bar(x=xs, y=ys, orientation="h", marker=dict(color=cols, cornerradius=6),
                           text=labels, textposition="outside", cliponaxis=False,
                           textfont=dict(size=12, color="#334155"),
                           hovertext=hover, hoverinfo="text"))
    fig.update_layout(template="plotly_white", height=max(180, 44 * len(items) + 40), bargap=0.35,
                      margin=dict(l=4, r=90, t=6, b=6), paper_bgcolor="#fff", plot_bgcolor="#fff", dragmode=False,
                      font=dict(family="Pretendard Variable, Pretendard, Noto Sans KR, sans-serif", size=13, color="#0f172a"),
                      hoverlabel=dict(bgcolor="#fff", font_size=12, bordercolor="#e2e8f0"))
    fig.update_xaxes(visible=False, range=[0, max(xs) * 1.05])
    fig.update_yaxes(showgrid=False, ticks="", ticklabelstandoff=8, tickfont=dict(size=13, color="#0f172a"))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    # 섹터별 종목 칩
    chips = []
    for k, a in reversed(items):
        nm = " ".join(f"<span class='fchip'>{escape(n)} {v / total * 100:.0f}%</span>"
                      for n, v in sorted(a["names"], key=lambda x: -x[1]))
        chips.append(f"<div class='ck' style='grid-template-columns:110px 1fr'><span class='k'>{escape(k)}</span>"
                     f"<span class='bchips' style='flex-wrap:wrap'>{nm}</span></div>")
    with st.expander("섹터별 종목 보기", icon=":material/category:"):
        html("<div class='checks'>" + "".join(chips) + "</div>")
        st.caption("섹터는 네이버 업종 분류를 자동으로 가져와요. 바꾸고 싶으면 기록 → 판단 기준의 '섹터'에 적으면 그 이름이 우선이에요.")


def raised(h) -> str:
    """최근 7일 안에 추가 매수로 손절가가 올라갔으면 표시."""
    log = [x for x in (h.get("stop_log") or []) if x.get("from") is not None and x.get("to") is not None]
    if not log:
        return ""
    x = log[-1]
    try:
        recent = (date.today() - date.fromisoformat(x["date"])).days <= 7
    except Exception:
        recent = False
    return "<i class='upmark'> ▲상향</i>" if recent and x.get("to", 0) > x.get("from", 0) else ""


def atr_popovers(need_rows):
    """ATR 입력이 필요한 종목마다 버튼 → 눌러서 직접 입력. 저장하면 그 버튼은 사라진다."""
    if not need_rows:
        return
    html(f"<div class='notice warn'><b>ATR 입력이 필요한 종목 {len(need_rows)}개</b> — 지금은 임시로 기본 8%로 계산 중이에요. "
         "아래 버튼을 눌러 직접 입력하면 그 종목은 목록에서 사라져요.</div>")
    cols = st.columns(min(4, len(need_rows)))
    for i, r in enumerate(need_rows):
        h, pos = r["h"], r["pos"]
        mk = h["market"]
        with cols[i % len(cols)]:
            with st.popover(f"ATR 입력 · {h['name']}", icon=":material/edit:", width="stretch"):
                ref_e = core.auto_atr(h, r["df"]) if r["df"] is not None and not r["df"].empty else None
                ref_n = r["ind"]["atr_pct"] if r["ind"] else None
                html(f"<div class='sub' style='margin:0 0 6px'><b>{escape(h['name'])}</b> · 평단 {fmt(pos.avg, mk)} · {pos.qty:,.0f}주<br>"
                     f"참고 ATR(20): 진입일 {f'{ref_e:g}%' if ref_e else '-'} · 지금 {f'{ref_n:.1f}%' if ref_n and not np.isnan(ref_n) else '-'}</div>")
                with st.form(f"atrpop_{h['id']}", border=False):
                    a_in = st.number_input("내 ATR %", min_value=0.0, max_value=50.0, value=None, step=0.5, format="%.1f",
                                           placeholder="예: 10")
                    s_in = st.number_input("또는 손절가 직접", min_value=0.0, value=None, format="%.2f",
                                           placeholder="적으면 ATR 자동 계산")
                    st.caption(f"손절률 = MAX({S['stop_pct']:g}%, ATR) · 익절가 = 첫 매수가 × (1 + {S['target_r']:g} × 손절률)")
                    if st.form_submit_button("저장", type="primary", width="stretch"):
                        if not a_in and not s_in:
                            st.error("ATR이나 손절가 중 하나를 넣어 주세요.")
                        else:
                            imp = core.implied_atr(pos.avg, s_in) if s_in else None
                            h["stop"] = float(s_in) if s_in else None
                            h["atr_pct"] = imp if imp is not None else (round(float(a_in), 2) if a_in else None)
                            h["atr_src"] = "stop" if (imp is not None or (s_in and not a_in)) else "manual"
                            h["stop_floor"] = None
                            core.reset_target(h, S)
                            lv = core.levels(h, pos.avg, S)
                            save(f"atr {h['code']}")
                            st.session_state["flash"] = (f"{h['name']} ATR 적용 — 손절가 {fmt(lv['stop'], mk)} · "
                                                         f"익절가 {fmt(lv['target'], mk)}")
                            st.rerun()


def quick_trade(rows):
    """보드 바로 아래에서 추가 매수·매도 기록. 넣는 즉시 손절가(새 평단 기준)·익절가(고정)를 보여준다."""
    if not rows:
        return
    html("<div class='sec'>빠른 매매 기록</div>"
         "<div class='sub'>추가 매수를 넣으면 익절가는 그대로, 손절가는 새 평단 기준으로 올라가요</div>")
    nonce = st.session_state.get("qt_nonce", 0)
    with st.container(border=True):
        names = {r["h"]["id"]: f"{r['h']['name']} · {r['pos'].qty:,.0f}주" for r in rows}
        a, b, c, d = st.columns([1.6, 1.1, 1, 0.8], vertical_alignment="bottom")
        hid = a.selectbox("종목", list(names), format_func=names.get, key=f"qt_h_{nonce}")
        side = b.segmented_control("구분", ["추가 매수", "매도", "잔고 맞추기"], default="추가 매수", key=f"qt_s_{nonce}")
        fix = side == "잔고 맞추기"
        price = c.number_input("실제 평단" if fix else "단가", min_value=0.0, value=None, placeholder="0", format="%.2f",
                               key=f"qt_p_{nonce}")
        qty = d.number_input("실제 수량" if fix else "수량", min_value=0.0, value=None, placeholder="0", step=1.0,
                             format="%.0f", key=f"qt_q_{nonce}")
        r = next(x for x in rows if x["h"]["id"] == hid)
        h, pos = r["h"], r["pos"]
        mk = h["market"]
        side_k = "sell" if side == "매도" else "buy"
        if fix:
            st.caption(f"앱 기록: {pos.qty:,.0f}주 · 평단 {fmt(pos.avg, mk)} — 증권사 앱의 실제 보유수량과 평단을 넣으면 "
                       "차이만큼 추가 매수/매도로 기록해서 맞춰요")
            if price and qty is not None:
                plan_ = core.reconcile({"holdings": [h]}, [{"name": h["name"], "code": h["code"], "qty": float(qty),
                                                            "avg_price": float(price), "price": r["ind"]["price"]}])
                p_ = plan_[0] if plan_ else None
                if not p_ or not p_["side"] or not p_["trade_qty"]:
                    html("<div class='notice info'>앱 기록과 같아요. 맞출 게 없어요.</div>")
                    price = qty = None
                else:
                    side_k, price, qty = p_["side"], float(p_["trade_price"]), float(p_["trade_qty"])
                    html(f"<div class='notice info'>맞추기: <b>{escape(p_['action'])}</b> @ {fmt(price, mk)} 로 기록돼요</div>")
        if price and qty:
            if side_k == "sell" and qty > pos.qty:
                html(f"<div class='notice warn'>보유 수량({pos.qty:,.0f}주)보다 많이 팔 수 없어요.</div>")
            else:
                pv = core.trade_preview(h, side_k, float(price), float(qty), S, ACC["account"])
                b0, a1 = pv["before"], pv["after"]
                arrow = lambda x0, x1: ("<b class='up'>▲</b>" if x1 > x0 + 1e-9 else "<b class='down'>▼</b>" if x1 < x0 - 1e-9 else "")
                html("<div class='qt-pv'>"
                     f"<span><label>평단</label>{fmt(b0['avg'], mk)} → <b>{fmt(a1['avg'], mk)}</b></span>"
                     f"<span><label>손절가</label>{fmt(b0['stop'], mk)} → <b>{fmt(a1['stop'], mk)}</b> {arrow(b0['stop'], a1['stop'])}</span>"
                     f"<span><label>익절가 (고정)</label><b>{fmt(a1['target'], mk)}</b> 그대로</span>"
                     + (f"<span><label>손익비</label>{b0['rr']:.1f} → <b>{a1['rr']:.1f}</b> : 1</span>" if b0["rr"] and a1["rr"] else "")
                     + (f"<span><label>손절 시 계좌</label><b>−{a1['loss_pct']:.2f}%</b></span>" if a1["loss_pct"] is not None else "")
                     + "</div>"
                     + "".join(f"<div class='notice warn'>{escape(w)}</div>" for w in pv["warn"])
                     + "".join(f"<div class='notice info'>{escape(i)}</div>" for i in pv["info"]))
        if st.button("기록하기", type="primary", width="stretch", key=f"qt_go_{nonce}"):
            if not price or not qty:
                st.error("단가와 수량을 넣어 주세요.")
            elif side_k == "sell" and qty > pos.qty:
                st.error(f"보유 수량({pos.qty:,.0f}주)보다 많이 팔 수 없어요.")
            else:
                pv = core.apply_trade(h, side_k, date.today().isoformat(), float(price), float(qty), "", S, ACC["account"])
                save(f"trade {h['code']}")
                st.session_state["flash"] = (f"{h['name']} {'추가 매수' if side_k == 'buy' else '매도'} 기록 — 손절가 "
                                             f"{fmt(pv['before']['stop'], mk)} → {fmt(pv['after']['stop'], mk)} · "
                                             f"익절가 {fmt(pv['after']['target'], mk)} 그대로")
                st.session_state["qt_nonce"] = nonce + 1
                st.rerun()


def rule_line(j) -> str:
    r0 = j["rules"][0] if j["rules"] else None
    if not r0:
        return ""
    if j["level"] in ("sell", "take"):
        return (f"<div class='rule'><b>{escape(r0['title'])}</b> — {escape(r0['principle'])}<br>"
                f"<span class='mut'>지금</span> {escape(r0['fact'])}</div>")
    return f"<div class='why'>{escape(r0['fact'])}</div>"


with tab_sum:
    if st.session_state.get("flash"):
        html(f"<div class='notice' style='background:#ecfdf3;color:#067647'><b>✓</b> {escape(st.session_state.pop('flash'))}</div>")
    acc, init = ACC["account"], float(S["equity"])
    tot_ret = (acc / init - 1) * 100 if init else 0
    cv = equity_curve_cached(json.dumps(book, ensure_ascii=False, sort_keys=True), FX)
    day_chg = mdd = None
    if cv is not None and len(cv) > 1:
        day_chg = cv["equity"].iloc[-1] - cv["equity"].iloc[-2]
        mdd = cv["dd"].min()
    html("<div class='hero'>"
         f"<div><div class='lab'>계좌 총액</div><div class='big'>{won(acc)}</div></div>"
         f"<div><div class='lab'>누적 수익률</div><div class='ret {sign_cls(tot_ret)}'>{tot_ret:+.2f}%</div></div>"
         f"<div><div class='lab'>손익</div><div class='ret {sign_cls(acc - init)}'>{acc - init:+,.0f}원</div></div>"
         + (f"<div><div class='lab'>오늘</div><div class='ret {sign_cls(day_chg)}'>{day_chg:+,.0f}원</div></div>"
            if day_chg is not None else "")
         + f"<div class='sm'>초기 자본금 {won(init)}"
         + (f" · 증권사 총자산 {won(book['snapshots'][-1]['total_asset'])} ({book['snapshots'][-1]['date'][5:].replace('-', '.')} 사진)"
            if book.get("snapshots") and book["snapshots"][-1].get("total_asset") else "")
         + (f" · 예수금 {won(book['snapshots'][-1]['cash'])}"
            if book.get("snapshots") and book["snapshots"][-1].get("cash") else "")
         + (f" · 최대 낙폭 {mdd:.1f}%" if mdd is not None else "") + "</div></div>")

    c_board, c_chart, c_sec, c_idx = st.container(), st.container(), st.container(), st.container()
    with c_sec:
        sector_section(ROWS)
    with c_chart:
        html("<div class='sec'>계좌 수익률</div>")
        if cv is not None and len(cv) > 1:
            rng = st.segmented_control("기간", ["1개월", "3개월", "전체"], default="전체", key="cv_rng",
                                       label_visibility="collapsed")
            days = {"1개월": 22, "3개월": 66}.get(rng)
            cvv = cv.tail(days + 1) if days else cv
            kdf, _ = hist("KOSPI", "KR")
            bench = None
            if not kdf.empty:
                k = kdf["Close"].reindex(cvv.index, method="ffill").dropna()
                if len(k):
                    bench = (k / k.iloc[0] - 1) * 100 + cvv["ret"].iloc[0]
            st.plotly_chart(curve_chart(cvv, bench), width="stretch", config={"displayModeBar": False})
            st.caption("계좌 총액 = 초기 자본금 + 실현손익 + 평가손익 (엑셀 계좌일지의 누적잔고와 같은 계산). 점선은 같은 기간 KOSPI.")
        else:
            html("<div class='notice info'>매매를 기록하면 계좌 수익률 그래프가 여기에 그려져요. "
                 "설정 탭에서 초기 자본금을 먼저 넣어 주세요.</div>")
    with c_idx:
        html("<div class='sec'>시장</div>")
        chips = []
        for name in ("KOSPI", "KOSDAQ", "S&P500", "NASDAQ"):
            r = regime(name)
            if r:
                chips.append(f"<div class='chip'><div class='n'><span class='dot {'on' if r['above'] else 'off'}'></span>{name}</div>"
                             f"<div class='v'>{r['price']:,.2f}</div>"
                             f"<div class='g {sign_cls(r['gap'])}'>60일선 {r['gap']:+.1f}%</div></div>")
            else:
                chips.append(f"<div class='chip'><div class='n'>{name}</div><div class='v mut'>-</div></div>")
        html("<div class='chips'>" + "".join(chips) + "</div>")
        kr_rest = [n for n in ("KOSPI", "KOSDAQ") if (r := regime(n)) and not r["above"]]
        if kr_rest:
            html(f"<div class='notice warn'>{' · '.join(kr_rest)} 지수가 60일선 아래예요. 원칙상 신규 매수는 쉬는 구간입니다.</div>")
    with c_board:
        if not ROWS:
            html("<div class='notice info'>아직 보유 종목이 없어요. 기록 탭에서 추가하거나, 설정 탭에서 엑셀 계좌일지를 가져오세요.</div>")
        else:
            cost = sum(r["pos"].avg * r["pos"].qty * r["fx"] for r in ROWS)
            risk = sum(max(0.0, (r["pos"].avg - r["j"]["stop"])) * r["pos"].qty * r["fx"] for r in ROWS if r["j"])
            n_act = sum(1 for r in ROWS if r["j"] and r["j"]["level"] in ("sell", "take"))
            html("<div class='kpis'>"
                 f"<div class='kpi'><div class='l'>지금 대응할 종목</div><div class='v {'up' if n_act else ''}'>{n_act}개</div>"
                 f"<div class='d'>손절·청산·익절 신호</div></div>"
                 f"<div class='kpi'><div class='l'>주식 비중</div><div class='v'>{cost / acc * 100:.0f}%</div>"
                 f"<div class='d'>매입 {won(cost)}</div></div>"
                 f"<div class='kpi'><div class='l'>걸린 위험</div><div class='v'>{risk / acc * 100:.2f}%</div>"
                 f"<div class='d'>전부 손절 시 계좌 손실</div></div>"
                 f"<div class='kpi'><div class='l'>보유 종목</div><div class='v'>{len(ROWS)} / {S['max_positions']}</div>"
                 f"<div class='d'>1R = {won(acc * S['risk_pct'] / 100)}</div></div>"
                 "</div>")

            atr_popovers([r for r in ROWS if core.atr_status(r["h"]) != "applied"])
            html("<div class='sec'>판단 보드</div><div class='sub'>손절·청산이 맨 위에, 근거가 된 내 원칙과 함께 나와요</div>")
            items = sorted(ROWS, key=lambda r: LEVEL_ORDER[r["j"]["level"]] if r["j"] else 9)
            out = ["<div class='brow bhead'><span></span><span>종목</span><span style='text-align:center'>판정</span><span>현재가</span>"
                   "<span>평단 · 수익률</span><span class='rrh'><span>손절가 (−1R · 잠금)</span><span>3R 익절가</span></span>"
                   "<span>이동평균선</span><span>수급 5일 (외·기)</span></div>"]
            for r in items:
                h, pos, ind, j = r["h"], r["pos"], r["ind"], r["j"]
                mk = h["market"]
                stt = core.atr_status(h)
                sp_now = core.stop_pct_of(h, S)
                if stt == "applied" and h.get("atr_pct"):
                    a_ = float(h["atr_pct"])
                    atr_lab = (f"<span class='mini ok'>ATR {a_:g}%</span>" if a_ >= S["stop_pct"]
                               else f"<span class='mini ok'>ATR {a_:g}% → {S['stop_pct']:g}%</span>")
                elif stt == "applied":
                    atr_lab = "<span class='mini ok'>손절가 직접</span>"
                elif stt == "check":
                    atr_lab = "<span class='mini need'>ATR 확인</span>"
                else:
                    atr_lab = "<span class='mini need' title='ATR을 넣기 전이라 임시로 기본 손절폭 적용 중'>ATR 입력</span>"
                # 비중: 계좌 대비 이 종목 매입금액 비중. 1R(1유닛) = 계좌의 7~8% (설정값). 그보다 작으면 정찰병
                unit = float(S.get("unit_pct", 7.5))
                wgt = pos.avg * pos.qty * r["fx"] / ACC["account"] * 100 if ACC["account"] else 0
                units = wgt / unit if unit else 0
                if units < 0.85:
                    size_lab = f"<span class='mini scout' title='계좌의 {wgt:.1f}% (1R = {unit:g}%)'>정찰병 {wgt:.1f}%</span>"
                else:
                    u_lab = f"{round(units * 2) / 2:g}R"
                    size_lab = f"<span class='mini full' title='계좌의 {wgt:.1f}% (1R = {unit:g}%)'>{u_lab} · {wgt:.1f}%</span>"
                r3_now = ind is not None and ind["price"] >= j["target"] if j else False
                r3_lab = ""
                if r3_now:
                    r3_lab = f"<span class='mini r3'>🎯 {S['target_r']:g}R 달성</span>"
                elif h.get("r3_hit"):
                    r3_lab = (f"<span class='mini r3past' title='{h['r3_hit']}에 익절가 도달'>"
                              f"{S['target_r']:g}R 달성 {h['r3_hit'][5:].replace('-', '.')}</span>")
                nm_e = escape(h['name'])
                name = (f"<span class='bn' title='{nm_e}'><b>{nm_e}</b>"
                        f"<small>{escape(h['code'])}</small><span class='tags'>{r3_lab}{size_lab}{atr_lab}</span></span>")
                if not ind:
                    out.append(f"<div class='brow l-warn'><span class='bar'></span>{name}"
                               "<span class='bsig b-warn'>시세 없음</span><span class='bwide mut'>코드를 확인해 주세요</span></div>")
                    continue
                ma = [("5", "20", ind["ma5"], ind["ma20"]), ("20", "60", ind["ma20"], ind["ma60"]),
                      ("60", "120", ind["ma60"], ind["ma120"])]
                # 칸 고정: [정배열][5·20][20·60][60·120] — 없으면 빈칸으로 자리만 지킴
                ma_html = ("<span class='mchip ok strong'>정배열</span>" if ind["aligned"] else "<span class='mslot'></span>")
                ma_html += "".join(
                    (f"<span class='mchip {'ok' if a_ > b_ else 'no'}'>{x}{'>' if a_ > b_ else '<'}{y}</span>"
                     if not (np.isnan(a_) or np.isnan(b_)) else "<span class='mslot'></span>")
                    for x, y, a_, b_ in ma)
                fl = r.get("flows") or {}
                if fl.get("ok"):
                    def won_b(q):
                        v = q * ind["price"] * r["fx"] / 1e8
                        return f"{v:+,.0f}억" if abs(v) >= 1 else f"{v * 1e4:+,.0f}만"
                    fl_html = (f"<span class='fchip {sign_cls(fl['frgn5'])}'>외 {won_b(fl['frgn5'])}</span>"
                               f"<span class='fchip {sign_cls(fl['inst5'])}'>기 {won_b(fl['inst5'])}</span>")
                else:
                    fl_html = "<span class='fchip'>외 -</span><span class='fchip'>기 -</span>"
                tmp = "" if stt == "applied" else "<i>임시</i>"
                if h.get("stop_floor") and h.get("stop_floor_note") and j["stop"] >= pos.avg:
                    tmp = f"<b class='lock'>🔒{escape(h['stop_floor_note'].split(' ')[0])}</b>"
                note = ""
                if j["level"] in ("sell", "take") and j["rules"]:
                    note = (f"<span class='bnote'>{escape(SHORT.get(j['rules'][0]['key'], ''))}"
                            f"<span class='nf'> — {escape(j['rules'][0]['fact'])}</span></span>")
                px, st_, tg = ind["price"], j["stop"], j["target"]
                ce = core.calc_explain(h, pos.avg, S)
                to_stop = (st_ / px - 1) * 100            # 현재가 → −1R 손절가까지
                to_tgt = (tg / px - 1) * 100              # 현재가 → 3R 익절가까지
                lo_, hi_ = min(st_, px), max(tg, px)
                pct = lambda v: max(0.0, min(100.0, (v - lo_) / (hi_ - lo_) * 100)) if hi_ > lo_ else 50.0
                rr_cell = (
                    "<span class='rr'>"
                    f"<span class='rr-top'><b title='{escape(ce['stop'])}'>{fmt(st_, mk)}</b>"
                    f"<b title='{escape(ce['target'])}'>{fmt(tg, mk)}</b></span>"
                    f"<span class='rr-bar'><em style='left:{pct(pos.avg):.1f}%' title='평단'></em>"
                    f"<i style='left:{pct(px):.1f}%' title='현재가'></i></span>"
                    f"<span class='rr-bot'><small class='down'>{'이탈' if to_stop >= 0 else f'{to_stop:.1f}%'}{tmp}{raised(h)}</small>"
                    + (f"<small class='r3txt'>🎯 달성 {abs(to_tgt):.1f}% 초과</small>" if to_tgt <= 0 else f"<small class='up'>+{to_tgt:.1f}%</small>")
                    + "</span></span>")
                vals = (f"<span class='bv' data-l='현재가'>{fmt(px, mk)}<small class='{sign_cls(ind['chg'])}'>{ind['chg']:+.1f}%</small></span>"
                        f"<span class='bv' data-l='평단 · 수익률'>{fmt(pos.avg, mk)}"
                        f"<small><b class='{sign_cls(j['pnl_pct'])}'>{j['pnl_pct']:+.1f}%</b> · {j['r_mult']:+.1f}R</small></span>"
                        f"{rr_cell}")
                out.append(
                    f"<div class='brow l-{j['level']}{' r3row' if r3_now else ''}'><span class='bar'></span>{name}"
                    f"<span class='bsig b-{j['level']}'>{escape(j['signal'])}</span>{vals}"
                    f"<span class='mrow'>{vals}</span>"
                    f"<span class='bmeta'><span class='bchips ma'>{ma_html}</span>"
                    f"<span class='bchips fl'>{fl_html}</span></span>{note}</div>")
            html("<div class='board'>" + "".join(out) + "</div>")
            html("<div class='sub' style='margin-top:6px'><b>비중</b> = 매입금액 ÷ 계좌, 1R = 계좌의 "
                 f"{S.get('unit_pct', 7.5):g}% ({won(ACC['account'] * S.get('unit_pct', 7.5) / 100)}) · 그보다 작으면 <b>정찰병</b> · "
                 "손절가·익절가 아래 % = <b>지금 가격에서 그 가격까지 남은 등락률</b> "
                 "(막대의 ● 현재가, | 평단) · 이동평균선: 초록 = 위, 빨강 = 아래 · "
                 "수급: 최근 5거래일 외국인·기관 순매수 금액(주수 × 현재가) · 자세한 근거는 판단 탭</div>")
            with st.expander("손절가 · 익절가 계산식 보기 (숫자 검산용)", icon=":material/calculate:"):
                lines = []
                for r in items:
                    if not r["j"]:
                        continue
                    ce = core.calc_explain(r["h"], r["pos"].avg, S)
                    lines.append(f"<div class='ck' style='grid-template-columns:120px 1fr'><span class='k'>{escape(r['h']['name'])}</span>"
                                 f"<span class='t'>손절가: {escape(ce['stop'])}<br>익절가: {escape(ce['target'])}</span></div>")
                html("<div class='checks'>" + "".join(lines) + "</div>")
                st.caption("익절가는 평단이 아니라 '첫 매수가' 기준으로 고정이에요(추가 매수해도 그대로). "
                           "사진·엑셀로 넣은 종목은 그때의 평단이 첫 매수가로 들어가 있어서, 실제 첫 매수가와 다르면 "
                           "아래 표의 '익절 기준가'를 고쳐 주세요. 한국 주식은 호가 단위로 손절가는 올림, 익절가는 내림해요.")
            quick_trade([r for r in ROWS if r["j"]])

            html("<div class='sec'>종목별 ATR · 손절가 · 3R</div>"
                 f"<div class='sub'>'내 ATR %'나 '직접 손절가'를 <b>직접 넣어야</b> 적용돼요 (자동으로 채우지 않아요). "
                 f"손절률 = MAX({S['stop_pct']:g}%, ATR). 오른쪽 참고 ATR(20)은 입력할 때 보는 용도예요.</div>")
            atr_editor(ROWS)


# ─────────────────────────── 기록 (넣기·수정) ───────────────────────────
with tab_edit:
    # ── 사진으로 한 번에 넣기 ──
    html("<div class='sec'>사진으로 한 번에 넣기</div>"
         "<div class='sub'>증권사 앱 잔고 화면을 캡처해서 올리면 계좌 금액과 보유 종목을 읽어 앱에 맞춰 줘요</div>")
    with st.container(border=True):
        if not AI.get("anthropic_key") and not AI.get("gemini_key"):
            html("<div class='notice warn'>사진 읽기를 쓰려면 Streamlit Secrets에 <b>GEMINI_API_KEY</b>(무료) 또는 "
                 "<b>ANTHROPIC_API_KEY</b>를 넣어 주세요.</div>")
        html("<div class='dropnote'>캡처 사진을 아래 칸으로 <b>끌어다 놓으세요</b> · 여러 장 한 번에 가능 · "
             "카톡·캡처 도구에서 복사했다면 <b>붙여넣기</b> 버튼</div>")
        imgs = st.file_uploader("잔고 화면 캡처", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
                                label_visibility="collapsed", key="img_up")
        pasted = st.session_state.setdefault("pasted_imgs", [])
        c1, c2 = st.columns([1, 1])
        with c1:
            try:
                from streamlit_paste_button import paste_image_button
                pr = paste_image_button("붙여넣기 (복사한 사진)", background_color="#0f172a",
                                        hover_background_color="#334155", key=f"paste_{len(pasted)}")
                if pr.image_data is not None:
                    buf = io.BytesIO()
                    pr.image_data.convert("RGB").save(buf, format="JPEG", quality=90)
                    pasted.append(buf.getvalue())
                    st.rerun()
            except ImportError:
                pass
        with c2:
            if pasted and st.button(f"붙여넣은 사진 {len(pasted)}장 지우기", icon=":material/close:"):
                st.session_state.pasted_imgs = []
                st.rerun()
        if pasted:
            st.image(pasted, width=120)
        all_imgs = [f.getvalue() for f in (imgs or [])] + pasted
        if all_imgs and st.button(f"사진 {len(all_imgs)}장 읽기", type="primary", icon=":material/document_scanner:",
                                  disabled=not (AI.get("anthropic_key") or AI.get("gemini_key"))):
            with st.spinner("사진을 읽는 중... (10~30초)"):
                try:
                    parsed = core.read_holdings_image(all_imgs, AI)
                    for r_ in parsed["holdings"]:
                        if not r_.get("code") and r_["market"] == "KR":
                            found = search(r_["name"])
                            if found:
                                r_["code"] = found[0]["code"]
                        elif not r_.get("code"):
                            r_["code"] = r_["name"].upper()
                    st.session_state.img_parsed = parsed
                    st.session_state.pasted_imgs = []
                except Exception as e:
                    st.error(f"사진을 못 읽었어요: {e}")

        parsed = st.session_state.get("img_parsed")
        if parsed:
            acc_p = parsed["account"]
            if any(v is not None for v in acc_p.values()):
                html("<div class='kpis'>"
                     + "".join(f"<div class='kpi'><div class='l'>{lab}</div><div class='v'>"
                               f"{(f'{acc_p[k]:+.2f}%' if k == 'total_ret' else f'{acc_p[k]:,.0f}원') if acc_p.get(k) is not None else '-'}"
                               "</div></div>"
                               for k, lab in (("total_asset", "총자산"), ("cash", "예수금"),
                                              ("total_eval", "주식 평가금액"), ("total_ret", "총수익률")))
                     + "</div>")
            st.caption("읽은 내용이에요. 틀린 칸은 눌러서 고치세요.")
            pdf = pd.DataFrame(parsed["holdings"], columns=["name", "code", "qty", "avg_price", "price", "market", "check"])
            if (pdf["check"].fillna("") != "").any():
                html("<div class='notice warn'>사진에서 읽은 수량이 매입·평가금액과 맞지 않는 종목이 있어 금액 기준으로 고쳤어요. "
                     "'확인' 칸을 보고 실제 잔고와 같은지 꼭 봐 주세요.</div>")
            ed = st.data_editor(pdf, hide_index=True, width="stretch", num_rows="dynamic", key="img_ed",
                                column_config={
                                    "name": st.column_config.TextColumn("종목명", required=True),
                                    "code": st.column_config.TextColumn("코드"),
                                    "qty": st.column_config.NumberColumn("수량", format="%,.0f"),
                                    "avg_price": st.column_config.NumberColumn("평단", format="%,.2f"),
                                    "price": st.column_config.NumberColumn("현재가", format="%,.2f"),
                                    "market": st.column_config.SelectboxColumn("시장", options=["KR", "US"]),
                                    "check": st.column_config.TextColumn("확인", disabled=True)})
            rows_in = [{k: (None if pd.isna(v) else v) for k, v in r_.items()} for r_ in ed.to_dict("records")
                       if r_.get("name") and not pd.isna(r_.get("qty"))]
            plan = core.reconcile(book, rows_in)
            if plan:
                st.caption("앱에 이렇게 반영돼요. 빼고 싶은 줄은 체크를 끄세요.")
                pl = pd.DataFrame([{"반영": p_["action"] not in ("변동 없음", "사진에 없음", "평단만 다름 (확인)"),
                                    "할 일": p_["action"], "종목": p_["name"], "코드": p_.get("code") or "",
                                    "수량": p_["trade_qty"], "가격": p_["trade_price"]} for p_ in plan])
                pl_ed = st.data_editor(pl, hide_index=True, width="stretch", key="img_plan",
                                       disabled=["할 일", "종목", "코드"],
                                       column_config={"반영": st.column_config.CheckboxColumn("반영"),
                                                      "수량": st.column_config.NumberColumn("수량", format="%,.0f"),
                                                      "가격": st.column_config.NumberColumn("가격", format="%,.2f",
                                                                                          help="사진에 없는 종목 매도가는 비우면 현재가")})
                a, b = st.columns(2)
                tdate = a.date_input("매매 날짜", value=date.today(), format="YYYY.MM.DD", key="img_date",
                                     help="신규 종목은 실제 매수일로 바꾸면 수익률 그래프가 더 정확해요")
                sync_cap = b.checkbox("사진의 총자산에 맞춰 계좌 금액 조정", value=acc_p.get("total_asset") is not None,
                                      disabled=acc_p.get("total_asset") is None,
                                      help="앱의 계좌 총액(초기 자본금 + 실현 + 평가손익)이 증권사 총자산과 같아지도록 초기 자본금을 맞춰요")
                if st.button("앱에 반영하기", type="primary", icon=":material/done_all:", width="stretch"):
                    n_done = 0
                    for p_, (_, row) in zip(plan, pl_ed.iterrows()):
                        if not row["반영"] or not p_["side"] or not row["수량"]:
                            continue
                        price = row["가격"] if not pd.isna(row["가격"]) else None
                        if p_["hid"]:
                            h = next(x for x in book["holdings"] if x["id"] == p_["hid"])
                        else:
                            h = core.new_holding(p_.get("code") or p_["name"], p_["name"], p_.get("market", "KR"))
                            if h["market"] == "KR":
                                found = search(h["name"])
                                if found and "KOSDAQ" in str(found[0].get("market", "")).upper():
                                    h["index"] = "KOSDAQ"
                            book["holdings"].append(h)
                        if price is None:
                            df_h, _ = hist(h["code"], h["market"])
                            price = float(df_h["Close"].iloc[-1]) if not df_h.empty else core.position(h).avg
                        h["trades"].append({"date": tdate.isoformat(), "side": p_["side"], "price": float(price),
                                            "qty": float(row["수량"]), "memo": "사진 반영"})
                        n_done += 1
                    if any(v is not None for v in acc_p.values()):
                        book.setdefault("snapshots", []).append({"date": date.today().isoformat(), **acc_p})
                    if sync_cap and acc_p.get("total_asset"):
                        fx_now = fx_rate() or S["fx"]
                        realized = sum(core.position(h).realized * (fx_now if h["market"] == "US" else 1)
                                       for h in book["holdings"])
                        unreal = 0.0
                        for h in book["holdings"]:
                            p0 = core.position(h)
                            if p0.qty <= 0:
                                continue
                            df_h, _ = hist(h["code"], h["market"])
                            if df_h.empty:
                                continue
                            unreal += (float(df_h["Close"].iloc[-1]) - p0.avg) * p0.qty * (fx_now if h["market"] == "US" else 1)
                        S["equity"] = round(acc_p["total_asset"] - realized - unreal)
                    save(f"photo sync {n_done}")
                    st.session_state.pop("img_parsed", None)
                    st.rerun()

    html("<div class='sec'>새 종목 추가</div>")
    with st.container(border=True):
        mkt_label = st.segmented_control("시장", ["한국", "미국"], default="한국", key="new_mkt",
                                         label_visibility="collapsed")
        market = "US" if mkt_label == "미국" else "KR"
        code_default, name_default = "", ""
        if market == "KR":
            q = st.text_input("종목 검색", key="q", placeholder="종목명으로 검색 (예: 삼성전자)",
                              label_visibility="collapsed")
            if q.strip():
                found = search(q.strip())
                if found:
                    pick = st.selectbox("검색 결과", found, label_visibility="collapsed",
                                        format_func=lambda x: f"{x['name']}  ·  {x['code']}  ·  {x['market']}")
                    code_default, name_default = pick["code"], pick["name"]
                else:
                    st.caption("검색 결과가 없어요. 아래에 6자리 코드를 직접 넣어 주세요.")
        with st.form("new_holding", clear_on_submit=True, border=False):
            a, b = st.columns(2)
            code = a.text_input("종목코드", value=code_default, placeholder="005930 / NVDA")
            name = b.text_input("종목명", value=name_default, placeholder="비우면 자동으로 채워요")
            a, b, c = st.columns(3)
            idx_opts = ["KOSPI", "KOSDAQ"] if market == "KR" else ["NASDAQ", "S&P500"]
            index_name = a.selectbox("비교 지수", idx_opts)
            sector = b.text_input("섹터", placeholder="선택")
            bdate = c.date_input("매수일", value=date.today(), format="YYYY.MM.DD")
            a, b, c = st.columns(3)
            price = a.number_input("매수 단가", min_value=0.0, value=None, placeholder="0",
                                   step=100.0 if market == "KR" else 0.01, format="%.2f")
            qty = b.number_input("수량", min_value=0.0, value=None, placeholder="0", step=1.0, format="%.0f")
            atr_in = c.number_input("ATR %", min_value=0.0, max_value=50.0, value=None, step=0.5, format="%.1f",
                                    placeholder="직접 입력 (비우면 나중에 입력 필요 표시)",
                                    help="손절률 = MAX(기본 손절폭, ATR%) — 엑셀 계좌일지와 같은 공식")
            a, b = st.columns([1, 2])
            stop = a.number_input("손절가 (선택)", min_value=0.0, value=None, placeholder="비우면 ATR로 자동",
                                  format="%.2f")
            memo = b.text_input("매수 이유", placeholder="메모 (선택)")
            if st.form_submit_button("추가하기", type="primary", width="stretch", icon=":material/add:"):
                code = code.strip().upper()
                if not code or not price or not qty:
                    st.error("코드, 단가, 수량을 넣어 주세요.")
                else:
                    df, auto_name = hist(code, market)
                    if df.empty:
                        st.warning("시세를 못 찾았어요. 코드가 맞는지 확인해 주세요 (일단 저장은 해요).")
                    h = core.new_holding(code, name or auto_name or code, market, sector)
                    h["index"] = index_name
                    h["stop"] = stop or None
                    h["memo"] = memo
                    h["trades"].append({"date": bdate.isoformat(), "side": "buy", "price": price,
                                        "qty": qty, "memo": memo})
                    if core.implied_atr(price, stop) is not None:
                        h["atr_pct"], h["atr_src"] = core.implied_atr(price, stop), "stop"
                    elif atr_in:
                        h["atr_pct"], h["atr_src"] = round(float(atr_in), 2), "manual"
                    core.reset_target(h, S)
                    book["holdings"].append(h)
                    save(f"add {code}")
                    st.rerun()

    html("<div class='sec'>보유 종목 고치기</div>")
    if not book["holdings"]:
        html("<div class='notice info'>아직 종목이 없어요.</div>")
    else:
        labels = {h["id"]: f"{h['name']}  ·  {h['code']}" + ("" if core.position(h).qty > 0 else "  ·  청산")
                  for h in book["holdings"]}
        hid = st.selectbox("종목", list(labels), format_func=labels.get, key="edit_pick",
                           label_visibility="collapsed")
        h = next(x for x in book["holdings"] if x["id"] == hid)
        pos = core.position(h)
        mk = h["market"]
        lv_e = core.levels(h, pos.avg, S) if pos.qty > 0 else None
        rr_e = ((lv_e["target"] - pos.avg) / (pos.avg - lv_e["stop"])) if lv_e and pos.avg > lv_e["stop"] else None
        html("<div class='kpis k3'>"
             f"<div class='kpi'><div class='l'>보유 · 평단</div><div class='v'>{pos.qty:,.0f}주</div>"
             f"<div class='d'>평단 {fmt(pos.avg, mk)}</div></div>"
             f"<div class='kpi'><div class='l'>손절가</div><div class='v'>{fmt(lv_e['stop'], mk) if lv_e else '-'}</div>"
             f"<div class='d'>{escape(lv_e['note']) if lv_e else ''}</div></div>"
             f"<div class='kpi'><div class='l'>익절가 ({S['target_r']:g}R 고정)</div><div class='v'>{fmt(lv_e['target'], mk) if lv_e else '-'}</div>"
             f"<div class='d'>{f'손익비 {rr_e:.1f} : 1' if rr_e else ''} · 실현 {fmt(pos.realized, mk)}</div></div>"
             "</div>")

        t_trade, t_hist, t_rule, t_del = st.tabs(["매매 추가", "매매 내역", "판단 기준", "삭제"])
        with t_trade:
            nk = f"{hid}_{st.session_state.get(f'trade_nonce_{hid}', 0)}"   # 기록 후 입력칸 비우기용
            side = st.segmented_control("구분", ["추가 매수", "매도"], default="추가 매수", key=f"side_{nk}")
            a, b, c = st.columns(3)
            tdate = a.date_input("날짜", value=date.today(), format="YYYY.MM.DD", key=f"tdate_{nk}")
            tprice = b.number_input("단가", min_value=0.0, value=None, placeholder="0", format="%.2f", key=f"tprice_{nk}")
            tqty = c.number_input("수량", min_value=0.0, value=None, placeholder="0", step=1.0, format="%.0f",
                                  key=f"tqty_{nk}")
            tmemo = st.text_input("메모", placeholder="불타기 / 3R 절반 익절 / 손절 …", key=f"tmemo_{nk}")
            side_k = "sell" if side == "매도" else "buy"
            if tprice and tqty:
                if side_k == "sell" and tqty > pos.qty:
                    html(f"<div class='notice warn'>보유 수량({pos.qty:,.0f}주)보다 많이 팔 수 없어요.</div>")
                else:
                    pv = core.trade_preview(h, side_k, float(tprice), float(tqty), S, ACC["account"])
                    b0, a1 = pv["before"], pv["after"]

                    def cell(v0, v1, kind):
                        if v0 is None or v1 is None:
                            return "<td>-</td><td>-</td>"
                        if kind == "qty":
                            f0, f1 = f"{v0:,.0f}주", f"{v1:,.0f}주"
                        elif kind == "pct":
                            f0, f1 = f"{v0:.2f}%", f"{v1:.2f}%"
                        elif kind == "rr":
                            f0, f1 = f"{v0:.1f} : 1", f"{v1:.1f} : 1"
                        else:
                            f0, f1 = fmt(v0, mk), fmt(v1, mk)
                        if abs(v1 - v0) < 1e-9:
                            tag = "<span class='mut'> (그대로)</span>"
                        else:
                            tag = f" <span class='{'up' if v1 > v0 else 'down'}'>{'▲' if v1 > v0 else '▼'}</span>"
                        return f"<td>{f0}</td><td><b>{f1}</b>{tag}</td>"
                    stop_lab = f"손절가 (평단 −{core.levels(h, max(a1['avg'], 1), S)['stop_pct']:g}%)"
                    rows_ = [("수량", "qty", "qty"), ("평단", "avg", "px"), (stop_lab, "stop", "px"),
                             (f"익절가 ({S['target_r']:g}R, 고정)", "target", "px"), ("손익비 (새 평단 기준)", "rr", "rr"),
                             ("손절 시 계좌 손실", "loss_pct", "pct")]
                    body = "".join(f"<tr><th>{lab}</th>{cell(b0[k], a1[k], kind)}</tr>" for lab, k, kind in rows_)
                    html("<div class='pv'><div class='pv-h'>미리보기 — 기록하면 이렇게 바뀌어요</div>"
                         f"<table><tr><th></th><th>지금</th><th>{'추가 매수 후' if side_k == 'buy' else '매도 후'}</th></tr>"
                         f"{body}</table>"
                         + "".join(f"<div class='notice warn'>{escape(w)}</div>" for w in pv["warn"])
                         + "".join(f"<div class='notice info'>{escape(i)}</div>" for i in pv["info"])
                         + "</div>")
            if st.button("기록하기", type="primary", width="stretch", key=f"rec_{nk}"):
                if not tprice or not tqty:
                    st.error("단가와 수량을 넣어 주세요.")
                elif side_k == "sell" and tqty > pos.qty:
                    st.error(f"보유 수량({pos.qty:,.0f}주)보다 많이 팔 수 없어요.")
                else:
                    core.apply_trade(h, side_k, tdate.isoformat(), float(tprice), float(tqty), tmemo, S, ACC["account"])
                    save(f"trade {h['code']}")
                    st.session_state[f"trade_nonce_{hid}"] = st.session_state.get(f"trade_nonce_{hid}", 0) + 1
                    st.rerun()
        with t_hist:
            if h.get("stop_log"):
                html("<div class='sub' style='margin:4px 0 6px'>손절가 변경 기록</div><div class='checks'>" + "".join(
                    f"<div class='ck' style='grid-template-columns:90px 1fr auto'><span class='t'>{escape(x['date'][5:].replace('-', '.'))}</span>"
                    f"<span class='k'>{escape(x['event'])}</span><span class='t'>"
                    + (f"손절가 {fmt(x['from'], mk)} → <b>{fmt(x['to'], mk)}</b> · " if x.get("from") is not None else "")
                    + f"익절가 {fmt(x['target'], mk)}</span></div>"
                    for x in reversed(h["stop_log"])) + "</div>")
            st.caption("칸을 눌러 바로 고칠 수 있어요. 맨 아래 줄에서 추가, 왼쪽 체크 후 삭제.")
            tdf = pd.DataFrame(h["trades"] or [], columns=["date", "side", "price", "qty", "memo"])
            if len(tdf):
                tdf["date"] = pd.to_datetime(tdf["date"]).dt.date
            tdf["side"] = tdf["side"].map({"buy": "매수", "sell": "매도"})
            edited = st.data_editor(
                tdf, num_rows="dynamic", width="stretch", key=f"ed_{hid}", hide_index=True,
                column_config={
                    "date": st.column_config.DateColumn("날짜", required=True, format="YYYY.MM.DD"),
                    "side": st.column_config.SelectboxColumn("구분", options=["매수", "매도"], required=True),
                    "price": st.column_config.NumberColumn("단가", min_value=0.0, required=True, format="%,.2f"),
                    "qty": st.column_config.NumberColumn("수량", min_value=0.0, required=True, format="%,.0f"),
                    "memo": st.column_config.TextColumn("메모")})
            if st.button("내역 저장", key=f"sv_{hid}", icon=":material/save:"):
                ed = edited.dropna(subset=["date", "side", "price", "qty"])
                h["trades"] = [{"date": pd.Timestamp(r["date"]).date().isoformat(),
                                "side": "buy" if r["side"] == "매수" else "sell",
                                "price": float(r["price"]), "qty": float(r["qty"]),
                                "memo": "" if pd.isna(r["memo"]) else str(r["memo"])}
                               for _, r in ed.iterrows()]
                save(f"edit trades {h['code']}")
                st.rerun()
        with t_rule:
            with st.form(f"meta_{hid}", border=False):
                a, b = st.columns(2)
                new_name = a.text_input("종목명", value=h["name"])
                new_code = b.text_input("종목코드", value=h["code"])
                a, b = st.columns(2)
                new_sector = a.text_input("섹터", value=h.get("sector", ""))
                new_atr = b.number_input("ATR %", min_value=0.0, max_value=50.0,
                                         value=float(h["atr_pct"]) if h.get("atr_pct") else None, step=0.5,
                                         format="%.1f", placeholder="비우면 기본 손절폭",
                                         help=f"손절률 = MAX({S['stop_pct']:g}%, ATR%). 목표가 = 평단 × (1 + {S['target_r']:g} × 손절률)")
                a, b, c = st.columns(3)
                new_stop = a.number_input("손절가 직접", min_value=0.0, value=float(h["stop"]) if h.get("stop") else None,
                                          placeholder="비우면 ATR로 자동", format="%.2f",
                                          help="불타기·본전 이동 때 올려 주세요. 넣으면 ATR 계산보다 우선")
                tm_opts = ["자동", "5", "20", "50"]
                new_tm = b.selectbox("추세 기준선", tm_opts, index=tm_opts.index(str(h.get("trend_ma", "자동"))),
                                     format_func=lambda x: x if x == "자동" else f"{x}일선",
                                     help="자동: 최근 20일 +30% 이상 5일선, +8% 이상 20일선, 그 외 50일선")
                fo_opts = {"자동 (실적 표)": None, "증가 맞음": True, "증가 아님": False}
                cur = next(k for k, v in fo_opts.items() if v == h.get("fund_override"))
                new_fo = c.selectbox("영업이익 판단", list(fo_opts), index=list(fo_opts).index(cur))
                idx_opts = ["KOSPI", "KOSDAQ"] if h["market"] == "KR" else ["NASDAQ", "S&P500"]
                new_idx = st.selectbox("비교 지수", idx_opts,
                                       index=idx_opts.index(h["index"]) if h.get("index") in idx_opts else 0)
                new_memo = st.text_area("메모", value=h.get("memo", ""), placeholder="매수 이유, 시나리오")
                if st.form_submit_button("저장", type="primary", width="stretch"):
                    imp = core.implied_atr(pos.avg, new_stop)
                    if (new_atr or None) != (h.get("atr_pct") or None) or imp is not None:
                        h["atr_src"] = "stop" if imp is not None else ("manual" if new_atr else None)
                    h.update(name=new_name, code=new_code.strip().upper() or h["code"], sector=new_sector,
                             atr_pct=imp if imp is not None else (round(float(new_atr), 2) if new_atr else None),
                             stop=new_stop or None, trend_ma=new_tm,
                             fund_override=fo_opts[new_fo], index=new_idx, memo=new_memo)
                    h["stop_floor"] = None
                    core.reset_target(h, S)
                    save(f"meta {h['code']}")
                    st.rerun()
        with t_del:
            st.caption("매매 기록까지 모두 지워져요. 청산한 종목은 지우지 않아도 설정 탭 기록에 남아요.")
            if st.checkbox("이 종목을 지울게요", key=f"del_ck_{hid}") and \
                    st.button("삭제", key=f"del_{hid}", icon=":material/delete:"):
                book["holdings"] = [x for x in book["holdings"] if x["id"] != hid]
                save(f"delete {h['code']}", allow_empty=True)
                st.rerun()


# ─────────────────────────── 판단 ───────────────────────────
def chart(df, h, pos, j):
    d = df.tail(130)
    fig = go.Figure(go.Candlestick(
        x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="",
        increasing_line_color="#e5484d", decreasing_line_color="#2f6fed",
        increasing_fillcolor="#e5484d", decreasing_fillcolor="#2f6fed", showlegend=False))
    for n, colr in ((5, "#f59e0b"), (20, "#10b981"), (50, "#8b5cf6")):
        fig.add_trace(go.Scatter(x=d.index, y=df["Close"].rolling(n).mean().tail(130), mode="lines",
                                 line=dict(width=1.3, color=colr), name=f"{n}일선", hoverinfo="skip"))
    for y, label, colr in ((pos.avg, "평단", "#64748b"), (j["stop"], "손절", "#dc2626"),
                           (j["target"], f"{S['target_r']:g}R", "#16a34a")):
        fig.add_hline(y=y, line_dash="dot", line_width=1, line_color=colr,
                      annotation_text=f"{label} {fmt(y, h['market'])}", annotation_position="top left",
                      annotation_font=dict(size=11, color=colr))
    fig.update_layout(template="plotly_white", height=400, margin=dict(l=4, r=4, t=28, b=4),
                      xaxis_rangeslider_visible=False, dragmode=False, hovermode="x unified",
                      paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                      font=dict(family="Pretendard Variable, Pretendard, sans-serif", size=11, color="#64748b"),
                      legend=dict(orientation="h", y=1.08, x=0, font=dict(size=11)))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])], showgrid=False, linecolor="#e8ebf0")
    fig.update_yaxes(gridcolor="#f1f3f6", side="right", tickformat=",")
    return fig


with tab_judge:
    ok_rows = sorted([r for r in ROWS if r["j"]], key=lambda r: LEVEL_ORDER[r["j"]["level"]])
    if not ok_rows:
        html("<div class='notice info'>판단할 보유 종목이 없어요.</div>")
    else:
        names = {r["h"]["id"]: f"{r['h']['name']}  —  {r['j']['signal']}" for r in ok_rows}
        pick = st.selectbox("종목", list(names), format_func=names.get, key="judge_pick",
                            label_visibility="collapsed")
        r = next(x for x in ok_rows if x["h"]["id"] == pick)
        h, pos, ind, j = r["h"], r["pos"], r["ind"], r["j"]
        mk = h["market"]

        r0 = j["rules"][0] if j["rules"] else None
        acts = "".join(f"<span class='act'>{escape(a)}</span>" for a in j["actions"])
        html(f"<div class='verdict v-{j['level']}'><div class='word'>{escape(j['signal'])}</div>"
             f"<div class='one'><div class='nm'>{escape(h['name'])} <span style='font-weight:500;font-size:.8rem'>{escape(h['code'])}</span></div>"
             f"<span>{escape(r0['fact']) if r0 else ''}</span>"
             + (f"<div class='acts'>{acts}</div>" if acts else "") + "</div></div>")

        html("<div class='sec'>왜 이런 판단인가요</div><div class='sub'>내 매매 원칙 → 지금 이 종목의 상태</div>")
        cards = []
        for rr in j["rules"]:
            cards.append(f"<div class='rc l-{rr['level']}'><div class='t'>{escape(rr['title'])}</div>"
                         f"<div class='p'>{escape(rr['principle'])}</div>"
                         f"<div class='f'>{escape(rr['fact'])}</div></div>")
        html("<div class='rules'>" + "".join(cards) + "</div>")

        html("<div class='sec'>숫자</div>")
        stop_gap = (j["stop"] / ind["price"] - 1) * 100
        tgt_gap = (j["target"] / ind["price"] - 1) * 100
        atr_set = h.get("atr_pct")
        stt_j = core.atr_status(h)
        if stt_j != "applied":
            ref_ = core.auto_atr(h, r["df"])
            html("<div class='notice warn'><b>" + ("ATR 입력 필요" if stt_j == "missing" else "ATR 확인 필요") + "</b>&nbsp;— "
                 + ("아직 내가 정한 ATR이 없어 임시로 기본 8%로 계산 중이에요. " if stt_j == "missing"
                    else f"예전에 들어간 {float(atr_set):g}%를 아직 확인하지 않았어요. ")
                 + (f"참고로 진입일 ATR(20)은 {ref_:g}%예요. " if ref_ else "")
                 + "아래 '내 ATR %'나 '직접 손절가'를 넣고 저장해 주세요.</div>")
        html("<div class='kpis k3'>"
             f"<div class='kpi'><div class='l'>현재가</div><div class='v'>{fmt(ind['price'], mk)}</div>"
             f"<div class='d {sign_cls(ind['chg'])}'>{ind['chg']:+.2f}%</div></div>"
             f"<div class='kpi'><div class='l'>수익률</div><div class='v {sign_cls(j['pnl_pct'])}'>{j['pnl_pct']:+.2f}%</div>"
             f"<div class='d'>{j['r_mult']:+.2f}R · 1R = {fmt(j['r_unit'], mk)}</div></div>"
             f"<div class='kpi'><div class='l'>평가손익</div>"
             f"<div class='v {sign_cls(ind['price'] - pos.avg)}'>{(ind['price'] - pos.avg) * pos.qty * r['fx']:+,.0f}원</div>"
             f"<div class='d'>{pos.qty:,.0f}주 · 평단 {fmt(pos.avg, mk)}</div></div>"
             "</div>"
             "<div class='kpis k3'>"
             f"<div class='kpi'><div class='l'>내 ATR</div><div class='v'>"
             f"{(f'{float(atr_set):g}%' if atr_set else '직접 손절가') if stt_j == 'applied' else ('확인 필요' if stt_j == 'check' else '입력 필요')}</div>"
             f"<div class='d'>손절률 {j['stop_pct']:g}%{'' if stt_j == 'applied' else ' (임시)'}"
             f" · 지금 ATR(20) {ind['atr_pct']:.1f}%</div></div>"
             f"<div class='kpi'><div class='l'>손절가</div><div class='v'>{fmt(j['stop'], mk)}</div>"
             f"<div class='d'>{stop_gap:+.1f}% · {escape(j['stop_note'])}</div></div>"
             f"<div class='kpi'><div class='l'>익절가 ({S['target_r']:g}R 고정)</div><div class='v'>{fmt(j['target'], mk)}</div>"
             f"<div class='d'>{tgt_gap:+.1f}% · 첫 매수 때 정한 값 (추가 매수해도 고정)</div></div>"
             "</div>")
        with st.form(f"jatr_{h['id']}", border=False):
            a_, b_, c_ = st.columns([1, 1, 1], vertical_alignment="bottom")
            new_atr = a_.number_input("내 ATR % (20일)", min_value=0.0, max_value=50.0, step=0.5, format="%.1f",
                                      value=float(atr_set) if atr_set and stt_j != "missing" else None,
                                      placeholder=f"직접 입력 (지금 ATR20 {ind['atr_pct']:.1f})")
            new_stop = b_.number_input("또는 직접 손절가", min_value=0.0, format="%.2f",
                                       value=float(h["stop"]) if h.get("stop") else None, placeholder="적으면 ATR 자동 계산")
            if c_.form_submit_button("저장", type="primary", width="stretch"):
                h["stop"] = float(new_stop) if new_stop else None
                imp = core.implied_atr(pos.avg, h["stop"])
                h["atr_pct"] = imp if imp is not None else (round(float(new_atr), 2) if new_atr else None)
                h["atr_src"] = "stop" if imp is not None else ("manual" if new_atr else None)
                h["stop_floor"] = None
                core.reset_target(h, S)
                save(f"atr {h['code']}")
                st.rerun()
        if h.get("stop"):
            imp_ = core.implied_atr(pos.avg, float(h["stop"]))
            html(f"<div class='notice info'>직접 넣은 손절가 {fmt(float(h['stop']), mk)} 기준"
                 + (f" → ATR {imp_:g}%로 계산, {S['target_r']:g}R 목표가 {fmt(j['target'], mk)}" if imp_ is not None
                    else " (평단 이상 — 수익 보호용, R·3R은 원래 ATR 기준)")
                 + ". 손절가를 비우면 내 ATR%로 계산해요.</div>")
        if atr_set and ind["atr_pct"] > float(atr_set) * 1.3:
            html(f"<div class='notice warn'>지금 ATR(20)이 {ind['atr_pct']:.1f}%로 설정값 {float(atr_set):g}%보다 많이 커졌어요. "
                 "변동성이 커진 구간이니 손절폭을 다시 볼 만해요 (기록 → 판단 기준).</div>")

        html("<div class='sec'>원칙 점검</div>")
        rows_html = []
        for k, v, t in j["checks"]:
            mark = "<span class='mk y'>✓</span>" if v else ("<span class='mk n'>✕</span>" if v is False
                                                             else "<span class='mk z'>–</span>")
            rows_html.append(f"<div class='ck'>{mark}<span class='k'>{escape(k)}</span><span class='t'>{escape(t)}</span></div>")
        html("<div class='checks'>" + "".join(rows_html) + "</div>")

        html("<div class='sec'>차트</div>")
        st.plotly_chart(chart(r["df"], h, pos, j), width="stretch", config={"displayModeBar": False})

        f = r.get("fund") or {}
        fl = r.get("flows") or {}
        html("<div class='sec'>실적 · 수급 · 리포트 · 뉴스</div>")
        t_f, t_s, t_r, t_n = st.tabs(["영업이익", "수급", "리포트", "뉴스"])
        with t_f:
            html(f"<div class='box'>{escape(f.get('detail') or '실적 자료를 못 가져왔어요.')}"
                 + ("<br><span class='mut'>기록 → 판단 기준에서 직접 판단값 사용 중</span>" if h.get("fund_override") is not None else "")
                 + "</div>")
            ser = (f.get("series") or {}).get("quarter") or []
            if ser:
                fig = go.Figure(go.Bar(
                    x=[x[0] + (" (E)" if x[2] else "") for x in ser], y=[x[1] for x in ser],
                    marker_color=["#cbd5e1" if x[2] else ("#e5484d" if x[1] >= 0 else "#2f6fed") for x in ser],
                    text=[f"{x[1]:,.0f}" for x in ser], textposition="outside",
                    hovertemplate="%{x}<br>영업이익 %{y:,.0f}억<extra></extra>"))
                fig.update_layout(template="plotly_white", height=240, margin=dict(l=4, r=4, t=16, b=4),
                                  paper_bgcolor="#fff", plot_bgcolor="#fff", dragmode=False,
                                  font=dict(family="Pretendard Variable, Pretendard, sans-serif", size=11, color="#64748b"))
                fig.update_yaxes(gridcolor="#f1f3f6", ticksuffix="억", tickformat=",")
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
                st.caption("분기 영업이익(억원). 회색은 증권사 컨센서스(추정치).")
        with t_s:
            if fl.get("ok"):
                html("<div class='kpis'>"
                     f"<div class='kpi'><div class='l'>5일 기관</div><div class='v {sign_cls(fl['inst5'])}'>{fl['inst5']:+,.0f}주</div></div>"
                     f"<div class='kpi'><div class='l'>5일 외국인</div><div class='v {sign_cls(fl['frgn5'])}'>{fl['frgn5']:+,.0f}주</div></div>"
                     f"<div class='kpi'><div class='l'>20일 기관</div><div class='v {sign_cls(fl['inst20'])}'>{fl['inst20']:+,.0f}주</div></div>"
                     f"<div class='kpi'><div class='l'>20일 외국인</div><div class='v {sign_cls(fl['frgn20'])}'>{fl['frgn20']:+,.0f}주</div></div>"
                     "</div>")
                daily = fl.get("daily") or []
                if daily:
                    dd = pd.DataFrame(daily[:20])
                    dd["date"] = dd["date"].astype(str).str[-4:].str[:2] + "." + dd["date"].astype(str).str[-2:]
                    dd = dd.iloc[::-1]
                    fig = go.Figure()
                    fig.add_trace(go.Bar(x=dd["date"], y=dd["frgn"], name="외국인", marker_color="#0f172a"))
                    fig.add_trace(go.Bar(x=dd["date"], y=dd["inst"], name="기관", marker_color="#94a3b8"))
                    fig.update_layout(template="plotly_white", height=240, barmode="group", dragmode=False,
                                      margin=dict(l=4, r=4, t=24, b=4), paper_bgcolor="#fff", plot_bgcolor="#fff",
                                      legend=dict(orientation="h", y=1.15, x=0),
                                      font=dict(family="Pretendard Variable, Pretendard, sans-serif", size=11, color="#64748b"))
                    fig.update_yaxes(gridcolor="#f1f3f6", tickformat=",", zerolinecolor="#cbd5e1")
                    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
                    st.caption("일별 순매수(주). 원칙: 외국인·투신·연기금 수급이 받쳐주는지.")
            else:
                html("<div class='notice info'>수급 자료를 못 가져왔어요. 잠시 뒤 새로고침해 보세요.</div>")
            html(f"<div class='box' style='margin-top:8px'>거래량 평소의 {ind['vol_ratio']:.1f}배 · 종가위치 {ind['dcr']:.0f}% · "
                 f"52주 고점 대비 {ind['from_high']:+.1f}% · {'정배열' if ind['aligned'] else '정배열 아님'}</div>")
        with t_r:
            reps = reports(h["code"]) if mk == "KR" else []
            if reps:
                html("<div class='checks'>" + "".join(
                    f"<div class='ck' style='grid-template-columns:1fr auto'><a href='{escape(x['url'])}' target='_blank' "
                    f"style='color:var(--ink);font-weight:600;text-decoration:none'>{escape(x['title'])}</a>"
                    f"<span class='t'>{escape(x['broker'])} · {escape(x['date'])}</span></div>" for x in reps) + "</div>")
            else:
                html("<div class='notice info'>최근 증권사 리포트가 없거나 못 가져왔어요.</div>")
        with t_n:
            nws = news(h["code"]) if mk == "KR" else []
            if nws:
                html("<div class='checks'>" + "".join(
                    f"<div class='ck' style='grid-template-columns:1fr auto'><a href='{escape(x['url'])}' target='_blank' "
                    f"style='color:var(--ink);text-decoration:none'>{escape(x['title'])}</a>"
                    f"<span class='t'>{escape(x['office'])} · {escape(x['date'])}</span></div>" for x in nws) + "</div>")
            else:
                html("<div class='notice info'>뉴스를 못 가져왔어요.</div>")

        if h.get("memo"):
            html(f"<div class='callout'><div class='h'>메모</div><div class='b'>{escape(h['memo'])}</div></div>")

        c1, c2 = st.columns(2)
        with c1:
            if mk == "KR":
                st.link_button("네이버 증권에서 뉴스·리포트", icon=":material/open_in_new:", width="stretch",
                               url=f"https://finance.naver.com/item/main.naver?code={h['code']}")
        with c2:
            with st.popover("불타기하면 몇 주?", icon=":material/add_chart:", width="stretch"):
                new_stop = ind["price"] * (1 - j["stop_pct"] / 100)
                sz = core.size_for(ind["price"], new_stop, {**S, "equity": ACC["account"]}, r["fx"])
                st.markdown(f"현재가 {fmt(ind['price'], mk)}, 손절 {j['stop_pct']:g}% ({fmt(new_stop, mk)}) 기준  \n"
                            f"1유닛 = **{sz['qty']:,}주** · 약 {won(sz['value'])} · 계좌의 {sz['weight']:.1f}%")
                st.caption(f"추가 후 손절가는 최소 평단 {fmt(pos.avg, mk)} 이상으로")

    bad = [r for r in ROWS if not r["j"]]
    if bad:
        html("<div class='notice warn'>시세를 못 가져온 종목: "
             + ", ".join(f"{escape(r['h']['name'])}({escape(r['h']['code'])})" for r in bad) + "</div>")


# ─────────────────────────── 계산기 ───────────────────────────
with tab_calc:
    html("<div class='sec'>몇 주 살까</div>"
         f"<div class='sub'>계좌 {won(ACC['account'])} × 위험 {S['risk_pct']:g}% = 한 번에 잃어도 되는 돈(1R) "
         f"<b>{won(ACC['account'] * S['risk_pct'] / 100)}</b></div>")
    with st.container(border=True):
        a, b = st.columns(2)
        cm = a.segmented_control("시장", ["한국", "미국"], default="한국", key="calc_m")
        how = b.segmented_control("손절 기준", ["%", "가격"], default="%", key="calc_how")
        a, b = st.columns(2)
        entry = a.number_input("매수가", min_value=0.0, value=None, placeholder="0", format="%.2f", key="calc_e")
        if how == "가격":
            stop_v = b.number_input("손절가", min_value=0.0, value=None, placeholder="0", format="%.2f") or 0.0
        else:
            sp = b.number_input("손절 % (= MAX(기본, ATR))", min_value=0.5, value=float(S["stop_pct"]), step=0.5)
            stop_v = (entry or 0) * (1 - sp / 100)
    if entry and 0 < stop_v < entry:
        fxm = FX if cm == "미국" else 1.0
        sz = core.size_for(entry, stop_v, {**S, "equity": ACC["account"]}, fxm)
        html("<div class='kpis k3'>"
             f"<div class='kpi'><div class='l'>살 수량</div><div class='v'>{sz['qty']:,}주</div>"
             f"<div class='d'>손절가 {stop_v:,.2f}</div></div>"
             f"<div class='kpi'><div class='l'>매수 금액</div><div class='v'>{won(sz['value'])}</div>"
             f"<div class='d'>계좌의 {sz['weight']:.1f}%</div></div>"
             f"<div class='kpi'><div class='l'>{S['target_r']:g}R 목표가</div>"
             f"<div class='v'>{entry + (entry - stop_v) * S['target_r']:,.2f}</div>"
             f"<div class='d'>+{(entry - stop_v) / entry * S['target_r'] * 100:.1f}%</div></div>"
             "</div>")
        if sz["weight"] > 100 / max(1, S["max_positions"] - 1) * 1.5:
            html("<div class='notice warn'>비중이 커요. 손절폭이 너무 좁지 않은지 확인해 주세요.</div>")
        if cm == "미국":
            st.caption(f"환율 {FX:,.1f}원 적용")


# ─────────────────────────── 설정 ───────────────────────────
with tab_set:
    html("<div class='sec'>청산한 종목</div>")
    closed = []
    for h in book["holdings"]:
        pos = core.position(h)
        if pos.qty <= 0 and h["trades"]:
            last = max(t["date"] for t in h["trades"])
            cost = sum(float(t["price"]) * float(t["qty"]) for t in h["trades"] if t["side"] == "buy")
            closed.append({"종목": h["name"], "첫 매수": pos.first_date, "마지막 매도": last,
                           "실현손익": round(pos.realized),
                           "수익률": round(pos.realized / cost * 100, 2) if cost else 0})
    if closed:
        wins = sum(1 for c in closed if c["실현손익"] > 0)
        total = sum(c["실현손익"] for c in closed)
        html("<div class='kpis k3'>"
             f"<div class='kpi'><div class='l'>청산</div><div class='v'>{len(closed)}건</div></div>"
             f"<div class='kpi'><div class='l'>승률</div><div class='v'>{wins / len(closed) * 100:.0f}%</div>"
             f"<div class='d'>{wins}승 {len(closed) - wins}패</div></div>"
             f"<div class='kpi'><div class='l'>실현손익 합계</div><div class='v {sign_cls(total)}'>{total:+,.0f}</div>"
             f"<div class='d'>미국 종목은 달러 기준</div></div></div>")
        st.dataframe(pd.DataFrame(closed).sort_values("마지막 매도", ascending=False), hide_index=True,
                     width="stretch",
                     column_config={"수익률": st.column_config.NumberColumn(format="%+.2f%%"),
                                    "실현손익": st.column_config.NumberColumn(format="%,d")})
    else:
        html("<div class='notice info'>아직 청산한 종목이 없어요.</div>")

    html("<div class='sec'>계좌 금액 · 매매 원칙 숫자</div>")
    with st.form("settings"):
        a, b, c = st.columns(3)
        eq = a.number_input("초기 자본금 (원)", min_value=0, value=int(S["equity"]), step=1_000_000, format="%d",
                            help="계좌를 시작한 원금, 또는 기준일의 계좌 총액. 이후 계좌 총액 = 이 값 + 실현손익 + 평가손익")
        sd0 = pd.to_datetime(S.get("start_date")).date() if S.get("start_date") else None
        sd = b.date_input("기준일", value=sd0, format="YYYY.MM.DD", help="비우면 첫 매매일부터 그래프를 그려요")
        rp = c.number_input("1회 위험 (계좌 대비 %)", min_value=0.1, max_value=2.0, value=float(S["risk_pct"]), step=0.1,
                            help="2% 초과 금지")
        a, b, c = st.columns(3)
        spct = a.number_input("기본 손절폭 %", min_value=1.0, value=float(S["stop_pct"]), step=0.5,
                              help="손절률 = MAX(이 값, 종목별 ATR%)")
        mp = b.number_input("최대 종목 수", min_value=1, value=int(S["max_positions"]))
        fxv = c.number_input("예비 환율", min_value=0.0, value=float(S["fx"]))
        unit_v = st.number_input("1R 비중 (계좌 대비 %)", min_value=1.0, max_value=50.0,
                                 value=float(S.get("unit_pct", 7.5)), step=0.5,
                                 help="이 비중만큼 들어가 있으면 1R(1유닛). 이보다 작으면 판단 보드에 '정찰병'으로 표시")
        a, b = st.columns(2)
        tr = a.number_input("절반 익절 R", min_value=1.0, value=float(S["target_r"]), step=0.5)
        be = b.number_input("본전 손절가 이동 R", min_value=0.5, value=float(S["breakeven_r"]), step=0.5)
        if st.form_submit_button("저장", type="primary", width="stretch"):
            S.update(unit_pct=float(unit_v))
            S.update(equity=eq, start_date=sd.isoformat() if sd else "", risk_pct=rp, stop_pct=spct,
                     max_positions=int(mp), fx=fxv,
                     target_r=tr, breakeven_r=be)
            save("settings")
            st.rerun()

    html("<div class='sec'>엑셀 계좌일지 가져오기</div>"
         "<div class='sub'>추세추종_돌파매매_계좌일지(.xlsx)의 설정·매매일지 시트를 읽어요. 1차·2차 청산도 매도로 들어가요.</div>")
    with st.container(border=True):
        xf = st.file_uploader("계좌일지 엑셀", type=["xlsx"], label_visibility="collapsed")
        if xf:
            try:
                parsed = core.parse_journal_xlsx(xf)
            except Exception as e:
                parsed = None
                st.error(f"엑셀을 못 읽었어요: {e}")
            if parsed:
                prev = pd.DataFrame([{"종목": t["name"], "진입일": t["date"], "진입가": t["entry"], "수량": t["qty"],
                                      "ATR%": t["atr_pct"], "청산": len(t["sells"])} for t in parsed["trades"]])
                st.dataframe(prev, hide_index=True, width="stretch")
                ps = parsed["settings"]
                if ps:
                    lab = {"equity": "초기 자본금", "risk_pct": "리스크 %", "target_r": "목표 R", "stop_pct": "기본 손절폭 %"}
                    st.caption("엑셀 설정도 함께 반영: " + " · ".join(f"{lab.get(k, k)} {v:,.1f}".replace(".0", "")
                                                                  for k, v in ps.items()))
                replace = st.checkbox("기존 종목을 지우고 엑셀 내용으로 새로 채우기")
                if st.button(f"{len(parsed['trades'])}건 가져오기", type="primary", icon=":material/upload_file:"):
                    if replace:
                        book["holdings"] = []
                    S.update({k: v for k, v in ps.items() if k in ("equity", "risk_pct", "target_r", "stop_pct")})
                    miss = []
                    for t in parsed["trades"]:
                        found = search(t["name"])
                        code = found[0]["code"] if found else ""
                        if not code:
                            miss.append(t["name"])
                        idx = "KOSDAQ" if found and "KOSDAQ" in str(found[0].get("market", "")).upper() else "KOSPI"
                        h = core.new_holding(code or t["name"], t["name"], "KR")
                        h.update(index=idx, atr_pct=t["atr_pct"], atr_src="manual" if t["atr_pct"] else None, memo=t["memo"])
                        h["trades"].append({"date": t["date"], "side": "buy", "price": t["entry"], "qty": t["qty"],
                                            "memo": "엑셀 일지"})
                        for sl in t["sells"]:
                            if sl["qty"] > 0:
                                h["trades"].append({"date": sl["date"], "side": "sell", "price": sl["price"],
                                                    "qty": sl["qty"], "memo": "엑셀 일지 청산"})
                        book["holdings"].append(h)
                    save("import journal xlsx")
                    if miss:
                        st.warning("코드를 못 찾은 종목: " + ", ".join(miss) + " — 기록 → 판단 기준에서 코드를 넣어 주세요.")
                    else:
                        st.rerun()

    html("<div class='sec'>백업 · 한꺼번에 넣기</div>")
    with st.container(border=True):
        st.download_button("백업 파일 받기", json.dumps(book, ensure_ascii=False, indent=2),
                           file_name=f"portfolio_{date.today():%Y%m%d}.json", mime="application/json",
                           icon=":material/download:")
        up = st.file_uploader("백업 파일로 되돌리기", type="json")
        if up and st.button("이 파일로 덮어쓰기", icon=":material/restore:"):
            st.session_state.book = core.normalize_book(json.load(up))
            book = st.session_state.book
            save("restore backup")
            st.rerun()
        csv_txt = st.text_area("CSV로 한꺼번에 넣기", height=110,
                               placeholder="코드,이름,시장(KR/US),매수일,단가,수량\n005930,삼성전자,KR,2026-09-01,72000,100")
        if st.button("넣기", icon=":material/playlist_add:") and csv_txt.strip():
            df = pd.read_csv(io.StringIO(csv_txt), header=None,
                             names=["code", "name", "market", "date", "price", "qty"], dtype={"code": str})
            added = 0
            for _, rr in df.iterrows():
                code = str(rr["code"]).strip().upper()
                mkt = str(rr["market"]).strip().upper()
                code = code.zfill(6) if mkt == "KR" and code.isdigit() else code
                h = next((x for x in book["holdings"] if x["code"] == code and core.position(x).qty > 0), None)
                if h is None:
                    h = core.new_holding(code, str(rr["name"]), mkt)
                    book["holdings"].append(h)
                h["trades"].append({"date": str(rr["date"]), "side": "buy", "price": float(rr["price"]),
                                    "qty": float(rr["qty"]), "memo": "CSV"})
                added += 1
            save(f"csv import {added}")
            st.rerun()

    html("<div class='sec'>저장 상태</div>")
    html(f"<div class='notice {'info' if STORE_OK else 'warn'}'>{'✓ ' if STORE_OK else ''}{escape(STORE_MSG)}"
         f" · 마지막 저장 {escape(book.get('updated') or '-')}</div>")
    if STORE_OK:
        st.caption("종목 기록은 저장소의 data 브랜치에 따로 저장돼요. 앱 파일(main)을 고쳐 올려도 기록은 그대로이고, "
                   "저장할 때마다 GitHub에 기록이 남아 예전 상태로 되돌릴 수도 있어요.")
    inf = store.info
    html("<div class='checks'>"
         f"<div class='ck'><span></span><span class='k'>불러온 곳</span><span class='t'>{escape(str(inf['loaded_from']))}"
         f" · 종목 {inf['loaded_count'] if inf['loaded_count'] is not None else '-'}개</span></div>"
         f"<div class='ck'><span></span><span class='k'>마지막 저장</span><span class='t'>{escape(str(inf['saved_to']))}"
         f" · 종목 {inf['saved_count'] if inf['saved_count'] is not None else '-'}개 · "
         f"{'다시 읽어 확인 ✓' if inf['verified'] else ('확인 실패 ✕' if inf['verified'] is False else '이번 접속에서 저장 안 함')}"
         "</span></div>"
         f"<div class='ck'><span></span><span class='k'>지금 화면</span><span class='t'>종목 {len(book['holdings'])}개</span></div>"
         "</div>")
    c1, c2 = st.columns(2)
    if c1.button("저장 연결 다시 확인", icon=":material/sync:", width="stretch"):
        st.session_state.store_status = store.check()
        st.rerun()
    if c2.button("GitHub에서 다시 불러오기", icon=":material/cloud_download:", width="stretch",
                 help="앱을 새로 켰을 때와 똑같이 저장소에서 읽어 와요. 여기서 종목이 보이면 제대로 저장된 거예요."):
        st.session_state.book = store.load()
        st.rerun()
    a, b = st.columns([3, 1], vertical_alignment="center")
    if b.button("잠그기", icon=":material/lock:", width="stretch"):
        st.session_state.authed = False
        st.query_params.clear()
        st.rerun()
