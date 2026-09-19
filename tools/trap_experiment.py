"""追踪极限环实验: 把脚本果蝇放在食饵旁各种偏差角度, 看它能否落到 d<9。

用法: python tools/trap_experiment.py
每组合跑 15 秒, 输出: 落地用时(未落地=INF) 和期末距离。
"""
import os
import sys
import math

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame  # noqa: E402
from pygame.math import Vector2 as V2
import main as M  # noqa: E402


def trial(start_d, err_deg):
    pygame.init()
    g = M.Game(headless=True)
    g.autopilot = False
    g.frog.pos.update(V2(4000, 4000))            # 青蛙放逐
    c = g.crumbs[0]
    cp = c.pos()
    fly = [f for f in g.flies if isinstance(f, M.ScriptedFly)][0]
    for f in g.flies:
        if f is not fly:
            f.pos.update(V2(-4000, -4000))
    # 起点: 距食饵 start_d, 朝向与"指向食饵"偏差 err_deg
    ang = math.radians(err_deg)
    fly.pos.update(cp + V2(-math.cos(ang) * start_d, -math.sin(ang) * start_d))
    fly.heading = 0.0
    fly.z = fly.z_target = M.FlyBase.CRUISE_Z
    fly.state = "觅食"
    fly.food = None
    dt = 1 / 60
    land = None
    end_d = start_d
    for i in range(int(15 / dt)):
        g.simulate(dt, V2(0), False, False)
        d = fly.pos.distance_to(fly.food.pos()) if fly.food else 999
        if fly.state == "进食":
            land = i * dt
            break
        if fly.food is c:
            end_d = d
    pygame.quit()
    return land, end_d


if __name__ == "__main__":
    print("起点距离  朝向偏差   落地用时   期末距离")
    for d0 in (120, 60):
        for err in (0, 30, 60, 90, 120, 150):
            land, end = trial(d0, err)
            lt = f"{land:5.1f}s" if land else "  INF "
            print(f"{d0:6d}    {err:4d}°    {lt}    {end:5.1f}px")
