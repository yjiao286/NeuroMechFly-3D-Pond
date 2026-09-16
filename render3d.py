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
            a = int(alpha * (1.0 - f) ** 0.75)
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
    blob = _shadow_blob(80, int(clamp(150 * strength, 20, 200)))
    size = (max(2, int(w * 2)), max(2, int(h * 2)))
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


def sphere(painter, cam, pos, r, color, bias=0.0, layer=4):
    """球体：边缘压暗 + 逐层向光面提亮 + 镜面高光 + 细描边。
    bias>0 = 视觉上前置。"""
    p = cam.project(pos)
    if p is None:
        return
    sx, sy, depth = p
    rs = cam.screen_radius(r, depth)
    if rs < 0.55:
        return
    rad = min(max(1, int(rs)), 4000)
    dark = shade(color, 0.58)
    lit = add_light(shade(color, 1.04), 0.12)
    steps = 2 if rad <= 7 else 3                # 小球少画几层, 省时间也够看
    rings = [(max(1, int(rad * (1 - 0.54 * (i + 1) / steps))),
              (-0.32 * rad * (i + 1) / steps, -0.38 * rad * (i + 1) / steps),
              mix(dark, lit, (i + 1) / steps)) for i in range(steps)]
    hx, hy = -0.38 * rad, -0.44 * rad
    spec = max(1, int(rad * 0.26))

    def draw(s, sx=sx, sy=sy, rad=rad, dark=dark, rings=rings, hx=hx, hy=hy,
             spec=spec, color=color):
        pygame.draw.circle(s, dark, (int(sx), int(sy)), rad)
        for rr, (ox, oy), col in rings:
            pygame.draw.circle(s, col, (int(sx + ox), int(sy + oy)), rr)
        if rad >= 8:
            pygame.draw.circle(s, mix(color, WHITE, 0.50), (int(sx + hx), int(sy + hy)), spec)
            pygame.draw.circle(s, mix(color, WHITE, 0.88),
                               (int(sx + hx * 1.15), int(sy + hy * 1.15)),
                               max(1, int(spec * 0.45)))
        if rad >= 5:
            pygame.draw.circle(s, shade(color, 0.42), (int(sx), int(sy)), rad, 1)

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
    edge = shade(color, 0.60)
    lit = add_light(shade(color, 1.05), 0.10)
    inner = []
    for i in range(1, 4):
        f = i / 3
        k = 1.0 - 0.34 * f
        ox, oy = lx * span * 0.16 * f, ly * span * 0.16 * f
        inner.append(([(cx + (px - cx) * k + ox, cy + (py - cy) * k + oy)
                       for px, py in pts2d], mix(edge, lit, f * 0.95)))
    hx, hy = cx + lx * span * 0.30, cy + ly * span * 0.24
    hr = max(1.0, span * 0.16 * sheen * 2.0)

    def draw(s, pts2d=pts2d, edge=edge, inner=inner, outline=outline, owidth=owidth,
             hx=hx, hy=hy, hr=hr, color=color, sheen=sheen):
        pygame.draw.polygon(s, edge, pts2d)
        for pts, col in inner:
            pygame.draw.polygon(s, col, pts)
        if outline:
            pygame.draw.polygon(s, outline, pts2d, owidth)
        if sheen > 0 and hr >= 1.5:
            pygame.draw.circle(s, mix(color, WHITE, 0.30 * sheen), (int(hx), int(hy)), int(hr))
            pygame.draw.circle(s, mix(color, WHITE, 0.55 * sheen),
                               (int(hx), int(hy)), max(1, int(hr * 0.5)))

    painter.add(depth - bias, draw, layer)
    return depth - bias


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
