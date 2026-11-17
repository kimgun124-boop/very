"""실제 네이버 자료로 탭별 열리는 시간을 재는 점검 도구(GitHub Actions 전용, 앱에는 영향 없음)."""
import asyncio, time, re, json, sys
from playwright.async_api import async_playwright
TABS = [("기본",["내 종목","차트","리포트","리스트"]),("신고가·돌파",["신고가 후보","신고가","신규상장","52주 최대 거래량","거래량 폭발","인더스트리 액션","매수 후보"]),
        ("타점·매매",["초기 매수","눌림 타점","1:2:6 매매","종가배팅"]),("섹터 흐름",["큰 그림","거래대금","급상승","차기 주도","회복 리더","증거 쌓기","글로벌 피어"]),
        ("시장·이벤트",["시장 신호","하락장 점검","시나리오","실적·지분","현금창출"])]
BUSY = "()=>!!document.querySelector('[data-testid=stStatusWidget]')"
async def idle(pg, cap=300):
    t0 = time.time(); await pg.wait_for_timeout(150)
    while await pg.evaluate(BUSY) and time.time() - t0 < cap: await pg.wait_for_timeout(40)
    return time.time() - t0
async def main():
    res = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1400, "height": 900})
        t0 = time.time(); await pg.goto("http://localhost:8501", timeout=600000)
        await pg.wait_for_selector('.st-key-main_nav', timeout=600000); d = await idle(pg, 600)
        print("FIRST_SCREEN", round(time.time() - t0, 1), flush=True)
        for rnd in (1, 2, 3):
            if rnd == 2: await pg.wait_for_timeout(60000)
            for g, tabs in TABS:
                await pg.locator('.st-key-main_nav button', has_text=g).first.click(); await idle(pg)
                await pg.wait_for_timeout(800)
                for t in tabs:
                    await pg.wait_for_timeout(700)
                    t0 = time.time()
                    await pg.locator('.st-key-main_nav button').filter(has_text=re.compile(re.escape(t) + '$')).first.click()
                    d = await idle(pg)
                    exc = await pg.evaluate("()=>[...document.querySelectorAll('[data-testid=stException],[data-testid=stAlertContentWarning]')].map(e=>e.innerText.slice(0,160))")
                    print(f"R{rnd} {t:14s} {d:6.2f}s {exc[:2]}", flush=True)
                    res.append({"r": rnd, "tab": t, "s": round(d, 2), "warn": exc[:3]})
        await b.close()
    json.dump(res, open("perf_result.json", "w"), ensure_ascii=False, indent=1)
asyncio.run(main())
