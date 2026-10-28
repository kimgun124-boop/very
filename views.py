"""📚 관점 학습 — 깡토의 추세추종 × 너쟁이(추노 스터디, 직장인 테크노 펀더멘털리스트) 인터뷰에서 뽑은 관점.

보드가 쓰는 곳
- 🌏 글로벌 피어: 한국 밸류체인 분류 ↔ 해외 같은 업종(피어)·수요처(고객사) 바구니를 나란히 — '우리만 빠지는가?'
- 🪜 증거 쌓기: 실적 서프라이즈 → 국내 섹터 액션 → 글로벌 피어 동반 → 리포트·뉴스 → 대장주·밸류.
  정보가 구체화될수록 비중을 늘리는(피라미딩) 단계를 분류별로 보여줘요.
- 👀 보유 점검: 수익 쿠션 훼손 · 타임컷 · 글로벌 피어 · 밸류 쿠션 항목(holdings.masters_view)
- 깡토 급락장 Q&A(KQ_VIEWS): 🚦 시장 신호 · 📉 하락장 점검 관점 노트, 보유 점검의 '같은 섹터 정찰병 우르르' 경고, AI 판단 참고
모든 판단은 효석 원칙(리스크관리 > 멘탈 > 종목 > 타점)이 우선이고, 아래 관점은 '재량'을 보태는 참고예요.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _is_kr(c) -> bool:
    import data
    return data.is_kr(c)

# ─────────────────────────── 관점(원문 요지) ───────────────────────────
NJ_SRC = "깡토의 추세추종 · 너쟁이(추노) 인터뷰"
NJ_VIEWS = [
    ("리스크", "리스크 관리가 전부", "언제·어떻게 사는가(타점·기법)보다 리스크 관리가 가장 중요. 2% 룰로 하방을 막고 상방만 본다. "
     "손절을 지키면 '나는 여전히 통계 안에 있다'."),
    ("쿠션", "수익 쿠션을 먼저 만든다", "시장 흐름을 따라 남보다 먼저(선제적으로) 들어가야 쿠션이 생기고, 쿠션이 있으면 위에서 흔들려도 견딘다. "
     "늦게 사면 안전 마진이 없어 휩소에 잦은 하차."),
    ("쿠션", "쿠션이 훼손되면 정리", "하락이 평균 단가를 위협하면(쿠션 훼손) '저평가니까 언젠간' 같은 희망 회로 없이 기계적으로 최대한 정리."),
    ("손절", "아이디어 훼손이면 1~2%라도", "7~8%는 통계적 기준일 뿐 — 아이디어가 훼손됐거나 시장이 너무 나쁘면 1~2%에서도 덜어내거나 타이트하게 손절."),
    ("손절", "타임컷", "아이디어대로라면 며칠 안에 올라야 한다. 며칠 안에 안 오르면 2~3%든 따지지 않고 정리. 제일 좋은 종목은 사자마자 오르는 종목."),
    ("발굴", "실적 서프라이즈 → 원인 → 수요처 → 노출도", "서프라이즈가 나면 '왜?'를 판다(예: 심텍 서프라이즈 → SOCAMM → 엔비디아 칩 수요 → "
     "엔비디아 매출 비중·노출도 큰 기업 → 테스트 소켓·프로브카드). 해외 뉴스(대만 쇼티지)·해외 피어 실적 코멘트로 좁힌다."),
    ("비중", "정보가 구체화될수록 피라미딩", "처음 실적은 '좋아질 수도' 수준 → 일부만. 해외 뉴스·피어 기업 섹터 액션이 확인될 때마다 집중도를 높인다. "
     "한 번에 다 사지 않고 증거가 쌓일 때마다."),
    ("비중", "저평가 쪽에 피라미딩", "성장은 빠른데 비싼 종목 vs 조금 느려도 싼 종목이면 후자 — 안전 마진이 있어야 적극 피라미딩·홀딩이 심리적으로 가능."),
    ("익절", "밸류가 과하면 일부만", "밸류가 너무 높아졌다고 판단되면 일부 익절, 나머지는 추세가 꺾일 때까지 끌고 간다(끝은 아무도 모름)."),
    ("시장", "글로벌 피어로 확인", "한국 소부장이 빠질 때 미국·대만·일본 피어를 본다 — 해외 피어가 안 꺾였으면 국내만의 흔들림일 수 있다. "
     "시장 흐름은 여러 나라가 같이 움직이는 경우가 많다."),
    ("시장", "글로벌 섹터 액션 > 국내 작은 테마", "AI가 주도 산업이면 그 안에, 그중에서도 글로벌로 같이 움직이는 섹터(후공정·OSAT 등)에 있으려 한다."),
    ("트리거", "트리거는 여러 개", "신고가 돌파는 트리거 중 하나 — 포켓피봇·정배열·섹터 액션·펀더멘털을 바꿀 뉴스도 트리거. "
     "단, 재량이 쌓인 '응용 단계'에서. 초보는 통계 룰 안에서."),
    ("선별", "이유 있는 상승만", "테크노 펀더멘털: 차트만 보면 시총 작은 종목·동전주도 걸린다. 펀더멘털 이유가 있는 상승만 추려서 따라간다."),
    ("습관", "매일 '왜 올랐나·왜 빠졌나'", "시장이 무엇에 주목하는지 매일 기록 — 작은 트리거도 수집해 손절을 각오하고 도전. 승률 30~40%라 용기 있게 계속 진입."),
    ("습관", "복기로 재량을 쌓는다", "투자 = 통계 + 재량. 재량은 복기로만 쌓인다 — 진입은 순간이라, 과거 경험이 정리돼 있어야 망설이지 않는다."),
    ("멘탈", "피곤할 때 뇌동매매 금지", "피곤한 상태·스스로 합리화하며 매수 버튼을 누를 때가 큰 손실(LNG 사례). 인지하면 당일 바로 손절."),
    ("공부", "넓게 알고 상방만 압축", "가치투자자만큼 깊게가 아니라 일반인보다 훨씬 많이 — 많은 기업을 '좋아질 포인트' 위주로 안다"
     "(너나위식: 전국 아파트를 다 알아야 저평가가 보인다). 하방은 손절·2% 룰이 막으니 이상한 회사만 아니면 상방 그림만 공부."),
    ("직장인", "타이밍보다 관리", "점심시간·종가 매수로 충분 — 흐름 안에 있으면 언제 샀는지보다 어떻게 관리하느냐가 중요. "
     "손절은 자동 주문(-7~8%)으로 걸어 두고, 며칠 안에 안 오르면 타임컷."),
    ("초보", "통계 안에서 놀기", "가치투자(미래 이익↑ → 주가↑)와 추세추종은 통계가 작동하지만 단타는 통계가 잘 안 맞는다. "
     "초보는 통계 룰을 정확히 지키면서 스터디·리포트·블로그로 재량을 늘려 간다 — 돈을 벌면서 실력이 느는 길."),
]

# 깡토 Q&A — 2026.10 급락장, 반도체 소부장 정찰병 복기(피에스케이홀딩스·심텍·엑시콘)
KQ_SRC = "깡토의 추세추종 Q&A(2026.10 급락장 · 소부장 정찰병 복기)"
KQ_VIEWS = [
    ("시장", "추세가 있느냐가 전부", "코어위브 부도설·AI 버블 논쟁·금리 같은 거시 노이즈는 복합 요인이라 답이 없다(결국 유동성 문제, 시간이 해결). "
     "추세추종은 '지금 추세가 진행 중인가'만 본다. AI 버블은 닷컴 때처럼 웬만한 종목이 10~20배 오른 적이 없어 아직 버블이라 부르기도 이르다는 관점."),
    ("시장", "한국은 원래 박스피", "횡보장의 잦은 손절로 계좌가 조금씩 갈리는 게 추세추종에서 가장 어려운 구간. "
     "예전과 다른 건 하루 변동성이 1~2% → 3~4%로 커져 사람을 피곤하게 만든다는 것뿐."),
    ("재진입", "선을 정하지 말고 FTD 추적", "'어디까지 회복하면 들어간다'는 선 대신 팔로스루데이 — 바닥권 반등 3~4일차에 강한 상승이 나오면 시도해 볼 만. "
     "비중은 점진적으로: 시장이 좋아지면 늘리고 나빠지면 줄인다. 바닥·상승 시점은 아무도 예상 못 한다."),
    ("재진입", "추세추종은 늦은 매매", "반등을 예상하고 미리 사지 않는다. 추세가 다 나온 뒤 천천히 들어가도 늦지 않다. "
     "쉴 때는 쉬는 게 최선 — 바닥 매매할 필요 없음. 대부분은 못 쉬어서 문제."),
    ("정찰병", "정찰병은 죽는 게 당연", "시장 회복은 순식간이라 타이밍을 알 수 없다 → 쉬는 중에도 정찰병을 조금씩 넣어 두고, "
     "반등이 나와 달려가면 그때 빠르게 비중을 늘린다. 수익이든 손실이든 계좌에 큰 영향이 없어야 정찰병(모니터링 단계)."),
    ("정찰병", "같은 섹터에 우르르 넣지 않기", "다 반도체인데 반도체 정찰병을 여러 개 한꺼번에·너무 빨리 넣으면 작은 손절이 모여 계좌가 갈린다. "
     "바스켓 매매는 좋지만 종목 수만큼 모니터링·대응이 될 때만."),
    ("손절", "변동성이 커도 손절을 넓히지 않는다", "'-8%보다 넓게 잡았으면 안 털렸을 것' — 넓히면 손실만 더 커진다. "
     "'언젠간 반등'·존버는 가치투자의 언어고, 추세가 꺾이면 나오는 추세추종 사전엔 없다."),
    ("타점", "늦었다/빠르다의 기준 = 첫 신고가", "섹터가 신고가를 가기 시작할 때만 샀어도 대부분 소폭 수익으로 마무리. "
     "사람들은 새롭고 신선한 걸 좋아한다 — 누가 먼저 달리고, 먼저 거래량이 터지고, 먼저 신고가를 가느냐가 기본 키포인트."),
    ("타점", "부족함 없는 돌파일", "거래량 전일 2배·평균 이상 + 신고가 갱신 + 장대양봉이 한날 나오면 부족함이 없는 날"
     "(예: 피에스케이홀딩스 9/29). 그날 샀다면 정찰병이어도 소폭 수익으로 나왔을 자리."),
    ("재진입", "손절한 종목도 다시 신고가면 다시 본다", "반등 하루로는 오를지 내릴지 알 수 없다. 전고점(신고가 근처) 돌파가 기준이고, "
     "최소한 포켓피봇·로우치트 + 장대양봉은 나와야 의미를 가져 볼 만(예: 심텍 — 15만 원대 장대양봉이면 관심, 16만 원대면 신고가 재도전으로 봄)."),
    ("하락장", "하락장에서 버틴 종목을 길게", "과대낙폭은 반등이 빠르지만 3~5개월 보면 대부분 다시 하락. "
     "조정장에서 먼저 신고가를 찍고 버틴 주도주·강한 섹터를 체크해 길게 끌고 가는 게 훨씬 효율적(예: 엑시콘 — 시총은 작음)."),
    ("핵심", "슈퍼루키 1~2종목이 계좌를 끈다", "계좌를 올리는 건 자잘한 여러 종목이 아니라 강하게 오르는 한두 종목. "
     "일찍 사느냐가 아니라, 늦게 사더라도 추세가 강력하면 피라미딩으로 강한 종목에 비중을 싣는 것이 핵심 — 강한 추세 종목을 놓치지 말 것."),
    ("시장", "한국에 추세가 없으면 다른 시장도", "국내에 추세가 없어도 다른 섹터·산업·시장(미국·아시아)엔 있을 수 있다. 꼭 한국만 고집할 필요 없음."),
    ("성향", "저점매수는 종목을 빠삭하게 알 때만", "시장이 빠져도 이 기업은 문제없다고 확신할 만큼 알아야 가치투자 저점 매수가 된다(제대로 하려면 4~5년). "
     "추세추종은 강한 종목을 사면 되니 기준이 명확 — 추세 없는 장은 공부할 기회. 하이브리드(테크노 펀더멘털)냐 순수 차트냐는 성향."),
]

# 리 태너(오닐 CAN SLIM, TraderLion 강연 정리) — 매수 가격·초기 매수·포트폴리오·매도·사후 분석
LT_SRC = "리 태너 강연(TraderLion) 정리"
LT_VIEWS = [
    ("매수", "피벗에서 정확히, 가능하면 더 싸게", "표준 피벗 포인트에 최대한 가깝게. 숙련되면 표준 피벗보다 낮은 초기 매수 지점(Early Entry)을 적극 활용 — "
     "같은 7% 손절이어도 손절 가격이 낮아져 정상 조정에 덜 털리고, 실패도 빨리 알아챔."),
    ("매수", "초기 매수 지점 4+2", "컵 위드 핸들: 표준 핸들 고점 / 컵→핸들 하락 추세선 / 핸들 고점 추세선 / 조용한 거래량의 핸들 저점. "
     "이중바닥: 하락 추세선 돌파 / Shakeout +3(두 번째 저점이 첫 저점을 깼다가 첫 저점 +$3(30~60달러), +10%(30달러 미만), +5%(60달러 초과) 회복)."),
    ("매수", "거래량 확인 전에 산다", "돌파 거래량 +50%를 원하지만 확인되면 이미 피벗보다 1~2% 위 — 미리 정한 가격에 스톱 주문으로 산다. "
     "거래량이 안 붙어 실패할 위험은 손절 규칙으로 감수."),
    ("포트", "5~7종목 동일 비중", "자금에 따라 5개(20%)·6개(16.7%)·7개(14.3%). 7% 손절이면 종목당 계좌 -1.0~1.4%. 여러 종목이 동시에 깨지면 손실이 커지니 "
     "포트 전체 위험 노출도 본다. 최대 50% 마진."),
    ("추가", "성공한 종목에만 추가", "물타기 대신 상승 종목에: 50일선 반등, 3~4주 타이트 구간 뒤 돌파, 컵위드핸들 뒤 신고가, 새 베이스 돌파. "
     "추가 규모는 직전 총보유의 20%(1,000→1,200→1,440→1,728주). 마지막 매수가 +6% 안에서만(넘으면 절반 또는 생략), 목표까지 5% 남으면 추가 안 함."),
    ("매도", "7% 손절, 실제론 더 빨리", "매수가 -7% 손절(장중 흔들림 방지 스톱은 -9%), 돌파 실패·거래량 실린 급락이면 2~3%에서도 정리. "
     "+10% 전까지는 최초 손절 유지, +10% 뒤엔 최고가 대비 12~14% 트레일링."),
    ("매도", "8주 보유 예외", "피벗 돌파 뒤 3주 안에 +20%면 대형 성장주 가능성 — 목표 도달해도 최소 8주 보유(위험 신호는 별도로)."),
    ("매도", "+50% 뒤엔 주봉", "초기 매수가 +50% 이상이면 주봉 규칙: 10주선을 처음 깨는 주에 거래량이 평균보다 40%↑면 중요 매도 신호. "
     "10주선 아래 장기 부진·클라이맥스도 매도 고려. 크리스 캐처식: 10일선 아래 마감한 날의 저가를 다음에 깨면 매도."),
    ("시장", "조정장에도 기회비용", "하락이 무서워 반등을 놓치는 것도 비용 — 반등 실패 손실(-3% 등)은 참여 비용으로 받아들임. "
     "조정장에서 이전 저점을 지키고 RS선이 먼저 신고가를 내는 종목(직전 IBD 50·주도주)을 먼저 본다."),
    ("복기", "사후 분석으로 규칙 개선", "수익 거래: 실적 성장·사전 상승·베이스 종류·돌파 거래량·RS·시장·매수 가격·추가 매수·최대 상승/조정·매도 시점. "
     "손실 거래: 정상 손절(시스템 안 손실) vs 규칙 위반(개선할 문제)을 구분."),
]
# 깡토 — 분산일·FTD 한국 지수 백테스트, 하락장 과대낙폭 vs 견조 종목
KT_SRC = "깡토 블로그(분산일·FTD 35년 백테스트, 하락장 과대낙폭 vs 견조)"
KT_VIEWS = [
    ("FTD", "FTD는 '허락'이지 '공격'이 아님", "하락장 바닥 뒤엔 항상 FTD가 나왔지만(코스피 25/25, 코스닥 24/24, 중앙값 바닥 뒤 7거래일) "
     "하락장 FTD 52번 중 25번은 진짜 바닥 전에 나왔다가 실패. 나오기 전엔 올라타지 않고, 나오면 점진 베팅, 랠리 저점을 깨면 판단을 되돌림."),
    ("분산일", "책 기준 분산일은 한국에선 경고 과다", "5주 4~5개·-0.2% 기준을 코스피에 그대로 쓰면 상승장 아무 날의 87%가 경고 상태 — "
     "천장 앞이라고 더 많지 않음. 한국 지수 변동성에 맞춰 '평소 대비'로 봐야 의미."),
    ("하락장", "과대낙폭은 짧게, 견조주는 길게", "하락장 매수 1개월은 과대낙폭(-67%)이 강했지만 1년 뒤 중앙값 -19.9%·오른 비율 29%. "
     "견조주(고점 -8%)는 1년 +16.7%, FTD 뒤 반년 17번 중 15번 과대낙폭을 앞섬."),
    ("하락장", "맨 끝 -60%는 피한다", "문제는 고점에서 3분의 2가 날아간 맨 끝 10% — 대개 실적·업황·인기 붕괴 이유가 있음. 사면 짧게, 틀렸을 때 나올 자리 먼저, 물타기 금지."),
    ("하락장", "종목보다 시장이 먼저", "과대낙폭 매수 = 바닥에 거는 베팅(V자면 승, 긴 하락장이면 대패). 시장 위치(상승 추세·둔화·조정·반등 시도)부터 확인. "
     "추세추종인데 하락장에 현금 비중이 낮다면 '추세추종 호소인'인지 돌아보기."),
]
AI_VIEWS = "\n".join(f"- {t}: {x}" for _, t, x in NJ_VIEWS)
AI_VIEWS += "\n" + "\n".join(f"- [리 태너] {t}: {x}" for _, t, x in LT_VIEWS)
AI_VIEWS += "\n" + "\n".join(f"- [깡토 시장] {t}: {x}" for _, t, x in KT_VIEWS)
AI_VIEWS += "\n" + "\n".join(f"- [깡토 급락장 Q&A] {t}: {x}" for _, t, x in KQ_VIEWS)

# ─────────────────────────── 한국 분류 ↔ 해외 피어·수요처 ───────────────────────────
# peers = 같은 일을 하는 해외 기업(보드의 해외 분류 이름), demand = 이 분류의 수요처(고객사) 바구니
_SEMI_DEMAND = ["AI 인프라: 본진", "하이퍼스케일러"]
PEER_MAP: dict[str, dict] = {
    "후공정 테스트 장비": {"peers": ["AI 인프라: 테스트·계측"], "demand": _SEMI_DEMAND},
    "후공정 테스트 부품": {"peers": ["AI 인프라: 테스트·계측"], "demand": _SEMI_DEMAND},
    "후공정 패키징·검사 장비": {"peers": ["AI 인프라: 장비·패키징"], "demand": _SEMI_DEMAND},
    "OSAT": {"peers": ["AI 인프라: 장비·패키징"], "demand": _SEMI_DEMAND},
    "패키징 소재": {"peers": ["AI 인프라: 장비·패키징"], "demand": _SEMI_DEMAND},
    "전공정 장비": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "전공정 계측": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "전공정 부품": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "전공정 소재": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "인프라 장비": {"peers": ["반도체 장비(글로벌)"], "demand": ["메모리(글로벌)", "AI 인프라: 본진"]},
    "메모리·파운드리": {"peers": ["메모리(글로벌)", "파운드리(글로벌)"], "demand": ["하이퍼스케일러", "칩 설계(GPU·CPU·ASIC)"]},
    "기판": {"peers": ["기판(해외)", "MLCC"], "demand": _SEMI_DEMAND},
    "MLCC·수동부품": {"peers": ["MLCC"], "demand": _SEMI_DEMAND},
    "설계(IP·팹리스·디자인하우스)": {"peers": ["칩 설계(GPU·CPU·ASIC)", "EDA"], "demand": ["하이퍼스케일러"]},
    "AIDC 전력기기": {"peers": ["전력·에너지(해외)"], "demand": ["하이퍼스케일러", "네오클라우드"]},
    "AIDC 전선": {"peers": ["전력·에너지(해외)"], "demand": ["하이퍼스케일러", "네오클라우드"]},
    "AIDC 발전·개발": {"peers": ["AIDC 온사이트 발전", "전력·에너지(해외)"], "demand": ["하이퍼스케일러"]},
    "AIDC 온사이트 발전": {"peers": ["AIDC 온사이트 발전"], "demand": ["하이퍼스케일러"]},
    "AIDC ESS": {"peers": ["전력·에너지(해외)"], "demand": ["하이퍼스케일러"]},
    "AIDC 태양광·풍력 제조": {"peers": ["전력·에너지(해외)"], "demand": ["하이퍼스케일러"]},
    "2차전지 소재": {"peers": ["리튬"], "demand": ["자동차·로봇·소재(해외)"]},
    "리튬·원료": {"peers": ["리튬"], "demand": ["자동차·로봇·소재(해외)"]},
    "희토류": {"peers": ["희토류"], "demand": []},
    "발사체·위성": {"peers": ["발사체·위성"], "demand": []},
    "우주 특수합금": {"peers": ["우주 특수합금"], "demand": ["발사체·위성"]},
    "방산·우주": {"peers": ["방산·우주(해외)"], "demand": []},
    "AI·소프트웨어": {"peers": ["AI 소프트웨어"], "demand": ["하이퍼스케일러"]},
    "보안·양자": {"peers": ["사이버보안", "양자컴퓨팅"], "demand": []},
    "통신·네트워크·광": {"peers": ["AI 인프라: 신호·광학"], "demand": ["하이퍼스케일러", "AI 인프라: 본진"]},
    "의료기기·AI·유통": {"peers": ["바이오(해외)"], "demand": []},
}


def _stats_one(df_h: pd.DataFrame | None, live: float | None = None) -> dict | None:
    """일봉 하나 → 1·5·20일 수익률, 20일선 위, 52주 고점 거리. (해외는 마지막 거래일 기준)"""
    if df_h is None or len(df_h) < 30:
        return None
    c = pd.to_numeric(df_h["close"], errors="coerce").to_numpy(dtype=float)
    h = pd.to_numeric(df_h["high"], errors="coerce").to_numpy(dtype=float)
    if live and live > 0 and c[-1] > 0 and abs(live / c[-1] - 1) < 0.31:      # 상하한가(±30%) 안의 실시간 가격만
        try:
            last = pd.Timestamp(df_h["date"].iloc[-1]).date()
        except (KeyError, ValueError, TypeError):
            last = None
        import data
        today = data.now_kst().date()
        if last == today:                              # 오늘 봉이 있으면 덮어쓰고
            c = c.copy()
            c[-1] = live
        elif data.market_status({}).startswith("장중") and last is not None and last < today:
            c, h = np.append(c, live), np.append(h, live)    # 장중인데 오늘 봉이 아직 없으면 붙여요
    c = c[np.isfinite(c)]
    if len(c) < 30 or c[-1] <= 0:
        return None
    hi = np.nanmax(h[-250:]) if len(h) else np.nan
    hi = max(hi, c[-1]) if hi == hi else c[-1]
    return {"r1": (c[-1] / c[-2] - 1) * 100, "r5": (c[-1] / c[-6] - 1) * 100, "r20": (c[-1] / c[-21] - 1) * 100,
            "a20": bool(c[-1] > np.mean(c[-20:])), "near": bool(c[-1] >= hi * 0.95)}


def basket(codes, histories: dict, live: dict | None = None) -> dict | None:
    """바구니(여러 종목)의 중앙값 수익률·20일선 위 비율·신고가 5% 안 비율."""
    rows = []
    for c in codes:
        st = _stats_one((histories.get(c) or (None, None, None))[1], (live or {}).get(c))
        if st:
            rows.append(st)
    if not rows:
        return None
    t = pd.DataFrame(rows)
    return {"n": len(t), "r1": float(t["r1"].median()), "r5": float(t["r5"].median()), "r20": float(t["r20"].median()),
            "a20": float(t["a20"].mean() * 100), "near": float(t["near"].mean() * 100)}


def _strong(b: dict | None) -> bool | None:
    if not b:
        return None
    return bool(b["r20"] > 0 and b["a20"] >= 60)


def _weak(b: dict | None) -> bool | None:
    if not b:
        return None
    return bool(b["r5"] < 0 and b["a20"] < 40)


def peer_verdict(kr: dict | None, pe: dict | None) -> tuple[str, str]:
    """(라벨, 설명) — 너쟁이 관점: 해외 피어가 안 꺾였으면 국내만의 흔들림일 수 있다."""
    if not kr or not pe:
        return "자료 부족", "해외 피어 일봉이 아직 없어요."
    kr_dn = kr["r5"] < -2 or kr["a20"] < 40
    kr_up = kr["r5"] > 0 and kr["a20"] >= 60
    if kr_dn and _strong(pe):
        return "🟡 한국만 약함", "해외 피어는 20일선 위·상승 중 — 국내만의 흔들림(수급·반대매매)일 수 있어요. 펀더멘털 훼손 신호는 아님."
    if kr_dn and _weak(pe):
        return "🔴 글로벌 동반 약세", "해외 피어도 같이 꺾임 — 섹터 전체 조정. 쿠션 없는 포지션은 리스크 관리 먼저."
    if kr_up and _strong(pe):
        return "🟢 글로벌 섹터 액션", "한국·해외가 같이 강함 — 글로벌 흐름 안의 섹터(너쟁이가 선호하는 자리)."
    if kr_up and _weak(pe):
        return "🟠 국내만 강함", "해외 피어는 약한데 국내만 강함 — 국내 테마성일 수 있어 신뢰도 낮춤."
    return "⚪ 혼조", "뚜렷한 동반·괴리 없음."


def peer_table(df: pd.DataFrame, histories: dict) -> list[dict]:
    """한국 분류마다 국내 바구니 vs 해외 피어 vs 수요처."""
    kr = df[df["code"].map(_is_kr)]
    live = {c: p for c, p in zip(kr["code"], pd.to_numeric(kr["price"], errors="coerce")) if p == p}
    os_ = df[~df["code"].isin(kr["code"])]
    by_os = {g: list(s["code"]) for g, s in os_.groupby("group")}
    out = []
    for g, sub in kr.groupby("group"):
        mp = PEER_MAP.get(g)
        if not mp:
            continue
        pcodes = [c for x in mp["peers"] for c in by_os.get(x, [])]
        dcodes = [c for x in mp["demand"] for c in by_os.get(x, [])]
        kb = basket(sub["code"], histories, live)
        pb = basket(pcodes, histories)
        db = basket(dcodes, histories) if dcodes else None
        lab, why = peer_verdict(kb, pb)
        out.append({"group": g, "sector": sub["sector"].iloc[0], "kr": kb, "peer": pb, "demand": db,
                    "peer_names": ", ".join(os_.set_index("code").loc[[c for c in pcodes if c in set(os_["code"])], "name"]
                                            .astype(str).head(8)) if pcodes else "",
                    "peer_groups": " · ".join(mp["peers"]), "demand_groups": " · ".join(mp["demand"]),
                    "verdict": lab, "why": why, "peer_strong": _strong(pb), "demand_strong": _strong(db)})
    return out


def kr_peer_leaders(df: pd.DataFrame, rs_min: float = 70, grs_min: float = 70, min_universe: int = 4) -> pd.DataFrame:
    """글로벌 피어 대비 강한 한국 종목.
    글로벌 RS = 같은 분류의 국내 종목 + 해외 피어 종목을 한 무리로 놓고, 보드와 같은 오닐식 RS 원점수(rs_raw:
    0.4×3개월 + 0.2×6·9·12개월 수익률, 각 시장 통화 기준)의 백분위(1~99). 피어 초과 = 종목 수익률 − 피어 바구니 중앙값."""
    cols = ["code", "name", "group", "sector", "rs", "grs", "g_rank", "g_n", "ex1m", "ex3m", "ret_1m", "ret_3m",
            "peer_1m", "peer_3m", "peer_top", "peer_top_ret3m", "price", "change", "to_high", "lead_rank", "lead_ok",
            "cap_krw", "peer_groups", "ok"]
    if df is None or df.empty or "rs_raw" not in df:
        return pd.DataFrame(columns=cols)
    kr = df[df["code"].map(_is_kr)].drop_duplicates("code")
    os_ = df[~df["code"].map(_is_kr)].drop_duplicates("code")
    num = lambda s: pd.to_numeric(s, errors="coerce")  # noqa: E731
    rows = []
    for g, sub in kr.groupby("group"):
        mp = PEER_MAP.get(g)
        if not mp:
            continue
        peers = os_[os_["group"].isin(mp["peers"])]
        peers = peers[num(peers["rs_raw"]).notna()]
        if len(peers) < 2:
            continue
        uni = pd.concat([sub[["code", "rs_raw"]], peers[["code", "rs_raw"]]])
        uni = uni[num(uni["rs_raw"]).notna()]
        if len(uni) < min_universe:
            continue
        raw = num(uni["rs_raw"])
        pct = (raw.rank(pct=True, method="average") * 98 + 1).round()
        grs = dict(zip(uni["code"], pct))
        rank = dict(zip(uni["code"], raw.rank(ascending=False, method="min").astype(int)))
        p1, p3 = float(num(peers.get("ret_1m")).median()), float(num(peers.get("ret_3m")).median())
        top = peers.loc[num(peers["rs_raw"]).idxmax()]
        for r in sub.itertuples():
            if r.code not in grs:
                continue
            g_rs, rs = grs[r.code], getattr(r, "rs", None)
            r1, r3 = getattr(r, "ret_1m", None), getattr(r, "ret_3m", None)
            ex1 = None if r1 is None or r1 != r1 or p1 != p1 else float(r1) - p1
            ex3 = None if r3 is None or r3 != r3 or p3 != p3 else float(r3) - p3
            ok = bool(rs is not None and rs == rs and rs >= rs_min and g_rs >= grs_min and (ex3 is None or ex3 > 0))
            rows.append({"code": r.code, "name": r.name, "group": g, "sector": getattr(r, "sector", None),
                         "rs": rs, "grs": g_rs, "g_rank": rank[r.code], "g_n": len(uni), "ex1m": ex1, "ex3m": ex3,
                         "ret_1m": r1, "ret_3m": r3, "peer_1m": p1, "peer_3m": p3,
                         "peer_top": str(top.get("name")), "peer_top_ret3m": top.get("ret_3m"),
                         "price": getattr(r, "price", None), "change": getattr(r, "change", None),
                         "to_high": getattr(r, "to_high", None), "lead_rank": getattr(r, "lead_rank", None),
                         "lead_ok": getattr(r, "lead_ok", None), "cap_krw": getattr(r, "cap_krw", None),
                         "peer_groups": " · ".join(mp["peers"]), "ok": ok})
    out = pd.DataFrame(rows, columns=cols)
    if out.empty:
        return out
    return out.sort_values(["ok", "grs", "rs"], ascending=False).reset_index(drop=True)


# ─────────────────────────── 증거 사다리(피라미딩 단계) ───────────────────────────
def fwd_per(fin: pd.DataFrame | None, price: float | None) -> tuple[float | None, float | None]:
    """(올해 예상 PER, PEG). EPS 예상(E)이 없으면 최근 실적 EPS."""
    if fin is None or fin.empty or "eps" not in fin or not price:
        return None, None
    act = fin[~fin["is_est"]].dropna(subset=["eps"]) if "is_est" in fin else fin.dropna(subset=["eps"])
    est = fin[fin["is_est"]].dropna(subset=["eps"]) if "is_est" in fin else fin.iloc[0:0]
    eps = float(est["eps"].iloc[0]) if len(est) else (float(act["eps"].iloc[-1]) if len(act) else None)
    if not eps or eps <= 0:
        return None, None
    per = price / eps
    base = float(act["eps"].iloc[-1]) if len(act) else None
    g = (eps / base - 1) * 100 if base and base > 0 and len(est) else None
    return per, (per / g if g and g > 0 else None)


LADDER = [("실적", "실적 서프라이즈"), ("섹터", "국내 섹터 액션"), ("글로벌", "글로벌 피어 동반"), ("근거", "리포트·뉴스 뒷받침")]
STAGE_TXT = {0: "관찰", 1: "정찰병(1유닛 미만)", 2: "1유닛", 3: "2유닛", 4: "3유닛(최대)"}


def evidence_ladder(df: pd.DataFrame, peers: list[dict], surprises: list[dict], reports: list[dict],
                    fins: dict, leaders: set, recent_days: int = 30) -> pd.DataFrame:
    """분류별 증거 4단계 + 대장주·밸류. 증거 개수 = 피라미딩 단계(비중은 원칙의 3유닛·종목당 1.5% 위험 안에서)."""
    kr = df[df["code"].map(_is_kr)].copy()
    if kr.empty:
        return pd.DataFrame()
    pmap = {p["group"]: p for p in peers}
    by_code_group = dict(zip(kr["code"], kr["group"]))
    sur_g: dict = {}
    for s in surprises:
        g = by_code_group.get(s.get("code"))
        if g and (s.get("beat") or 0) > 0:
            sur_g.setdefault(g, []).append(s)
    cut = (pd.Timestamp.now(tz="Asia/Seoul") - pd.Timedelta(days=recent_days)).strftime("%Y-%m-%d")
    rep_g: dict = {}
    for r in reports:
        if str(r.get("added_at") or r.get("date") or "")[:10] < cut:
            continue
        for s in r.get("stocks") or []:
            g = by_code_group.get(s.get("code"))
            if g:
                rep_g.setdefault(g, set()).add(r.get("title") or "리포트")
    rows = []
    for g, sub in kr.groupby("group"):
        hot = sub[pd.to_numeric(sub["days_since_high"], errors="coerce") <= 5]
        tvx = pd.to_numeric(sub.get("tv_x"), errors="coerce") if "tv_x" in sub else pd.Series(dtype=float)
        surge = int(((tvx >= 1.5) & (pd.to_numeric(sub["change"], errors="coerce") > 0)).sum()) if len(tvx) else 0
        sect = g in leaders or len(hot) >= 2 or surge >= 2
        p = pmap.get(g)
        glob = bool(p and p.get("peer_strong"))
        sur = sur_g.get(g, [])
        rep = rep_g.get(g, set())
        steps = {"실적": bool(sur), "섹터": sect, "글로벌": glob, "근거": bool(rep)}
        n = sum(steps.values())
        lead = sub.sort_values(["lead_rank"] if "lead_rank" in sub else ["rs"], ascending=True if "lead_rank" in sub else False,
                               na_position="last").iloc[0]
        per, peg = fwd_per(fins.get(lead["code"]), float(lead["price"]) if lead.get("price") == lead.get("price") else None)
        rows.append({"group": g, "sector": sub["sector"].iloc[0], "stage": n, "stage_txt": STAGE_TXT[n], **steps,
                     "sur_txt": ", ".join(f"{s.get('name') or s.get('code')} {s['beat']:+.0f}%" for s in sur[:3]),
                     "sect_txt": (f"신고가 {len(hot)}" + (f" · 거래 1.5배↑ {surge}" if surge else "") + (" · 주도섹터" if g in leaders else "")),
                     "glob_txt": (p["verdict"] if p else "해외 피어 없음"),
                     "rep_txt": ", ".join(list(rep)[:2]),
                     "leader": f"{lead['name']} · {lead['code']}", "lead_rs": lead.get("rs"), "lead_to_high": lead.get("to_high"),
                     "per": per, "peg": peg, "n": len(sub)})
    out = pd.DataFrame(rows)
    return out.sort_values(["stage", "sector"], ascending=[False, True]).reset_index(drop=True)


def value_cushion(per: float | None, peg: float | None) -> tuple[str, str]:
    """너쟁이: 저평가면 피라미딩이 편하고, 고평가면 일부 익절·추가 매수 신중."""
    if per is None:
        return "info", "예상 PER 없음"
    if peg is not None and peg <= 1.0:
        return "good", f"PER {per:.1f} · PEG {peg:.2f} — 성장 대비 싼 편: 피라미딩 심리 여유"
    if per >= 40 or (peg is not None and peg >= 2.0):
        return "warn", f"PER {per:.1f}" + (f" · PEG {peg:.2f}" if peg else "") + " — 비싼 편: 추가 매수 신중, 과열이면 일부 익절"
    return "info", f"PER {per:.1f}" + (f" · PEG {peg:.2f}" if peg else "")
