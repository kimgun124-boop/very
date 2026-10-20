"""🌍 매크로 아침 브리핑 (GitHub Actions에서 평일 아침 한 번 실행).

앱의 🌍 매크로 시황 탭과 같은 데이터(macro.snapshot)로 미국 마감 숫자와 교차확인 뉴스만 디스코드로 보내요.
필요한 설정: GitHub 저장소 Settings → Secrets → Actions 에 DISCORD_WEBHOOK_URL (매수 알림과 같은 값)
테스트: python macro_brief.py  (웹훅이 없으면 화면에만 출력)
"""
from __future__ import annotations

import alert_ia
import macro


def fmt(snap: dict) -> str:
    sm, ver = snap["sm"], snap["ver"]
    lines = [f"🌍 **매크로 브리핑** {snap['at']:%m/%d %H:%M}", "· " + "\n· ".join(macro.facts(snap)), ""]
    for i in macro.INDICES[:8]:
        s = sm.get(i["yf"])
        if s and s["d1"] is not None:
            lines.append(f"{i['name']} {s['last']:,.2f} ({s['d1']:+.2f}%) [{ver.get(i['yf'], ('없음',))[0]}] {s['date']:%m/%d}")
    for m in macro.MACRO:
        s = sm.get(m["yf"])
        if s and m["kind"] == "rate" and s.get("a1") is not None:
            lines.append(f"{m['name']} {s['last']:.3f}% ({s['a1']:+.3f}%p)")
        elif s and m["name"] in ("원/달러", "WTI", "금") and s["d1"] is not None:
            lines.append(f"{m['name']} {s['last']:,.2f} ({s['d1']:+.2f}%)")
    news = [n for n in snap["news"] if n["status"] == "교차확인"][:8]
    if news:
        lines += ["", "📰 교차확인 뉴스 (언론사 2곳 이상)"]
        lines += [f"· {n['title']} — {', '.join(n['sources'][:3])}" for n in news]
    return "\n".join(lines)


if __name__ == "__main__":
    alert_ia.send(fmt(macro.snapshot()))
