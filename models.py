"""生物建模层：青蛙与果蝇的形体、姿态、着色。

几何写成"规格表 + 姿态函数"，而不是一长串手调的多边形：
  · 体形 = 沿体轴的半宽表 → ribbon_pts 生成平滑有机轮廓；
  · 体块 = 几层同轮廓的"穹顶"(dome)，逐层向光面内缩提亮，得到连续的明暗过渡；
  · 肢体 = 关节链(髋→膝→踝)，姿态随跳跃/行走插值。

每只生物都在自己的超采样画布内绘制(render3d.Painter.creature)，因此轮廓抗锯齿、
内部各层的深度排序稳定。这里只管"长什么样"，行为逻辑仍留在 main.py。
"""

from __future__ import annotations

import math

import pygame

from render3d import (LIGHT_XY, V3, add_light, blob, clamp, dome,
                      flat_polygon, limb, loft, mix, polyline, ribbon_pts, scaled2d,
                      segment, shade, soft_shadow, sphere, translucent_polys)

# ------------------------------------------------------------------ 通用小工具


def ramp(table, s):
    """(位置, 值) 表上的平滑插值(两端夹住)。规格表都用它读。"""
    if s <= table[0][0]:
        return table[0][1]
    if s >= table[-1][0]:
        return table[-1][1]
    for i in range(len(table) - 1):
        s0, v0 = table[i]
        s1, v1 = table[i + 1]
        if s0 <= s <= s1:
            f = (s - s0) / (s1 - s0)
            return v0 + (v1 - v0) * f * f * (3 - 2 * f)
    return table[-1][1]


def _capsule2(p0, p1, r0, r1, n=14):
    """2D 胶囊轮廓(两端半径可不同)：两段半圆 + 外公切线。"""
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    pts = []
    for i in range(n + 1):
        a = ang + math.pi / 2 + math.pi * i / n
        pts.append((p0[0] + math.cos(a) * r0, p0[1] + math.sin(a) * r0))
    for i in range(n + 1):
        a = ang - math.pi / 2 + math.pi * i / n
        pts.append((p1[0] + math.cos(a) * r1, p1[1] + math.sin(a) * r1))
    return pts


def _ellipse2(cx, cy, rx, ry, n=24):
    return [(cx + math.cos(2 * math.pi * i / n) * rx,
             cy + math.sin(2 * math.pi * i / n) * ry) for i in range(n)]


def _quad_pts(p0, p1, p2, n=9):
    """二次贝塞尔采样——蹼缘的凹弧用它才顺。"""
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


class Body:
    """一只生物的局部坐标系(前=+x, 侧=+y)与世界坐标/相机之间的桥。

    scale 把整只生物的局部尺寸统一放大/缩小——果蝇就靠它整体调大小,
    姿态与几何代码一行都不用改。
    """

    def __init__(self, pos, heading, z, scale=1.0, pitch=0.0):
        self.x, self.y, self.z = pos[0], pos[1], z
        self.heading = heading
        self.scale = scale
        self.pitch = pitch          # 抬头/低头(绕侧轴), 正=抬头
        self.c, self.s = math.cos(heading), math.sin(heading)

    def at(self, lx, ly, lz=0.0):
        lx, ly, lz = lx * self.scale, ly * self.scale, lz * self.scale
        if self.pitch:
            pc, ps = math.cos(self.pitch), math.sin(self.pitch)
            lx, lz = lx * pc - lz * ps, lx * ps + lz * pc
        return V3(self.x + lx * self.c - ly * self.s,
                  self.y + lx * self.s + ly * self.c, self.z + lz)

    def up(self, world_dz):
        """把"世界高度差"换算回局部 z(缩放后仍然对得上绝对高度, 比如落到叶面)。"""
        return world_dz / self.scale

    def pts(self, pts2, z):
        return [self.at(px, py, z) for px, py in pts2]

    def ring(self, lx, ly, z, rx, ry, n=18):
        return self.pts(_ellipse2(lx, ly, rx, ry, n), z)


# ================================================================== 青蛙
# 自然侧褶蛙配色：背橄榄绿带深斑、体侧偏黄绿、腹面米白、眼金环黑瞳。
SKIN = {
    "flank": (176, 196, 138),
    "side": (118, 158, 78),
    "mid": (100, 142, 66),
    "back": (86, 128, 58),
    "back_lit": (144, 182, 96),
    "fold": (190, 214, 142),
    "spot": (66, 96, 48),
    "throat": (210, 212, 178),
    "iris": (188, 154, 58),
    "iris_dark": (112, 86, 30),
    "pupil": (34, 26, 18),
    "tympanum": (96, 98, 68),
}

# 俯视半宽表: (体轴坐标 s, 半宽)。s=+38 吻端, s=-38 尾端。
# 对着实拍图量出来的比例: 头在眼处最宽、身体在髋部最宽、吻端短而钝。
FROG_WIDTH = [(-38, 3.4), (-32, 11.6), (-24, 18.4), (-14, 21.4), (-4, 21.8),
              (6, 20.6), (14, 18.8), (20, 18.2), (25, 17.8), (30, 14.6),
              (34, 10.0), (38, 5.2)]

# 背脊高度系数: (体轴坐标, 高度倍数)。吻端明显低于腰腹, 侧视才像蛙而不像圆盘。
FROG_TOP = [(-38, 0.72), (-28, 0.96), (-14, 1.0), (0, 0.97), (10, 0.90),
            (19, 0.94), (26, 0.86), (32, 0.66), (38, 0.44)]

# 体块: 截面自下而上按 (1 - f^p)^q 收拢。p=3.2 保证"背是平的、边是圆的"的蛙形。
FROG_BODY_P, FROG_BODY_Q = 3.2, 0.52
FROG_HEIGHT = 15.5                    # 躯干厚度(世界单位)

# 脚: 后足五趾带蹼(趾长 18), 前足四趾无蹼(趾长 11); 趾扇角与趾根半径
FOOT = {
    "hind": dict(toes=5, length=16.0, r=2.05, fan=math.radians(62), webbed=True),
    "front": dict(toes=4, length=7.2, r=1.45, fan=math.radians(48), webbed=False),
}


def _foot(painter, cam, body, s, y, fwd, kind, z, m, bias=0.0):
    """一只脚：踝 + 放射状脚趾(+ 后足趾间蹼)。

    全部用世界坐标画(不再是屏幕空间贴片), 所以任何机位下透视都正确——
    低角度看是"贴在叶面上的脚", 俯视看是"张开的趾"。
    """
    spec = FOOT[kind]
    n = spec["toes"]
    tips = []
    for k in range(n):
        f = k / (n - 1) - 0.5
        ang = fwd + f * spec["fan"]
        L = spec["length"] * (1.0 - 0.18 * (abs(f) * 2) ** 1.5)
        tips.append((ang, L))
    base = body.at(s, y, z)
    toe_c = SKIN["side"] if kind == "hind" else SKIN["mid"]
    for ang, L in tips:
        tip = body.at(s + math.cos(ang) * L, y + math.sin(ang) * L, z - 0.55)
        limb(painter, cam, base, tip, spec["r"], toe_c, taper=0.62, bias=bias,
             layer=painter.MAIN)
        sphere(painter, cam, tip, spec["r"] * 0.78, add_light(toe_c, 0.06),
               bias=bias + 0.05, layer=painter.MAIN, sheen=0.45)
    if spec["webbed"]:                                   # 趾间蹼: 贴住趾间、外缘内凹的薄膜
        items = []
        for k in range(n - 1):
            a0, L0 = tips[k]
            a1, L1 = tips[k + 1]
            fc = 0.42
            p0 = body.at(s + math.cos(a0) * L0 * fc, y + math.sin(a0) * L0 * fc, z - 0.6)
            p1 = body.at(s + math.cos(a1) * L1 * fc, y + math.sin(a1) * L1 * fc, z - 0.6)
            amid = (a0 + a1) / 2
            Lm = (L0 + L1) * 0.5 * fc
            pc = body.at(s + math.cos(amid) * Lm * 0.66, y + math.sin(amid) * Lm * 0.66,
                         z - 0.72)
            mid0 = body.at(s + math.cos(a0) * L0 * fc * 0.55,
                           y + math.sin(a0) * L0 * fc * 0.55, z - 0.65)
            mid1 = body.at(s + math.cos(a1) * L1 * fc * 0.55,
                           y + math.sin(a1) * L1 * fc * 0.55, z - 0.65)
            items.append(([base, mid0, p0, pc, p1, mid1],
                          mix(SKIN["flank"], SKIN["side"], 0.30)))
        translucent_polys(painter, cam, items, alpha=126, bias=bias - 0.02,
                          layer=painter.MAIN)
    sphere(painter, cam, base, spec["r"] * 1.5, mix(SKIN["mid"], SKIN["back"], 0.3),
           bias=bias, layer=painter.MAIN, sheen=0.3)


def _legs(painter, cam, frog, body, k_air, wk):
    """四肢关节链：地面=折叠收拢, 腾空=后蹬前伸。

    后肢是蛙的招牌：髋(体内) → 膝(体侧最宽处, 略前于髋) → 踝(向后折回体侧)
    → 足(趾朝前外张)。折成 Z 字贴住身体；起跳时整条链向后蹬直、前肢前伸。
    """
    thigh_c, shank_c = SKIN["side"], SKIN["mid"]
    for m in (-1, 1):
        wob = wk * 1.6 * m
        if k_air > 0.02:                                   # ---- 腾空: 后蹬 ----
            hip = (-6, m * 19)
            knee = (-6 - 26 * k_air, m * (19 + 7 * k_air))
            ankle = (-9 - 46 * k_air, m * (19 + 1 * k_air))
            foot_s, foot_y = ankle[0] - 3, ankle[1] - 1
            toe_fwd = math.pi - m * 0.34
        else:                                              # ---- 落地: 折叠 ----
            hip = (-6, m * 19)
            knee = (2 + wob * 0.3, m * (32.0 + wob * 0.35))
            ankle = (-17 + wob * 0.5, m * (30.5 + wob * 0.3))
            foot_s, foot_y = ankle[0] - 3.5, ankle[1] - 1.5
            toe_fwd = m * 0.72
        hw = body.at(hip[0], hip[1], 5.0 if k_air < 0.5 else 4.4)
        kw = body.at(knee[0], knee[1], 6.0 - 0.8 * k_air)
        aw = body.at(ankle[0], ankle[1], 4.6 - 0.4 * k_air)
        limb(painter, cam, hw, kw, 11.6 - 1.2 * k_air, thigh_c, taper=0.74,
             bias=-0.34, layer=painter.MAIN)      # 负 = 后置, 让身体盖住腿根
        limb(painter, cam, kw, aw, 6.4 - 0.6 * k_air, shank_c, taper=0.68,
             bias=-0.32, layer=painter.MAIN)
        _foot(painter, cam, body, foot_s, foot_y, toe_fwd, "hind",
              4.0 - 0.4 * k_air, m, bias=-0.30)
        # ---- 前肢: 短, 肘略外撑, 掌撑在胸前 ----
        if k_air > 0.02:
            sh, el = (12, m * 11), (20 + 14 * k_air, m * (15 + 5 * k_air))
            wr = (25 + 22 * k_air, m * (15 + 4 * k_air))
            hand_fwd = m * 0.20
        else:
            sh, el = (12, m * 11), (18 + wk * 0.4, m * (17.0 + wob * 0.4))
            wr = (22 + wk * 0.6, m * (16.2 + wob * 0.5))
            hand_fwd = m * 0.34
        shw = body.at(sh[0], sh[1], 5.2)
        elw = body.at(el[0], el[1], 4.2 - 0.5 * k_air)
        wrw = body.at(wr[0], wr[1], 3.4 - 0.7 * k_air)
        limb(painter, cam, shw, elw, 5.8, thigh_c, taper=0.76, bias=-0.30,
             layer=painter.MAIN)
        limb(painter, cam, elw, wrw, 4.6, shank_c, taper=0.78, bias=-0.28,
             layer=painter.MAIN)
        _foot(painter, cam, body, wr[0] + 1.0, wr[1], hand_fwd, "front",
              3.2 - 0.6 * k_air, m, bias=-0.34)


def _tympanum(body, s, m, z, r):
    """鼓膜: 长在头侧的一块竖向椭圆(法线朝侧方), 侧视才看得清。"""
    w = ramp(FROG_WIDTH, s)
    pts = []
    for i in range(14):
        a = math.tau * i / 14
        pts.append(body.at(s + math.cos(a) * r, m * w, z + math.sin(a) * r * 0.86))
    return pts


def _surface_z(s, y, top_fn):
    """体表在局部坐标 (s, y) 处的高度。

    体块是由"半宽按 (1-f^P)^Q 收拢"的层叠出来的, 所以知道某点的横向占比 k=|y|/W(s),
    就能反解出它在第几层 f, 也就知道那里的皮面有多高——眼睛必须坐在这个面上,
    否则就会像贴上去的球。
    """
    w = ramp(FROG_WIDTH, s)
    if w <= 0.01:
        return top_fn(s)
    k = clamp(abs(y) / w, 0.0, 1.0)
    f = max(0.0, 1.0 - k ** (1.0 / FROG_BODY_Q)) ** (1.0 / FROG_BODY_P)
    return top_fn(s) * f


def _eye(painter, cam, body, h, m, blink, top_fn):
    """一只蛙眼。

    关键是**只画露在皮面之上的那半个球**: 真蛙的眼球大半埋在眼窝里, 从上看是皮面上
    鼓起的一颗半球 + 一圈暗睑缝。整颗球一起画的话, 没有任何东西能遮住它埋进去的部分,
    看上去就永远像"摆上去的球"。
    """
    sx, sy = 25.5, m * 14.0
    surf = _surface_z(sx, sy, top_fn)              # 该处皮面高度
    r_eye = 5.0
    z_eye = surf - 0.7                             # 半球底面: 就落在皮面上

    def ball(f):
        """半球的一层: 环按"半径均匀"排布。

        若按球面高度均匀排布, 赤道附近会连着叠好几层几乎一样大的环, 暗色堆在边上
        就成了一圈"火山口"。按半径均匀排布, 最外圈只有薄薄一层, 过渡才干净。
        """
        k = max(0.0, 1.0 - f)
        h = math.sqrt(max(0.0, 1.0 - k * k))
        return body.pts(_ellipse2(sx, sy, r_eye * k, r_eye * 0.97 * k, 18),
                        z_eye + r_eye * h)

    if blink:                                      # 眨眼: 皮色眼皮盖住整颗眼
        loft(painter, cam, lambda f: body.pts(
            _ellipse2(sx, sy, r_eye * (1 - 0.45 * f), r_eye * 0.97 * (1 - 0.45 * f), 16),
            z_eye + 6.2 * f), SKIN["mid"], SKIN["fold"], layers=7, bias=1.36,
            edge_k=0.72, light_mix=0.06)
        return
    # 眼窝: 一圈皮色鼓包, 眼睛才像"长在头上"
    socket = body.at(sx - 0.6, sy * 0.92, surf - 2.4)
    blob(painter, cam, socket.x, socket.y, socket.z, 7.6, 7.0, 4.6,
         mix(SKIN["mid"], SKIN["back"], 0.10), heading=h, layers=9, taper=0.44,
         bias=1.14)
    # 睑缝: 眼球根部一圈暗色
    flat_polygon(painter, cam,
                 [body.at(px, py, surf - 0.25)
                  for px, py in _ellipse2(sx, sy, r_eye * 1.08, r_eye * 1.03, 18)],
                 shade(SKIN["mid"], 0.50), bias=1.28)
    # 眼球: 半球(逐层收拢的球面明暗, 底暗顶亮)
    loft(painter, cam, ball, SKIN["iris"], SKIN["iris"], layers=13, bias=1.40,
         edge_k=0.80, light_mix=0.12)
    # 瞳孔: 贴在"前外上"表面的水平椭圆(平面垂直于视线方向)
    gaze = (0.20, m * 0.46, 0.86)
    gl = math.sqrt(sum(c * c for c in gaze))
    gaze = tuple(c / gl for c in gaze)
    axis_u = (0.92, -m * 0.39, 0.0)
    ul = math.hypot(axis_u[0], axis_u[1])
    axis_u = (axis_u[0] / ul, axis_u[1] / ul, 0.0)
    axis_v = (gaze[1] * axis_u[2] - gaze[2] * axis_u[1],
              gaze[2] * axis_u[0] - gaze[0] * axis_u[2],
              gaze[0] * axis_u[1] - gaze[1] * axis_u[0])
    ctr = (sx + gaze[0] * r_eye * 0.90, sy + gaze[1] * r_eye * 0.90,
           z_eye + r_eye * gaze[2] * 0.92)
    pupil = []
    for i in range(14):
        ang = math.tau * i / 14
        ca, sa = math.cos(ang) * 1.85, math.sin(ang) * 0.78
        pupil.append(body.at(ctr[0] + axis_u[0] * ca + axis_v[0] * sa,
                             ctr[1] + axis_u[1] * ca + axis_v[1] * sa,
                             ctr[2] + axis_v[2] * sa))
    flat_polygon(painter, cam, pupil, SKIN["pupil"], bias=1.72)
    # 上眼睑: 一片皮色眉檐压住眼球后上方, 只留前外侧露出来
    lid = body.at(sx - 2.4, sy * 0.80, 0.0)
    blob(painter, cam, lid.x, lid.y, z_eye + r_eye * 0.52, r_eye * 0.92, r_eye * 0.84, 2.4,
         mix(SKIN["side"], SKIN["back"], 0.28), heading=h, layers=6, taper=0.52,
         bias=1.80)
    sphere(painter, cam, body.at(sx + 1.0 + gaze[0] * 2.6, sy * 0.94 + gaze[1] * 2.6,
                                 z_eye + r_eye * 0.92 * gaze[2] + 1.1), 0.7,
           (248, 250, 240), bias=1.96, sheen=0.0)


def draw_frog(painter, cam, frog, t, pads):
    """青蛙：接触阴影 → 四肢 → 一体成型的躯干(逐层收拢) → 头面细节 → 眼睛。"""
    x, y = frog.pos
    h = frog.heading
    z = frog.z
    gz = 0.0
    for p in pads:
        gz = max(gz, p.height_at(frog.pos, t))
    s_scale = 1.0 - z / 170.0
    airborne = frog.state == "air"
    moving = getattr(frog, "_moving", False) and not airborne
    k_air = clamp(z / (frog.JUMP_H * 0.8), 0.0, 1.0) if airborne else 0.0
    wk = math.sin(frog.walk_phase) if moving else 0.0
    breathe = 1.0 + (0.016 * math.sin(t * 2.6) if not airborne and not moving else 0.0)
    bob = abs(math.sin(frog.walk_phase)) * 1.4 if moving else 0.0
    body = Body(frog.pos, h, z + bob + gz * 0.92)     # 站在荷叶上时整体抬起
    outline = ribbon_pts(lambda s: ramp(FROG_WIDTH, s), -38, 38, n=22, cap_n=11)

    soft_shadow(painter, cam, V3(x + 9, y + 7, gz + 0.16), 40 * s_scale, 30 * s_scale,
                0.66, bias=-4, layer=painter.BANK)

    def top_z(s):
        """背脊高度: 吻端向下收, 腰腹最厚。"""
        return 1.2 + FROG_HEIGHT * ramp(FROG_TOP, s) * breathe

    with painter.creature(cam, V3(x, y, z + 7.5), 58, bias=0.2) as (p, c):
        _legs(p, c, frog, body, k_air, wk)

        def section(f, scale=1.0, dz=0.0, ox=0.0, oy=0.0):
            k = max(0.20, (1.0 - f ** FROG_BODY_P) ** FROG_BODY_Q) * scale
            pts = scaled2d(outline, k, ox, oy)
            return [body.at(px, py, top_z(px) * f + dz) for px, py in pts]

        # 躯干: 逐层收拢的一次成型体块(向光侧偏移让侧壁自然变暗)
        loft(p, c, lambda f: section(f, ox=LIGHT_XY[0] * 4.2 * f, oy=LIGHT_XY[1] * 3.4 * f),
             SKIN["side"], SKIN["back_lit"], layers=30, bias=0.40, edge_k=0.58)
        # 湿皮肤的柔光: 两片低透明度的高光带, 顺着背脊走向(不是一块硬亮斑)
        for (ds, dy, rx2, ry2, al) in ((-6, -1.0, 15.0, 7.0, 44), (10, 0.6, 10.0, 5.0, 34)):
            pts = [body.at(px, py, top_z(px) + 0.35)
                   for px, py in _ellipse2(ds, dy, rx2, ry2, 20)]
            translucent_polys(p, c, [(pts, add_light(SKIN["back_lit"], 0.30))],
                              alpha=al, bias=1.08, layer=painter.MAIN)
        # 迷彩斑: 贴在背面, 小而多, 不读成"洞"
        for (sx, sy), sr in getattr(frog, "skin_spots", ()):
            sw = ramp(FROG_WIDTH, sx)
            sy = clamp(sy, -0.52 * sw, 0.52 * sw)
            r = min(sr * 0.40, sw * 0.20)
            sp = [body.at(px, py, top_z(px) * 0.99)
                  for px, py in _ellipse2(sx, sy, r * 1.3, r * 0.9, 14)]
            flat_polygon(p, c, sp, mix(SKIN["back"], SKIN["spot"], 0.46), bias=1.10)
        # 背中浅色纵线 + 两侧背侧褶(实拍里最显眼的浅色脊线)
        mid = [body.at(s, 0.0, top_z(s) * 0.99) for s in range(-26, 12, 4)]
        polyline(p, c, mid, mix(SKIN["fold"], SKIN["back_lit"], 0.4), 2)
        for m in (-1, 1):
            fold = [body.at(s, m * ramp(FROG_WIDTH, s) * 0.78, top_z(s) * 0.92 + 0.6)
                    for s in (20, 14, 8, 2, -4, -10)]
            polyline(p, c, fold, SKIN["fold"], 2)
        # 鼻孔 / 口裂 / 鼓膜
        for m in (-1, 1):
            sphere(p, c, body.at(34.0, m * 3.4, top_z(34.0) * 0.97 + 0.3), 1.0,
                   shade(SKIN["mid"], 0.55), bias=1.2, sheen=0.40)
            line = [body.at(37.4, m * 1.8, top_z(37.4) * 0.46)]
            for i in range(1, 8):
                f = i / 7
                s = 37.4 - 26.0 * f
                line.append(body.at(s, m * ramp(FROG_WIDTH, s) * (0.64 + 0.34 * f),
                                    top_z(s) * (0.40 - 0.07 * f)))
            polyline(p, c, line, shade(SKIN["mid"], 0.46), 2)
            tym = _tympanum(body, 17.5, m, _surface_z(17.5, m * ramp(FROG_WIDTH, 17.5) * 0.9,
                                                      top_z) * 0.94, 2.9)
            flat_polygon(p, c, tym, mix(SKIN["mid"], SKIN["tympanum"], 0.22), bias=0.86)
            polyline(p, c, tym + [tym[0]], shade(SKIN["mid"], 0.72), 1)
        # 眼睛: 眼窝(皮色鼓包) → 睑缘暗缝 → 半球眼球 → 水平瞳孔 → 眉檐 → 高光
        for m in (-1, 1):
            _eye(p, c, body, h, m, frog.blink > 0, top_z)


# ================================================================== 果蝇
# 黑腹果蝇天然配色: 砖红复眼、琥珀色胸背带黑刚毛、腹部深色横带、玻璃质透明翅。
FLY_SKIN = {
    "thorax": (182, 144, 96),
    "thorax_dark": (126, 94, 58),
    "scutellum": (166, 128, 84),
    "abdomen": (150, 112, 72),
    "band": (78, 54, 38),
    "eye": (126, 42, 36),
    "eye_lit": (198, 104, 82),
    "leg": (152, 118, 78),
    "leg_dark": (110, 82, 54),
    "bristle": (56, 40, 28),
    "wing": (228, 236, 238),
    "haltere": (234, 210, 132),
}

FLY_SCALE = 1.85               # 果蝇整体大小(在池塘里要看清楚, 原尺寸只有十几像素)

# 六足的关节链: (髋, 膝, 跗) —— 局部坐标, 前足最长、后足最短。
FLY_LEGS = (
    ((3.9, 1.15), (5.6, 2.35), (5.6, 2.15)),      # 前足: 朝前撑
    ((1.2, 1.35), (2.5, 3.05), (1.3, 3.35)),      # 中足: 略外撑
    ((-1.4, 1.25), (-2.9, 2.85), (-4.6, 2.70)),   # 后足: 朝后撑
)
FLY_WING = dict(root=(1.05, 0.60, 3.05), length=13.5, width=3.3)
FLY_FOLD_TIP = (-12.0, 2.05, 3.40)      # 收翅时翅尖落在腹末之外一点点


def _fly_ovoid(body, x, hl, hw, z0, height, f, shrink=0.0, droop=0.0, p=2.4, q=0.5):
    """果蝇胸/腹的某一层截面: 沿体轴收拢的椭圆(droop = 越靠后越往下垂)。"""
    k = (1.0 - f ** p) ** q
    return [body.at(px, py, z0 + height * f - droop * (x - shrink * f))
            for px, py in _ellipse2(x - shrink * f, 0.0, hl * k, hw * k, 20)]


def _wing_quad(body, kind, spread, beat, m, fold):
    """一片翅的四个基准点(根/尖/侧向), 按展开度在"收翅"与"振翅"之间插值。

    收翅: 贴在背上、翅尖超过腹末并微微交叠; 振翅: 向两侧张开, 翅尖随拍打上下划弧。
    """
    rx, ry, rz = FLY_WING["root"]
    L = FLY_WING["length"]
    root = body.at(rx, m * ry, rz)
    fx, fy, fz = FLY_FOLD_TIP
    fold_tip = body.at(fx, m * fy, fz)
    # 振翅: 翅尖 = 翅根 + 单位方向 × 翅长。方位角以前后扫为主(约 70° 侧向 ± 扫掠),
    # 高度按正弦上下划——真实果蝇的拍打是大幅上下, 不是水平"剪刀"。
    ang = 1.22 + 0.34 * beat
    fly_tip = body.at(rx + math.cos(ang) * L, m * (ry + math.sin(ang) * L),
                      rz + 0.75 + 2.5 * beat)   # 上下弧别打到自己的身体
    tip = V3(fold_tip.x + (fly_tip.x - fold_tip.x) * spread,
             fold_tip.y + (fly_tip.y - fold_tip.y) * spread,
             fold_tip.z + (fly_tip.z - fold_tip.z) * spread)
    return root, tip


def _wing_pts(body, root, tip, width, m, kind):
    """翅形轮廓: 前缘较直、后缘外弧、翅尖收圆(果蝇翅比"桨"长得多)。"""
    dx, dy, dz = tip.x - root.x, tip.y - root.y, tip.z - root.z
    hl = math.hypot(dx, dy) or 1.0
    # 水平面内的横向单位向量(翅宽方向)
    wx, wy = -dy / hl, dx / hl
    prof = ((0.00, 0.55), (0.24, 1.00), (0.56, 0.98), (0.84, 0.72), (1.00, 0.10),
            (0.88, -0.46), (0.58, -0.90), (0.26, -0.78))
    pts = []
    for u, s in prof:
        w = width * s * 0.5
        pts.append(V3(root.x + dx * u + wx * w, root.y + dy * u + wy * w,
                      root.z + dz * u))
    return pts


def _fly_wings(painter, cam, fly, body, spread, flying):
    """翅膀: 玻璃质半透明 + 翅脉。展开度 spread 让起降时的收/展是连续的。"""
    wing_c = FLY_SKIN["wing"]
    beat = math.sin(fly.wing_phase)
    for m in (-1, 1):
        ph = fly.wing_phase + (0.0 if m > 0 else math.pi)
        b = beat if m > 0 else math.sin(ph)
        root, tip = _wing_quad(body, None, spread, b, m, None)
        pts = _wing_pts(body, root, tip, FLY_WING["width"] * (0.72 + 0.28 * spread),
                        m, None)
        alpha = int(96 + 46 * spread)
        translucent_polys(painter, cam, [(pts, wing_c)], alpha=alpha, bias=1.52)
        # 翅脉: 从翅根放射到翅内几个点 + 一条前缘高光
        for (u, s) in ((0.92, 0.08), (0.66, 0.00), (0.40, -0.18)):
            dx, dy, dz = tip.x - root.x, tip.y - root.y, tip.z - root.z
            hl = math.hypot(dx, dy) or 1.0
            wx, wy = -dy / hl, dx / hl
            w = FLY_WING["width"] * s * 0.5
            limb(painter, cam, root,
                 V3(root.x + dx * u + wx * w, root.y + dy * u + wy * w, root.z + dz * u),
                 0.15, (250, 252, 250), taper=0.5, bias=1.44)
        limb(painter, cam, root,
             V3(root.x + (tip.x - root.x) * 0.92, root.y + (tip.y - root.y) * 0.92,
                root.z + (tip.z - root.z) * 0.92), 0.18, (252, 252, 248), taper=0.5,
             bias=1.42)


def draw_fly(painter, cam, fly, t, pads):
    """果蝇：影子 → 六足 → 胸腹(逐层收拢) → 刚毛/触角 → 复眼 → 翅膀。

    翅膀的展开度随高度连续变化(起飞展开、落地收拢), 腿的姿态也在"飞/走/擦眼/进食"
    之间插值, 因此状态切换不会突然跳变。
    """
    x, y = fly.pos
    h = fly.heading
    z = fly.z
    gz = 0.0
    for p in pads:
        gz = max(gz, p.height_at(fly.pos, t))
    # 飞行程度: 0=完全落地, 1=完全在空中(翅膀展开、腿收起)
    land_z = 3.0 * FLY_SCALE                        # 落在叶面上的身体高度
    fly_amt = clamp((z - land_z * 1.1) / (4.8 * FLY_SCALE), 0.0, 1.0)
    flying = fly_amt > 0.5
    eating = fly.eating_now and fly.groom_t <= 0
    # 领域对峙(Game._update_contests 每帧配对): 攻击方先逼近(逻辑层驱动),
    # 贴近后以 ~1.1 次/秒的节奏大幅冲撞(lunge)+展翅威胁; 被压方身体压低、
    # 颤抖、被逼退——真实果蝇食源攻击里一眼可辨的两个角色。
    # 冲撞幅度随与对手的距离缩放: 还没逼近时挥空拳只会显得抽风
    lunge = crouch = tremble = 0.0
    foe = getattr(fly, "contest_foe", None)
    if getattr(fly, "contest_t", 0.0) > 0.0 and foe is not None:
        if getattr(fly, "contest_role", "") == "attacker":
            # 贴脸(≤22px)全力, 32px 外不出拳(还在逼近路上); 与逻辑层的
            # 20px 站定阈值对齐——站定的位置必须在全幅度圈内
            prox = clamp((32.0 - foe.pos.distance_to(fly.pos)) / 10.0, 0.0, 1.0)
            lunge = prox * max(0.0, math.sin(fly.contest_t * 7.0)) ** 1.5
        else:
            crouch = clamp(fly.contest_t / 1.5, 0.0, 1.0)
            tremble = crouch * math.sin(t * 30.0)
    contesting = lunge > 0.0 or crouch > 0.0
    # 离地就不再摆梳洗姿势: 行为层起飞/逃跑会清 groom_t, 这里是姿态层的兜底,
    # 否则"飞到一半还在擦眼睛"。对峙时也不再擦眼——前足要用在冲撞上。
    grooming = fly.groom_t > 0 and fly_amt < 0.35 and not contesting
    sh = clamp(1.0 - z / 55.0, 0.25, 1.0)
    # 梳洗进度(带 0.15s 淡入淡出): 动作幅度在默认视角下必须够大才看得见
    groom_amt = 0.0
    if grooming:
        total = getattr(fly, "GROOM_TIME", 1.6)
        groom_amt = clamp(min((total - fly.groom_t) / 0.18, fly.groom_t / 0.18), 0.0, 1.0)
    body_z = z + gz * 0.92 * (1.0 - fly_amt) - 1.7 * crouch * (1.0 - fly_amt)
    # 梳洗时抬头 + 轻微点头; 攻击冲撞时高抬前身向对手猛探; 被压方低头伏低
    bx = fly.pos.x + fly.contest_dir.x * (5.0 * lunge) \
        + (-fly.contest_dir.y) * (0.7 * tremble)
    by = fly.pos.y + fly.contest_dir.y * (5.0 * lunge) \
        + fly.contest_dir.x * (0.7 * tremble)
    body = Body((bx, by), h, body_z, scale=FLY_SCALE,
                pitch=-0.14 * groom_amt + 0.07 * groom_amt * math.sin(t * 13.0)
                + 0.50 * lunge - 0.08 * crouch)
    # 到叶面的世界高度差(负=在下方); 口器最多下探 2.6, 六足可以够到叶面
    surf_dz = gz + 0.35 - body_z
    prob_z = body.up(clamp(surf_dz, -2.6, 0.0))
    foot_z = body.up(clamp(surf_dz, -6.0, 0.6))
    soft_shadow(painter, cam, V3(x + 4 * sh, y + 3 * sh, gz + 0.16),
                9.4 * FLY_SCALE * sh, 5.6 * FLY_SCALE * sh, 0.58, bias=-4)

    with painter.creature(cam, V3(x, y, z + 3.0), 15.6 * FLY_SCALE, bias=0.1) as (p, c):
        # ---- 六足: 髋→膝→跗; 空中向后收拢, 落地三角步态(摆动相抬脚) ----
        for i, (hip, knee, foot) in enumerate(FLY_LEGS):
            for m in (1, -1):
                if lunge > 0.05 and i == 0:
                    # 冲撞前足: 双前足高高抬过头顶向对手方向猛探(真实 lunge 的
                    # "出拳"), 高 bias 压过复眼, 和擦眼姿势同一层待遇
                    bias = 1.30
                    hp = body.at(hip[0], m * hip[1], 1.2)
                    kn = body.at(knee[0] + 0.9, m * knee[1] * 0.9, 2.0 + 2.0 * lunge)
                    ft = body.at(foot[0] + 1.2 + 4.2 * lunge, m * foot[1] * 0.7,
                                 2.6 + 4.4 * lunge)
                elif grooming and i == 0:
                    # 前足举到复眼"上方"画圈擦洗。
                    # 关键: 脚要抬得比眼顶(z≈4.4)更高、并且用更高的 bias 画,
                    # 否则会被头和复眼整个盖住——之前就是这样, 动作在做却看不见。
                    bias = 1.36          # 正 = 前置: 前足要压在复眼之上
                    ph = t * 6.5 + (0 if m > 0 else math.pi)
                    # 髋 → 膝(向外、向下撑出去) → 跗(贴在复眼表面画圈)。
                    # 关键是"像人抬手挠头": 肘向外下方撑、手落在眼面上。
                    # 之前把腿几乎举成一条直线伸到脸前面, 看起来像触角而不是在擦眼睛。
                    hp = body.at(hip[0], m * hip[1], 1.9 + 0.4 * groom_amt)
                    kn = body.at(knee[0] * 0.60,
                                 m * (knee[1] * 1.05 + 0.55 * groom_amt),
                                 3.0 + 0.45 * groom_amt)
                    ft = body.at(5.5 + math.cos(ph) * (1.25 + 0.35 * groom_amt),
                                 m * (1.50 + math.sin(ph) * (0.70 + 0.30 * groom_amt)),
                                 4.70 + 0.55 * math.sin(ph) + 0.45 * groom_amt)
                elif grooming:                              # 中/后足: 向外后方岔开撑住
                    # 抬头梳洗时胸腹会压住普通站姿的腿——稍微岔开,
                    # 让六只脚都露在腹部轮廓外面
                    bias = -0.16
                    hp = body.at(hip[0], m * hip[1] * 1.08, 1.0)
                    kn = body.at(knee[0] * 0.9, m * knee[1] * 1.16, 1.5)
                    ft = body.at(foot[0] - 0.9, m * foot[1] * 1.18, 0.3)
                elif eating and i == 0:                     # 前足搭在食饵上搓动
                    bias = -0.16
                    rub = math.sin(t * 16 + (0 if m > 0 else math.pi)) * 1.1
                    hp = body.at(hip[0], m * hip[1], 1.0)
                    kn = body.at(knee[0], m * knee[1], foot_z * 0.55 + 0.9)
                    ft = body.at(foot[0] * 0.55 + 1.0, m * (foot[1] * 0.45 + rub),
                                 foot_z + 0.45)
                elif eating:                                # 中/后足撑在叶面上
                    bias = -0.16
                    hp = body.at(hip[0], m * hip[1], 1.0)
                    kn = body.at(knee[0], m * knee[1], foot_z * 0.5 + 0.8)
                    ft = body.at(foot[0], m * foot[1], foot_z)
                elif fly_amt > 0.02:                        # 空中: 腿贴身后收
                    bias = -0.16
                    jit = math.sin(t * 30 + i * 2.1) * 0.5
                    hp = body.at(hip[0] * 0.9, m * hip[1] * 0.85, 0.9)
                    kn = body.at(knee[0] * 0.5 - 1.6 * fly_amt,
                                 m * knee[1] * 0.62, -0.4)
                    ft = body.at(foot[0] * 0.3 - 3.4 * fly_amt,
                                 m * foot[1] * 0.45, -2.2 + jit)
                else:                                       # 三角步态: 摆动相抬脚
                    bias = -0.16
                    g = (i + (0 if m > 0 else 1)) % 2
                    phs = fly.leg_phase * 6 + g * math.pi
                    stride = math.sin(phs) * 1.7
                    lift = max(0.0, math.cos(phs)) * 1.5    # 抬起再落下
                    hp = body.at(hip[0], m * hip[1], 1.0)
                    kn = body.at(knee[0], m * knee[1], 1.6 + lift * 0.5)
                    ft = body.at(foot[0] + stride, m * foot[1], 0.35 + lift)
                if grooming and i == 0:
                    tc, sc = mix(FLY_SKIN["leg"], (218, 194, 144), 0.40), \
                        mix(FLY_SKIN["leg_dark"], (206, 178, 128), 0.45)
                else:
                    tc, sc = FLY_SKIN["leg"], FLY_SKIN["leg_dark"]
                limb(p, c, hp, kn, 0.78, tc, taper=0.74, bias=bias)
                limb(p, c, kn, ft, 0.50, sc, taper=0.44, bias=bias - 0.02, cap=True)
        # ---- 腹部: 向后收细、略下垂, 背面四道深色横带 ----
        loft(p, c, lambda f: _fly_ovoid(body, -4.2, 4.7, 1.72, 0.25, 2.5, f,
                                        shrink=1.0, droop=0.034, p=2.0),
             FLY_SKIN["abdomen"], add_light(FLY_SKIN["abdomen"], 0.12), layers=7,
             bias=0.42, edge_k=0.62)
        for bs in (-1.8, -3.7, -5.6, -7.3):                 # 横带: 贴在后缘的窄条
            w = max(0.40, 1.72 * (1.16 + bs / 7.6))
            ring = [body.at(px, py, 0.25 + 2.6 * 0.60 - 0.030 * (bs + 4.2))
                    for px, py in _ellipse2(bs, 0.0, 0.78, w * 0.96, 12)]
            flat_polygon(p, c, ring, mix(FLY_SKIN["abdomen"], FLY_SKIN["band"], 0.82),
                         bias=0.74)
        # ---- 胸部: 最宽最厚的一段(飞行肌), 琥珀色带黑刚毛 ----
        loft(p, c, lambda f: _fly_ovoid(body, 1.5, 2.9, 2.45, 0.1, 3.5, f),
             FLY_SKIN["thorax_dark"], add_light(FLY_SKIN["thorax"], 0.10), layers=8,
             bias=0.52, edge_k=0.60)
        sc = body.at(-1.0, 0, 2.6)                          # 小盾片
        sphere(p, c, sc, 1.15, FLY_SKIN["scutellum"], bias=0.72, sheen=0.3)
        for k in range(5):                                  # 背中刚毛(向后倾)
            b0 = body.at(0.0 + 0.95 * k, ((k % 2) - 0.5) * 1.0, 3.35)
            b1 = body.at(-0.7 + 0.95 * k, ((k % 2) - 0.5) * 1.7, 5.0)
            segment(p, c, b0, b1, FLY_SKIN["bristle"], 1, bias=-0.88)
        for m in (-1, 1):                                   # 平衡棒
            sphere(p, c, body.at(-1.5, m * 1.6, 1.3), 0.8, FLY_SKIN["haltere"],
                   bias=0.80, sheen=0.4)
        # ---- 头: 复眼(横向压扁的椭球, 几乎占满头部) + 额板 + 单眼 ----
        head = body.at(5.5, 0, 1.5)
        sphere(p, c, head, 1.55, FLY_SKIN["thorax_dark"], bias=1.0, sheen=0.3)
        for m in (-1, 1):
            ec = body.at(5.4, m * 1.18, 1.80)
            blob(p, c, ec.x, ec.y, ec.z - 1.1, 2.45, 2.30, 3.7,
                 FLY_SKIN["eye"], heading=h + m * 0.20, layers=10, taper=0.40, bias=1.12)
            sphere(p, c, body.at(6.2, m * 0.95, 3.35), 0.55, FLY_SKIN["eye_lit"],
                   bias=1.30, sheen=0.15)
        frons = body.at(6.6, 0, 2.3)                        # 额板(两眼之间的小三角)
        sphere(p, c, frons, 0.85, FLY_SKIN["thorax"], bias=1.22, sheen=0.3)
        for (ox, oy) in ((-0.5, 0.0), (0.5, 0.0), (0.0, 0.5)):   # 三个单眼
            sphere(p, c, body.at(5.0 + ox, oy * 1.4, 3.5), 0.30, (72, 54, 40),
                   bias=1.32, sheen=0.5)
        for m in (-1, 1):                                   # 触角
            limb(p, c, body.at(6.9, m * 0.55, 2.5), body.at(8.1, m * 1.0, 3.0),
                 0.28, FLY_SKIN["leg_dark"], taper=0.55, bias=0.9, cap=True)
        pb = body.at(6.7, 0, 1.1)                           # 口器
        sphere(p, c, pb, 0.8, FLY_SKIN["leg_dark"], bias=1.2, sheen=0.4)
        if eating:
            limb(p, c, body.at(7.3, 0, 1.1), body.at(10.4, 0, prob_z), 0.5,
                 FLY_SKIN["leg"], taper=0.5, bias=0.2)
            sphere(p, c, body.at(10.4, 0, prob_z), 0.5, FLY_SKIN["leg_dark"],
                   bias=0.25, sheen=0.4)
        # ---- 翅膀 ----
        # 梳洗时翅膀轻微抬起并颤动: 翅是全身最大的一块, 剪影变化远看也认得出
        quiver = 0.20 * groom_amt * (0.55 + 0.45 * math.sin(t * 17))
        _fly_wings(p, c, fly, body,
                   spread=clamp(fly_amt * 1.25 + quiver + 0.52 * lunge, 0.0, 1.0),
                   flying=flying)   # 冲撞时翅膀同步大幅半展(展翅威胁)
