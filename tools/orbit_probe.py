"""绕食极限环探针: 果蝇在 9~60px 处转圈不落地的程度。

用法: python tools/orbit_probe.py [秒数] [--harass]
--harass: 青蛙每 2.5s 瞬移到随机食饵旁——模拟真实游玩中被反复惊飞后
          随机朝向重新进场(极限环的温床), 静止青蛙的探针测不到这种情形。
区分两类果蝇分别统计"距认领食饵 9~60px、食饵有空位、却持续不落地"的连续区段。
"""
import os
import random
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame  # noqa: E402

import main  # noqa: E402

V2 = pygame.math.Vector2


def run(seconds=240.0, harass=False):
    pygame.init()
    g = main.Game(headless=True)
    g.autopilot = False
    rng = random.Random(77)
    dt = 1 / 60
    frames = int(seconds / dt)
    stats = {"scripted": [0.0, 0], "brain": [0.0, 0]}   # [累计, 区段数]
    cur = {"scripted": 0.0, "brain": 0.0}
    worst = {"scripted": 0.0, "brain": 0.0}
    meals = 0
    was = set()
    for fr in range(frames):
        if harass and fr % int(2.5 / dt) == 0:
            c = rng.choice(g.crumbs)
            g.frog.pos.update(c.pos() + V2(rng.uniform(-140, 140), rng.uniform(-140, 140)))
        g.simulate(dt, V2(0), False, False)
        for f in g.flies:
            if f.eating_now and id(f) not in was:
                meals += 1
                was.add(id(f))
            if not f.eating_now:
                was.discard(id(f))
            kind = "brain" if f is g.brain_fly() else "scripted"
            orbiting = False
            if f.food is not None and not f.eating_now and not f.food.full(f):
                d = f.pos.distance_to(f.food.pos())
                orbiting = 9.0 < d < 60.0
            if orbiting:
                cur[kind] += dt
                if cur[kind] > 5.0:
                    if cur[kind] - dt <= 5.0:
                        stats[kind][1] += 1
                    worst[kind] = max(worst[kind], cur[kind])
            else:
                cur[kind] = 0.0
    pygame.quit()
    mode = "骚扰模式" if harass else "静蛙模式"
    print(f"[{mode}] 顿数={meals}")
    for kind in ("scripted", "brain"):
        n, w = stats[kind][1], worst[kind]
        print(f"  {kind:9s}: >5s绕食区段={n:3d}个  最长={w:5.1f}s")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    run(float(args[0]) if args else 240.0, harass="--harass" in sys.argv)
