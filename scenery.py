"""3D 池塘场景：水面、焦散、涟漪、荷叶荷花、食饵、沙岸/岩石/草丛/芦苇/灌木与天空。

视觉做法：所有平面物都带"受光"层次（边缘压暗 → 向光面提亮 → 高光/镜面），
水面用低频涌浪 + 高频纹样 + 焦散三层叠加，落地物一律带柔光阴影。
层间仍用微小深度偏置(bias)防穿模：同一物体各层显式错开排序。
"""

from __future__ import annotations

import math
import random

import pygame

from render3d import (LIGHT_XY, V3, add_light, clamp, dome, flat_polygon, mix,
                      polyline, segment, shade, soft_shadow, sphere,
                      Painter)

POND_W2, POND_H2 = 620, 350          # 池塘半宽 / 半高（世界单位）
WATER_S = (30, 76, 66)               # 近岸水色（南）
WATER_M = (52, 104, 92)              # 中段
WATER_N = (116, 156, 146)            # 远岸水色（北，大气雾化）
GLINT = (226, 240, 232)              # 水面高光
FOAM = (206, 226, 214)               # 岸边泡沫
SAND = (204, 184, 146)
SAND_WET = (150, 128, 96)            # 水线附近的湿沙
SAND_TOP = (222, 204, 168)
MEADOW = (96, 132, 66)
BANK_H = 18                          # 岸高
PAD_TOP = 2.2                        # 荷叶叶面高度


def _lerp_color(a, b, f):
    return tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))


def _hash01(i, j, k=0):
    """确定性伪随机(0~1)，用于给水面/沙面加纹样，不随帧抖动。"""
    n = (int(i) * 73856093) ^ (int(j) * 19349663) ^ (int(k) * 83492791)
    n &= 0xFFFFFFFF
    n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0


# ---------------------------------------------------------------- 有机岸线
_SHORE_BASE = None


def _shore_base():
    """静态有机池岸：96 个 (x, y, nx, ny, s)。

    把矩形水边界沿内法线做不均匀收缩(三种频率的正弦叠加)，得到不规则但
    平滑的池边；nx/ny 指向池心，s 是归一化周长参数。确定性生成，不随帧变化。
    收缩量限制在 14~56，保证水线永远在游戏活动区(±545/±285)之外。
    """
    global _SHORE_BASE
    if _SHORE_BASE is None:
        pts = []
        pw, ph = POND_W2, POND_H2
        w, per = 2 * pw, 4 * pw + 4 * ph
        for i in range(96):
            s = i / 96
            d = s * per
            if d < w:                                   # 南边
                bx, by, nx, ny = -pw + d, -ph, 0, 1
            elif d < w + 2 * ph:                        # 东边
                bx, by, nx, ny = pw, -ph + (d - w), -1, 0
            elif d < 2 * w + 2 * ph:                    # 北边
                bx, by, nx, ny = pw - (d - w - 2 * ph), ph, 0, -1
            else:                                       # 西边
                bx, by, nx, ny = -pw, ph - (d - 2 * w - 2 * ph), 1, 0
            inset = (30 + 17 * math.sin(s * math.tau * 2 + 1.3)
                     + 9 * math.sin(s * math.tau * 5 + 4.1)
                     + 5 * math.sin(s * math.tau * 9 + 2.2))
            inset = clamp(inset, 14, 56)
            pts.append((bx + nx * inset, by + ny * inset, nx, ny, s))
        _SHORE_BASE = pts
    return _SHORE_BASE


def shore_line(t):
    """某一时刻的水线：静态岸线加轻微吞吐(两簇不同频率的呼吸波)。"""
    out = []
    for (bx, by, nx, ny, s) in _shore_base():
        lap = (2.4 * math.sin(s * math.tau * 3 - t * 1.05)
               + 1.3 * math.sin(s * math.tau * 7 + t * 0.7))
        out.append((bx + nx * lap, by + ny * lap, nx, ny, s))
    return out


def inside_shore(px, py):
    """点是否在静态水线以内(射线法)。布景(浮萍等)用它在岸边留白。"""
    pts = _shore_base()
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        if (y1 > py) != (y2 > py):
            xc = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px < xc:
                inside = not inside
    return inside


def _beach_band(px, py, nx, ny):
    """岸点沿外法线到堤壁的距离(多留 12 压进墙脚，防止露缝)。"""
    if nx > 0:
        return POND_W2 - px + 12
    if nx < 0:
        return px + POND_W2 + 12
    if ny > 0:
        return py + POND_H2 + 12
    return POND_H2 - py + 12


_CLOUD_CACHE = {}


def _cloud_sprite(blobs=6, size=128):
    """预生成一朵柔边云(SRCALPHA 贴图)，避免每帧算羽化。"""
    key = (blobs, size)
    surf = _CLOUD_CACHE.get(key)
    if surf is None:
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        for i in range(blobs):
            f = (i + 0.5) / blobs
            cx = size * (0.18 + 0.64 * f)
            cy = size * (0.60 - 0.20 * math.sin(f * math.pi))
            r = size * (0.16 + 0.12 * math.sin(f * 2.4))
            for k in range(4, 0, -1):
                a = int(52 * (1 - k / 5.0) + 12)
                pygame.draw.circle(surf, (255, 255, 255, a),
                                   (int(cx), int(cy)), int(r * k / 4), 0)
        _CLOUD_CACHE[key] = surf
    return surf


_CLOUDS = [(2.1, 900, 6200, 6), (3.4, 1350, 8200, 8), (4.6, 700, 5400, 5),
           (0.7, 1500, 7000, 9), (5.6, 1050, 6600, 6), (1.4, 1750, 9000, 7),
           (5.0, 820, 5800, 5), (2.9, 1200, 7600, 8)]


def draw_sky(surf, cam, t):
    """天空：天顶冷色 → 地平线暖光的渐变 + 太阳辉光 + 缓慢飘动的云。"""
    hor = cam.project(cam.pos + cam.fwd * 3000)
    if hor is None:
        return
    hy = clamp(int(hor[1]), 0, surf.get_height())
    bands = 20
    for i in range(bands):
        f = i / (bands - 1)
        col = mix((92, 126, 158), (214, 216, 190), f ** 1.35)
        col = mix(col, (236, 222, 186), max(0.0, f - 0.72) * 2.4)
        y0 = int(hy * i / bands)
        y1 = int(hy * (i + 1) / bands) + 1
        pygame.draw.rect(surf, col, (0, y0, surf.get_width(), y1 - y0))
    pygame.draw.rect(surf, (12, 40, 38), (0, hy, surf.get_width(), surf.get_height() - hy))
    # 太阳辉光：沿主光方向在地平线附近洒一层暖光
    sun = cam.project(V3(LIGHT_XY[0] * 2400, LIGHT_XY[1] * 2400, 620))
    if sun and sun[1] < hy + 60:
        glow = _cloud_sprite(4, 96)
        for k, alpha in ((7.0, 26), (3.6, 34), (1.6, 46)):
            r = int(90 * k / 4)
            img = pygame.transform.smoothscale(glow, (r * 2, r * 2))
            img.set_alpha(alpha)
            surf.blit(img, (int(sun[0] - r), int(sun[1] - r - 20)))
    for (ang, height, radius, blobs) in _CLOUDS:
        drift = t * 22.0
        wx = math.cos(ang) * radius + drift
        wy = math.sin(ang) * radius * 0.6
        p = cam.project(V3(wx, wy, height))
        if p is None:
            continue
        sx, sy, depth = p
        if not (-200 < sx < surf.get_width() + 200 and -120 < sy < hy + 40):
            continue
        w = cam.screen_radius(radius * 0.45, depth) * 2.0
        h = w * 0.42
        if w < 8:
            continue
        img = pygame.transform.smoothscale(_cloud_sprite(blobs, 128),
                                           (max(8, int(w)), max(8, int(h))))
        img.set_alpha(150)
        surf.blit(img, (int(sx - w / 2), int(sy - h / 2)))


def caustic(x, y, t):
    """焦散光斑场：两组波纹光丝的乘积 × 低频明暗遮罩，随时间流动。"""
    v1 = math.sin(x * 0.037 + 2.6 * math.sin(y * 0.017 + t * 0.35))
    v2 = math.sin(y * 0.021 + 2.2 * math.sin(x * 0.041 + 2.1 + t * 0.28))
    m = 0.5 + 0.5 * math.sin(x * 0.008 + y * 0.006) * np_sin_mix(x, y)
    return (math.exp(-(v1 * v1) * 4.0) * math.exp(-(v2 * v2) * 4.0)) * (0.30 + 0.70 * m)


def np_sin_mix(x, y):
    return math.sin(y * 0.009 - x * 0.005)


def water_color(x, y, t):
    """水面底色：南北大气渐变 + 中心加深的深水区 + 极缓的涌浪。

    刻意不引入高频项——底色按大格绘制，格内是纯色，只有低频变化才不会露出格子接缝；
    高频的水纹交给上面那层小碎块与焦散闪点。"""
    f = (y + POND_H2) / (2 * POND_H2)
    base = _lerp_color(WATER_S, WATER_M, f * 2.0) if f < 0.5 \
        else _lerp_color(WATER_M, WATER_N, (f - 0.5) * 2.0)
    r = math.hypot(x / POND_W2, y / POND_H2)          # 池心水深更大, 颜色更沉
    base = _lerp_color(base, (24, 60, 52), 0.16 * clamp(1.12 - r, 0.0, 1.0))
    swell = math.sin(x * 0.0042 + t * 0.21) * math.sin(y * 0.0051 - t * 0.17)
    return mix(base, GLINT, 0.022 * swell + 0.010)


def draw_pond(painter, cam, t):
    """水面（大气渐变 + 涌浪 + 细纹）+ 焦散 + 岸边泡沫。"""
    # 底色只随 y 缓慢变化 → 行分得细(梯度平滑)、列分得粗(横向几乎不变), 既没有接缝也不贵
    rows, cols = 40, 8
    for i in range(rows):
        y0 = -POND_H2 + 2 * POND_H2 * i / rows
        y1 = -POND_H2 + 2 * POND_H2 * (i + 1) / rows
        for j in range(cols):
            x0 = -POND_W2 + 2 * POND_W2 * j / cols
            x1 = -POND_W2 + 2 * POND_W2 * (j + 1) / cols
            flat_polygon(painter, cam,
                         [V3(x0, y0, 0), V3(x1, y0, 0), V3(x1, y1, 0), V3(x0, y1, 0)],
                         water_color((x0 + x1) / 2, (y0 + y1) / 2, t), layer=0)
    # 高频水纹：短横划(读作水面细波), 比统一网格细, 不会出现方块接缝
    for gi in range(24):
        for gj in range(16):
            r1 = _hash01(gi, gj, 3)
            if r1 < 0.55:
                continue
            x = -POND_W2 + 1240 * (gi + _hash01(gi, gj, 11)) / 24
            y = -POND_H2 + 700 * (gj + _hash01(gi, gj, 12)) / 16
            s = 4.0 + 7.0 * _hash01(gi, gj, 13)
            a = (_hash01(gi, gj, 14) - 0.5) * 0.55        # 大体沿水面横向
            bright = _hash01(gi, gj, 15) > 0.5
            col = mix(water_color(x, y, t), GLINT if bright else (10, 34, 32),
                      0.030 + 0.045 * (r1 - 0.55) / 0.45)
            dx, dy = math.cos(a) * s, math.sin(a) * s * 0.30
            flat_polygon(painter, cam,
                         [V3(x - dx, y - dy, 0.06), V3(x + dx, y + dy, 0.06),
                          V3(x + dx, y + dy + 0.9, 0.06), V3(x - dx, y - dy + 0.9, 0.06)],
                         col, layer=1)
    for k in range(18):                                   # 水下泥沙明暗斑(大而淡)
        x = -POND_W2 + 80 + _hash01(k, 41, 31) * (2 * POND_W2 - 160)
        y = -POND_H2 + 60 + _hash01(k, 42, 31) * (2 * POND_H2 - 120)
        r = 46 + 90 * _hash01(k, 43, 31)
        tone = (16, 44, 38) if _hash01(k, 44, 31) > 0.5 else GLINT
        col = mix(water_color(x, y, t), tone, 0.10 + 0.06 * _hash01(k, 45, 31))
        rot = _hash01(k, 46, 31) * math.tau
        flat_polygon(painter, cam,
                     [V3(x + math.cos(rot + a * 0.9) * r,
                         y + math.sin(rot + a) * r * 0.68, 0.03)
                      for a in [2 * math.pi * i / 8 for i in range(8)]],
                     col, layer=1)
    for gx in range(36):                                  # 焦散闪点：小而淡, 避免悬浮感
        for gy in range(24):
            x = -POND_W2 + 1240 * gx / 35
            y = -POND_H2 + 700 * gy / 23
            c = caustic(x, y, t)
            if c > 0.70:
                k = int(30 + 78 * (c - 0.70) / 0.30)
                s = 2.6 + 2.8 * (c - 0.70)
                a = t * 0.6 + x * 0.01
                pts = [V3(x + s * math.cos(a), y + s * math.sin(a), 0.16),
                       V3(x - s * math.sin(a), y + s * math.cos(a), 0.16),
                       V3(x - s * math.cos(a), y - s * math.sin(a), 0.16),
                       V3(x + s * math.sin(a), y - s * math.cos(a), 0.16)]
                flat_polygon(painter, cam, pts, (int(k * 0.55), k, int(k * 0.8)), layer=1)
    # 会动的水线与浅水带：轻微吞吐, 画在 BANK 层(晚于机位缓存的静态沙滩与焦散)。
    shore = shore_line(t)
    n_s = len(shore)
    for i in range(n_s):                                  # 浅水带: 靠岸 30 单位内的透亮水色
        x0, y0, nx0, ny0, _ = shore[i]
        x1, y1, nx1, ny1, _ = shore[(i + 1) % n_s]
        flat_polygon(painter, cam,
                     [V3(x0 + nx0 * 30, y0 + ny0 * 30, 0.05),
                      V3(x1 + nx1 * 30, y1 + ny1 * 30, 0.05),
                      V3(x1, y1, 0.05), V3(x0, y0, 0.05)],
                     mix(water_color((x0 + x1) / 2, (y0 + y1) / 2, t),
                         (176, 218, 200), 0.26), layer=Painter.BANK)
    for i in range(n_s):                                  # 水线亮边(贴在水侧)
        x0, y0, nx0, ny0, _ = shore[i]
        x1, y1, nx1, ny1, _ = shore[(i + 1) % n_s]
        flat_polygon(painter, cam,
                     [V3(x0, y0, 0.34), V3(x1, y1, 0.34),
                      V3(x1 + nx1 * 2.4, y1 + ny1 * 2.4, 0.34),
                      V3(x0 + nx0 * 2.4, y0 + ny0 * 2.4, 0.34)],
                     mix(FOAM, WATER_S, 0.30), layer=Painter.BANK)


def draw_beach_base(painter, cam):
    """静态湿沙滩带(水线→堤壁)与沙面湿痕：只跟机位有关, 供岸基缓存调用。"""
    shore = _shore_base()
    n_s = len(shore)
    for i in range(n_s):                                  # 湿沙滩: 水线到堤壁之间的滩涂
        x0, y0, nx0, ny0, _ = shore[i]
        x1, y1, nx1, ny1, _ = shore[(i + 1) % n_s]
        px0, py0 = x0 - nx0 * _beach_band(x0, y0, nx0, ny0), \
            y0 - ny0 * _beach_band(x0, y0, nx0, ny0)
        px1, py1 = x1 - nx1 * _beach_band(x1, y1, nx1, ny1), \
            y1 - ny1 * _beach_band(x1, y1, nx1, ny1)
        g1 = _hash01(i, 7, 51)
        col = mix(SAND_WET, SAND, 0.30 + 0.42 * _hash01(i, 8, 52))
        col = mix(col, (255, 250, 238) if g1 > 0.5 else (112, 94, 70),
                  (g1 - 0.5 if g1 > 0.5 else 0.5 - g1) * 0.16)
        flat_polygon(painter, cam,
                     [V3(x0, y0, 0.3), V3(x1, y1, 0.3), V3(px1, py1, 0.3), V3(px0, py0, 0.3)],
                     col, layer=Painter.WATER)
    for i in range(n_s):                                  # 水线上方的常年湿痕(沙侧)
        x0, y0, nx0, ny0, _ = shore[i]
        x1, y1, nx1, ny1, _ = shore[(i + 1) % n_s]
        flat_polygon(painter, cam,
                     [V3(x0 - nx0 * 3.4, y0 - ny0 * 3.4, 0.33),
                      V3(x1 - nx1 * 3.4, y1 - ny1 * 3.4, 0.33),
                      V3(x1 - nx1 * 6.2, y1 - ny1 * 6.2, 0.33),
                      V3(x0 - nx0 * 6.2, y0 - ny0 * 6.2, 0.33)],
                     mix(FOAM, SAND_WET, 0.45), layer=Painter.WATER)


class Ripples:
    """扩散涟漪：3D 水面上的圆环（外圈淡、内圈亮，带一点高光）。"""

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
            ring = [V3(it["pos"].x + it["r"] * math.cos(a),
                       it["pos"].y + it["r"] * math.sin(a), 0.8)
                    for a in [2 * math.pi * i / 22 for i in range(23)]]
            polyline(painter, cam, ring, mix(shade(FOAM, 0.55), FOAM, k),
                     2 if k > 0.5 else 1, layer=1)
            if k > 0.35:                                   # 内圈暗一档, 涟漪才有"厚度"
                inner = [V3(it["pos"].x + (it["r"] - 3) * math.cos(a),
                            it["pos"].y + (it["r"] - 3) * math.sin(a), 0.74)
                         for a in [2 * math.pi * i / 18 for i in range(19)]]
                polyline(painter, cam, inner, mix(WATER_S, GLINT, 0.25 * k), 1, layer=1)


class LilyPad3D:
    """荷叶：带缺口的圆叶, 叶面做受光渐变 + 放射叶脉 + 镜面高光, 随水波起伏。
    叶影/叶面/内层/叶脉各层高度与 bias 显式错开防穿模。"""

    def __init__(self, pos, r, with_flower=False):
        self.pos = pygame.math.Vector2(pos)
        self.r = r
        self.phase = random.uniform(0, math.tau)
        self.notch = random.uniform(0, math.tau)
        self.flower = with_flower
        self.tone = random.uniform(0.90, 1.12)                  # 每片叶子的个体色差
        self.marks = [(random.uniform(0, math.tau), random.uniform(0.2, 0.75),
                       random.uniform(1.5, 3.2), random.random() < 0.5)
                      for _ in range(7)]                        # 叶面斑纹(预生成防抖动)
        self.veins = [random.uniform(-0.28, 0.28) for _ in range(7)]

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
        base = shade((62, 126, 66), self.tone)
        # 柔光叶影(投影在叶面下方的水面上)
        soft_shadow(painter, cam, V3(self.pos.x + 4, self.pos.y + 5, 0.25),
                    self.r * 1.02, self.r * 0.94, 0.72, bias=-4, layer=3)
        # 叶缘厚度：略大一圈的深色叶子垫在下面, 露出一点点边
        flat_polygon(painter, cam, [V3(p.x + 1.6, p.y + 2.0, p.z - 0.35)
                                    for p in self.leaf_pts(z, 1.045)],
                     shade(base, 0.52), bias=-1.5, layer=3)
        dome(painter, cam, self.leaf_pts(z), base, bias=-1.0, layer=3,
             outline=shade(base, 0.70), owidth=2, sheen=0.20)
        flat_polygon(painter, cam, self.leaf_pts(z, 0.86), add_light(base, 0.10),
                     bias=0.5, layer=3)
        # 叶脉：由叶心向叶缘放射, 越靠边越淡
        for idx, a in enumerate(self.veins):
            ang = self.notch + 0.75 + idx * (2 * math.pi - 1.5) / 6.7
            mid = V3(self.pos.x + math.cos(ang) * self.r * 0.45,
                     self.pos.y + math.sin(ang) * self.r * 0.41, z + 0.05)
            tip = V3(self.pos.x + math.cos(ang) * self.r * 0.80,
                     self.pos.y + math.sin(ang) * self.r * 0.73, z + 0.05)
            segment(painter, cam, V3(self.pos.x, self.pos.y, z + 0.1), mid,
                    mix(base, (168, 214, 132), 0.32), 2, bias=0.6, layer=3)
            segment(painter, cam, mid, tip, mix(base, (150, 200, 120), 0.18), 1,
                    bias=0.6, layer=3)
        for (ma, md, mr, dark) in self.marks:                    # 叶面斑纹
            px = self.pos.x + math.cos(ma) * self.r * md
            py = self.pos.y + math.sin(ma) * self.r * 0.86 * md
            col = mix(base, (36, 78, 40), 0.55) if dark else mix(base, (150, 206, 122), 0.45)
            flat_polygon(painter, cam,
                         [V3(px + math.cos(a) * mr, py + math.sin(a) * mr * 0.8, z + 0.12)
                          for a in [2 * math.pi * i / 6 for i in range(6)]],
                         col, bias=0.35, layer=3)
        # 近岸一侧的叶缘高光：让叶子"翻"起来一点点
        rim = []
        for i in range(9):
            a = self.notch + 2.2 + i * 0.24
            rim.append(V3(self.pos.x + math.cos(a) * self.r * 0.99,
                          self.pos.y + math.sin(a) * self.r * 0.91, z + 0.14))
        polyline(painter, cam, rim, mix(base, (198, 232, 160), 0.34), 2, layer=3)
        if self.flower:
            self._draw_flower(painter, cam, z)

    def _draw_flower(self, painter, cam, z):
        """杯状荷花: 两层花瓣面片(粉尖白底) + 黄色莲蓬, 参考真实荷花照片。"""
        cx, cy = self.pos
        sway = math.sin(self.phase) * 0.06

        def petal(a, r_in, r_tip, w, z_tip, col, tip_col):
            cta, sta = math.cos(a), math.sin(a)
            bl = V3(cx + cta * r_in - sta * w, cy + sta * r_in + cta * w, z + 2.8)
            br = V3(cx + cta * r_in + sta * w, cy + sta * r_in - cta * w, z + 2.8)
            tl = V3(cx + cta * r_tip * 0.82 - sta * w * 0.55,
                    cy + sta * r_tip * 0.82 + cta * w * 0.55, z + z_tip)
            tr = V3(cx + cta * r_tip * 0.82 + sta * w * 0.55,
                    cy + sta * r_tip * 0.82 - cta * w * 0.55, z + z_tip)
            tip = V3(cx + cta * r_tip, cy + sta * r_tip, z + z_tip * 0.82)
            flat_polygon(painter, cam, [bl, tl, tip, tr, br], col, bias=0.35, layer=3)
            flat_polygon(painter, cam,
                         [tl, tip, tr, V3((tl.x + tr.x) / 2, (tl.y + tr.y) / 2, z + z_tip * 0.6)],
                         tip_col, bias=0.42, layer=3)

        for i in range(8):                                    # 外层大花瓣: 粉尖
            a = 2 * math.pi * i / 8 + self.phase + sway
            petal(a, 2.5, 12.5, 2.2, 8.2, (238, 166, 202), (250, 196, 220))
        for i in range(6):                                    # 内层花瓣: 更立更浅
            a = 2 * math.pi * i / 6 + self.phase * 1.3 + 0.3
            petal(a, 1.6, 8.0, 1.8, 9.6, (250, 196, 224), (255, 224, 238))
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
        self.seed_off = random.uniform(0, 6.28)
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
        soft_shadow(painter, cam, V3(cx + 1, cy + 1, PAD_TOP + 0.9), 5.0 * s, 3.6 * s,
                    0.55, bias=0.05, layer=3)
        grains = ((0, 0, 2.3, (162, 116, 68)), (3, 2, 1.9, (146, 100, 58)),
                  (-3, 1.5, 1.8, (172, 128, 78)), (1, -3, 1.7, (154, 108, 62)))
        for dx, dy, rr, col in grains:
            sphere(painter, cam, V3(cx + dx * s, cy + dy * s, PAD_TOP + 1.2), rr * s,
                   col, bias=0.2, layer=3)
        sphere(painter, cam, V3(cx - 1.5 * s, cy - 1.5 * s, PAD_TOP + 2.1), 1.2 * s,
               (214, 170, 116), bias=0.25, layer=3)


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
    """浮萍: 一簇簇的小圆叶漂在水面(参考真实池塘照片), 只落在水线以内。"""
    clusters = []
    for _ in range(n):
        for _try in range(24):
            x = random.uniform(-POND_W2 + 40, POND_W2 - 40)
            y = random.uniform(-POND_H2 + 40, POND_H2 - 40)
            if inside_shore(x, y):
                break
        else:
            continue
        dots = [(random.uniform(-4.5, 4.5), random.uniform(-4.5, 4.5),
                 random.uniform(0.9, 2.0), random.uniform(0, math.tau)) for _ in range(random.randint(3, 6))]
        clusters.append({"x": x, "y": y, "dots": dots, "phase": random.uniform(0, math.tau)})
    return clusters


def draw_duckweed(painter, cam, t, clusters):
    for cl in clusters:
        drift = math.sin(t * 0.22 + cl["phase"]) * 3.0
        for dx, dy, r, rot in cl["dots"]:
            px, py = cl["x"] + dx + drift, cl["y"] + dy
            top = (120, 186, 90) if (dx + dy) > 0 else (98, 164, 78)
            flat_polygon(painter, cam,
                         [V3(px + math.cos(rot + a) * r * 1.15,
                             py + math.sin(rot + a) * r * 0.95, 0.18)
                          for a in [2 * math.pi * i / 7 for i in range(7)]],
                         shade(top, 0.72), bias=-0.2, layer=1)
            flat_polygon(painter, cam,
                         [V3(px + math.cos(rot + a) * r, py + math.sin(rot + a) * r * 0.85, 0.26)
                          for a in [2 * math.pi * i / 7 for i in range(7)]],
                         top, bias=0.2, layer=1)


def make_bank_props():
    """岸边一次性布景：岩石、草丛、芦苇香蒲、灌木丛、沙面卵石。"""
    rng = random.Random(20)
    rocks, grass, reeds, bushes, pebbles = [], [], [], [], []
    for x in range(-560, 561, 95):                        # 远岸岩石与灌木
        rocks.append((x + rng.uniform(-24, 24), POND_H2 - 4, rng.uniform(6, 13),
                      rng.choice(((158, 150, 132), (140, 134, 122), (170, 160, 138)))))
    for x in range(-520, 521, 78):
        bushes.append((x + rng.uniform(-24, 24), POND_H2 + 28 + rng.uniform(-14, 22),
                       rng.uniform(30, 60), rng.choice(((74, 110, 60), (86, 122, 66), (66, 100, 56)))))
    for y in range(-240, 241, 130):                       # 两岸各几丛
        bushes.append((-(POND_W2 + 40), y, rng.uniform(26, 42), (78, 114, 62)))
        bushes.append((POND_W2 + 40, y + 60, rng.uniform(26, 42), (70, 106, 58)))
        rocks.append((-(POND_W2 - 4), y, rng.uniform(6, 11), (152, 146, 130)))
        rocks.append((POND_W2 - 4, y + 50, rng.uniform(6, 11), (162, 154, 136)))
    for x in range(-590, 591, 22):                        # 远岸芦苇（密）
        reeds.append((x + rng.uniform(-8, 8), POND_H2 + 3 + rng.uniform(-3, 5),
                      rng.uniform(52, 104), rng.random() < 0.30, rng.uniform(0, math.tau)))
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
    rim = 44
    for _ in range(64):                                   # 岸顶散落的卵石(沙面纹理)
        side = rng.randrange(4)
        if side == 0:
            pebbles.append((rng.uniform(-POND_W2 - rim, POND_W2 + rim),
                            POND_H2 + rng.uniform(2, rim - 4), rng.uniform(1.4, 3.4)))
        elif side == 1:
            pebbles.append((rng.uniform(-POND_W2 - rim, POND_W2 + rim),
                            -POND_H2 - rng.uniform(2, rim - 4), rng.uniform(1.4, 3.4)))
        elif side == 2:
            pebbles.append((-(POND_W2 + rng.uniform(2, rim - 4)),
                            rng.uniform(-POND_H2, POND_H2), rng.uniform(1.4, 3.4)))
        else:
            pebbles.append((POND_W2 + rng.uniform(2, rim - 4),
                            rng.uniform(-POND_H2, POND_H2), rng.uniform(1.4, 3.4)))
    return {"rocks": rocks, "grass": grass, "reeds": reeds, "bushes": bushes,
            "pebbles": pebbles}


def _sand_color(x, z):
    f = clamp(z / BANK_H, 0.0, 1.0)
    base = mix(SAND_WET, SAND, clamp(f * 1.5, 0, 1))
    base = mix(base, SAND_TOP, clamp((f - 0.6) * 2.2, 0, 1))
    grain = (_hash01(x // 18, z // 6, 7) - 0.5) * 0.10
    return mix(base, (255, 250, 240) if grain > 0 else (110, 92, 66), abs(grain))


def draw_banks(painter, cam, t, props):
    """四面沙岸堤壁 + 岸顶 + 岩石/草丛/芦苇/灌木。"""
    draw_bank_base(painter, cam, props)
    draw_bank_plants(painter, cam, t, props)


def draw_bank_base(painter, cam, props):
    """岸上不随时间变化的部分：堤壁、草地、卵石、岩石、灌木。

    这部分只跟机位有关，主循环会把它缓存成一张离屏贴图，机位不动时直接 blit。
    """
    pw, ph, h = POND_W2, POND_H2, BANK_H
    seg = 8
    walls = []
    for i in range(seg):
        x0, x1 = -pw + 2 * pw * i / seg, -pw + 2 * pw * (i + 1) / seg
        y0, y1 = -ph + 2 * ph * i / seg, -ph + 2 * ph * (i + 1) / seg
        walls.append(([V3(x0, ph, 0), V3(x1, ph, 0), V3(x1, ph, h), V3(x0, ph, h)],
                      (x0 + x1) / 2, "y+"))
        walls.append(([V3(x0, -ph, 0), V3(x1, -ph, 0), V3(x1, -ph, h), V3(x0, -ph, h)],
                      (x0 + x1) / 2, "y-"))
        walls.append(([V3(-pw, y0, 0), V3(-pw, y1, 0), V3(-pw, y1, h), V3(-pw, y0, h)],
                      (y0 + y1) / 2, "x-"))
        walls.append(([V3(pw, y0, 0), V3(pw, y1, 0), V3(pw, y1, h), V3(pw, y0, h)],
                      (y0 + y1) / 2, "x+"))
    # 立面: 由湿到干的高度渐变(分 4 条带, 颜色连续不出现方块接缝)
    for pts, coord, side in walls:
        for k in range(4):
            z0, z1 = h * k / 4, h * (k + 1) / 4
            band = []
            for p in pts:
                band.append(V3(p.x, p.y, clamp(p.z, z0, z1)))
            x_ref = coord if side[0] == "x" else abs(coord)
            col = _sand_color(x_ref, (z0 + z1) / 2)
            if side[1] == "+":                            # 南壁受光更多
                col = add_light(col, 0.05)
            else:
                col = shade(col, 0.97)
            flat_polygon(painter, cam, band, col, layer=3)
    rim = 44
    for pts in ([V3(-pw - rim, ph, h + 0.3), V3(pw + rim, ph, h + 0.3),
                 V3(pw + rim, ph + rim, h + 0.3), V3(-pw - rim, ph + rim, h + 0.3)],
                [V3(-pw - rim, -ph, h + 0.3), V3(pw + rim, -ph, h + 0.3),
                 V3(pw + rim, -ph - rim, h + 0.3), V3(-pw - rim, -ph - rim, h + 0.3)],
                [V3(-pw, -ph, h + 0.3), V3(-pw, ph, h + 0.3),
                 V3(-pw - rim, ph + rim, h + 0.3), V3(-pw - rim, -ph - rim, h + 0.3)],
                [V3(pw, -ph, h + 0.3), V3(pw, ph, h + 0.3),
                 V3(pw + rim, ph + rim, h + 0.3), V3(pw + rim, -ph - rim, h + 0.3)]):
        flat_polygon(painter, cam, pts, _sand_color(0, BANK_H * 0.9), layer=3)
    # 塘外草地: 铺满远方, 镜头低角度时不再露出虚空
    FAR = 2600
    for near, far, side in ((ph + rim, FAR, "n"), (-FAR, -ph - rim, "s"),
                            (-FAR, pw * -1 - rim, "w"), (pw + rim, FAR, "e")):
        steps = 6
        for i in range(steps):                         # 由近及远的雾化渐变草地
            f0, f1 = i / steps, (i + 1) / steps
            col = mix(MEADOW, (132, 156, 116), f0 ** 1.15 * 0.55)
            if side in ("n", "s"):
                a0 = near + (far - near) * f0
                a1 = near + (far - near) * f1
                pts = ([V3(-FAR, a0, h - 0.5), V3(FAR, a0, h - 0.5),
                        V3(FAR, a1, h - 0.5), V3(-FAR, a1, h - 0.5)] if side == "n" else
                       [V3(-FAR, a1, h - 0.5), V3(FAR, a1, h - 0.5),
                        V3(FAR, a0, h - 0.5), V3(-FAR, a0, h - 0.5)])
            else:
                a0 = near + (far - near) * f0
                a1 = near + (far - near) * f1
                lo, hi = -ph - rim, ph + rim
                pts = ([V3(a0, lo, h - 0.5), V3(a1, lo, h - 0.5),
                        V3(a1, hi, h - 0.5), V3(a0, hi, h - 0.5)] if side == "w" else
                       [V3(a1, lo, h - 0.5), V3(a0, lo, h - 0.5),
                        V3(a0, hi, h - 0.5), V3(a1, hi, h - 0.5)])
            flat_polygon(painter, cam, pts, col, layer=3)
    for k in range(46):                                # 草地上零散的深色草丛
        a = _hash01(k, 3, 21) * math.tau
        d = 120 + 1500 * _hash01(k, 4, 22)
        tx, ty = math.cos(a) * d, math.sin(a) * d
        if abs(tx) < pw + 30 and abs(ty) < ph + 30:
            continue
        r = 12 + 26 * _hash01(k, 5, 23)
        flat_polygon(painter, cam,
                     [V3(tx + math.cos(a2) * r, ty + math.sin(a2) * r * 0.7, h - 0.4)
                      for a2 in [2 * math.pi * i / 7 for i in range(7)]],
                     mix(MEADOW, (44, 72, 40), 0.45), layer=3)
    for (px, py, pr) in props["pebbles"]:                 # 沙面卵石
        sphere(painter, cam, V3(px, py, h + 0.35), pr, (196, 182, 156), layer=3)
    for (rx, ry, rr, rcol) in props["rocks"]:             # 岩石
        soft_shadow(painter, cam, V3(rx + rr * 0.35, ry + rr * 0.30, h + 0.4),
                    rr * 0.95, rr * 0.62, 0.62, bias=-0.4, layer=3)
        sphere(painter, cam, V3(rx, ry, h + rr * 0.22), rr, rcol, layer=3, sheen=0.45)
        for k in range(2):                                # 岩面斑点
            a = 1.1 + k * 2.0
            sphere(painter, cam,
                   V3(rx + math.cos(a) * rr * 0.42, ry + math.sin(a) * rr * 0.30,
                      h + rr * 0.25), rr * 0.16, shade(rcol, 0.82), bias=0.2, layer=3)
    for (bx, by, br, bc) in props["bushes"]:              # 灌木丛（主球 + 次球簇）
        if br > 42:
            soft_shadow(painter, cam, V3(bx + br * 0.3, by + br * 0.25, h + 0.4),
                        br * 0.88, br * 0.58, 0.58, bias=-0.4, layer=3)
        blobs = ((0.0, 0.0, 1.0, 0.0), (0.55, 0.15, 0.58, 0.10),
                 (-0.5, -0.16, 0.5, -0.06), (0.12, -0.5, 0.46, 0.06))
        for dx, dy, k, lift in blobs:
            col = mix(bc, (18, 34, 20), 0.14) if lift < 0 else add_light(bc, 0.07 * k)
            sphere(painter, cam,
                   V3(bx + dx * br, by + dy * br, h + br * (0.72 + lift)),
                   br * k, col, layer=3, sheen=0.35)
        for k in range(2):                                # 叶簇纹理
            a = 0.7 + k * 1.26
            sphere(painter, cam,
                   V3(bx + math.cos(a) * br * 0.62, by + math.sin(a) * br * 0.5,
                      h + br * (0.85 + 0.08 * math.sin(a))),
                   br * 0.17, add_light(bc, 0.16), bias=0.3, layer=3, sheen=0.4)


def draw_bank_plants(painter, cam, t, props):
    """会随风摆动的部分：草丛与芦苇香蒲（每帧实时画）。"""
    h = BANK_H
    for (gx, gy), gh, gph in props["grass"]:              # 草丛
        for k in range(3):
            a = gph + k * 0.42 - 0.84
            lean = 0.45 + 0.35 * math.sin(t * 0.6 + gph + k)
            mid = V3(gx + math.cos(a) * 3.4 * lean, gy + math.sin(a) * 3.4 * lean, h + gh * 0.55)
            tip = V3(gx + math.cos(a) * 5.6 * lean, gy + math.sin(a) * 5.6 * lean, h + gh)
            base_c = (74, 116, 54) if k % 2 else (88, 132, 62)
            segment(painter, cam, V3(gx, gy, h), mid, base_c, 2, layer=3)
            segment(painter, cam, mid, tip, add_light(base_c, 0.16), 2, layer=3)
    for (rx, ry, rh, cattail, ph2) in props["reeds"]:     # 芦苇与香蒲
        sway = math.sin(t * 0.9 + ph2) * 3.0
        base = V3(rx, ry, h)
        mid = V3(rx + sway * 0.45, ry + 1.5, h + rh * 0.55)
        tip = V3(rx + sway, ry, h + rh)
        segment(painter, cam, base, mid, (52, 96, 46), 3, layer=3)
        segment(painter, cam, mid, tip, (92, 140, 66), 3, layer=3)
        blade_mid = V3(rx + sway * 0.5 - 5, ry - 3, h + rh * 0.45)
        segment(painter, cam, base, blade_mid, (72, 116, 58), 2, layer=3)
        if cattail:                                       # 香蒲穗: 圆柱 + 高光
            c0 = V3(rx + sway, ry, h + rh - 15)
            c1 = V3(rx + sway, ry, h + rh - 3)
            segment(painter, cam, c0, c1, (86, 56, 32), 6, layer=3)
            segment(painter, cam, V3(c0.x - 0.6, c0.y - 0.6, c0.z + 2),
                    V3(c1.x - 0.6, c1.y - 0.6, c1.z - 2), (134, 92, 52), 2, layer=3)
            segment(painter, cam, c1, V3(rx + sway, ry, h + rh + 1.5), (128, 96, 58), 2, layer=3)


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
