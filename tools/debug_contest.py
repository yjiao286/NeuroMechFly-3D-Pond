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
    # 攻击方: 落定在自己那块(食饵0)的远端, 要先逼近 40px 才进入贴脸对峙
    bf.pos.update(c0.pos() + V2(-10, -8))
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
    for i in range(int(2.4 / DT)):
        g.t += DT
        g.simulate(DT, V2(0), False, False)
        ct = victim.contest_t
        if i in (15, 45, 75, 105, 135):      # ~0.25/0.75/1.25/1.75/2.25s
            grab(g, center, f"t={i*DT:.2f}s ct={ct:.2f}", tiles)
    montage([t for t, _ in tiles], 5, (400, 300), [l for _, l in tiles],
            "/tmp/pond-views/contest.png", "CONTEST: approach -> lunge -> yield")
    pygame.quit()


main()
