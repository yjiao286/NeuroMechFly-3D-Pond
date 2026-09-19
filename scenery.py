"""3D 池塘场景：水面、焦散、涟漪、荷叶荷花、食饵、沙岸/岩石/草丛/芦苇/灌木与天空。

视觉做法：所有平面物都带"受光"层次（边缘压暗 → 向光面提亮 → 高光/镜面），
水面用低频涌浪 + 高频纹样 + 焦散三层叠加，落地物一律带柔光阴影。
层间仍用微小深度偏置(bias)防穿模：同一物体各层显式错开排序。
"""

from __future__ import annotations

import math
import random

import numpy as np
import pygame

from render3d import (LIGHT_XY, V3, add_light, clamp, dome, flat_polygon, limb, mix,
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


# ---------------------------------------------------------------- 水面(逐像素)
# 水面是整幅画面最大的一块, 用多边形拼必然露格子。这里改成"逐像素解析":
# 每个像素反投影到 z=0 的水面上得到世界坐标, 再解析地算浅深、菲涅尔天空反射、
# 焦散与涟漪。静态部分(遮罩+基础水色)按机位缓存, 每帧只算会动的那几项。
WATER_Q = 2                              # 静止机位: 1/2 分辨率(焦散最锐)
WATER_Q_MOVING = 3                       # 转动镜头时降到 1/3: 每帧都要重算, 省一半时间
DEEP = (33, 74, 65)                      # 池心深水
SHALLOW = (118, 158, 124)                # 近岸浅水(能看见塘底)
SKY_REFLECT = (152, 186, 186)            # 掠射角反射的天光
_WATER_CACHE: dict = {}


def _shore_mask(q, w, h, cam):
    """岸线遮罩: 把岸线多边形投影后光栅化。

    逐像素射线法(96 边 × 25 万像素)要几十毫秒; 交给 pygame 的 C 填充 + 一次
    surfarray 读回只要 1~2 毫秒, 而且边界同样是像素级精确的。
    """
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    pts = []
    for (x, y, nx, ny, _s) in _shore_base():
        d = V3(x, y, 0.3) - cam.pos
        depth = max(cam.NEAR * 0.5, d.dot(cam.fwd))     # 近平面之后按夹住的深度投影
        pts.append(((cam.cx + cam.focal * d.dot(cam.right) / depth) / q,
                    (cam.cy - cam.focal * d.dot(cam.up) / depth) / q))
    pygame.draw.polygon(surf, (255, 255, 255, 255), pts)
    return np.ascontiguousarray(pygame.surfarray.array_alpha(surf).T > 127)


def _water_plane(cam, q=WATER_Q):
    """屏幕像素 → 水面世界坐标 + 岸线遮罩 + 基础水色(带机位缓存)。"""
    key = (round(cam.pos.x, 2), round(cam.pos.y, 2), round(cam.pos.z, 2),
           round(cam.fwd.x, 4), round(cam.fwd.y, 4), round(cam.fwd.z, 4),
           round(cam.right.x, 4), round(cam.right.y, 4), round(cam.right.z, 4),
           round(cam.up.x, 4), round(cam.up.y, 4), round(cam.up.z, 4),
           round(cam.focal, 1), cam.w, cam.h, q)
    hit = _WATER_CACHE.get(key)
    if hit is not None:
        return hit
    w, h = max(1, cam.w // q), max(1, cam.h // q)
    px = (np.arange(w) + 0.5) * q
    py = (np.arange(h) + 0.5) * q
    X = (px - cam.cx) / cam.focal
    Y = -(py - cam.cy) / cam.focal
    rx, ry, rz = cam.right
    ux, uy, uz = cam.up
    fx, fy, fz = cam.fwd
    Dx = fx + X[None, :] * rx + Y[:, None] * ux
    Dy = fy + X[None, :] * ry + Y[:, None] * uy
    Dz = fz + X[None, :] * rz + Y[:, None] * uz
    with np.errstate(divide="ignore", invalid="ignore"):
        tt = np.where(Dz < -1e-4, -cam.pos.z / Dz, np.inf)
    tt = np.where(np.isfinite(tt), tt, 0.0).astype(np.float32)   # 打不到水面的像素归零
    wx = cam.pos.x + Dx * tt
    wy = cam.pos.y + Dy * tt
    mask = _shore_mask(q, w, h, cam) & (tt > 0.0)
    nrm = np.sqrt(Dx * Dx + Dy * Dy + Dz * Dz)
    graze = np.clip(1.0 - np.abs(Dz) / np.maximum(nrm, 1e-6), 0.0, 1.0) ** 2.2
    rr = np.sqrt((wx / POND_W2) ** 2 + (wy / POND_H2) ** 2)
    # 浅水是"贴着岸的一圈": rr→1(岸线) 时最浅, 池心(rr→0)最深。
    # 之前写成 (1-rr) 正好反了——池心发亮、四角最黑, 看着就像池塘四角糊了墨。
    shallow = np.clip((rr - 0.58) / 0.46, 0.0, 1.0) ** 0.90   # 宽而缓的浅滩过渡
    deep = np.array(DEEP, np.float32)
    near = np.array(SHALLOW, np.float32)
    sky = np.array(SKY_REFLECT, np.float32)
    col = deep + (near - deep) * shallow[..., None]
    col = col + (sky - col) * (graze * 0.24)[..., None]
    swell = 0.5 + 0.5 * np.sin(wx * 0.0042) * np.sin(wy * 0.0051)   # 大尺度涌浪
    col = col * (0.965 + 0.07 * swell)[..., None]
    base = np.zeros((h, w, 4), np.uint8)
    base[..., :3] = np.clip(col, 0, 255).astype(np.uint8)
    base[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
    hit = {"base": base, "wx": wx, "wy": wy, "mask": mask, "shallow": shallow,
           "size": (w, h), "q": q}
    if len(_WATER_CACHE) > 8:
        _WATER_CACHE.clear()
    _WATER_CACHE[key] = hit
    return hit


def draw_pond(painter, cam, t, moving=False):
    """水面(逐像素解析) + 岸边浅水带 + 水线亮边。

    水面图像每 3 帧重建一次: 焦散本身是慢动作, 复用上一张完全看不出来,
    但每帧省下的 numpy 混合与缩放是实打实的。
    """
    wp = _water_plane(cam, WATER_Q_MOVING if moving else WATER_Q)
    st = wp
    if st.get("surf") is None or st.get("age", 0) >= 3:
        wx, wy, mask, shallow = wp["wx"], wp["wy"], wp["mask"], wp["shallow"]
        arr = wp["base"].copy()
        if mask.any():
            mx, my, ms = wx[mask], wy[mask], shallow[mask]
            # 焦散: 两个方向都被慢波扭曲的正弦坐标系取"细亮线", 相乘成交叉光网。
            u = (mx * 0.026 + 0.95 * np.sin(my * 0.011 + t * 0.20)
                 + 0.45 * np.sin(mx * 0.008 - t * 0.13))
            v = (my * 0.023 + 0.95 * np.sin(mx * 0.013 - t * 0.17)
                 + 0.45 * np.sin(my * 0.009 + t * 0.15))
            l1 = np.abs(np.sin(u)) ** 16
            l2 = np.abs(np.sin(v)) ** 16
            glow = np.clip(l1 + l2 + 1.5 * l1 * l2, 0.0, 1.0) * 0.44 * (0.30 + 0.70 * ms)
            sub = arr[mask]
            rgb = sub[:, :3].astype(np.float32)
            glint = np.array(GLINT, np.float32)
            rgb += (glint - rgb) * (glow * 0.34)[:, None]
            sub[:, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
            arr[mask] = sub
        surf = pygame.image.frombuffer(arr.tobytes(), wp["size"], "RGBA")
        st["surf"] = pygame.transform.smoothscale(surf, (cam.w, cam.h))
        ys, xs = np.nonzero(mask)
        q = wp["q"]                        # 必须用这张水面实际用的倍率(转动时是 WATER_Q_MOVING)
        st["box"] = ((int(xs.min()) * q, int(ys.min()) * q,
                      int(xs.max()) * q + q, int(ys.max()) * q + q)
                     if xs.size else (0, 0, 0, 0))
        st["age"] = 0
    st["age"] = st.get("age", 0) + 1
    img, (bx0, by0, bx1, by1) = st["surf"], st["box"]
    if bx1 > bx0:
        rect = pygame.Rect(bx0, by0, bx1 - bx0, by1 - by0)
        rect = rect.clip(pygame.Rect(0, 0, cam.w, cam.h))
        painter.add(1e9, lambda s, img=img, rect=rect: s.blit(img, rect.topleft, rect),
                    Painter.WATER)

    # 岸边: 贴在水侧的亮边(会随吞吐轻微移动, 画在 BANK 层)
    shore = shore_line(t)
    n_s = len(shore)
    for i in range(n_s):
        x0, y0, nx0, ny0, _ = shore[i]
        x1, y1, nx1, ny1, _ = shore[(i + 1) % n_s]
        flat_polygon(painter, cam,
                     [V3(x0, y0, 0.34), V3(x1, y1, 0.34),
                      V3(x1 + nx1 * 2.4, y1 + ny1 * 2.4, 0.34),
                      V3(x0 + nx0 * 2.4, y0 + ny0 * 2.4, 0.34)],
                     mix(FOAM, (150, 190, 176), 0.35), layer=Painter.BANK)


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
        for i in range(33):
            a = a0 + (2 * math.pi - 0.60) * i / 32
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
        flat_polygon(painter, cam, [V3(p.x + 1.8, p.y + 2.2, p.z - 0.42)
                                    for p in self.leaf_pts(z, 1.05)],
                     shade(base, 0.60), bias=-1.5, layer=3, aa=True)
        dome(painter, cam, self.leaf_pts(z), base, bias=-1.0, layer=3,
             color_top=add_light(base, 0.10), sheen=0.26, steps=5, spread=0.10)
        flat_polygon(painter, cam, self.leaf_pts(z + 0.5, 0.90), add_light(base, 0.07),
                     bias=0.5, layer=3, aa=True)
        flat_polygon(painter, cam, self.leaf_pts(z + 0.9, 0.62), add_light(base, 0.13),
                     bias=0.6, layer=3)
        rim2 = [V3(px, py, z + 0.35) for px, py in
                [(p.x, p.y) for p in self.leaf_pts(z, 0.995)]]
        polyline(painter, cam, rim2 + [rim2[0]], mix(base, (196, 232, 156), 0.40), 2,
                 layer=3)
        # 叶脉：由叶心向叶缘放射, 越靠边越淡
        for idx, a in enumerate(self.veins):
            ang = self.notch + 0.75 + idx * (2 * math.pi - 1.5) / 6.7
            mid = V3(self.pos.x + math.cos(ang) * self.r * 0.45,
                     self.pos.y + math.sin(ang) * self.r * 0.41, z + 0.05)
            tip = V3(self.pos.x + math.cos(ang) * self.r * 0.80,
                     self.pos.y + math.sin(ang) * self.r * 0.73, z + 0.05)
            segment(painter, cam, V3(self.pos.x, self.pos.y, z + 0.95), mid,
                    mix(base, (176, 220, 140), 0.46), 2, bias=0.72, layer=3)
            segment(painter, cam, mid, tip, mix(base, (160, 208, 128), 0.24), 1,
                    bias=0.72, layer=3)
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

        for i in range(7):                                    # 外层: 微微张开的花瓣
            a = 2 * math.pi * i / 7 + self.phase + sway
            petal(a, 2.2, 10.2, 3.0, 7.4, (236, 170, 200), (248, 202, 222))
        for i in range(5):                                    # 内层: 立起来收成花苞
            a = 2 * math.pi * i / 5 + self.phase * 1.3 + 0.35
            petal(a, 1.4, 5.0, 2.6, 11.0, (248, 198, 220), (255, 228, 240))
        sphere(painter, cam, V3(cx, cy, z + 9.4), 2.2, (250, 214, 196), bias=0.45,
               layer=3, sheen=0.5)


class FoodCrumb:
    """荷叶上的食饵碎屑：果蝇的觅食目标，被啃食后缩小，2.5~4 秒后长回来。

    一块碎屑同一时刻只容 1 只果蝇进食（CAPACITY=1）——这是"抢食"的物理约束：
    认领/在途的果蝇会把后来者挤到别的荷叶上去, 或者让它在上面盘旋等位。
    """

    CAPACITY = 1                       # 同时能站几只果蝇

    def __init__(self, pad, side=None):
        self.pad = pad
        self.amount = 0.0
        self.timer = random.uniform(0.5, 3.0)
        self.offset = pygame.math.Vector2(0, 0)
        self.claims = 0                # 已认领(把它当目标)的果蝇数
        self.feeders = 0               # 正在这块碎屑上进食的果蝇数
        # 在途认领者名单, 每帧由 Game.simulate 重建: [(距离, 果蝇id, 是否进食)]
        self.waiters = []
        # 在座进食者登记: 帧首按 eating_now 重建, 帧内由开吃闸门实时增删——
        # feeders 统计是帧首快照, 同帧先后落地的两只会都看到"0人占座",
        # 只有这个集合能在同一帧内仲裁出唯一入座者
        self.seated = set()
        self.side = side               # 同片荷叶上的第几块(0/1): 重生在对侧半边
        self.seed_off = random.uniform(0, 6.28)
        self.respawn()

    def seats_for(self, dist):
        """从 dist 处看这块碎屑还剩几个空位 = 容量 − 正在进食 − 比我更近的在途者。

        规则是"最近者得座": 只统计严格比我更近的认领者, 距离并列表外者按先到
        先得(同一帧里谁都看谁不顺眼的情况由进食闸门的粘性兜底)。
        曾经的两个坑:
          · 只按"认领数"判满 → 两只互相认领就都以为没位置, 全在天上排队
            (容量=1 时只要两只同时等位就互相锁死, 谁也不落);
          · 只按"正在进食"判满 → 同时到达的两只会一起落下去, 超出容量。
        "最近者得座"对任意容量都给出唯一的落座者, 两类问题都不存在。
        """
        closer = sum(1 for (d, _fid, eating) in self.waiters if not eating and d < dist)
        return max(0, self.CAPACITY - self.feeders - closer)

    def full(self, for_fly=None):
        dist = for_fly.pos.distance_to(self.pos()) if for_fly is not None else 0.0
        return self.seats_for(dist) <= 0

    def respawn(self):
        self.amount = 6.0
        # 有荷花的荷叶: 食饵放远一点, 不和花瓣/落下的果蝇挤在一起;
        # 同片荷叶的第二块固定在对侧半边, 两块碎屑不会叠在一起
        if self.side is None:
            a = random.uniform(0, math.tau)
        else:
            a = self.side * math.pi + random.uniform(-0.6, 0.6)
        d = random.uniform(0.15, 0.5) * self.pad.r if not self.pad.flower \
            else random.uniform(0.55, 0.8) * self.pad.r
        self.offset = pygame.math.Vector2(math.cos(a) * d, math.sin(a) * d * 0.9)

    def bite(self, dt):
        self.amount = max(0.0, self.amount - dt * 0.8)
        if self.amount <= 0:
            self.timer = random.uniform(2.5, 4.0)       # 吃完很快长回来(整池周转的节奏)
            return True
        return False

    def pos(self):
        return self.pad.pos + self.offset

    def draw(self, painter, cam, t):
        if self.amount <= 0:
            return
        # 画进生物层(4)而不是荷叶层(3): 层内按视深排序, 食饵落在荷叶远侧时
        # 视深比整片叶面的平均深度大, 会被叶面整个盖住(以前单食饵随机摆放
        # 时隐时现, 双食饵后必有一块消失)。生物层整层在荷叶之后, 一劳永逸;
        # 和站在旁边/后面的果蝇同层按深度互相遮挡, 依然正确。
        s = 0.45 + 0.55 * self.amount / 6.0
        k = 1.45                                    # 食饵随果蝇体型同步放大
        cx, cy = self.pos()
        soft_shadow(painter, cam, V3(cx + 1, cy + 1, PAD_TOP + 0.9), 5.0 * k * s,
                    3.6 * k * s, 0.55, bias=0.05, layer=Painter.MAIN)
        grains = ((0, 0, 2.3, (162, 116, 68)), (3, 2, 1.9, (146, 100, 58)),
                  (-3, 1.5, 1.8, (172, 128, 78)), (1, -3, 1.7, (154, 108, 62)))
        for dx, dy, rr, col in grains:
            sphere(painter, cam, V3(cx + dx * k * s, cy + dy * k * s, PAD_TOP + 1.2),
                   rr * k * s, col, bias=0.2, layer=Painter.MAIN)
        sphere(painter, cam, V3(cx - 1.5 * k * s, cy - 1.5 * k * s, PAD_TOP + 2.1),
               1.2 * k * s, (214, 170, 116), bias=0.25, layer=Painter.MAIN)


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
    for (gx, gy), gh, gph in props["grass"]:              # 草丛: 三片锥形叶, 随相位摆动
        for k in range(3):
            a = gph + k * 0.42 - 0.84
            lean = 0.45 + 0.35 * math.sin(t * 0.6 + gph + k)
            mid = V3(gx + math.cos(a) * 3.4 * lean, gy + math.sin(a) * 3.4 * lean,
                     h + gh * 0.55)
            tip = V3(gx + math.cos(a) * 5.6 * lean, gy + math.sin(a) * 5.6 * lean, h + gh)
            base_c = mix((70, 110, 52), (96, 140, 66), 0.35 + 0.5 * ((k * 7 + int(gph * 9)) % 3) / 2)
            segment(painter, cam, V3(gx, gy, h), mid, base_c, 2, layer=3)
            segment(painter, cam, mid, tip, add_light(base_c, 0.16), 2, layer=3)
    for (rx, ry, rh, cattail, ph2) in props["reeds"]:     # 芦苇与香蒲
        sway = math.sin(t * 0.9 + ph2) * 3.0
        base = V3(rx, ry, h)
        mid = V3(rx + sway * 0.45, ry + 1.5, h + rh * 0.55)
        tip = V3(rx + sway, ry, h + rh)
        limb(painter, cam, base, mid, 1.7, (56, 100, 48), taper=0.72, layer=3)
        limb(painter, cam, mid, tip, 1.22, (96, 146, 68), taper=0.34, layer=3)
        blade_mid = V3(rx + sway * 0.5 - 5, ry - 3, h + rh * 0.42)
        limb(painter, cam, base, blade_mid, 1.1, (74, 120, 58), taper=0.10, layer=3)
        if cattail:                                       # 香蒲穗: 锥形圆柱 + 受光面
            c0 = V3(rx + sway, ry, h + rh - 15)
            c1 = V3(rx + sway, ry, h + rh - 3)
            limb(painter, cam, c0, c1, 2.9, (84, 54, 32), taper=1.0, layer=3)
            limb(painter, cam, V3(c0.x - 0.7, c0.y - 0.7, c0.z + 2),
                 V3(c1.x - 0.7, c1.y - 0.7, c1.z - 2), 1.1,
                 add_light((84, 54, 32), 0.22), taper=0.9, layer=3)
            limb(painter, cam, c1, V3(rx + sway, ry, h + rh + 1.5), 0.8,
                 (128, 96, 58), taper=0.3, layer=3)
