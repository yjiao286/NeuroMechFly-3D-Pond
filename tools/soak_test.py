"""混沌压力测试: 青蛙随机瞬移+连续吐舌+乱跳, 高频触发逃跑/中断/让位/重生全路径。

用法: python tools/soak_test.py [秒数]
通过标准: 无异常抛出; 结束时打印统计。
"""
import os
import sys
import random

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame  # noqa: E402

import main  # noqa: E402

V2 = pygame.math.Vector2


def run(seconds=300.0):
    pygame.init()
    g = main.Game(headless=True)
    g.autopilot = False
    rng = random.Random(1234)
    dt = 1 / 60
    frames = int(seconds / dt)
    eaten0 = g.frog.eaten
    meals = 0
    was = set()
    gf0 = 0
    for fr in range(frames):
        if fr % 45 == 0:                       # 每 0.75s 青蛙随机瞬移
            g.frog.pos.update(rng.uniform(-500, 500), rng.uniform(-270, 270))
            g.frog.heading = rng.uniform(0, 6.28)
        tongue = rng.random() < 0.08           # 8% 帧吐舌
        jump = rng.random() < 0.02
        g.simulate(dt, V2(0), jump, tongue)
        bf = g.brain_fly()
        if bf:
            gf0 = max(gf0, bf.brain.gf_count)
        for f in g.flies:
            if f.eating_now and id(f) not in was:
                meals += 1
                was.add(id(f))
            if not f.eating_now:
                was.discard(id(f))
    pygame.quit()
    print(f"[soak] {seconds:.0f}s OK  青蛙吃掉={g.frog.eaten - eaten0} "
          f"果蝇用餐次数={meals} 存活={len(g.flies)} GF最高={gf0}")


if __name__ == "__main__":
    run(float(sys.argv[1]) if len(sys.argv) > 1 else 300.0)
