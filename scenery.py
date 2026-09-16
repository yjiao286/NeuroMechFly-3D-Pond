"""3D 池塘场景：水面、焦散闪点、涟漪、荷叶荷花、食饵、沙岸/岩石/草丛/芦苇/灌木与天空。

层间用微小深度偏置(bias)防穿模：同一物体各层显式错开排序。
"""

from __future__ import annotations

import math
import random

import pygame

from render3d import V3, clamp, flat_polygon, polyline, segment, sphere

POND_W2, POND_H2 = 620, 350          # 池塘半宽 / 半高（世界单位）
WATER_S = (34, 82, 68)               # 近岸水色（南）
WATER_N = (108, 148, 136)            # 远岸水色（北，大气雾化）
SAND = (198, 180, 142)
SAND_D = (168, 148, 112)
BANK_H = 18                          # 岸高
PAD_TOP = 2.2                        # 荷叶叶面高度


def caustic(x, y, t):
    """焦散光斑场：两组波纹光丝的乘积 × 低频明暗遮罩，随时间流动。"""
    v1 = math.sin(x * 0.037 + 2.6 * math.sin(y * 0.017 + t * 0.35))
    v2 = math.sin(y * 0.021 + 2.2 * math.sin(x * 0.041 + 2.1 + t * 0.28))
    m = 0.5 + 0.5 * math.sin(x * 0.008 + y * 0.006) * np_sin_mix(x, y)
    return (math.exp(-(v1 * v1) * 4.0) * math.exp(-(v2 * v2) * 4.0)) * (0.30 + 0.70 * m)


def np_sin_mix(x, y):
    return math.sin(y * 0.009 - x * 0.005)


def _lerp_color(a, b, f):
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def draw_pond(painter, cam, t):
    """水面（近深远浅的大气层次）+ 泥岸 + 流动的焦散闪点。"""
    rows = 26
    for i in range(rows):
        y0 = -POND_H2 + 2 * POND_H2 * i / rows
        y1 = -POND_H2 + 2 * POND_H2 * (i + 1) / rows
        f = (y0 + POND_H2) / (2 * POND_H2)
        pts = [V3(-POND_W2, y0, 0), V3(POND_W2, y0, 0), V3(POND_W2, y1, 0), V3(-POND_W2, y1, 0)]
        flat_polygon(painter, cam, pts, _lerp_color(WATER_S, WATER_N, f), layer=0)
    for gx in range(40):                                  # 焦散闪点：小而淡, 避免悬浮感
        for gy in range(26):
            x = -POND_W2 + 1240 * gx / 39
            y = -POND_H2 + 700 * gy / 25
            c = caustic(x, y, t)
            if c > 0.68:
                k = int(38 + 66 * (c - 0.68) / 0.32)
                s = 2.2 + 2.2 * (c - 0.68)
                a = t * 0.6 + x * 0.01
                pts = [V3(x + s * math.cos(a), y + s * math.sin(a), 0.15),
                       V3(x - s * math.sin(a), y + s * math.cos(a), 0.15),
                       V3(x - s * math.cos(a), y - s * math.sin(a), 0.15),
                       V3(x + s * math.sin(a), y - s * math.cos(a), 0.15)]
                flat_polygon(painter, cam, pts, (int(k * 0.5), k, int(k * 0.8)), layer=1)


class Ripples:
    """扩散涟漪：3D 水面上的圆环。"""

    def __init__(self):
        self.items = []

    def add(self, pos, strength=1.0):
        for i, delay in enumerate((0.0, 0.16, 0.36)):
            self.items.append({"pos": pygame.math.Vector2(pos), "r": 5 + i * 7,
                               "speed": 52 + 26 * strength, "life": 1.0,
                               "strength": strength, "delay": delay})

    def update(self, dt):
        keep = []
        for it in self.items:
            if it["delay"] > 0:
                it["delay"] -= dt
                keep.append(it)
                continue
            it["r"] += it["speed"] * dt
            it["life"] -= dt * 1.15
            if it["life"] > 0:
                keep.append(it)
        self.items = keep

    def draw(self, painter, cam):
        for it in self.items:
            if it["delay"] > 0:
                continue
            k = max(0.0, min(1.0, it["life"] * min(1.0, it["strength"] + 0.3)))
            c = (int(40 + 165 * k), int(90 + 150 * k), int(84 + 136 * k))
            pts = [V3(it["pos"].x + it["r"] * math.cos(a),
                      it["pos"].y + it["r"] * math.sin(a), 0.8)
                   for a in [2 * math.pi * i / 20 for i in range(21)]]
            polyline(painter, cam, pts, c, 2 if k > 0.5 else 1, layer=1)


class LilyPad3D:
    """荷叶：缺口圆叶 + 叶脉，随水波起伏；偶尔开一朵多层荷花。
    叶影/叶面/内层/叶脉各层高度与 bias 显式错开防穿模。"""

    def __init__(self, pos, r, with_flower=False):
        self.pos = pygame.math.Vector2(pos)
        self.r = r
        self.phase = random.uniform(0, math.tau)
        self.notch = random.uniform(0, math.tau)
        self.flower = with_flower
        self.marks = [(random.uniform(0, math.tau), random.uniform(0.2, 0.75),
                       random.uniform(1.5, 3.2), random.random() < 0.5)
                      for _ in range(7)]                    # 叶面斑纹(预生成防抖动)

    def leaf_pts(self, z, scale=1.0):
        pts = [V3(self.pos.x, self.pos.y, z)]
        a0 = self.notch + 0.30
        for i in range(19):
            a = a0 + (2 * math.pi - 0.60) * i / 18
            pts.append(V3(self.pos.x + math.cos(a) * self.r * scale,
                          self.pos.y + math.sin(a) * self.r * 0.92 * scale, z))
        return pts

    def surface_dist(self, pos):
        dx = pos.x - self.pos.x
        dy = (pos.y - self.pos.y) / 0.92
        return math.hypot(dx, dy) - self.r

    def height_at(self, pos, t):
        """点 pos 处的叶面高度(随波起伏), 不在叶上返回 0。"""
        if self.surface_dist(pos) < 0:
            return PAD_TOP + math.sin(t * 1.2 + self.phase) * 1.6
        return 0.0

    def draw(self, painter, cam, t):
        bob = math.sin(t * 1.2 + self.phase) * 1.6
        z = PAD_TOP + bob
        # 影子强制后置(bias=-4): 无论机位转到哪个方位, 影子都不会盖到叶面上
        shadow = [V3(p.x + 2, p.y + 2, 0.3) for p in self.leaf_pts(z, 0.92)]
        flat_polygon(painter, cam, shadow, (10, 36, 30), bias=-4, layer=3)
        # 叶面/内层/叶脉全部同层绘制, 仅用 bias 排序——高视角下不会错位戳出叶缘
        flat_polygon(painter, cam, self.leaf_pts(z), (58, 120, 62), (40, 88, 48), 2, layer=3)
        flat_polygon(painter, cam, self.leaf_pts(z, 0.86), (74, 140, 70), bias=0.5, layer=3)
        # 叶面斑纹
        for (ma, md, mr, dark) in self.marks:
            px = self.pos.x + math.cos(ma) * self.r * md
            py = self.pos.y + math.sin(ma) * self.r * 0.86 * md
            col = (52, 106, 52) if dark else (88, 150, 78)
            flat_polygon(painter, cam,
                         [V3(px + math.cos(a) * mr, py + math.sin(a) * mr * 0.8, z + 0.1)
                          for a in [2 * math.pi * i / 6 for i in range(6)]],
                         col, bias=0.3, layer=3)
        for k in range(6):
            a = self.notch + 0.9 + k * (2 * math.pi - 1.8) / 5
            tip = V3(self.pos.x + math.cos(a) * self.r * 0.8,
                     self.pos.y + math.sin(a) * self.r * 0.74, z)
            segment(painter, cam, V3(self.pos.x, self.pos.y, z), tip, (42, 90, 48), 2, bias=0.6, layer=3)
        if self.flower:
            self._draw_flower(painter, cam, z)

    def _draw_flower(self, painter, cam, z):
        """杯状荷花: 两层花瓣面片(粉尖白底) + 黄色莲蓬, 参考真实荷花照片。"""
        cx, cy = self.pos
        sway = math.sin(self.phase) * 0.06

        def petal(a, r_in, r_tip, w, z_tip, col):
            cta, sta = math.cos(a), math.sin(a)
            bl = V3(cx + cta * r_in - sta * w, cy + sta * r_in + cta * w, z + 2.8)
            br = V3(cx + cta * r_in + sta * w, cy + sta * r_in - cta * w, z + 2.8)
            tl = V3(cx + cta * r_tip * 0.82 - sta * w * 0.55,
                    cy + sta * r_tip * 0.82 + cta * w * 0.55, z + z_tip)
            tr = V3(cx + cta * r_tip * 0.82 + sta * w * 0.55,
                    cy + sta * r_tip * 0.82 - cta * w * 0.55, z + z_tip)
            tip = V3(cx + cta * r_tip, cy + sta * r_tip, z + z_tip * 0.82)
            flat_polygon(painter, cam, [bl, tl, tip, tr, br], col, bias=0.35, layer=3)

        for i in range(8):                                    # 外层大花瓣: 粉尖
            a = 2 * math.pi * i / 8 + self.phase + sway
            petal(a, 2.5, 12.5, 2.2, 8.2, (240, 158, 196))
        for i in range(6):                                    # 内层花瓣: 更立更浅
            a = 2 * math.pi * i / 6 + self.phase * 1.3 + 0.3
            petal(a, 1.6, 8.0, 1.8, 9.6, (248, 190, 218))
        sphere(painter, cam, V3(cx, cy, z + 7.0), 3.1, (246, 212, 96), bias=0.4, layer=3)
        for i in range(6):                                    # 花蕊
            a = 2 * math.pi * i / 6 + 0.4
            sphere(painter, cam, V3(cx + math.cos(a) * 2.4, cy + math.sin(a) * 2.4, z + 8.2),
                   1.0, (252, 232, 140), bias=0.5, layer=3)


class FoodCrumb:
    """荷叶上的食饵碎屑：果蝇的觅食目标，被啃食后缩小，8~14 秒后长回来。"""

    def __init__(self, pad):
        self.pad = pad
        self.amount = 0.0
        self.timer = random.uniform(0.5, 3.0)
        self.offset = pygame.math.Vector2(0, 0)
        self.crowd = 0
        self.respawn()

    def respawn(self):
        self.amount = 4.0
        a = random.uniform(0, math.tau)
        # 有荷花的荷叶: 食饵放远一点, 不和花瓣/落下的果蝇挤在一起
        d = random.uniform(0.15, 0.5) * self.pad.r if not self.pad.flower \
            else random.uniform(0.55, 0.8) * self.pad.r
        self.offset = pygame.math.Vector2(math.cos(a) * d, math.sin(a) * d * 0.9)

    def bite(self, dt):
        self.amount = max(0.0, self.amount - dt * 0.55)
        if self.amount <= 0:
            self.timer = random.uniform(6.0, 10.0)      # 吃完过一阵长回来
            return True
        return False

    def pos(self):
        return self.pad.pos + self.offset

    def draw(self, painter, cam, t):
        if self.amount <= 0:
            return
        s = 0.45 + 0.55 * self.amount / 4.0
        cx, cy = self.pos()
        for dx, dy in ((0, 0), (3, 2), (-3, 1.5), (1, -3)):
            sphere(painter, cam, V3(cx + dx * s, cy + dy * s, PAD_TOP + 1.2), 2.1 * s,
                   (156, 110, 64), bias=0.2, layer=3)
        sphere(painter, cam, V3(cx - 1.5 * s, cy - 1.5 * s, PAD_TOP + 2.0), 1.1 * s,
               (206, 158, 104), bias=0.25, layer=3)


def make_pads(n=6):
    pads, tries = [], 0
    while len(pads) < n and tries < 500:
        tries += 1
        pos = pygame.math.Vector2(random.uniform(-POND_W2 + 150, POND_W2 - 150),
                                  random.uniform(-POND_H2 + 130, POND_H2 - 130))
        if all(p.pos.distance_to(pos) > 200 for p in pads):
            pads.append(LilyPad3D(pos, random.uniform(46, 66), with_flower=random.random() < 0.45))
    return pads


def make_duckweed(n=14):
    """浮萍: 一簇簇的小圆叶漂在水面(参考真实池塘照片)。"""
    clusters = []
    for _ in range(n):
        x = random.uniform(-POND_W2 + 40, POND_W2 - 40)
        y = random.uniform(-POND_H2 + 40, POND_H2 - 40)
        dots = [(random.uniform(-4.5, 4.5), random.uniform(-4.5, 4.5),
                 random.uniform(0.9, 2.0)) for _ in range(random.randint(3, 6))]
        clusters.append({"x": x, "y": y, "dots": dots, "phase": random.uniform(0, math.tau)})
    return clusters


def draw_duckweed(painter, cam, t, clusters):
    for cl in clusters:
        drift = math.sin(t * 0.22 + cl["phase"]) * 3.0
        for dx, dy, r in cl["dots"]:
            px, py = cl["x"] + dx + drift, cl["y"] + dy
            pts = [V3(px + math.cos(a) * r, py + math.sin(a) * r * 0.85, 0.25)
                   for a in [2 * math.pi * i / 6 for i in range(6)]]
            col = (118, 184, 88) if (dx + dy) > 0 else (96, 160, 76)
            flat_polygon(painter, cam, pts, col, layer=1)


def make_bank_props():
    """岸边一次性布景：岩石、草丛、芦苇香蒲、灌木丛。"""
    rng = random.Random(20)
    rocks, grass, reeds, bushes = [], [], [], []
    for x in range(-560, 561, 95):                        # 远岸岩石与灌木
        rocks.append((x + rng.uniform(-24, 24), POND_H2 - 4, rng.uniform(6, 13)))
    for x in range(-520, 521, 78):
        bushes.append((x + rng.uniform(-24, 24), POND_H2 + 28 + rng.uniform(-14, 22),
                       rng.uniform(30, 60), rng.choice(((74, 110, 60), (86, 122, 66), (66, 100, 56)))))
    for y in range(-240, 241, 130):                       # 两岸各几丛
        bushes.append((-(POND_W2 + 40), y, rng.uniform(26, 42), (78, 114, 62)))
        bushes.append((POND_W2 + 40, y + 60, rng.uniform(26, 42), (70, 106, 58)))
        rocks.append((-(POND_W2 - 4), y, rng.uniform(6, 11)))
        rocks.append((POND_W2 - 4, y + 50, rng.uniform(6, 11)))
    for x in range(-590, 591, 22):                        # 远岸芦苇（密）
        reeds.append((x + rng.uniform(-8, 8), POND_H2 - 10 + rng.uniform(-6, 6),
                      rng.uniform(55, 112), rng.random() < 0.45, rng.uniform(0, math.tau)))
    for y in range(-300, 301, 52):                        # 两侧较密
        reeds.append((-(POND_W2 - 8) + rng.uniform(-5, 5), y, rng.uniform(46, 84),
                      rng.random() < 0.35, rng.uniform(0, math.tau)))
        reeds.append((POND_W2 - 8 + rng.uniform(-5, 5), y + 26, rng.uniform(46, 84),
                      rng.random() < 0.35, rng.uniform(0, math.tau)))
    for _ in range(110):                                  # 草丛
        edge = rng.randrange(3)
        if edge == 0:
            pos = (rng.uniform(-POND_W2, POND_W2), POND_H2 - 2)
        elif edge == 1:
            pos = (-(POND_W2 - 2), rng.uniform(-POND_H2, POND_H2))
        else:
            pos = (POND_W2 - 2, rng.uniform(-POND_H2, POND_H2))
        grass.append((pos, rng.uniform(12, 24), rng.uniform(0, math.tau)))
    return {"rocks": rocks, "grass": grass, "reeds": reeds, "bushes": bushes}


def draw_banks(painter, cam, t, props):
    """四面沙岸堤壁 + 岸顶 + 岩石/草丛/芦苇/灌木。"""
    pw, ph, h = POND_W2, POND_H2, BANK_H
    seg = 6
    walls = []
    for i in range(seg):
        x0, x1 = -pw + 2 * pw * i / seg, -pw + 2 * pw * (i + 1) / seg
        c = SAND if i % 2 == 0 else SAND_D
        walls.append(([V3(x0, ph, 0), V3(x1, ph, 0), V3(x1, ph, h), V3(x0, ph, h)], c))
        walls.append(([V3(x0, -ph, 0), V3(x1, -ph, 0), V3(x1, -ph, h), V3(x0, -ph, h)], c))
        y0, y1 = -ph + 2 * ph * i / seg, -ph + 2 * ph * (i + 1) / seg
        walls.append(([V3(-pw, y0, 0), V3(-pw, y1, 0), V3(-pw, y1, h), V3(-pw, y0, h)], c))
        walls.append(([V3(pw, y0, 0), V3(pw, y1, 0), V3(pw, y1, h), V3(pw, y0, h)], c))
    for pts, c in walls:
        flat_polygon(painter, cam, pts, c, layer=3)
    rim = 44
    for pts in ([V3(-pw - rim, ph, h + 0.3), V3(pw + rim, ph, h + 0.3),
                 V3(pw + rim, ph + rim, h + 0.3), V3(-pw - rim, ph + rim, h + 0.3)],
                [V3(-pw - rim, -ph, h + 0.3), V3(pw + rim, -ph, h + 0.3),
                 V3(pw + rim, -ph - rim, h + 0.3), V3(-pw - rim, -ph - rim, h + 0.3)],
                [V3(-pw, -ph, h + 0.3), V3(-pw, ph, h + 0.3),
                 V3(-pw - rim, ph + rim, h + 0.3), V3(-pw - rim, -ph - rim, h + 0.3)],
                [V3(pw, -ph, h + 0.3), V3(pw, ph, h + 0.3),
                 V3(pw + rim, ph + rim, h + 0.3), V3(pw + rim, -ph - rim, h + 0.3)]):
        flat_polygon(painter, cam, pts, SAND, layer=3)
    # 塘外草地: 铺满远方, 镜头低角度时不再露出虚空
    FAR = 2600
    meadow = (98, 132, 66)
    for pts in ([V3(-FAR, ph + rim, h - 0.5), V3(FAR, ph + rim, h - 0.5),
                 V3(FAR, FAR, h - 0.5), V3(-FAR, FAR, h - 0.5)],
                [V3(-FAR, -ph - rim, h - 0.5), V3(FAR, -ph - rim, h - 0.5),
                 V3(FAR, -FAR, h - 0.5), V3(-FAR, -FAR, h - 0.5)],
                [V3(-FAR, -ph - rim, h - 0.5), V3(-pw - rim, -ph - rim, h - 0.5),
                 V3(-pw - rim, ph + rim, h - 0.5), V3(-FAR, ph + rim, h - 0.5)],
                [V3(pw + rim, -ph - rim, h - 0.5), V3(FAR, -ph - rim, h - 0.5),
                 V3(FAR, ph + rim, h - 0.5), V3(pw + rim, ph + rim, h - 0.5)]):
        flat_polygon(painter, cam, pts, meadow, layer=3)
    for (rx, ry, rr) in props["rocks"]:                   # 岩石
        sphere(painter, cam, V3(rx, ry, h + rr * 0.15), rr, (152, 144, 126), layer=3)
    for (gx, gy), gh, gph in props["grass"]:              # 草丛（三叶小扇）
        for k in range(3):
            a = gph + k * 0.5 - 0.5
            tip = V3(gx + math.cos(a) * 5, gy + math.sin(a) * 5, h + gh)
            segment(painter, cam, V3(gx, gy, h), tip, (96, 138, 64), 2, layer=3)
    for (rx, ry, rh, cattail, ph2) in props["reeds"]:     # 芦苇与香蒲
        sway = math.sin(t * 0.9 + ph2) * 3.0
        base = V3(rx, ry, h)
        tip = V3(rx + sway, ry, h + rh)
        segment(painter, cam, base, tip, (62, 108, 52), 3, layer=3)
        segment(painter, cam, base, V3(rx + sway * 0.5 - 6, ry - 3, h + rh * 0.55),
                (80, 126, 62), 2, layer=3)
        if cattail:
            segment(painter, cam, V3(rx + sway, ry, h + rh - 9), tip, (98, 66, 38), 6, layer=3)
    for (bx, by, br, bc) in props["bushes"]:              # 灌木丛（主球 + 次球簇）
        sphere(painter, cam, V3(bx, by, h + br * 0.75), br, bc, layer=3)
        sphere(painter, cam, V3(bx + br * 0.5, by + br * 0.15, h + br * 0.55), br * 0.55, bc, layer=3)


def build_vignette(w, h):
    """四周暗角，增强纵深（2D 叠加层）。"""
    import numpy as np
    yy, xx = np_mgrid(h, w)
    d = np.sqrt(((xx - w / 2) / (w * 0.55)) ** 2 + ((yy - h / 2) / (h * 0.55)) ** 2)
    alpha = np.clip((d - 0.72) * 2.6, 0, 1) * 110
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 3] = alpha.astype(np.uint8)
    return pygame.image.frombuffer(arr.tobytes(), (w, h), "RGBA")


def np_mgrid(h, w):
    import numpy as np
    return np.mgrid[0:h, 0:w]
