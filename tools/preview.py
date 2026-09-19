"""模型审片台：离屏渲染青蛙 / 果蝇 / 池塘的多视角拼图，供"看一眼再改"的迭代。

用法：
    .venv/bin/python tools/preview.py frog      # 青蛙转台（8 个朝向 × 2 个机位）
    .venv/bin/python tools/preview.py fly       # 果蝇转台 + 特写
    .venv/bin/python tools/preview.py pond      # 池塘全景 / 俯视 / 近岸
    .venv/bin/python tools/preview.py wide      # 游戏默认视角

产物写到 /tmp/pond-views/<name>.png。
"""

from __future__ import annotations

import math
import os
import random
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame                                                     # noqa: E402
from pygame.math import Vector2 as V2                             # noqa: E402

import main as M                                                  # noqa: E402
from render3d import V3                                           # noqa: E402

OUT = "/tmp/pond-views"
GAME = None


def boot():
    """起一个无头 Game，关掉自动驾驶，把生物挪到镜头外等每个 view 自己摆位。"""
    global GAME
    if GAME is not None:
        return GAME
    pygame.init()
    g = M.Game(headless=True)
    g.autopilot = False
    g.sounds.enabled = False
    GAME = g
    return g


def park_all():
    """把所有生物藏到远处，让每个 view 自己决定谁出镜。"""
    g = boot()
    g.frog.pos.update(V2(4000, 4000))
    g.frog.heading = 0.0
    g.frog.z = 0.0
    for f in g.flies:
        f.pos.update(V2(4000, 4000))
    return g


def shot(focus, yaw_deg, elev_deg, dist, crop=(560, 350), out_size=None):
    """摆机位 → 渲染一帧 → 按 focus 裁一块返回。"""
    g = boot()
    g.shake = 0.0
    g.cam_target.update(V3(focus[0], focus[1], focus[2] if len(focus) > 2 else 0))
    g.cam_yaw = math.radians(yaw_deg)
    g.cam_elev = math.radians(elev_deg)
    g.dist = dist
    g.draw()
    frame = g.screen.copy()
    p = g.cam.project(V3(*focus))
    cx, cy = (p[0], p[1]) if p else (M.W / 2, M.H / 2)
    cw, ch = crop
    box = pygame.Rect(int(cx - cw / 2), int(cy - ch / 2), cw, ch)
    box = box.clip(pygame.Rect(0, 0, M.W, M.H))
    tile = pygame.Surface((cw, ch))
    tile.fill((0, 0, 0))
    tile.blit(frame, (box.x - box.x, box.y - box.y), box)
    if out_size and out_size != (cw, ch):
        tile = pygame.transform.smoothscale(tile, out_size)
    return tile


def montage(tiles, cols, tile_size, labels, path, title=""):
    rows = (len(tiles) + cols - 1) // cols
    tw, th = tile_size
    pad, top = 6, 34
    surf = pygame.Surface((cols * tw + (cols + 1) * pad,
                           rows * th + (rows + 1) * pad + top))
    surf.fill((26, 30, 34))
    font = pygame.font.Font(None, 22)
    for i, (t, lab) in enumerate(zip(tiles, labels)):
        r, c = divmod(i, cols)
        x = pad + c * (tw + pad)
        y = top + pad + r * (th + pad)
        img = pygame.transform.smoothscale(t, (tw, th)) if t.get_size() != (tw, th) else t
        surf.blit(img, (x, y))
        pygame.draw.rect(surf, (70, 78, 86), (x, y, tw, th), 1)
        tagname = font.render(lab, True, (226, 232, 238))
        surf.blit(tagname, (x + 4, y + th - 20))
    if title:
        surf.blit(font.render(title, True, (255, 224, 150)), (pad, 10))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pygame.image.save(surf, path)
    print(f"→ {path}  ({surf.get_width()}x{surf.get_height()})")


# ------------------------------------------------------------------ views
def view_frog():
    """青蛙转台：同一机位下转 8 个朝向 + 俯视/侧视机位各一张。"""
    g = park_all()
    frog = g.frog
    frog.pos.update(V2(0, 0))
    frog.heading = 0.0
    frog.z = 0.0
    frog.blink = 0.0
    tiles, labels = [], []
    for i in range(8):
        frog.heading = math.tau * i / 8
        tiles.append(shot((0, 0, 8), 225, 32, 430, crop=(430, 300)))
        labels.append(f"heading {i * 45}° · elev32")
    for elev in (12, 55, 80):
        frog.heading = math.radians(35)
        tiles.append(shot((0, 0, 8), 225, elev, 430, crop=(430, 300)))
        labels.append(f"heading 35° · elev{elev}")
    montage(tiles, 4, (430, 300), labels, f"{OUT}/frog.png", "FROG turntable")


def view_close():
    """青蛙特写: 一眼看清头、眼、前后肢的关系。"""
    g = park_all()
    frog = g.frog
    frog.pos.update(V2(0, 0))
    frog.heading = math.radians(35)
    frog.z = 0.0
    frog.blink = 0.0
    tiles, labels = [], []
    for elev, dist, crop in ((34, 300, (380, 250)), (34, 300, (380, 250)),
                             (70, 320, (380, 250)), (14, 300, (380, 250))):
        tiles.append(shot((0, 0, 6), 225, elev, dist, crop=crop))
        labels.append(f"elev{elev} d{dist}")
    frog.heading = math.radians(215)
    tiles.append(shot((0, 0, 6), 225, 34, 300, crop=(380, 250)))
    labels.append("from behind")
    frog.heading = math.radians(35)
    tiles.append(shot((0, 0, 6), 315, 34, 300, crop=(380, 250)))
    labels.append("side-on")
    tiles.append(shot((0, 0, 6), 45, 34, 300, crop=(380, 250)))
    labels.append("other side")
    tiles.append(shot((0, 0, 6), 225, 6, 300, crop=(380, 250)))
    labels.append("grazing elev6")
    montage(tiles, 3, (380, 250), labels, f"{OUT}/frog-close.png", "FROG close-up")


def view_fly():
    g = park_all()
    fly = g.flies[1]
    fly.pos.update(V2(0, 0))
    fly.z = M.FlyBase.CRUISE_Z
    fly.z_target = M.FlyBase.CRUISE_Z
    fly.groom_t = 0.0
    fly.eating_now = False
    tiles, labels = [], []
    for i in range(6):
        fly.heading = math.tau * i / 6
        fly.wing_phase = 0.0
        tiles.append(shot((0, 0, 17), 225, 40, 420, crop=(300, 220)))
        labels.append(f"flying h{i * 60}°")
    fly.z = M.FlyBase.LAND_Z                    # 落地收翅
    for i in range(3):
        fly.heading = math.tau * i / 3
        tiles.append(shot((0, 0, 5), 225, 40, 420, crop=(300, 220)))
        labels.append(f"resting h{i * 120}°")
    for elev in (15, 45, 70):
        fly.z = 16.0
        fly.heading = math.radians(30)
        tiles.append(shot((0, 0, 17), 225, elev, 420, crop=(300, 220)))
        labels.append(f"flying elev{elev}")
    montage(tiles, 4, (300, 220), labels, f"{OUT}/fly.png", "FRUIT FLY turntable")
    # 特写: 翅脉/刚毛/复眼这个尺度才看得清
    tiles, labels = [], []
    for z_, lab in ((M.FlyBase.CRUISE_Z, "flying"), (M.FlyBase.LAND_Z, "resting")):
        fly.z = z_
        fly.z_target = z_
        for i in range(3):
            fly.heading = math.tau * i / 3
            fly.wing_phase = i * 1.1
            tiles.append(shot((0, 0, z_ + 1.5), 225, 42, 150, crop=(260, 200)))
            labels.append(f"{lab} h{i * 120}")
    fly.heading = math.radians(30)
    fly.z = M.FlyBase.LAND_Z
    tiles.append(shot((0, 0, 5), 225, 12, 150, crop=(260, 200)))
    labels.append("resting side")
    fly.eating_now = True
    tiles.append(shot((0, 0, 5), 225, 42, 150, crop=(260, 200)))
    labels.append("feeding")
    fly.eating_now = False
    fly.groom_t = 1.0
    tiles.append(shot((0, 0, 5), 225, 42, 150, crop=(260, 200)))
    labels.append("grooming")
    fly.groom_t = 0.0
    montage(tiles, 4, (260, 200), labels, f"{OUT}/fly-close.png", "FRUIT FLY close-up")


def view_pond():
    g = park_all()
    g.frog.pos.update(V2(0, -120))
    g.frog.heading = math.radians(90)
    tiles, labels = [], []
    for yaw, elev, dist, lab in ((225, 25, 1526, "默认越肩"),
                                 (225, 62, 1500, "高角度俯视"),
                                 (135, 18, 900, "低角度近岸"),
                                 (315, 25, 1100, "对岸视角")):
        tiles.append(shot((60, 60, 0), yaw, elev, dist, crop=(720, 450)))
        labels.append(f"{lab} yaw{yaw} elev{elev}")
    montage(tiles, 2, (720, 450), labels, f"{OUT}/pond.png", "POND views")


def view_wide():
    park_all()
    g = boot()
    g.frog.pos.update(V2(0, -120))
    g.frog.heading = math.radians(90)
    t = shot((80, 100, 0), 225, 25, 1526, crop=(1280, 800))
    montage([t], 1, (1280, 800), ["default"], f"{OUT}/wide.png", "GAME default view")


def view_doc():
    """README 用的成品图(带 HUD): 中景池塘 + 金色方框标注的神经元果蝇。"""
    park_all()
    g = boot()
    bf = g.brain_fly()
    bf.pos.update(V2(40, 40))
    bf.z = 16.0
    bf.z_target = 16.0
    g.show_brain = True
    g.frog.pos.update(V2(bf.pos.x - 120, bf.pos.y - 190))
    g.frog.heading = math.radians(60)
    for f in g.flies:
        if f is not bf:
            f.pos.update(V2(bf.pos.x + random.uniform(-260, 260),
                            bf.pos.y + random.uniform(-200, 200)))
    g.cam_target.update(V3(bf.pos.x, bf.pos.y, 4))
    g.cam_yaw = math.radians(215)
    g.cam_elev = math.radians(30)
    g.dist = 820.0
    g.simulate(1 / 60, V2(0), False, False)
    g.draw()
    frame = g.screen.copy()
    os.makedirs("docs/images", exist_ok=True)
    pygame.image.save(frame, "docs/images/pond-neural.png")
    print("→ docs/images/pond-neural.png")


def view_hero():
    """另外两张 README/文档用的成品图: 默认视角全景 + 荷叶上果蝇的特写。"""
    park_all()
    g = boot()
    g.show_brain = False
    g.frog.pos.update(V2(0, -120))
    g.frog.heading = math.radians(90)
    g.cam_target.update(V3(80, 100, 0))
    g.cam_yaw, g.cam_elev, g.dist = math.radians(225), math.radians(25), 1526.0
    g.simulate(1 / 60, V2(0), False, False)
    g.draw()
    pygame.image.save(g.screen.copy(), "docs/images/preview.png")
    print("→ docs/images/preview.png")

    bf = g.brain_fly()
    pad = max(g.pads, key=lambda p: p.r)
    bf.pos.update(pad.pos + V2(-6, 4))
    bf.z = bf.z_target = 5.0
    bf.groom_t = 0.0
    g.frog.pos.update(pad.pos + V2(-120, 132))
    g.cam_target.update(V3(bf.pos.x - 20, bf.pos.y + 18, 6))
    g.cam_yaw, g.cam_elev, g.dist = math.radians(228), math.radians(42), 455.0
    g.simulate(1 / 60, V2(0), False, False)
    g.draw()
    pygame.image.save(g.screen.copy(), "docs/images/near60.png")
    print("→ docs/images/near60.png")


VIEWS = {"frog": view_frog, "close": view_close, "fly": view_fly, "pond": view_pond,
         "wide": view_wide, "doc": view_doc, "hero": view_hero}

if __name__ == "__main__":
    names = sys.argv[1:] or list(VIEWS)
    for n in names:
        VIEWS[n]()
    pygame.quit()
