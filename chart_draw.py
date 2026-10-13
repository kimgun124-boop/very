"""차트 위에 수평선·추세선·형광펜을 그리고 지우는 기능.

st.plotly_chart 대신 같은 그림(fig)을 작은 HTML 칸 안에서 plotly.js로 직접 그려요.
그린 것은 그 브라우저에 종목별로 저장돼서, 시세가 갱신되거나 앱을 다시 열어도 그대로 남아요.
  - 수평선: 종목마다 하나의 묶음(일·주·월봉 어디서 봐도 같은 가격에 보여요)
  - 추세선·형광펜·칠하기: 종목 + 봉 종류(일/주/월)별로 따로 저장(날짜 기준이라 기간을 바꿔도 제자리)
PC와 휴대폰은 브라우저가 달라서 그림이 따로 저장돼요.
"""
import json

import streamlit as st

try:
    import plotly.offline as _po
    _PLOTLYJS_VER = _po.get_plotlyjs_version()
except Exception:       # 버전을 못 읽으면 넉넉한 최신판
    _PLOTLYJS_VER = "3.0.1"

_TOOLBAR_H = 66

_HTML = r"""<!doctype html><html lang="ko" translate="no"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<style>
 html,body{margin:0;padding:0;background:#fff;font-family:'Pretendard Variable',Pretendard,'Malgun Gothic',sans-serif;}
 #bar{display:flex;gap:6px;align-items:center;overflow-x:auto;white-space:nowrap;padding:4px 2px 2px;
      scrollbar-width:none;-webkit-overflow-scrolling:touch}
 #bar::-webkit-scrollbar{display:none}
 .b{flex:0 0 auto;border:1px solid #E4E6F0;background:#fff;color:#374151;border-radius:9px;padding:6px 11px;
    font:600 13px inherit;font-family:inherit;cursor:pointer;line-height:1.1;user-select:none}
 .b:hover{background:#F5F4FF}
 .b.on{background:#5B47F5;border-color:#5B47F5;color:#fff}
 .b.warn{color:#D93A3A}
 .sep{flex:0 0 1px;height:22px;background:#E4E6F0;margin:0 2px}
 .sw{flex:0 0 auto;width:22px;height:22px;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 1px #D5D8E3;cursor:pointer}
 .sw.on{box-shadow:0 0 0 2px #5B47F5}
 #hint{font-size:12px;color:#7A8A94;height:18px;line-height:18px;padding:2px 4px 0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
 #gd{width:100%}
</style></head><body>
<div id="bar">
 <span class="b on" data-t="pan">✋ 이동</span>
 <span class="b" data-t="hline">━ 수평선</span>
 <span class="b" data-t="line">╱ 추세선</span>
 <span class="b" data-t="pen">🖍 형광펜</span>
 <span class="b" data-t="rect">▭ 칠하기</span>
 <span class="b" data-t="erase">⌫ 지우개</span>
 <span class="sep"></span>
 <span class="b" id="undo">↶ 되돌리기</span>
 <span class="b warn" id="clear">🗑 전체 지우기</span>
 <span class="sep"></span>
 <span id="sws"></span>
</div>
<div id="hint"></div>
<div id="gd"></div>
<script>
const CFG = __CFG__;
const FIG = __FIG__;
function boot(){
 const gd = document.getElementById('gd');
 const HINTS = {
  pan:'드래그로 옮기고, 휠·두 손가락으로 확대해요. 그린 선은 눌러서 끌면 옮겨져요.',
  hline:'차트를 탭(클릭)하면 그 가격에 1자 수평선이 그어져요. 선을 끌면 위아래로 옮겨져요.',
  line:'시작점에서 끝점까지 드래그하면 직선이 그어져요.',
  pen:'드래그하는 대로 형광펜이 칠해져요.',
  rect:'드래그한 네모 영역이 형광펜처럼 칠해져요.',
  erase:'지울 선·형광펜을 탭(클릭)하면 지워져요.'};
 const COLORS = {
  line:['#E5484D','#2F6FEB','#111827','#F59E0B','#16A34A'],
  pen:['#FFE135','#7CFC6A','#FF8AD8','#5CE1FF','#FFB347']};
 const colorFor = t => (t==='pen'||t==='rect') ? 'pen' : 'line';
 let tool='pan', col={line:COLORS.line[0], pen:COLORS.pen[0]};

 // ── 저장소(브라우저별) ──
 const mem = {};
 const st = {
  get(k){ try{ const v = localStorage.getItem(k); return v ? JSON.parse(v) : null; }catch(e){ return mem[k]||null; } },
  set(k,v){ mem[k]=v; try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} }};
 const KH = 'vcb_hl:'+CFG.code, KD = 'vcb_dr:'+CFG.code+':'+CFG.tf;
 try{ const s = st.get('vcb_col'); if(s) col = Object.assign(col, s); }catch(e){}

 // ── x(날짜) ↔ 봉 위치 ──
 const cats = (FIG.data[0] && FIG.data[0].x) ? Array.from(FIG.data[0].x).map(String) : [];
 const cidx = {}; cats.forEach((c,i)=>cidx[c]=i);
 const N = cats.length;
 function toD(x){
  let v = Number(x); if(isNaN(v)) v = (x in cidx) ? cidx[x] : 0;
  const b = Math.min(Math.max(Math.round(v),0),N-1);
  return {d:cats[b], o:+(v-b).toFixed(3)};
 }
 function fromD(p){
  if(p.d in cidx) return cidx[p.d]+p.o;
  let lo=0, hi=N;               // 기간 밖 날짜: 가장 가까운 끝 바깥에 둬요
  while(lo<hi){ const m=(lo+hi)>>1; if(cats[m]<p.d) lo=m+1; else hi=m; }
  return lo<=0 ? -1+Math.min(p.o,0) : (lo>=N ? N+Math.max(p.o,0) : lo-0.5);
 }

 // ── 가격 표시·호가 단위 ──
 const fmt = v => Number(v).toLocaleString('ko-KR',{minimumFractionDigits:CFG.dec, maximumFractionDigits:CFG.dec});
 function snap(v){
  if(!CFG.krx) return +v.toFixed(CFG.dec);
  const t = v<2000?1:v<5000?5:v<20000?10:v<50000?50:v<200000?100:v<500000?500:1000;
  return Math.round(v/t)*t;
 }

 // ── 모양 만들기 ──
 function hlShape(y,c){
  return {type:'line', name:'hl', xref:'paper', x0:0, x1:1, yref:'y', y0:y, y1:y, editable:true,
   line:{color:c, width:1.6},
   label:{text:fmt(y), textposition:'end', yanchor:'bottom', font:{size:12, color:c}}};
 }
 function drShape(e){
  const xr=e.xr||'x', yr=e.yr||'y';
  const pts = e.p.map(([d,y])=>[fromD(d), y]);
  const s = {editable:true, xref:xr, yref:yr, name:'u_'+e.t};
  if(e.t==='path'){
   s.type='path'; s.path='M'+pts.map(p=>p[0]+','+p[1]).join('L');
   s.line={color:e.c, width:e.w||16}; s.opacity=e.op||0.45;
  } else if(e.t==='rect'){
   s.type='rect'; s.x0=pts[0][0]; s.y0=pts[0][1]; s.x1=pts[1][0]; s.y1=pts[1][1];
   s.fillcolor=e.c; s.line={width:0}; s.opacity=e.op||0.32;
  } else {
   s.type='line'; s.x0=pts[0][0]; s.y0=pts[0][1]; s.x1=pts[1][0]; s.y1=pts[1][1];
   s.line={color:e.c, width:e.w||2};
  }
  return s;
 }
 function parsePath(p){
  const out=[]; const re=/[ML]\s*([^,MLZ\s]+)\s*,\s*([^,MLZ\s]+)/g; let m;
  while((m=re.exec(p))) out.push([m[1], Number(m[2])]);
  return out;
 }

 // ── 그림 → 저장 ──
 const baseShapes = (FIG.layout.shapes||[]).map(s=>Object.assign({}, s, {name:'base', editable:false}));
 let hls = st.get(KH) || [], drs = st.get(KD) || [];
 let order = [];                  // 되돌리기용: 그린 순서
 function userShapes(){ return hls.map(h=>hlShape(h.y,h.c)).concat(drs.map(drShape)); }
 let busy=false;
 function redraw(){ busy=true; Plotly.relayout(gd, {shapes: baseShapes.concat(userShapes())}).then(()=>{busy=false;}); }
 function save(){ st.set(KH, hls); st.set(KD, drs); }
 function syncFromLayout(){
  const ss = (gd.layout.shapes||[]).filter(s=>s.name!=='base');
  const nh=[], nd=[]; let fix=false;
  ss.forEach(s=>{
   if(s.name==='hl'){
    const y = snap((Number(s.y0)+Number(s.y1))/2);
    if(s.x0!==0||s.x1!==1||s.y0!==y||s.y1!==y) fix=true;
    nh.push({y:y, c:(s.line&&s.line.color)||col.line});
   } else if(s.type==='path'){
    nd.push({t:'path', c:(s.line&&s.line.color)||col.pen, w:(s.line&&s.line.width)||16, op:s.opacity,
             xr:s.xref, yr:s.yref, p:parsePath(s.path||'').map(([x,y])=>[toD(x),y])});
   } else if(s.type==='rect'){
    nd.push({t:'rect', c:s.fillcolor||col.pen, op:s.opacity, xr:s.xref, yr:s.yref,
             p:[[toD(s.x0),Number(s.y0)],[toD(s.x1),Number(s.y1)]]});
   } else if(s.type==='line'){
    nd.push({t:'line', c:(s.line&&s.line.color)||col.line, w:(s.line&&s.line.width)||2, xr:s.xref, yr:s.yref,
             p:[[toD(s.x0),Number(s.y0)],[toD(s.x1),Number(s.y1)]]});
   }
  });
  // 새로 생긴 것이 있으면 되돌리기 순서에 넣어요
  if(nh.length>hls.length) order.push('h');
  if(nd.length>drs.length) order.push('d');
  hls=nh; drs=nd; save();
  if(fix) redraw();
 }

 // ── 도구 ──
 function setTool(t){
  tool=t;
  document.querySelectorAll('.b[data-t]').forEach(b=>b.classList.toggle('on', b.dataset.t===t));
  document.getElementById('hint').textContent = HINTS[t];
  const ns = {pen:{line:{color:col.pen, width:16}, opacity:0.45, fillcolor:'rgba(0,0,0,0)'},
              rect:{line:{width:0}, fillcolor:col.pen, opacity:0.32},
              line:{line:{color:col.line, width:2}, opacity:1}}[t];
  const dm = {line:'drawline', pen:'drawopenpath', rect:'drawrect'}[t] || 'pan';
  const u = {dragmode:dm}; if(ns) u.newshape = ns;
  Plotly.relayout(gd, u);
  drawSwatches();
 }
 function drawSwatches(){
  const box=document.getElementById('sws'); box.innerHTML='';
  box.style.display='inline-flex'; box.style.gap='6px';
  const k = colorFor(tool);
  COLORS[k].forEach(c=>{
   const s=document.createElement('span'); s.className='sw'+(col[k]===c?' on':''); s.style.background=c;
   s.title='색 고르기'; s.onclick=()=>{ col[k]=c; st.set('vcb_col', col); setTool(tool); };
   box.appendChild(s);
  });
 }
 document.querySelectorAll('.b[data-t]').forEach(b=>b.onclick=()=>setTool(b.dataset.t));
 document.getElementById('undo').onclick=()=>{
  let w = order.pop();
  if(!w) w = drs.length ? 'd' : (hls.length ? 'h' : null);
  if(w==='d') drs.pop(); else if(w==='h') hls.pop(); else return;
  save(); redraw();
 };
 document.getElementById('clear').onclick=()=>{
  if(!hls.length && !drs.length) return;
  if(!confirm('이 종목('+CFG.tf+')에 그린 선과 형광펜을 모두 지울까요?\n(수평선은 일·주·월봉 공통이라 같이 지워져요)')) return;
  hls=[]; drs=[]; order=[]; save(); redraw();
 };

 // ── 탭 위치 계산(수평선 긋기·지우개) ──
 function ax(ref,k){ const n = (ref||k).replace(/^[xy]/,''); return gd._fullLayout[(k==='x'?'xaxis':'yaxis')+n]; }
 function nearSeg(px,py,x1,y1,x2,y2){
  const dx=x2-x1, dy=y2-y1, L=dx*dx+dy*dy; let t = L ? ((px-x1)*dx+(py-y1)*dy)/L : 0;
  t=Math.max(0,Math.min(1,t)); const qx=x1+t*dx, qy=y1+t*dy; return Math.hypot(px-qx,py-qy);
 }
 function hitIndex(px,py){
  // 위에 그려진 것(나중 것)부터 찾아요
  const ss = userShapes(); let best=-1, bd=1e9;
  ss.forEach((s,i)=>{
   const xa = s.xref==='paper' ? null : ax(s.xref,'x'), ya = ax(s.yref,'y');
   if(!ya || (s.xref!=='paper' && !xa)) return;
   const X = v => xa._offset + xa.l2p(Number(v)), Y = v => ya._offset + ya.l2p(Number(v));
   let d=1e9;
   if(s.name==='hl'){ const xa1=gd._fullLayout.xaxis; if(px>=xa1._offset-4 && px<=xa1._offset+xa1._length+4) d=Math.abs(py-Y(s.y0)); }
   else if(s.type==='line') d=nearSeg(px,py,X(s.x0),Y(s.y0),X(s.x1),Y(s.y1));
   else if(s.type==='rect'){
    const l=Math.min(X(s.x0),X(s.x1)), r=Math.max(X(s.x0),X(s.x1)), t=Math.min(Y(s.y0),Y(s.y1)), b=Math.max(Y(s.y0),Y(s.y1));
    if(px>=l&&px<=r&&py>=t&&py<=b) d=0;
   } else if(s.type==='path'){
    const p = parsePath(s.path).map(([x,y])=>[X(x),Y(y)]);
    for(let j=1;j<p.length;j++) d=Math.min(d, nearSeg(px,py,p[j-1][0],p[j-1][1],p[j][0],p[j][1]));
    if(p.length===1) d=Math.hypot(px-p[0][0],py-p[0][1]);
    d -= ((s.line&&s.line.width)||16)/2;
   }
   const lim = 12;
   if(d<=lim && d<=bd){ bd=d; best=i; }
  });
  return best;
 }
 let down=null;
 gd.addEventListener('pointerdown', e=>{ down={x:e.clientX, y:e.clientY}; }, true);
 window.addEventListener('pointerup', e=>{
  if(!down) return; const moved = Math.hypot(e.clientX-down.x, e.clientY-down.y); down=null;
  if(moved>6 || (tool!=='hline' && tool!=='erase')) return;
  const r = gd.getBoundingClientRect(), px=e.clientX-r.left, py=e.clientY-r.top;
  if(tool==='hline'){
   const xa=gd._fullLayout.xaxis, ya=gd._fullLayout.yaxis;
   if(px<xa._offset||px>xa._offset+xa._length||py<ya._offset||py>ya._offset+ya._length) return;
   hls.push({y:snap(ya.p2l(py-ya._offset)), c:col.line}); order.push('h'); save(); redraw();
  } else {
   const i = hitIndex(px,py); if(i<0) return;
   if(i<hls.length) hls.splice(i,1); else drs.splice(i-hls.length,1);
   order=[]; save(); redraw();
  }
 }, true);

 // ── 그리기 ──
 const layout = Object.assign({}, FIG.layout, {shapes: baseShapes.concat(userShapes()), autosize:true});
 delete layout.width;
 Plotly.newPlot(gd, FIG.data, layout, {displaylogo:false, scrollZoom:true, responsive:true,
   modeBarButtonsToRemove:['select2d','lasso2d','autoScale2d']}).then(()=>{
  gd.on('plotly_relayout', ev=>{
   if(busy) return;
   if(Object.keys(ev).some(k=>k.indexOf('shapes')===0)) syncFromLayout();
  });
  setTool('pan');
 });
}
function load(srcs){
 const s=document.createElement('script'); s.src=srcs[0]; s.onload=boot;
 s.onerror=()=>{ if(srcs.length>1) load(srcs.slice(1));
  else document.getElementById('gd').textContent='차트 그림 도구를 불러오지 못했어요. 새로고침해 보세요.'; };
 document.head.appendChild(s);
}
load(['https://cdn.jsdelivr.net/npm/plotly.js-dist-min@__VER__/plotly.min.js',
      'https://unpkg.com/plotly.js-dist-min@__VER__/plotly.min.js']);
</script></body></html>"""


def render(fig, code: str, tf: str, currency: str = "KRW", key: str = "cc") -> bool:
    """그림 도구가 붙은 차트를 그려요. 실패하면 False(그때는 원래 st.plotly_chart로 그려요)."""
    try:
        import streamlit.components.v1 as components
    except Exception:
        components = None
    if components is None and not hasattr(st, "iframe"):
        return False
    dec = 0 if currency in ("KRW", "JPY") else 2
    cfg = {"code": str(code), "tf": tf, "dec": dec, "krx": currency == "KRW", "key": key}
    # Streamlit 화면은 축 글자 자리(여백)를 알아서 넓혀 줬는데, 직접 그릴 때는 직접 켜야 날짜·가격 글자가 안 잘려요
    import copy
    fig = copy.deepcopy(fig)
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)
    fig_json = fig.to_json().replace("</", "<\\/")
    page = (_HTML.replace("__CFG__", json.dumps(cfg, ensure_ascii=False))
            .replace("__VER__", _PLOTLYJS_VER)
            .replace("__FIG__", fig_json))
    height = int((fig.layout.height or 640) + _TOOLBAR_H)
    try:
        if components is not None and hasattr(components, "html"):
            components.html(page, height=height, scrolling=False)
        else:                               # components.html이 없어진 새 Streamlit
            st.iframe(page, height=height)
    except Exception:
        return False
    return True
