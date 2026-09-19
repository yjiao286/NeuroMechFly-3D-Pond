"""极简 3D 渲染层：透视投影 + 画家算法（按深度从远到近绘制），基于 pygame 2D 图元。

不依赖 OpenGL，保证在任何能跑 pygame 的机器上工作，且无头渲染可用于自动化测试。
"""

from __future__ import annotations

import math

import pygame

W, H = 1280, 800
UP = pygame.math.Vector3(0, 0, 1)
V3 = pygame.math.Vector3


def clamp(x, a, b):
    return max(a, min(b, x))


def shade(color, k):
    return (clamp(int(color[0] * k), 0, 255),
            clamp(int(color[1] * k), 0, 255),
            clamp(int(color[2] * k), 0, 255))


def mix(a, b, f):
    """颜色线性插值: f=0 → a, f=1 → b。"""
    f = clamp(f, 0.0, 1.0)
    return (clamp(int(a[0] + (b[0] - a[0]) * f), 0, 255),
            clamp(int(a[1] + (b[1] - a[1]) * f), 0, 255),
            clamp(int(a[2] + (b[2] - a[2]) * f), 0, 255))


# 世界空间的主光方向(俯视投影, 西北方高角度的太阳), 所有明暗都按它保持一致
LIGHT_XY = (-0.58, -0.42)
WHITE = (255, 255, 255)

# 风格参数：整体偏"哑光卡通"而不是塑料球——边缘只略压暗, 高光小而淡, 描边很轻。
EDGE_K = 0.80            # 暗面 = 本色 × EDGE_K
LIT_K = 1.07             # 亮面 = 本色 × LIT_K
SPEC_MIX = 0.30          # 镜面高光与白色的混合比(越大越亮)
SPEC_SIZE = 0.17         # 镜面高光半径 / 球半径
OUTLINE_K = 0.70         # 描边颜色


def add_light(color, k=0.25):
    return mix(color, WHITE, k)


def _screen_light(cam):
    """把世界光方向投影成屏幕方向, 让高光永远朝着光源那一侧。"""
    p0 = cam.project(V3(0, 0, 0))
    p1 = cam.project(V3(LIGHT_XY[0] * 120, LIGHT_XY[1] * 120, 0))
    if p0 is None or p1 is None:
        return (-0.55, -0.83)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    n = math.hypot(dx, dy) or 1.0
    return (dx / n, dy / n)


_BLOBS = {}
_SHADOW_SCALED = {}


def _shadow_blob(size=80, alpha=132):
    """预生成一张径向衰减的软阴影贴图(只做一次, 之后复用/缩放)。"""
    key = (size, alpha)
    surf = _BLOBS.get(key)
    if surf is None:
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        c = size / 2
        for i in range(size // 2, 0, -1):
            f = i / (size / 2)
            a = int(alpha * (1.0 - f) ** 1.15)
            pygame.draw.circle(surf, (6, 22, 18, a), (int(c), int(c)), i)
        _BLOBS[key] = surf
    return surf


def soft_shadow(painter, cam, pos, rx, ry=None, strength=1.0, bias=-6.0, layer=4):
    """地面柔光阴影: 一张径向渐变贴图按投影尺寸缩放贴到地面上(比硬多边形柔很多)。"""
    ry = rx if ry is None else ry
    p = cam.project(pos)
    if p is None:
        return
    sx, sy, depth = p
    w = cam.screen_radius(rx, depth)
    h = cam.screen_radius(ry, depth)
    if w < 1.0 or h < 1.0:
        return
    blob = _shadow_blob(80, int(clamp(96 * strength, 14, 130)))
    size = (max(2, int(w * 1.8)), max(2, int(h * 1.8)))
    key = (int(strength * 1000), size[0] // 4, size[1] // 4)
    img = _SHADOW_SCALED.get(key)
    if img is None:
        img = pygame.transform.smoothscale(blob, size)
        if len(_SHADOW_SCALED) > 400:            # 机位变化后旧尺寸会堆积, 定期清一次
            _SHADOW_SCALED.clear()
        _SHADOW_SCALED[key] = img

    def draw(s, sx=sx, sy=sy, size=size, img=img):
        s.blit(img, (int(sx - size[0] / 2), int(sy - size[1] / 2)))

    painter.add(depth - bias, draw, layer)


def ellipse_pts(cx, cy, z, rx, ry, heading=0.0, n=14):
    """世界空间水平椭圆的多边形顶点。"""
    c, s = math.cos(heading), math.sin(heading)
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        ex, ey = math.cos(a) * rx, math.sin(a) * ry
        pts.append(V3(cx + ex * c - ey * s, cy + ex * s + ey * c, z))
    return pts


class Camera3D:
    def __init__(self, pos, target, focal=1400.0, cx=W / 2, cy=H * 0.44):
        self.pos = V3(pos)
        self.target = V3(target)
        self.focal = focal
        self.cx, self.cy = cx, cy
        self.w, self.h = W, H
        self._calc()

    def _calc(self):
        self.fwd = (self.target - self.pos).normalize()
        self.right = self.fwd.cross(UP)
        if self.right.length_squared() < 1e-6:
            self.right = V3(1, 0, 0)
        self.right.normalize_ip()
        self.up = self.right.cross(self.fwd).normalize()

    def set_view(self, pos, target):
        self.pos.update(pos)
        self.target.update(target)
        self._calc()

    NEAR = 14.0                          # 近平面距离

    def view(self, p):
        """世界坐标 → 视空间 (右, 上, 前)。"""
        d = p - self.pos
        return (d.dot(self.right), d.dot(self.up), d.dot(self.fwd))

    def project_v(self, v):
        vx, vy, vz = v
        return (self.cx + self.focal * vx / vz,
                self.cy - self.focal * vy / vz,
                vz)

    def project(self, p):
        d = p - self.pos
        depth = d.dot(self.fwd)
        if depth < self.NEAR:
            return None
        return (self.cx + self.focal * d.dot(self.right) / depth,
                self.cy - self.focal * d.dot(self.up) / depth,
                depth)

    def screen_radius(self, r, depth):
        return self.focal * r / depth


class SubCamera:
    """把主相机的成像平面平移/放大后的"子相机"。

    生物在自己的离屏画布里绘制时用它：屏幕坐标 = (主相机屏幕坐标 - 画布原点) × ss。
    因此焦距与主点按同样规则变换, 其余几何量(位置/朝向)与主相机完全一致——
    渲染图元拿到子相机就照常工作, 不需要任何特判。
    """

    def __init__(self, cam, ox, oy, ss):
        self.pos, self.target = cam.pos, cam.target
        self.fwd, self.right, self.up = cam.fwd, cam.right, cam.up
        self.NEAR = cam.NEAR
        self.ss = ss
        self.focal = cam.focal * ss
        self.cx = (cam.cx - ox) * ss
        self.cy = (cam.cy - oy) * ss
        self.w, self.h = cam.w, cam.h

    def project(self, p):
        d = p - self.pos
        depth = d.dot(self.fwd)
        if depth < self.NEAR:
            return None
        return (self.cx + self.focal * d.dot(self.right) / depth,
                self.cy - self.focal * d.dot(self.up) / depth,
                depth)

    def view(self, p):
        """世界坐标 → 视空间(子相机只是平移放大像平面, 视空间与主相机一致)。"""
        d = p - self.pos
        return (d.dot(self.right), d.dot(self.up), d.dot(self.fwd))

    def project_v(self, v):
        vx, vy, vz = v
        return (self.cx + self.focal * vx / vz, self.cy - self.focal * vy / vz, vz)

    def screen_radius(self, r, depth):
        return self.focal * r / depth


class Painter:
    """多层画家算法: 按 layer 从小到大分批绘制, 层内按深度远→近。
    层号约定: 0=水面 1=水波/焦散/涟漪 2=荷叶 3=岸上立体物 4=生物与食饵。
    大片地面永远先于站在它上面的生物; 立体物之间仍按深度互相遮挡。"""

    WATER, FX, PAD, BANK, MAIN = 0, 1, 2, 3, 4

    def __init__(self):
        self.layers = {}

    def add(self, depth, fn, layer=MAIN):
        self.layers.setdefault(layer, []).append((depth, fn))

    def flush(self, surf):
        for k in sorted(self.layers):
            items = self.layers[k]
            for _, fn in sorted(items, key=lambda it: -it[0]):
                fn(surf)
            self.layers[k].clear()

    def creature(self, cam, center, radius, layer=MAIN, bias=0.0, ss=3,
                 pad=1.22, opacity=255):
        """把一只生物画进独立的超采样画布, 再整体抗锯齿贴回场景。

        好处有三：轮廓不再是硬多边形锯齿；同一生物内部由子 painter 稳定排序,
        不会与自身抖动；对外只占一个绘制项, 场景排序更省。
        用法：
            with painter.creature(cam, pos, 40) as (sub, scam):
                blob(sub, scam, ...)
        """
        return _CreatureScope(self, cam, center, radius, layer, bias, ss, pad, opacity)


class _CreatureScope:
    def __init__(self, painter, cam, center, radius, layer, bias, ss, pad, opacity):
        self.painter, self.cam, self.center = painter, cam, center
        self.radius, self.layer, self.bias = radius, layer, bias
        self.ss, self.pad, self.opacity = ss, pad, opacity
        self.active = False

    def __enter__(self):
        cam = self.cam
        p = cam.project(self.center)
        if p is None:                                   # 整体在近平面之后
            return _Discard(), _NullCamera()
        sx, sy, depth = p
        r = cam.screen_radius(self.radius * self.pad, depth)
        if r < 1.0:
            return _Discard(), _NullCamera()
        x0 = int(sx - r)
        y0 = int(sy - r)
        x1 = int(sx + r) + 1
        y1 = int(sy + r) + 1
        # 裁到屏幕内(生物在画面边缘时只画露出的一半, 省填充)
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(cam.w, x1), min(cam.h, y1)
        if cx1 - cx0 < 2 or cy1 - cy0 < 2:
            return _Discard(), _NullCamera()
        self.box = (cx0, cy0, cx1, cy1)
        self.ss_surf = pygame.Surface(((cx1 - cx0) * self.ss, (cy1 - cy0) * self.ss),
                                      pygame.SRCALPHA)
        self.sub = Painter()
        self.scam = SubCamera(cam, cx0, cy0, self.ss)
        self.depth = depth
        self.active = True
        return self.sub, self.scam

    def __exit__(self, *exc):
        if not self.active:
            return False
        self.sub.flush(self.ss_surf)
        x0, y0, x1, y1 = self.box
        img = pygame.transform.smoothscale(self.ss_surf, (x1 - x0, y1 - y0))
        if self.opacity < 255:
            img.set_alpha(self.opacity)
        depth = self.depth - self.bias
        self.painter.add(depth, lambda s, img=img, x0=x0, y0=y0: s.blit(img, (x0, y0)),
                         self.layer)
        return False


class _Discard:
    """生物完全在画面外时的空 painter: 绘制调用被安静丢弃。"""

    WATER, FX, PAD, BANK, MAIN = 0, 1, 2, 3, 4

    def add(self, *a, **k):
        pass


class _NullCamera:
    """空相机: 所有图元都会因"在近平面之外"而直接返回, 不产生任何绘制。"""

    focal, cx, cy = 1.0, 0.0, 0.0
    NEAR, w, h = 1.0, 0, 0

    def project(self, p):
        return None

    def view(self, p):
        return (0.0, 0.0, -1.0)

    def project_v(self, v):
        return (0.0, 0.0, 0.0)

    def screen_radius(self, r, depth):
        return 0.0
def clip_near(pts, near):
    """Sutherland–Hodgman: 对近平面(vz >= near)裁剪视空间点列 (vx, vy, vz)。"""
    out = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        a_in = a[2] >= near
        b_in = b[2] >= near
        if a_in:
            out.append(a)
        if a_in != b_in:
            t = (near - a[2]) / (b[2] - a[2])
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t, near))
    return out


def flat_polygon(painter, cam, pts3, color, outline=None, owidth=2, bias=0.0, layer=4,
                 aa=False):
    """水平多边形（世界坐标点列），近平面裁剪后按平均深度排序。

    aa=True 时额外用抗锯齿线沿同一条边描一圈同色——填色本身是硬边的,
    这一圈会把边界像素混合掉, 荷叶/叶缘这类大块轮廓就不再是锯齿状。
    """
    vs = [cam.view(p) for p in pts3]
    vs = clip_near(vs, cam.NEAR)
    if len(vs) < 3:
        return None
    pts2d = [(cam.cx + cam.focal * vx / vz, cam.cy - cam.focal * vy / vz)
             for vx, vy, vz in vs]
    depth = sum(vz for _, _, vz in vs) / len(vs)

    def draw(s, pts=pts2d, c=color, o=outline, w=owidth, aa=aa):
        pygame.draw.polygon(s, c, pts)
        if aa and len(pts) > 2:
            pygame.draw.aalines(s, c, True, pts)
        if o:
            pygame.draw.polygon(s, o, pts, w)

    painter.add(depth - bias, draw, layer)
    return depth - bias


def sphere(painter, cam, pos, r, color, bias=0.0, layer=4, sheen=1.0):
    """球体：边缘压暗 + 逐层向光面提亮 + 镜面高光 + 细描边。
    bias>0 = 视觉上前置。sheen<1 可把高光压得更哑(灌木/岩石这类粗糙表面)。"""
    p = cam.project(pos)
    if p is None:
        return
    sx, sy, depth = p
    rs = cam.screen_radius(r, depth)
    if rs < 0.55:
        return
    rad = min(max(1, int(rs)), 4000)
    dark = shade(color, EDGE_K)
    lit = add_light(shade(color, LIT_K), 0.05)
    steps = 2 if rad <= 7 else 4                # 小球少画几层, 省时间也够看
    rings = [(max(1, int(rad * (1 - 0.46 * (i + 1) / steps))),
              (-0.24 * rad * (i + 1) / steps, -0.29 * rad * (i + 1) / steps),
              mix(dark, lit, ((i + 1) / steps) ** 0.85)) for i in range(steps)]
    hx, hy = -0.30 * rad, -0.34 * rad
    spec = max(1, int(rad * SPEC_SIZE * sheen))

    def draw(s, sx=sx, sy=sy, rad=rad, dark=dark, rings=rings, hx=hx, hy=hy,
             spec=spec, color=color, sheen=sheen):
        pygame.draw.circle(s, dark, (int(sx), int(sy)), rad)
        for rr, (ox, oy), col in rings:
            pygame.draw.circle(s, col, (int(sx + ox), int(sy + oy)), rr)
        if rad >= 8 and sheen > 0:
            pygame.draw.circle(s, mix(color, WHITE, SPEC_MIX * sheen),
                               (int(sx + hx), int(sy + hy)), spec)
        if rad >= 9 and sheen > 0.5:
            pygame.draw.circle(s, shade(color, OUTLINE_K), (int(sx), int(sy)), rad, 1)

    painter.add(depth - r * 0.01 - bias, draw, layer)


def ribbon_pts(width_fn, s0, s1, n=22, cap_n=9):
    """把"沿体轴的半宽函数"变成一条圆头的闭合有机轮廓(2D 局部坐标, +x 为前)。

    蛙、虫的身体都不是椭圆——前窄后宽、肩部略收、吻端圆钝。这里只描述
    "每个 s 处有多宽", 两端自动补半圆帽, 得到的就是一条平滑闭合曲线。
    """
    pts = []
    for i in range(n + 1):                       # 右侧: 后 → 前
        s = s0 + (s1 - s0) * i / n
        pts.append((s, max(0.0, width_fn(s))))
    r_cap = max(0.0, width_fn(s1))
    if r_cap > 0.01:                             # 吻端圆帽
        for i in range(1, cap_n):
            a = -math.pi / 2 + math.pi * i / cap_n
            pts.append((s1 + math.cos(a) * r_cap, math.sin(a) * r_cap))
    for i in range(n, -1, -1):                   # 左侧: 前 → 后
        s = s0 + (s1 - s0) * i / n
        pts.append((s, -max(0.0, width_fn(s))))
    r_cap = max(0.0, width_fn(s0))
    if r_cap > 0.01:                             # 尾端圆帽
        for i in range(1, cap_n):
            a = math.pi / 2 + math.pi * i / cap_n
            pts.append((s0 + math.cos(a) * r_cap, math.sin(a) * r_cap))
    return pts


def scaled2d(pts, k, lx=0.0, ly=0.0):
    """2D 点列绕原点缩放并平移(做体块分层/高光内缩用)。"""
    return [(x * k + lx, y * k + ly) for x, y in pts]


def loft(painter, cam, section, c_side, c_top, layers=16, bias=0.0, layer=4,
         edge_k=0.64, light_mix=0.10):
    """把同一条闭合轮廓按高度逐层收拢堆叠成体块(底大顶小) → 连续的曲面明暗。

    section(f) 返回第 f 层(0=底, 1=顶)的世界坐标点列; 层与层之间用极小的色差,
    叠出来是光滑的球面渐变, 而不是"梯田"。用于蛙体、果蝇胸腹这类有厚度的躯干。
    """
    layers = max(3, int(layers))
    for i in range(layers):
        f = i / (layers - 1)
        col = mix(shade(c_side, edge_k), add_light(c_top, light_mix), f ** 0.9)
        flat_polygon(painter, cam, section(f), col, bias=bias + f * 0.03, layer=layer)


def dome(painter, cam, pts3, color, bias=0.0, layer=4, outline=None, owidth=1,
         sheen=0.35, color_top=None, steps=4, spread=0.13):
    """把水平多边形画成受光的穹顶/叶片：边缘压暗, 内缩逐层向光面提亮, 再点一块高光。

    用于青蛙身体、荷叶、石头顶面这类"有厚度、被光照到"的平面物。
    """
    vs = [cam.view(p) for p in pts3]
    vs = clip_near(vs, cam.NEAR)
    if len(vs) < 3:
        return None
    pts2d = [(cam.cx + cam.focal * vx / vz, cam.cy - cam.focal * vy / vz)
             for vx, vy, vz in vs]
    depth = sum(vz for _, _, vz in vs) / len(vs)
    cx = sum(p[0] for p in pts2d) / len(pts2d)
    cy = sum(p[1] for p in pts2d) / len(pts2d)
    span = max(2.0, sum(math.hypot(p[0] - cx, p[1] - cy) for p in pts2d) / len(pts2d))
    lx, ly = _screen_light(cam)
    edge = shade(color, EDGE_K)
    lit = add_light(shade(color_top if color_top is not None else color, LIT_K), 0.04)
    inner = []
    for i in range(1, steps + 1):
        f = i / steps
        k = 1.0 - 0.30 * f
        ox, oy = lx * span * spread * f, ly * span * spread * f
        inner.append(([(cx + (px - cx) * k + ox, cy + (py - cy) * k + oy)
                       for px, py in pts2d], mix(edge, lit, (f ** 0.9) * 0.92)))
    # 高光跟随形状：再叠几层向光侧偏移的小内缩多边形, 比画一颗亮圆点自然得多
    core = mix(edge, lit, 0.92)
    spots = []
    for i, (k, add) in enumerate(((0.52, 0.06), (0.34, 0.11), (0.20, 0.16))):
        ox, oy = lx * span * 0.30, ly * span * 0.24
        pts = [(cx + (px - cx) * k + ox, cy + (py - cy) * k + oy) for px, py in pts2d]
        spots.append((pts, mix(core, WHITE, add * min(1.0, sheen * 1.4))))

    def draw(s, pts2d=pts2d, edge=edge, inner=inner, outline=outline, owidth=owidth,
             spots=spots, sheen=sheen):
        pygame.draw.polygon(s, edge, pts2d)
        for pts, col in inner:
            pygame.draw.polygon(s, col, pts)
        if outline:
            pygame.draw.polygon(s, outline, pts2d, owidth)
        if sheen > 0:
            for pts, col in spots:
                pygame.draw.polygon(s, col, pts)

    painter.add(depth - bias, draw, layer)
    return depth - bias


def blob(painter, cam, cx, cy, z0, rx, ry, height, color, heading=0.0, layers=6,
         bias=0.0, layer=4, taper=0.34, outline=None):
    """椭球体体积：把若干层水平椭圆从下往上叠起来(底层暗而大、顶层亮而小)。

    比单张平面多边形多一整个维度的信息——侧面能看出"厚度", 顶面有受光渐变,
    用来做青蛙的身体/头、果蝇的胸部这类需要立体感的躯干。
    整块体一次提交给 painter, 因此层间顺序永远稳定(不会与自身发生深度排序抖动)。
    """
    layers = max(2, int(layers))
    slices = []
    for i in range(layers):
        f = i / (layers - 1)                      # 0 = 底部, 1 = 顶部
        k = 1.0 - taper * (f ** 1.6)              # 越靠顶越小
        # 层间色差压到很小(约 2~3%/层), 叠出来才是连续曲面而不是"梯田"
        col = mix(shade(color, 0.66), add_light(color, 0.12), f ** 0.85)
        ox = LIGHT_XY[0] * rx * 0.14 * f          # 顶部向光侧偏移
        oy = LIGHT_XY[1] * ry * 0.14 * f
        pts3 = ellipse_pts(cx + ox, cy + oy, z0 + height * f, rx * k, ry * k, heading)
        vs = clip_near([cam.view(p) for p in pts3], cam.NEAR)
        if len(vs) < 3:
            continue
        pts2d = [(cam.cx + cam.focal * vx / vz, cam.cy - cam.focal * vy / vz)
                 for vx, vy, vz in vs]
        slices.append((pts2d, col))
    if not slices:
        return
    p = cam.project(V3(cx, cy, z0 + height * 0.4))
    depth = p[2] if p else 0.0

    def draw(s, slices=slices, outline=outline):
        for pts2d, col in slices:
            pygame.draw.polygon(s, col, pts2d)
        if outline:
            pygame.draw.polygon(s, outline, slices[0][0], 2)

    painter.add(depth - bias, draw, layer)


def segment(painter, cam, a, b, color, width=2, bias=0.0, layer=4):
    """两点间线段。bias>0 = 视觉上前置(与 flat_polygon/blob/sphere 一致)。"""
    va, vb = cam.view(a), cam.view(b)
    near = cam.NEAR
    da, db = va[2] - near, vb[2] - near
    if da < 0 and db < 0:
        return
    if da < 0 or db < 0:                       # 一端在近平面后: 裁剪到近平面
        t = da / (da - db)
        vx = va[0] + (vb[0] - va[0]) * t
        vy = va[1] + (vb[1] - va[1]) * t
        vz = near
        if da < 0:
            va = (vx, vy, vz)
        else:
            vb = (vx, vy, vz)
    pa = cam.project_v(va)
    pb = cam.project_v(vb)
    depth = (pa[2] + pb[2]) / 2

    def draw(s, pa=pa, pb=pb, c=color, w=width):
        pygame.draw.line(s, c, (pa[0], pa[1]), (pb[0], pb[1]), w)

    painter.add(depth - bias, draw, layer)


def translucent_polys(painter, cam, items, alpha=110, bias=0.0, layer=4):
    """半透明面片组(翅膀/蹼膜): 先画进独立 SRCALPHA 面, 再整体带 alpha 贴回。

    pygame.draw 直接写像素不做混合, 所以半透明只能靠 "独立面 + blit" 实现。
    同一组面片内部是覆盖关系, 这一组对外是一次混合, 视觉上正是翅膜该有的样子。
    items: [(世界坐标点列, 颜色)]
    """
    polys, box = [], None
    for pts3, color in items:
        pts2 = [cam.project(p) for p in pts3]
        if any(p is None for p in pts2):
            return
        xy = [(p[0], p[1]) for p in pts2]
        polys.append((xy, color))
        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        b = (min(xs), min(ys), max(xs), max(ys))
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                     max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        return
    x0, y0 = int(box[0]) - 2, int(box[1]) - 2
    w = max(2, int(box[2]) - x0 + 2)
    h = max(2, int(box[3]) - y0 + 2)
    if w > 3000 or h > 3000:
        return
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    for xy, color in polys:
        pygame.draw.polygon(surf, color, [(px - x0, py - y0) for px, py in xy])
    surf.set_alpha(alpha)
    p0 = cam.project(items[0][0][0])
    depth = p0[2] if p0 else 1.0
    painter.add(depth - bias, lambda s, surf=surf, x0=x0, y0=y0: s.blit(surf, (x0, y0)),
                layer)


def polyline(painter, cam, pts3, color, width=1, layer=4):
    """折线。层内深度取各点均值。"""
    pts2 = [cam.project(p) for p in pts3]
    if any(p is None for p in pts2):
        return
    pts2d = [(p[0], p[1]) for p in pts2]
    depth = sum(p[2] for p in pts2) / len(pts2)

    def draw(s, pts=pts2d, c=color, w=width):
        pygame.draw.lines(s, c, False, pts, w)

    painter.add(depth, draw)


def limb(painter, cam, a, b, r0, color, bias=0.0, layer=4, taper=0.62,
         shade_ratio=0.86, cap=False):
    """锥形肢体/茎：近端半径 r0、远端 r0×taper, 带圆柱明暗。

    以前用"粗线段"画, 近距离放大就露出方头和方肩; 现在画的是真正的胶囊多边形
    (两侧切线 + 两端半圆), 再叠一层向光偏移的亮面, 粗肢体也圆润。
    """
    va, vb = cam.view(a), cam.view(b)
    near = cam.NEAR
    if va[2] < near and vb[2] < near:
        return
    if va[2] < near or vb[2] < near:
        t = (near - va[2]) / (vb[2] - va[2])
        pt = (va[0] + (vb[0] - va[0]) * t,
              va[1] + (vb[1] - va[1]) * t, near)
        if va[2] < near:
            va = pt
        else:
            vb = pt
    pa, pb = cam.project_v(va), cam.project_v(vb)
    depth = (pa[2] + pb[2]) / 2
    ra = cam.screen_radius(r0, pa[2])
    rb = cam.screen_radius(r0 * taper, pb[2])
    if max(ra, rb) < 0.55:
        return
    ax, ay = pa[0], pa[1]
    bx, by = pb[0], pb[1]
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    if L < 1e-3:
        return
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux
    base = math.atan2(ny, nx)
    lx, ly = _screen_light(cam)
    dark = shade(color, shade_ratio)
    core = mix(color, add_light(color, 0.18), 0.5)

    if max(ra, rb) < 2.2:
        # 远/细肢体: 屏幕上一两个像素宽, 胶囊多边形看不出区别, 直接用线段更快
        a2, b2 = (pa[0], pa[1]), (pb[0], pb[1])

        def draw_thin(s, a2=a2, b2=b2, ra=ra, rb=rb, dark=dark, core=core,
                      lx=lx, ly=ly, cap=cap):
            w = max(ra, rb)
            pygame.draw.line(s, dark, a2, b2, max(2, int(w * 2)))
            ox, oy = lx * w * 0.5, ly * w * 0.5
            pygame.draw.line(s, core, (a2[0] + ox, a2[1] + oy), (b2[0] + ox, b2[1] + oy),
                             max(1, int(w)))
            if cap and ra >= 1.2:
                pygame.draw.circle(s, core, (int(a2[0]), int(a2[1])), max(1, int(ra * 0.8)))

        painter.add(depth - bias, draw_thin, layer)
        return

    def outline(ra_, rb_, ox=0.0, oy=0.0):
        pts = [(ax + nx * ra_ + ox, ay + ny * ra_ + oy),
               (bx + nx * rb_ + ox, by + ny * rb_ + oy)]
        for i in range(1, 4):
            ang = base - math.pi * i / 4
            pts.append((bx + math.cos(ang) * rb_ + ox, by + math.sin(ang) * rb_ + oy))
        pts.append((ax - nx * ra_ + ox, ay - ny * ra_ + oy))
        for i in range(1, 4):
            ang = base + math.pi + math.pi * i / 4
            pts.append((ax + math.cos(ang) * ra_ + ox, ay + math.sin(ang) * ra_ + oy))
        return pts

    edge_pts = outline(ra, rb)
    hi_pts = outline(max(0.4, ra * 0.60), max(0.4, rb * 0.60),
                     lx * ra * 0.32, ly * ra * 0.32)

    def draw(s, edge_pts=edge_pts, hi_pts=hi_pts, dark=dark, core=core,
             ra=ra, rb=rb, ax=ax, ay=ay, bx=bx, by=by, lx=lx, ly=ly, cap=cap):
        pygame.draw.polygon(s, dark, edge_pts)
        pygame.draw.polygon(s, core, hi_pts)
        if cap and ra >= 2.0:
            pygame.draw.circle(s, core, (int(ax + lx * ra * 0.30),
                                         int(ay + ly * ra * 0.30)), max(1, int(ra * 0.8)))
        if cap and rb >= 2.0:
            pygame.draw.circle(s, core, (int(bx + lx * rb * 0.30),
                                         int(by + ly * rb * 0.30)), max(1, int(rb * 0.8)))

    painter.add(depth + bias, draw, layer)
