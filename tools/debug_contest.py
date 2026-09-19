"""对峙动作审片: 神经个体(攻) vs 脚本个体(守)在食饵旁的冲撞/伏低全过程。"""
import os
import sys
import math

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame
from pygame.math import Vector2 as V2
import main as M
from preview import boot, montage
from render3d import V3

DT = 1 / 60


def grab(g, center, label, tiles):
    g.draw()
    frame = g.screen.copy()
    p = g.cam.project(center)
    cx, cy = (p[0], p[1]) if p else (M.W / 2, M.H / 2)
    box = pygame.Rect(int(cx - 200), int(cy - 150), 400, 300).clip((0, 0, M.W, M.H))
    tile = pygame.Surface((box.w, box.h))
    tile.blit(frame, (0, 0), box)
    tiles.append((tile, label))


def main():
    g = boot()
    g.frog.pos.update(V2(4000, 4000))
    pad = g.pads[0]
    c0, c1 = g.crumbs[0], g.crumbs[1]
    # 食饵摆近一点(同片荷叶两侧)
    c0.offset.update(V2(-14, 0))
    c1.offset.update(V2(14, 0))
    for f in g.flies:
        f.pos.update(V2(4000, 4000))
    bf = g.brain_fly()
    victim = next(f for f in g.flies if isinstance(f, M.ScriptedFly))
    # 攻击方: 落定在食饵0旁
    bf.pos.update(c0.pos() + V2(6, 6))
    bf.z = bf.z_target = M.FlyBase.LAND_Z
    bf.brain.resting = True
    bf.food = c0
    # 守方: 正在食饵1上进食
    victim.pos.update(c1.pos())
    victim.z = victim.z_target = M.FlyBase.LAND_Z
    victim.state = "进食"
    victim.food = c1
    victim.eat_t = 30.0
    g.cam_target.update(V3(c0.pos().x, c0.pos().y, 4))
    g.cam_yaw, g.cam_elev, g.dist = math.radians(225), math.radians(38), 240.0

    center = V3((c0.pos().x + c1.pos().x) / 2, (c0.pos().y + c1.pos().y) / 2, 4)
    tiles = []
    for i in range(int(0.7 / DT)):
        g.t += DT
        g.simulate(DT, V2(0), False, False)
        ct = victim.contest_t
        if i in (4, 14, 24, 34):            # ~0.07/0.23/0.40/0.57s
            grab(g, center, f"t={i*DT:.2f}s ct={ct:.2f} lunge相位", tiles)
    montage([t for t, _ in tiles], 4, (400, 300), [l for _, l in tiles],
            "/tmp/pond-views/contest.png", "TERRITORIAL CONTEST (attacker left/gold)")
    pygame.quit()


main()
