"""stocks.py의 종목코드가 네이버 종목명과 맞는지 점검합니다.

실행: python verify_codes.py   (윈도우는 verify.bat 더블클릭)
틀린 코드는 네이버 검색으로 후보 코드를 찾아 보여줘요.
"""
from __future__ import annotations

import data
from stocks import STOCKS

try:
    from stocks import PENDING
except ImportError:
    PENDING = []


def find_candidates(name: str) -> list[tuple[str, str]]:
    try:
        r = data.session.get(data.AUTOCOMPLETE_URL, params={"q": name, "target": "stock"}, timeout=6)
        payload = r.json()
    except Exception:  # 검색이 안 되면 후보 없이 넘어감
        return []
    found = []

    def walk(node):
        if isinstance(node, dict):
            code, nm = node.get("code"), node.get("name")
            if isinstance(code, str) and isinstance(nm, str) and code.isdigit() and len(code) == 6:
                found.append((code, nm))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return found[:3]


def main():
    ok, bad = 0, []
    kr_stocks = [s for s in STOCKS if data.is_kr(s["code"])]  # 해외 티커는 네이버로 점검할 수 없어 제외
    histories = data.fetch_histories([s["code"] for s in kr_stocks])
    for s in kr_stocks:
        naver_name, hist, error = histories[s["code"]]
        match = data.name_matches(s["name"], naver_name)
        if hist.empty:
            bad.append((s, f"조회 실패 ({error})"))
        elif match is False:
            bad.append((s, f"이름 불일치: 네이버에는 '{naver_name}'"))
        else:
            ok += 1

    print(f"\n정상 {ok}개 / 확인 필요 {len(bad)}개\n")
    for s, why in bad:
        print(f"- {s['name']} ({s['code']}): {why}")
        for code, nm in find_candidates(s["name"]):
            print(f"    후보: {nm} {code}")
    if bad:
        print("\nstocks.py에서 해당 줄의 코드를 후보 코드로 바꾸면 돼요. 사명만 바뀐 경우는 그대로 둬도 괜찮아요.")

    if PENDING:
        found, miss = data.resolve_codes([r[2] for r in PENDING])
        print(f"\n코드를 \"\"로 둔 종목 {len(PENDING)}개: 찾음 {len(found)}개 / 못 찾음 {len(miss)}개")
        for name, code in found.items():
            print(f"- {name}: {code}   (stocks.py의 \"\" 자리에 넣어 두면 앱이 검색하지 않아도 돼요)")
        for name, cands in miss.items():
            print(f"- {name}: 못 찾음" + ("".join(f"\n    후보: {nm} {c}" for c, nm in cands) if cands else ""))


if __name__ == "__main__":
    main()
