"""诊断: 俯视每片荷叶, 检查两块食饵是否都渲染、位置是否合理。"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import math
import pygame
from pygame.math import Vector2 as V2
import main as M
from render3d import V3
from preview import boot, shot, montage


def main():
    g = boot()
    park = M.Game(headless=True)
    # 直接检查数据: 每片荷叶两块碎屑的偏移
    by_pad = {}
    for i, c in enumerate(g.crumbs):
        by_pad.setdefault(id(c.pad), []).append((i, c))
    for pid, items in by_pad.items():
        pad = items[0][1].pad
        offs = [(i, c.side, round(c.offset.x), round(c.offset.y), round(c.amount, 1))
                for i, c in items]
        print(f"pad@({pad.pos.x:.0f},{pad.pos.y:.0f}) r={pad.r:.0f} "
              f"flower={pad.flower} crumbs={offs}")
    # 渲染: 每片荷叶俯视一张
    tiles, labels = [], []
    for k, pad in enumerate(g.pads):
        g.cam_target.update(V3(pad.pos.x, pad.pos.y, 0))
        g.cam_yaw = math.radians(225)
        g.cam_elev = math.radians(78)
        g.dist = 190.0
        g.draw()
        frame = g.screen.copy()
        p = g.cam.project(V3(pad.pos.x, pad.pos.y, 0))
        cx, cy = (p[0], p[1]) if p else (M.W / 2, M.H / 2)
        box = pygame.Rect(int(cx - 240), int(cy - 180), 480, 360)
        box = box.clip(pygame.Rect(0, 0, M.W, M.H))
        tile = pygame.Surface((box.w, box.h))
        tile.blit(frame, (0, 0), box)
        tiles.append(tile)
        labels.append(f"pad{k} r={pad.r:.0f} flower={pad.flower}")
    montage(tiles, 3, (480, 360), labels, "/tmp/pond-views/crumbs-debug.png",
            "CRUMB per pad (top-down)")
    pygame.quit()


main()
