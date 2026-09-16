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


class Painter:
    """多层画家算法: 按 layer 从小到大分批绘制, 层内按深度远→近。
    层号约定: 0=水面 1=水波/焦散/涟漪 2=荷叶/食饵 3=岸上立体物 4=生物。
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


def flat_polygon(painter, cam, pts3, color, outline=None, owidth=2, bias=0.0, layer=4):
    """水平多边形（世界坐标点列），近平面裁剪后按平均深度排序。"""
    vs = [cam.view(p) for p in pts3]
    vs = clip_near(vs, cam.NEAR)
    if len(vs) < 3:
        return None
    pts2d = [(cam.cx + cam.focal * vx / vz, cam.cy - cam.focal * vy / vz)
             for vx, vy, vz in vs]
    depth = sum(vz for _, _, vz in vs) / len(vs)

    def draw(s, pts=pts2d, c=color, o=outline, w=owidth):
        pygame.draw.polygon(s, c, pts)
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


def dome(painter, cam, pts3, color, bias=0.0, layer=4, outline=None, owidth=1,
         sheen=0.35):
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
    lit = add_light(shade(color, LIT_K), 0.04)
    inner = []
    for i in range(1, 5):
        f = i / 4
        k = 1.0 - 0.30 * f
        ox, oy = lx * span * 0.13 * f, ly * span * 0.13 * f
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
    """两点间线段。bias>0 = 视觉上后置（先绘制，可被同位置的身体遮住根部）。"""
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

    painter.add(depth + bias, draw, layer)


def polyline(painter, cam, pts3, color, width=1, layer=4):
    pts2 = [cam.project(p) for p in pts3]
    if any(p is None for p in pts2):
        return
    pts2d = [(p[0], p[1]) for p in pts2]
    depth = sum(p[2] for p in pts2) / len(pts2)

    def draw(s, pts=pts2d, c=color, w=width):
        pygame.draw.lines(s, c, False, pts, w)

    painter.add(depth, draw)


def limb(painter, cam, a, b, r0, color, bias=0.0, layer=4, taper=0.62, shade_ratio=0.86):
    """锥形肢体/茎：近端半径 r0、远端 r0×taper，带圆柱明暗。

    用于青蛙与果蝇的腿、芦苇茎等"有粗细变化的杆状物"——比等宽线段更像肢体。
    关节处用亮色填充(不是暗色圆帽), 这样两段肢体接在一起看不出"球关节"。
    bias>0 = 视觉上后置(与 segment 一致)。
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
    if ra < 0.6 and rb < 0.6:
        return
    lx, ly = _screen_light(cam)
    dark = shade(color, shade_ratio)
    core = mix(color, add_light(color, 0.18), 0.5)

    def draw(s, pa=pa, pb=pb, ra=ra, rb=rb, lx=lx, ly=ly, dark=dark, core=core):
        steps = 2 if max(ra, rb) < 4.5 else 3        # 细肢体少画一段
        for i in range(steps):
            f0, f1 = i / steps, (i + 1) / steps
            p0 = (pa[0] + (pb[0] - pa[0]) * f0, pa[1] + (pb[1] - pa[1]) * f0)
            p1 = (pa[0] + (pb[0] - pa[0]) * f1, pa[1] + (pb[1] - pa[1]) * f1)
            w0 = max(1.0, ra + (rb - ra) * f0)
            pygame.draw.line(s, dark, p0, p1, max(2, int(w0 * 2)))
            ox, oy = lx * w0 * 0.34, ly * w0 * 0.34
            pygame.draw.line(s, core, (p0[0] + ox, p0[1] + oy), (p1[0] + ox, p1[1] + oy),
                             max(1, int(w0)))
        # 关节处只用亮色补圆, 不画暗色外圈——否则每两段之间都顶着一颗"球关节"
        if ra >= 2.0:
            pygame.draw.circle(s, core, (int(pa[0] + lx * ra * 0.30),
                                         int(pa[1] + ly * ra * 0.30)), max(1, int(ra * 0.86)))
        if rb >= 2.0:
            pygame.draw.circle(s, core, (int(pb[0] + lx * rb * 0.30),
                                         int(pb[1] + ly * rb * 0.30)), max(1, int(rb * 0.86)))

    painter.add(depth + bias, draw, layer)
