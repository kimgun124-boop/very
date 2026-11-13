"""차가운 서버 첫 접속(cProfile) → 같은 서버 새 사용자 접속 시간을 재요."""
import os, sys, time, cProfile, pstats, io
APP = os.path.abspath(sys.argv[1]); OUT = sys.argv[2]
os.chdir(os.path.dirname(APP)); sys.path.insert(0, os.path.dirname(APP))
from streamlit.testing.v1 import AppTest
pr = cProfile.Profile()
at = AppTest.from_file(APP, default_timeout=900)
t0 = time.time(); pr.enable(); at.run(); pr.disable(); t1 = time.time() - t0
print(f"COLD first run {t1:.1f}s exc={[e.value[:200] for e in at.exception]}", flush=True)
s = io.StringIO(); ps = pstats.Stats(pr, stream=s)
ps.sort_stats("cumulative").print_stats(r"very/(app|data|scout|market|bigpicture|core|stocks|holdings|tracker|zongbae|views|recovery|theme|chart_draw|ipo|bear_check)\.py", 70)
open(f"{OUT}/cold_prof.txt", "w").write(f"cold {t1:.1f}s\n" + s.getvalue())
for i, wait in enumerate((5, 60)):
    time.sleep(wait)
    at2 = AppTest.from_file(APP, default_timeout=900)
    t0 = time.time(); at2.run()
    print(f"NEW VISITOR after {wait}s: {time.time()-t0:.1f}s exc={len(at2.exception)}", flush=True)
