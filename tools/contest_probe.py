"""对峙探针: 配对频率 / contest_t 累积分布 / 让位数。"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame  # noqa: E402

import main  # noqa: E402

V2 = pygame.math.Vector2


def run(seconds=240.0):
    pygame.init()
    g = main.Game(headless=True)
    g.autopilot = False
    dt = 1 / 60
    pair_frames = 0          # 任一配对存在的帧数
    max_ct = 0.0             # 达到过的最大 contest_t
    hist = {}                # contest_t 落入的区间
    yields0 = sum(getattr(f, "yielded", 0) for f in g.flies)
    for _ in range(int(seconds / dt)):
        g.simulate(dt, V2(0), False, False)
        bf = g.brain_fly()
        ct = bf.contest_t if bf else 0.0
        if ct > 0:
            pair_frames += 1
            max_ct = max(max_ct, ct)
            b = min(int(ct * 10), 9)
            hist[b] = hist.get(b, 0) + 1
    yields = sum(getattr(f, "yielded", 0) for f in g.flies) - yields0
    pygame.quit()
    print(f"配对帧={pair_frames}({100*pair_frames*dt/seconds:.1f}%时间) 最大contest_t={max_ct:.2f} 让位={yields}")
    print("contest_t分布(0.1s桶):", " ".join(f"{b/10:.1f}:{n}" for b, n in sorted(hist.items())))


if __name__ == "__main__":
    run(float(sys.argv[1]) if len(sys.argv) > 1 else 240.0)
