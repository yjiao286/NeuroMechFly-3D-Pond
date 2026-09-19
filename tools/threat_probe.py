"""青蛙停在食饵旁不同距离时, 果蝇还吃不吃?  复现玩家"停在荷叶边看果蝇"的场景。

用法: python tools/threat_probe.py
对 frog→食饵 距离 120/140/160/999(px) 各跑 150s:
  神经果蝇: 绕食时间(距认领食饵<50px非进食) / 歇resting次数 / GF逃逸次数 / 进食秒数
  脚本果蝇: 总进食秒数 / 吃过只数
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame  # noqa: E402

import main  # noqa: E402

V2 = pygame.math.Vector2


def trial(frog_dist, seconds=150.0):
    pygame.init()
    g = main.Game(headless=True)
    g.autopilot = False
    dt = 1 / 60
    c0 = g.crumbs[0]
    g.frog.pos = V2(c0.pos().x + frog_dist, c0.pos().y)   # 青蛙钉在食饵旁 frog_dist 处
    frames = int(seconds / dt)
    bf_cir = 0.0
    bf_eat = 0.0
    rests = 0
    was_rest = False
    gf0 = g.brain_fly().brain.gf_count
    sc_eat = 0.0
    sc_ate = set()
    for _ in range(frames):
        g.simulate(dt, V2(0), False, False)
        bf = g.brain_fly()
        if bf is None:
            break
        if bf.eating_now:
            bf_eat += dt
        elif bf.food is not None and bf.pos.distance_to(bf.food.pos()) < 50:
            bf_cir += dt
        if bf.resting and not was_rest:
            rests += 1
        was_rest = bf.resting
        for f in g.flies:
            if isinstance(f, main.ScriptedFly) and f.eating_now:
                sc_eat += dt
                sc_ate.add(id(f))
    gf = bf.brain.gf_count - gf0 if bf else -1
    pygame.quit()
    print(f"蛙距食饵{frog_dist:4d}px: 神经果蝇 绕食={bf_cir:5.1f}s 进食={bf_eat:4.1f}s "
          f"歇息{rests:2d}次 GF逃逸{gf:3d}次 | 脚本果蝇 进食={sc_eat:5.1f}s ({len(sc_ate)}只)")


if __name__ == "__main__":
    for d in (120, 140, 160, 999):
        trial(d)
