"""绕食极限环探针: 目标"空闲"时果蝇在 9~60px 处转圈不落地的程度。

用法: python tools/orbit_probe.py [秒数]
区分两类果蝇分别统计"距认领食饵 9~60px、食饵有空位、却持续不落地"的连续区段。
"""
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
    frames = int(seconds / dt)
    stats = {"scripted": [0.0, 0], "brain": [0.0, 0]}   # [当前区段时长, 区段数]
    cur = {"scripted": 0.0, "brain": 0.0}
    worst = {"scripted": 0.0, "brain": 0.0}
    for _ in range(frames):
        g.simulate(dt, V2(0), False, False)
        for f in g.flies:
            kind = "brain" if f is g.brain_fly() else "scripted"
            orbiting = False
            if f.food is not None and not f.eating_now and not f.food.full(f):
                d = f.pos.distance_to(f.food.pos())
                orbiting = 9.0 < d < 60.0
            if orbiting:
                cur[kind] += dt
                if cur[kind] > 5.0:
                    # 记一次区段(只在跨过阈值时计一次)
                    if cur[kind] - dt <= 5.0:
                        stats[kind][1] += 1
                    worst[kind] = max(worst[kind], cur[kind])
            else:
                cur[kind] = 0.0
    pygame.quit()
    for kind in ("scripted", "brain"):
        n, w = stats[kind][1], worst[kind]
        print(f"{kind:9s}: >5s绕食区段={n:3d}个  最长={w:5.1f}s")


if __name__ == "__main__":
    run(float(sys.argv[1]) if len(sys.argv) > 1 else 240.0)
