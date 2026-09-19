"""诊断: grooming vs resting 的果蝇, 侧视+俯视特写对比。"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
from pygame.math import Vector2 as V2
import main as M
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preview import boot, shot, montage


def main():
    g = boot()
    fly = g.flies[1]
    tiles, labels = [], []
    for mode, lab in (("rest", "resting"), ("groom", "grooming")):
        fly.pos.update(V2(0, 0))
        fly.heading = math.radians(30)
        fly.z = fly.z_target = M.FlyBase.LAND_Z
        fly.eating_now = False
        fly.leg_phase = 0.0
        fly.wing_phase = 0.0
        fly.groom_t = 1.0 if mode == "groom" else 0.0
        for elev, yaw, l in ((42, 225, "elev42"), (12, 225, "side"), (75, 225, "top")):
            tiles.append(shot((0, 0, 5), yaw, elev, 130, crop=(240, 190)))
            labels.append(f"{lab} {l}")
    montage(tiles, 3, (240, 190), labels, "/tmp/pond-views/groom-debug.png",
            "GROOM vs REST")
    pygame.quit()


import math
main()
