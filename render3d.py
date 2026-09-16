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
    """带简单高光的球体（投影为圆）。bias>0 = 视觉上前置。"""
    p = cam.project(pos)
    if p is None:
        return
    sx, sy, depth = p
    rs = cam.screen_radius(r, depth)

    def draw(s, sx=sx, sy=sy, rs=rs, c=color):
        rad = min(max(1, int(rs)), 4000)
        pygame.draw.circle(s, c, (int(sx), int(sy)), rad)
        pygame.draw.circle(s, shade(c, 1.4),
                           (int(sx - rs * 0.3), int(sy - rs * 0.35)), max(1, int(rs * 0.42)))

    painter.add(depth - r * 0.01 - bias, draw, layer)


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
