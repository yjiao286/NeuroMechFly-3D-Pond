"""果蝇池塘 3D —— 越肩视角控制青蛙蹦跳、吐舌头捕食果蝇。

镜头在青蛙后上方平滑跟随，面向整个池塘；远处是沙岸、岩石、芦苇与灌木丛。
9 只果蝇里 8 只是脚本化 NPC（觅食 → 进食 → 靠近就逃），只有 1 只由脉冲神经
网络驱动、会"思考"——它被金色方框标注出来。

所有落地生物都对荷叶高度有感知（脚踩在叶面上而不是陷进去），
配合逐深度排序与显式层间偏置防穿模。

操作：方向键/WASD 游动 · 空格 跳跃 · F/点击 吐舌 ·
      B 神经面板 · M 静音 · F11 全屏 · Esc 退出
"""

from __future__ import annotations

import math
import os
import random
import sys

import pygame

import scenery
from fly_brain import FlyBrain
from render3d import (Camera3D, Painter, V3, add_light, blob, clamp, dome,
                      ellipse_pts, flat_polygon, limb, mix, polyline, segment,
                      shade, soft_shadow, sphere)
from sounds import SoundKit

W, H = 1280, 800
FPS = 60
MARGIN_X, MARGIN_Y = 545, 285          # 蛙与虫的水面活动半幅
EAT_RADIUS = 60                        # 跳跃落点压杀半径
TONGUE_RANGE = 200                     # 舌头射程
TONGUE_CATCH = 17                      # 舌尖捕获半径
TONGUE_CONE = 1.15                     # 吐舌朝向锥（弧度）
TARGET_FLIES = 9                       # 存活果蝇数（8 脚本 + 1 神经元）
GOLD = (255, 200, 60)
FONT_PATHS = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)
V2 = pygame.math.Vector2
VEC = V2                            # 兼容别名


def load_cjk_font(size, bold=False):
    for path in FONT_PATHS:
        if os.path.exists(path):
            return pygame.font.Font(path, size)
    for name in ("pingfangsc", "hiraginosansgb", "stheiti", "arialunicodems"):
        found = pygame.font.match_font(name, bold=bold)
        if found:
            return pygame.font.Font(found, size)
    return pygame.font.Font(None, size)


def lerp_angle(a, b, k):
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return a + d * k


def ang_diff(a, b):
    return (b - a + math.pi) % (2 * math.pi) - math.pi


def rot2(vx, vy, heading):
    c, s = math.cos(heading), math.sin(heading)
    return (vx * c - vy * s, vx * s + vy * c)


def ground_z(pos, pads, t):
    """pos 处的地面高度：落在荷叶上=叶面高度(随波起伏)，否则=水面。"""
    gz = 0.0
    for p in pads:
        gz = max(gz, p.height_at(pos, t))
    return gz


# ---------------------------------------------------------------- 青蛙
class Frog:
    WALK = 175.0
    JUMP_TIME = 0.62
    JUMP_LEN = 310.0
    JUMP_H = 62.0
    TONGUE_OUT, TONGUE_HOLD, TONGUE_BACK = 0.11, 0.05, 0.14

    def __init__(self, pos):
        self.pos = V2(pos)
        self.z = 0.0                          # 跳跃高度
        self.heading = math.pi / 2            # 朝向池塘远处（北岸布景）
        self.state = "ground"
        self.t = 0.0
        self.walk_phase = 0.0
        self.hops = 0
        self.eaten = 0
        self.tongue = None                    # {"t", "phase", "target", "tip"}
        self.cooldown = 0.0
        self._trail = 0.0
        self._moving = False
        rng = random.Random(9)
        self.skin_spots = [((rng.uniform(-34, 34), rng.uniform(-22, 22)), rng.uniform(3.0, 5.5))
                           for _ in range(6)]  # 迷彩色皮肤斑点(预生成防抖动)

    def mouth_pos(self):
        mx, my = rot2(46, 0, self.heading)
        return V3(self.pos.x + mx, self.pos.y + my, self.z + 9)

    def update(self, dt, move, jump, tongue_cmd, tongue_target, ripples, sounds):
        landed = False
        self.cooldown = max(0.0, self.cooldown - dt)
        if self.state == "ground":
            self._moving = move.length_squared() > 0
            if self._moving:
                self.heading = lerp_angle(self.heading, math.atan2(move.y, move.x),
                                          1 - math.exp(-10 * dt))
                self.pos += move * self.WALK * dt
                self.walk_phase += dt * 9
                self._trail += dt
                if self._trail > 0.26:
                    self._trail = 0.0
                    ripples.add(self.pos + VEC(0, 12), 0.3)
            if jump:
                self.state = "air"
                self.t = 0.0
                self.jump_dir = VEC(math.cos(self.heading), math.sin(self.heading))
                self.hops += 1
                sounds.croak_()
                ripples.add(self.pos, 0.6)
            elif tongue_cmd and self.cooldown <= 0 and not self.tongue and tongue_target:
                self.tongue = {"t": 0.0, "phase": "out", "target": tongue_target, "tip": None}
                self.cooldown = 1.1
                sounds.tongue_()
        else:
            self.t += dt
            self.pos += self.jump_dir * (self.JUMP_LEN / self.JUMP_TIME) * dt
            x = self.t / self.JUMP_TIME
            self.z = self.JUMP_H * 4 * x * (1 - x)
            if self.t >= self.JUMP_TIME:
                self.state = "ground"
                self.z = 0.0
                landed = True
                ripples.add(self.pos, 1.0)
                sounds.splash_()
        self.pos.x = clamp(self.pos.x, -MARGIN_X, MARGIN_X)
        self.pos.y = clamp(self.pos.y, -MARGIN_Y, MARGIN_Y)

        # 舌头状态机：弹出 → 黏住 → 收回
        if self.tongue:
            tw = self.tongue
            tw["t"] += dt
            mouth = self.mouth_pos()
            if tw["phase"] == "out":
                k = min(1.0, tw["t"] / self.TONGUE_OUT)
                tw["tip"] = mouth.lerp(tw["target"].tip_pos(), k)
                if tw["t"] >= self.TONGUE_OUT:
                    tw["phase"] = "hold"
                    tw["t"] = 0.0
            elif tw["phase"] == "hold":
                tw["tip"] = tw["target"].tip_pos()
                if tw["t"] >= self.TONGUE_HOLD:
                    tw["phase"] = "back"
                    tw["t"] = 0.0
            else:
                k = min(1.0, tw["t"] / self.TONGUE_BACK)
                tw["tip"] = mouth.lerp(tw["tip"], k) if tw["t"] < self.TONGUE_BACK else mouth
                if tw["t"] >= self.TONGUE_BACK:
                    self.tongue = None
        return landed

    def _body_pts(self, z, scale=1.0):
        """俯视轮廓：前段收窄、后段饱满的青蛙体型(单条闭合多边形，不再是同心椭圆)。"""
        x, y, h = self.pos.x, self.pos.y, self.heading
        n = 26
        pts = []
        for i in range(n):
            a = 2 * math.pi * i / n
            fwd, side = math.cos(a), math.sin(a)
            rx = 37.0 * (1.0 + 0.13 * max(0.0, -fwd) - 0.11 * max(0.0, fwd))
            ry = 24.0 * (1.0 + 0.10 * max(0.0, -fwd) - 0.08 * max(0.0, fwd))
            ex, ey = rot2(fwd * rx * scale, side * ry * scale, h)
            pts.append(V3(x + ex, y + ey, z))
        return pts

    def _local(self, lx, ly, lz=None):
        """身体局部坐标(前=+x, 侧=+y) → 世界坐标。"""
        ex, ey = rot2(lx, ly, self.heading)
        return V3(self.pos.x + ex, self.pos.y + ey,
                  self.z + lz if lz is not None else self.z)

    def _hind_leg(self, painter, cam, side, gz, swing, airborne, skin, dark):
        """后腿：股 → 胫 → 跗，折成青蛙特有的 Z 形并收在身体两侧，末端是四趾蹼足。

        静止时是青蛙的坐姿——膝略微顶出身体轮廓, 长脚掌贴着身体侧面向前收；
        游动/跳跃时腿才蹬开(swing 驱动)。"""
        kick = swing / 9.0 if not airborne else 1.0      # -1 ~ 1
        hip = self._local(-8, side * 15, 9.5)
        if airborne:                                     # 腾空: 腿向后伸展
            knee = self._local(-30, side * 20, 8.0)
            ankle = self._local(-16, side * 26, 7.0)
            toe = self._local(2, side * 24, 6.5)
        else:                                            # 坐姿: 股向后外、胫折向前、脚掌贴身边
            knee = self._local(-24 - 3.0 * kick, side * 32 + 2.0 * abs(kick), 6.5)
            ankle = self._local(1 + 4.0 * kick, side * 33 + 2.0 * abs(kick),
                                gz - self.z + 2.2)
            toe = self._local(17 + 3.0 * kick, side * 29, gz - self.z + 1.4)
        # 股→胫→跗 三段共用端点、不加端帽 → 连成一条会折的腿, 而不是几节珠子
        limb(painter, cam, hip, knee, 11.5, mix(skin, dark, 0.14), bias=0.6, taper=0.76)
        limb(painter, cam, knee, ankle, 8.4, skin, bias=0.3, taper=0.74)
        limb(painter, cam, ankle, toe, 6.0, mix(skin, dark, 0.14), bias=0.2, taper=0.70)
        # 关节处用同色圆片"抹圆"转角: 既藏住折线的缺口, 又不会留下亮色珠子
        head_ang = self.heading
        flat_polygon(painter, cam, ellipse_pts(knee.x, knee.y, knee.z, 8.4, 7.4, head_ang),
                     skin, bias=0.28)
        flat_polygon(painter, cam, ellipse_pts(ankle.x, ankle.y, ankle.z, 6.0, 5.2, head_ang),
                     mix(skin, dark, 0.12), bias=0.18)
        for k in range(4):                                  # 蹼趾：从跗端向前扇开
            ta = self.heading + side * (0.46 - k * 0.30)
            tip = V3(toe.x + math.cos(ta) * 10, toe.y + math.sin(ta) * 10, toe.z)
            limb(painter, cam, toe, tip, 3.0, mix(skin, dark, 0.26), bias=0.1, taper=0.5,
                 cap=True)
        web = [toe]                                         # 趾间蹼：薄扇形膜, 让后足读作"桨"
        for k in (0, 3):
            ta = self.heading + side * (0.46 - k * 0.30)
            web.append(V3(toe.x + math.cos(ta) * 8.6, toe.y + math.sin(ta) * 8.6, toe.z - 0.2))
        flat_polygon(painter, cam, web, mix(skin, dark, 0.5), bias=0.05)

    def _front_leg(self, painter, cam, side, gz, swing, airborne, skin, dark):
        """前腿：肩 → 肘 → 腕，三趾。"""
        if airborne:
            elbow = self._local(25, side * 22, 5.0)
            wrist = self._local(32, side * 16, 3.0)
        else:
            elbow = self._local(24, side * 20, 3.0)
            wrist = self._local(33 + swing * 0.4 * side, side * 16, gz - self.z + 1.0)
        shoulder = self._local(14, side * 13, 7.0)
        limb(painter, cam, shoulder, elbow, 6.4, mix(skin, dark, 0.22), bias=0.4, taper=0.78)
        limb(painter, cam, elbow, wrist, 4.8, skin, bias=0.2, taper=0.66)
        for k in range(3):
            ta = self.heading + side * (0.42 - k * 0.42)
            tip = V3(wrist.x + math.cos(ta) * 8, wrist.y + math.sin(ta) * 8, wrist.z)
            limb(painter, cam, wrist, tip, 2.7, mix(skin, dark, 0.24), bias=0.1, taper=0.5,
                 cap=True)

    def draw(self, painter, cam, t, pads):
        x, y = self.pos
        h = self.heading
        z = self.z
        gz = ground_z(self.pos, pads, t)
        s = 1.0 - self.z / 170.0
        swing = math.sin(self.walk_phase) * 9 if self._moving and self.state == "ground" else 0.0
        airborne = self.state == "air"
        skin = (98, 162, 70)                        # 背部主色
        skin_dark = (58, 104, 44)                   # 阴影/边缘
        leg_skin = (88, 146, 62)
        soft_shadow(painter, cam, V3(x + 8, y + 5, gz + 0.18), 44 * s, 33 * s, 0.78, bias=-4)
        # 远侧腿先画, 近侧腿后画——深度排序会自动区分, 这里只给一点点偏置
        for side in (-1, 1):
            self._hind_leg(painter, cam, side, gz, swing, airborne, leg_skin, skin_dark)
        for side in (-1, 1):
            self._front_leg(painter, cam, side, gz, swing, airborne, leg_skin, skin_dark)
        # 身体：多层椭球体(有厚度), 再加一层略大的深色轮廓当投影边
        flat_polygon(painter, cam, self._body_pts(z + 4.0, 1.03), shade(skin_dark, 0.9),
                     bias=-1.2)
        blob(painter, cam, x, y, z + 4.2, 36.0, 23.5, 12.5, skin, heading=h,
             layers=14, taper=0.38, bias=0.3)
        for (sx, sy), sr in self.skin_spots:                       # 迷彩斑点
            ex, ey = rot2(sx * 0.92, sy * 0.92, h)
            flat_polygon(painter, cam, ellipse_pts(x + ex, y + ey, z + 8.4, sr, sr * 0.72, h),
                         mix(skin_dark, skin, 0.30), bias=0.4)
        ridge = []                                                 # 背脊高光
        for i in range(7):
            f = i / 6
            rx2, ry2 = rot2(-26 + 50 * f, math.sin(f * math.pi) * 3.0, h)
            ridge.append(V3(x + rx2, y + ry2, z + 12.6))
        polyline(painter, cam, ridge, add_light(skin, 0.26), 2)
        for bx, by, br in ((-10, -9, 7), (6, 8, 8), (-20, 2, 5), (14, -5, 5)):
            ex, ey = rot2(bx, by, h)
            flat_polygon(painter, cam, ellipse_pts(x + ex, y + ey, z + 9.7, br, br * 0.7, h),
                         mix(skin_dark, skin, 0.22), bias=0.45)
        # 头：比身体更宽的椭球, 与身体在肩部自然搭接；前端再收一个短吻
        blob(painter, cam, self._local(25, 0).x, self._local(25, 0).y, z + 4.4,
             21.5, 22.5, 11.0, add_light(skin, 0.03), heading=h, layers=11, taper=0.42,
             bias=0.6)
        blob(painter, cam, self._local(40, 0).x, self._local(40, 0).y, z + 4.4,
             14.0, 15.5, 8.5, add_light(skin, 0.05), heading=h, layers=8, taper=0.45,
             bias=0.7)
        mouth = [self._local(38, -14, 10.4), self._local(45, -7.5, 10.7),
                 self._local(47, 0, 10.8), self._local(45, 7.5, 10.7),
                 self._local(38, 14, 10.4)]
        polyline(painter, cam, mouth, shade(skin, 0.46), 2)
        for side in (-1, 1):                                       # 鼻孔
            n0 = self._local(43, side * 3.0, 12.0)
            sphere(painter, cam, n0, 1.1, shade(skin, 0.52), bias=0.8, sheen=0.4)
        # 眼睛：头前角的一对鼓包, 鼓包压在眼珠下面(眼珠"长"在头上, 不是浮在头顶)
        for side in (-1, 1):
            bulge = self._local(32, side * 13.0)
            blob(painter, cam, bulge.x, bulge.y, z + 4.6, 12.5, 11.0, 6.5,
                 mix(skin, skin_dark, 0.22), heading=h, layers=7, taper=0.40, bias=0.72)
            eye = self._local(33, side * 13.6, 14.6)
            sphere(painter, cam, eye, 7.2, (216, 184, 90), bias=0.85, sheen=0.5)
            pupil = self._local(36.8, side * 13.8, 15.0)
            sphere(painter, cam, pupil, 2.6, (44, 34, 26), bias=0.95, sheen=0.3)
        # 舌头（红色圆珠链，每颗独立深度）
        if self.tongue and self.tongue["tip"]:
            tip = self.tongue["tip"]
            mouth = self.mouth_pos()
            for i in range(7):
                p = mouth.lerp(tip, i / 6)
                sphere(painter, cam, p, 4.6 - 1.5 * (i / 6), (214, 68, 68))
            sphere(painter, cam, tip, 6.2, (238, 128, 128))


# ---------------------------------------------------------------- 果蝇
class FlyBase:
    BASE_SPEED = 85.0

    def __init__(self, pos, seed):
        self.pos = V2(pos)
        self.heading = random.uniform(0, math.tau)
        self.z = 16.0                  # 飞行高度（身体基准）
        self.z_target = 16.0
        self.wing_phase = random.uniform(0, math.tau)
        self.leg_phase = random.uniform(0, math.tau)
        self.alive = True
        self.buzz_jitter = random.Random(seed).uniform(0.85, 1.15)
        self.groom_t = 0.0             # 擦眼睛(梳洗)剩余时长
        self.groom_cd = random.uniform(5.0, 9.0)
        self.eating_now = False
        self._speed = 0.0

    def tip_pos(self):
        """舌头瞄准点。"""
        return V3(self.pos.x, self.pos.y, self.z + 3)

    def move_body(self, dt, speed, turn):
        self._speed = speed
        self.heading += turn * dt
        self.pos += V2(math.cos(self.heading), math.sin(self.heading)) * speed * dt
        if not (-MARGIN_X < self.pos.x < MARGIN_X and -MARGIN_Y < self.pos.y < MARGIN_Y):
            self.pos.x = clamp(self.pos.x, -MARGIN_X, MARGIN_X)
            self.pos.y = clamp(self.pos.y, -MARGIN_Y, MARGIN_Y)
            self.heading += math.pi
        if speed > 1 and self.z < 8:
            self.leg_phase += dt * speed / 9       # 步态相位随步速推进
        if self.z > 8:
            self.wing_phase += dt * 46 * math.tau  # 振翅相位
        self.z += (self.z_target - self.z) * min(1.0, dt * 5)

    def _update_groom(self, dt):
        """梳洗周期：落地后每隔几秒用前足擦一次眼睛。"""
        self.groom_cd -= dt
        if self.groom_cd <= 0 and self.groom_t <= 0:
            self.groom_t = 1.2
            self.groom_cd = random.uniform(4.0, 8.0)
        if self.groom_t > 0:
            self.groom_t = max(0.0, self.groom_t - dt)

    def _seg(self, painter, cam, a, b, color, w):
        segment(painter, cam, a, b, color, w)

    def draw(self, painter, cam, t, pads):
        x, y = self.pos
        h = self.heading
        z = self.z                          # 身体基准高度
        gz = ground_z(self.pos, pads, t)
        flying = z > 8
        sh = clamp(1.0 - z / 55.0, 0.25, 1.0)

        # 影子贴地（不穿进荷叶：影子高度=地面高度）
        soft_shadow(painter, cam, V3(x + 3 * sh, y + 2 * sh, gz + 0.16),
                    9.4 * sh, 5.6 * sh, 0.62, bias=-4)
        # 六足: 髋→膝→足 三点两段, 各状态独立步态
        legs = (
            ((2.4, -1.6), (6.2, -3.8), (9.0, -5.6)),
            ((0.2, -1.9), (2.4, -5.0), (3.0, -7.8)),
            ((-2.4, -1.8), (-4.8, -4.8), (-7.4, -6.8)),
            ((2.4, 1.6), (6.2, 3.8), (9.0, 5.6)),
            ((0.2, 1.9), (2.4, 5.0), (3.0, 7.8)),
            ((-2.4, 1.8), (-4.8, 4.8), (-7.4, 6.8)),
        )
        # 局部坐标 → 世界坐标(随身体朝向旋转)
        c_, s_ = math.cos(h), math.sin(h)

        def L(vx, vy, vz):
            return V3(x + (vx * c_ - vy * s_), y + (vx * s_ + vy * c_), vz)

        for i, (hip, knee, foot0) in enumerate(legs):
            side = 1 if foot0[1] > 0 else -1
            if flying:
                jit = math.sin(t * 30 + i * 2.1) * 0.8
                hp = L(hip[0] * 0.9, hip[1] * 0.9, z + 1.0)
                kn = L(knee[0] * 0.8, knee[1] * 0.8, z + 0.2)
                ft = L(foot0[0] - 3.5, foot0[1] * 0.7, z - 2.6 + jit)
            elif mode_eat(self):
                if i in (0, 3):                 # 前足搭在食饵上搓动, 辅助进食
                    rub = math.sin(t * 16 + (0 if i == 0 else math.pi)) * 1.4
                    hp = L(hip[0], hip[1], z + 1.0)
                    kn = L(knee[0], knee[1], gz + 1.6)
                    ft = L(foot0[0] + 1.5, foot0[1] * 0.45 + rub, gz + 0.6)
                elif i in (1, 4):               # 中足撑在叶面
                    hp = L(hip[0], hip[1], z + 1.0)
                    kn = L(knee[0], knee[1], gz + 1.2)
                    ft = L(foot0[0], foot0[1], gz + 0.3)
                else:                           # 后足交替微踏
                    s2 = math.sin(self.leg_phase * 6 + (0 if i == 2 else math.pi)) * 1.4
                    hp = L(hip[0], hip[1], z + 1.0)
                    kn = L(knee[0], knee[1], gz + 1.1)
                    ft = L(foot0[0] + s2, foot0[1], gz + 0.3)
            elif self.groom_t > 0:              # 前足抬到复眼上画圈擦洗
                if i in (0, 3):
                    ph = t * 13 + (0 if i == 0 else math.pi)
                    hp = L(hip[0], hip[1], z + 1.4)
                    kn = L(knee[0] * 0.9, knee[1] * 0.9, z + 2.6)
                    ft = L(6.8 + math.cos(ph) * 1.6, side * 1.9 + math.sin(ph) * 1.1, z + 2.9)
                elif i in (1, 4):
                    hp = L(hip[0], hip[1], z + 1.0)
                    kn = L(knee[0], knee[1], gz + 1.3)
                    ft = L(foot0[0], foot0[1], gz + 0.3)
                else:
                    hp = L(hip[0], hip[1], z + 1.0)
                    kn = L(knee[0], knee[1], gz + 1.2)
                    ft = L(foot0[0], foot0[1], gz + 0.3)
            else:                               # 三角步态行走
                g = 0 if i in (0, 4, 2) else 1
                stride = math.sin(self.leg_phase * 6 + g * math.pi) * 2.8
                hp = L(hip[0], hip[1], z + 1.0)
                kn = L(knee[0], knee[1], (z + 1.0 + gz) / 2 + 0.6)
                ft = L(foot0[0] + stride, foot0[1], gz + 0.25)
            # 股→胫→跗三段锥形, 关节用亮色补圆(不再是一颗颗球)
            limb(painter, cam, hp, kn, 0.80, (150, 116, 76), taper=0.72)
            limb(painter, cam, kn, ft, 0.52, (128, 96, 62), taper=0.55)
        # 身体: 三段连续椭球(头/胸/腹)——偏灰的琥珀棕, 不是橙糖色; 腹部略浅、末端收深
        thc = rot2(1.0, 0, h)
        blob(painter, cam, x + thc[0], y + thc[1], z + 0.1, 3.6, 3.0, 4.8,
             (168, 126, 84), heading=h, layers=10, taper=0.44, bias=0.8)
        abc = rot2(-4.4, 0, h)
        blob(painter, cam, x + abc[0], y + abc[1], z + 0.2, 3.5, 2.8, 3.6,
             (186, 146, 98), heading=h, layers=9, taper=0.52, bias=0.55)
        tipc = rot2(-8.0, 0, h)
        sphere(painter, cam, V3(x + tipc[0], y + tipc[1], z + 1.1), 1.6,
               (118, 84, 56), bias=0.5, sheen=0.55)
        # 刚毛: 胸部一列背中刚毛(少而清楚, 不堆细节)
        for k in range(5):
            bx0, by0 = rot2(-0.6 + 1.1 * k, (k % 2 - 0.5) * 1.3, h)
            b0 = V3(x + bx0, y + by0, z + 3.4)
            b1 = V3(b0.x + math.cos(h + math.pi / 2) * 1.1 * (1 if k % 2 else -1),
                    b0.y + math.sin(h + math.pi / 2) * 1.1 * (1 if k % 2 else -1), z + 4.8)
            segment(painter, cam, b0, b1, (74, 52, 34), 1, bias=0.5)
        # 平衡棒（后翅退化成的陀螺仪器官，飞行平衡用）
        for side in (-1, 1):
            hp = V3(x + rot2(-2.6, side * 2.4, h)[0], y + rot2(-2.6, side * 2.4, h)[1], z + 1.4)
            sphere(painter, cam, hp, 0.9, (232, 206, 122), bias=0.5)
        hd = V3(x + rot2(5.9, 0, h)[0], y + rot2(5.9, 0, h)[1], z + 2.3)
        sphere(painter, cam, hd, 2.5, (172, 128, 84), bias=1.1, sheen=0.7)
        # 触角
        for side in (-1, 1):
            a1 = V3(x + rot2(7.5, side * 0.9, h)[0], y + rot2(7.5, side * 0.9, h)[1], z + 3.0)
            a2 = V3(x + rot2(9.1, side * 1.7, h)[0], y + rot2(9.1, side * 1.7, h)[1], z + 3.3)
            limb(painter, cam, a1, a2, 0.5, (146, 108, 68), bias=-0.2, taper=0.7)
        # 喙/口器：平时也收在头下(果蝇一直带着口器, 进食时才伸出去)
        pb = V3(x + rot2(7.3, 0, h)[0], y + rot2(7.3, 0, h)[1], z + 1.1)
        sphere(painter, cam, pb, 1.0, (132, 92, 60), bias=1.2, sheen=0.4)
        # 砖红复眼一对：几乎占满头部, 互相贴近成 bilobed 整体(不会误读成两只虫)
        for side in (-1, 1):
            e = V3(x + rot2(6.2, side * 1.5, h)[0], y + rot2(6.2, side * 1.5, h)[1], z + 2.9)
            sphere(painter, cam, e, 2.6, (138, 74, 64), bias=1.4, sheen=0.45)
        # 口器(进食时伸向食饵)
        if mode_eat(self):
            p1 = V3(x + rot2(7.4, 0, h)[0], y + rot2(7.4, 0, h)[1], z + 1.6)
            p2 = V3(x + rot2(11.2, 0, h)[0], y + rot2(11.2, 0, h)[1], gz + 0.8)
            self._seg(painter, cam, p1, p2, (146, 108, 68), 2)
            sphere(painter, cam, p2, 1.3, (132, 92, 60), bias=0.3)
        # 双翅: 飞行展开振动(带翅脉), 落地收拢在背上
        wing_c = (228, 233, 231)
        if flying:
            for side in (-1, 1):
                flap = 0.5 * math.sin(self.wing_phase + (0 if side < 0 else math.pi))
                wang = h + side * (2.15 + flap)
                wx, wy = math.cos(wang), math.sin(wang)
                bx, by = rot2(-2, side * 1.6, h)
                L = 13.0
                base = V3(x + bx, y + by, z + 2.6)

                def W(u, s, dz):                    # 翅轴 u(0~1) × 横向 s → 世界坐标
                    return V3(base.x + wx * L * u - wy * side * s,
                              base.y + wy * L * u + wx * side * s, base.z + dz)

                pts = [base, W(0.30, 2.6, 1.5), W(0.68, 3.0, 1.2),      # 圆头水滴形翅
                       W(0.97, 1.4, 0.5), W(0.55, -0.9, -0.3)]
                flat_polygon(painter, cam,
                             [V3(p.x - wy * side * 0.35, p.y + wx * side * 0.35, p.z + 0.05)
                              for p in pts], (206, 220, 220), bias=0.26)
                dome(painter, cam, pts, wing_c, bias=0.3, sheen=0.16)
                for (u, s) in ((0.95, 0.9), (0.72, 0.2), (0.45, -0.5)):  # 翅脉
                    limb(painter, cam, W(0.08, 0.2, 0.4), W(u, s, 0.2), 0.22,
                         (186, 200, 202), bias=0.05, taper=0.6)
                limb(painter, cam, W(0.10, 1.4, 0.9), W(0.90, 1.5, 0.6), 0.18,
                     (252, 252, 244), bias=0.02, taper=0.8)      # 前缘高光
        else:
            # 收拢的翅: 覆盖在腹部两侧、翅尖略微内收(停歇姿态)
            for side in (-1, 1):
                def RL(u, s, dz):                   # 身体局部坐标 → 世界(收翅用)
                    return V3(x + rot2(u, side * s, h)[0], y + rot2(u, side * s, h)[1], z + dz)

                pts = [RL(-0.6, 1.9, 2.5), RL(-5.2, 2.4, 2.6),
                       RL(-9.4, 0.7, 2.3), RL(-8.4, 3.2, 2.1)]
                dome(painter, cam, pts, (220, 227, 224), bias=0.55, layer=4, sheen=0.18)
                limb(painter, cam, RL(-0.6, 1.9, 2.62), RL(-8.6, 1.6, 2.42), 0.2,
                     (244, 248, 244), bias=0.4, taper=0.7)


def mode_eat(fly):
    return fly.eating_now and not fly.groom_t > 0


class ScriptedFly(FlyBase):
    """脚本化 NPC：航点觅食 → 降落啃食 → 靠近青蛙 110 内拔腿就逃。没有神经元。"""

    def __init__(self, pos, seed):
        super().__init__(pos, seed)
        self.state = "觅食"
        self.food = None
        self.flee_t = 0.0
        self.eat_t = 0.0

    def update(self, dt, frog, crumbs, t):
        dfrog = self.pos.distance_to(frog.pos)
        if self.state != "逃离" and dfrog < 110:
            self.state = "逃离"
            self.flee_t = 1.1
            away = self.pos - frog.pos
            self.heading = math.atan2(away.y, away.x) + random.uniform(-0.3, 0.3)
            self.z_target = 19.0
        if self.state == "逃离":
            self.flee_t -= dt
            self.move_body(dt, 240, random.uniform(-1, 1) * dt * 2)
            if self.flee_t <= 0 or dfrog > 260:
                self.state = "觅食"
                self.food = None
                self.z_target = 16.0
        elif self.state == "进食":
            self.eat_t -= dt
            self.z_target = 3.4
            self._update_groom(dt)
            self.eating_now = self.groom_t <= 0        # 擦眼睛时前足腾不出空
            if self.eating_now and self.food and self.food.amount > 0:
                self.food.bite(dt)
            if self.eat_t <= 0 or not self.food or self.food.amount <= 0:
                self.state = "觅食"
                self.food = None
                self.eating_now = False
                self.z_target = 16.0
        else:
            if self.food is None or self.food.amount <= 0:
                alive = [c for c in crumbs if c.amount > 0.3]
                self.food = min(alive, key=lambda c: c.pos().distance_to(self.pos)
                                + c.crowd * 40) if alive else None
            if self.food:
                fp = self.food.pos()
                d = fp.distance_to(self.pos)
                self.heading += clamp(ang_diff(self.heading, math.atan2(fp.y - self.pos.y,
                                                                        fp.x - self.pos.x)), -1, 1) * 2.6 * dt
                self.move_body(dt, self.BASE_SPEED * self.buzz_jitter, 0)
                if d < 9:
                    self.state = "进食"
                    self.eat_t = random.uniform(2.6, 3.6)
            else:
                self.heading += math.sin(t * 0.8 + self.wing_phase) * 0.8 * dt
                self.move_body(dt, self.BASE_SPEED * 0.6, 0)
        if self.state != "进食":
            self.move_body(dt, 0, 0)   # 仅用于高度过渡

    def state_name(self):
        return self.state


class BrainFly(FlyBase):
    """唯一的"思考者"：脉冲神经网络驱动的果蝇个体（金色框标注）。

    巨纤维逃逸反射比脚本 NPC 灵敏得多（170 就触发，脚本 110），
    循气味趋向食饵，是否降落进食由歇息回路闸门决定。
    """

    def __init__(self, pos, seed):
        super().__init__(pos, seed)
        self.brain = FlyBrain(seed)
        self.hunger = 0.6
        self.rest_t = 0.0        # 歇息中没有进展(没得吃)的计时
        self.rest_total = 0.0    # 本次歇息总时长

    def _takeoff(self):
        self.brain.resting = False
        self.brain.escape_timer = 0.35
        self.z_target = 21.0
        self.rest_t = 0.0
        self.rest_total = 0.0

    @property
    def resting(self):
        return self.brain.resting

    def update(self, dt, frog, crumbs, t):
        dfrog = self.pos.distance_to(frog.pos)
        alive_food = [c for c in crumbs if c.amount > 0.3]
        food = min(alive_food, key=lambda c: c.pos().distance_to(self.pos)
                   + c.crowd * 40) if alive_food else None
        food_dist = food.pos().distance_to(self.pos) if food else 999.0
        cmd = self.brain.step(dt, dist_frog=dfrog, frog_airborne=frog.state == "air",
                              pad_dist=food_dist, t=t)
        if cmd["gf_fired"]:
            away = self.pos - frog.pos
            self.heading = math.atan2(away.y, away.x) + random.uniform(-0.3, 0.3)

        if self.brain.escape_timer > 0:
            self.z_target = 21.0
            self.eating_now = False
            self.move_body(dt, self.BASE_SPEED * cmd["thrust"], random.uniform(-1, 1) * dt)
        elif self.brain.resting:
            self.z_target = 3.4
            self.rest_t += dt
            self.rest_total += dt
            self._update_groom(dt)
            self.eating_now = False
            if food and food_dist < 26 and food.amount > 0:
                # 落在食饵上: 平滑转身爬向碎屑(贴近后进入死区, 不再转向防止头尾翻转)
                if food_dist > 8:
                    target = math.atan2(food.pos().y - self.pos.y, food.pos().x - self.pos.x)
                    self.heading = lerp_angle(self.heading, target, 1 - math.exp(-6 * dt))
                    crawl = min(30.0, food_dist * 4 + 6)
                else:
                    crawl = 0.0
                self.move_body(dt, crawl if self.groom_t <= 0 else 0.0, 0)
                if food_dist < 10 and food.amount > 0 and self.groom_t <= 0:
                    self.eating_now = True
                    food.bite(dt)
                    self.hunger = max(0.0, self.hunger - dt * 0.35)
                    self.rest_t = 0.0
                    if food.amount <= 0:
                        self._takeoff()
            else:
                self.move_body(dt, 0, 0)
            # 主动起飞：2.5 秒没吃到东西，或这顿歇满 12 秒
            if self.rest_t > 2.5 or self.rest_total > 12.0:
                self._takeoff()
        else:
            self.z_target = 16.0
            braking = 1.0
            if food and self.brain.escape_timer <= 0:
                braking = 0.45 if food_dist < 45 else 1.0   # 接近食饵减速, 让歇息电位积累
                k = clamp(ang_diff(self.heading,
                                   math.atan2(food.pos().y - self.pos.y, food.pos().x - self.pos.x)), -1, 1)
                self.heading += k * 1.6 * dt * (0.5 + self.hunger)    # 气味趋向
            self.move_body(dt, self.BASE_SPEED * cmd["thrust"] * self.buzz_jitter * braking, cmd["turn"])
        self.hunger = min(1.0, self.hunger + dt * 0.02)

    def state_name(self):
        return self.brain.state

    def neurons(self):
        return self.brain.neurons()


# ---------------------------------------------------------------- 游戏
class Game:
    def __init__(self, headless=False):
        self.headless = headless
        self.autopilot = headless
        self.screen = pygame.display.set_mode((W, H), pygame.SCALED | pygame.RESIZABLE)
        pygame.display.set_caption("果蝇池塘 3D · 空格跳跃 / F 吐舌")
        self.font_big = load_cjk_font(30, bold=True)
        self.font = load_cjk_font(19)
        self.font_small = load_cjk_font(14)
        self.cam = Camera3D((-900, -880, 640), (80, 100, 0), focal=1050)
        self.cam_target = V3(80, 100, 0)          # 注视点(左键拖动平移)
        self.cam_yaw = math.radians(225)          # 机位方位角(右键左右拖动旋转)
        self.cam_elev = math.radians(25)          # 仰角(右键上下拖动调整)
        self.dist = 1526.0                        # 机位距离(滚轮缩放)
        self._panning = False
        self._rotating = False
        self._down = None                         # [x, y, 累计位移]
        self.painter = Painter()
        self._bank_key = None
        self._bank_surf = None
        self.vignette = scenery.build_vignette(W, H)
        self.bank_props = scenery.make_bank_props()
        self.pads = scenery.make_pads()
        self.crumbs = [scenery.FoodCrumb(p) for p in self.pads]
        self.ripples = scenery.Ripples()
        self.duckweed = scenery.make_duckweed()
        self.sounds = SoundKit(enabled=not headless)
        self.frog = Frog((0, -120))
        self.flies = self._initial_flies()
        self.particles = []
        self.popups = []
        self.show_brain = False
        self.t = 0.0
        self.spawn_timer = 0.0
        self.ambient_timer = 0.0
        self.aim_target = None
        self.shake = 0.0
        self.fullscreen = False

    def _initial_flies(self):
        flies = []
        for i in range(TARGET_FLIES - 1):
            flies.append(ScriptedFly((random.uniform(-450, 450), random.uniform(-180, 260)), seed=i))
        flies.append(BrainFly((random.uniform(-260, 260), random.uniform(-80, 200)), seed=99))
        return flies

    def brain_fly(self):
        for f in self.flies:
            if isinstance(f, BrainFly):
                return f
        return None

    def _spawn_fly(self):
        edge = random.randrange(4)
        if edge == 0:
            pos = V2(random.uniform(-MARGIN_X, MARGIN_X), -MARGIN_Y + 14)
        elif edge == 1:
            pos = V2(random.uniform(-MARGIN_X, MARGIN_X), MARGIN_Y - 14)
        elif edge == 2:
            pos = V2(-MARGIN_X + 14, random.uniform(-MARGIN_Y, MARGIN_Y))
        else:
            pos = V2(MARGIN_X - 14, random.uniform(-MARGIN_Y, MARGIN_Y))
        if self.brain_fly() is None:
            self.flies.append(BrainFly(pos, seed=random.randrange(10 ** 6)))
        else:
            self.flies.append(ScriptedFly(pos, seed=random.randrange(10 ** 6)))

    # ---------- 更新 ----------
    def simulate(self, dt, move, jump, tongue_cmd):
        self.t += dt
        if self.autopilot:
            move, jump, tongue_cmd = self._autopilot()
        # 瞄准：朝向锥内最近的目标（瞄准圈 + 吐舌共用）
        self.aim_target = None
        best = 1e9
        for f in self.flies:
            d = f.pos.distance_to(self.frog.pos)
            ang = abs(ang_diff(self.frog.heading,
                               math.atan2(f.pos.y - self.frog.pos.y, f.pos.x - self.frog.pos.x)))
            if d < TONGUE_RANGE and ang < TONGUE_CONE and d < best:
                best, self.aim_target = d, f
        # 食饵拥挤度: 每颗食饵附近已有多少果蝇(供选食时避开拥挤)
        for c in self.crumbs:
            c.crowd = sum(1 for f in self.flies if f.pos.distance_to(c.pos()) < 16)
        landed = self.frog.update(dt, move, jump, tongue_cmd, self.aim_target,
                                  self.ripples, self.sounds)
        if landed:
            self.shake = 0.32
            self.try_eat(self.frog.pos, EAT_RADIUS, "压杀!")
        tip = self.frog.tongue["tip"] if self.frog.tongue else None
        if tip and self.frog.tongue["phase"] in ("out", "hold"):
            self.try_eat_tip(tip)
        for f in self.flies:
            f.update(dt, self.frog, self.crumbs, self.t)
        self.flies = [f for f in self.flies if f.alive]
        self.spawn_timer += dt
        if self.spawn_timer > 5.0 and len(self.flies) < TARGET_FLIES:
            self.spawn_timer = 0.0
            self._spawn_fly()
        for c in self.crumbs:
            if c.amount <= 0:
                c.timer -= dt
                if c.timer <= 0:
                    c.respawn()
        self.ambient_timer -= dt
        if self.ambient_timer <= 0:
            self.ambient_timer = random.uniform(0.7, 2.2)
            self.ripples.add(V2(random.uniform(-500, 500), random.uniform(-260, 260)), 0.25)
        self.ripples.update(dt)
        for p in self.particles:
            p["pos"] += p["vel"] * dt
            p["vel"].z -= 320 * dt
            p["life"] -= dt * 1.8
        self.particles = [p for p in self.particles if p["life"] > 0]
        for p in self.popups:
            p["pos"].z += 30 * dt
            p["life"] -= dt
        self.popups = [p for p in self.popups if p["life"] > 0]
        buzz = max((1 - f.pos.distance_to(self.frog.pos) / 430 for f in self.flies if f.z > 8),
                   default=0.0)
        self.sounds.set_buzz(max(0.0, min(1.0, buzz)) * 0.55)
        self.shake = max(0.0, self.shake - dt)

    def try_eat(self, point, radius, label):
        for f in list(self.flies):
            if V2(point.x, point.y).distance_to(f.pos) <= radius:
                self._consume(f, label)

    def try_eat_tip(self, tip):
        for f in list(self.flies):
            if f.tip_pos().distance_to(tip) <= TONGUE_CATCH:
                self._consume(f, "舌头!")

    def _consume(self, f, label):
        f.alive = False
        self.frog.eaten += 1
        self.sounds.crunch_()
        self.ripples.add(f.pos, 0.7)
        self.popups.append({"pos": V3(f.pos.x, f.pos.y, f.z + 8), "text": f"+1 {label}", "life": 1.1})
        for _ in range(12):
            a = random.uniform(0, math.tau)
            self.particles.append({
                "pos": V3(f.pos.x, f.pos.y, f.z + 4),
                "vel": V3(math.cos(a) * random.uniform(20, 90),
                          math.sin(a) * random.uniform(20, 90), random.uniform(40, 140)),
                "life": random.uniform(0.4, 0.8),
                "color": random.choice(((48, 40, 36), (96, 60, 40), (192, 36, 36)))})
        self.flies = [g for g in self.flies if g.alive]

    def _autopilot(self):
        """冒烟测试自动驾驶：走向目标，射程内吐舌，预判落点跳跃。"""
        move, jump, tongue = V2(0), False, False
        if self.flies:
            target = min(self.flies, key=lambda f: f.pos.distance_to(self.frog.pos))
            d = target.pos.distance_to(self.frog.pos)
            dd = target.pos - self.frog.pos
            if d > 70 and dd.length_squared() > 1:
                move = dd.normalize()
            if d < TONGUE_RANGE - 5 and self.frog.cooldown <= 0 and not self.frog.tongue:
                tongue = True
            elif self.frog.state == "ground" and 150 < d:
                land = self.frog.pos + V2(math.cos(self.frog.heading),
                                          math.sin(self.frog.heading)) * Frog.JUMP_LEN
                if min((f.pos.distance_to(land) for f in self.flies), default=1e9) < 55:
                    jump = True
        return move, jump, tongue

    # ---------- 输入 ----------
    def handle_event(self, e):
        """处理单个事件, 返回 (tongue_cmd, quit_req)。"""
        tongue_cmd = False
        quit_req = False
        if e.type == pygame.QUIT:
            quit_req = True
        elif e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
            quit_req = True
        elif e.type == pygame.KEYDOWN and e.key == pygame.K_b:
            self.show_brain = not self.show_brain
        elif e.type == pygame.KEYDOWN and e.key == pygame.K_m:
            self.sounds.toggle_mute()
        elif e.type == pygame.KEYDOWN and e.key == pygame.K_F11:
            self.fullscreen = not self.fullscreen
            self._apply_display()
        elif e.type == pygame.KEYDOWN and e.key == pygame.K_f:
            tongue_cmd = True
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self._panning = True
            self._down = [e.pos[0], e.pos[1], 0]
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            self._rotating = True
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            # 原地单击(几乎没有拖动)=吐舌; 拖动过=只是平移视角
            if self._down and self._down[2] < 6:
                tongue_cmd = True
            self._panning = False
            self._down = None
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 3:
            self._rotating = False
        elif e.type == pygame.MOUSEMOTION:
            if self._rotating:
                # 右键拖动: 水平转方位, 垂直调俯仰
                self.cam_yaw += e.rel[0] * 0.005
                self.cam_elev = clamp(self.cam_elev + e.rel[1] * 0.004,
                                      math.radians(18), math.radians(62))
            elif self._panning and self._down:
                # 左键拖动: 抓取式——图跟着手走(往哪拖画面就往哪滑)
                rel = (e.pos[0] - self._down[0], e.pos[1] - self._down[1])
                self._down[0], self._down[1] = e.pos[0], e.pos[1]
                self._down[2] += abs(rel[0]) + abs(rel[1])
                s = self.dist / 900.0                  # 平移速度随缩放自适应
                fx, fy = -math.cos(self.cam_yaw), -math.sin(self.cam_yaw)   # 镜头前方(屏幕上方)
                rx, ry = -math.sin(self.cam_yaw), math.cos(self.cam_yaw)   # 镜头右侧
                self.cam_target.x += (fx * rel[1] - rx * rel[0]) * s
                self.cam_target.y += (fy * rel[1] - ry * rel[0]) * s
                self.cam_target.x = clamp(self.cam_target.x, -scenery.POND_W2, scenery.POND_W2)
                self.cam_target.y = clamp(self.cam_target.y, -scenery.POND_H2, scenery.POND_H2)
        elif e.type == pygame.MOUSEWHEEL:
            self.dist = clamp(self.dist * (0.9 ** e.y), 420, 3200)   # 滚轮缩放(草地已铺满, 可贴近看果蝇动作)
        return tongue_cmd, quit_req

    # ---------- 绘制 ----------
    def draw(self):
        surf = self.screen
        # 机位 = 注视点 + 方位角/仰角/距离 构成的球坐标偏移
        ce, se = math.cos(self.cam_elev), math.sin(self.cam_elev)
        ca, sa = math.cos(self.cam_yaw), math.sin(self.cam_yaw)
        eye = self.cam_target + V3(self.dist * ce * ca, self.dist * ce * sa, self.dist * se)
        if self.shake > 0:
            eye += V3(random.uniform(-1, 1) * self.shake * 14,
                      random.uniform(-1, 1) * self.shake * 10,
                      random.uniform(-1, 1) * self.shake * 8)
        self.cam.set_view(eye, self.cam_target)
        scenery.draw_sky(surf, self.cam, self.t)
        painter, cam = self.painter, self.cam
        scenery.draw_pond(painter, cam, self.t)
        self._paint_bank_base(painter, cam)
        scenery.draw_bank_plants(painter, cam, self.t, self.bank_props)
        self.ripples.draw(painter, cam)
        scenery.draw_duckweed(painter, cam, self.t, self.duckweed)
        for pad in self.pads:
            pad.draw(painter, cam, self.t)
        for c in self.crumbs:
            c.draw(painter, cam, self.t)
        bf = self.brain_fly()
        for f in self.flies:
            if f is not bf:
                f.draw(painter, cam, self.t, self.pads)
        if bf:
            bf.draw(painter, cam, self.t, self.pads)
        self.frog.draw(painter, cam, self.t, self.pads)
        for p in self.particles:
            sphere(painter, cam, p["pos"], 1.6 * p["life"] + 0.6, p["color"])
        painter.flush(surf)
        # 瞄准圈（白色圆环，套在锥内最近目标上）
        if self.aim_target:
            sp = cam.project(V3(self.aim_target.pos.x, self.aim_target.pos.y, self.aim_target.z + 2))
            if sp:
                r = clamp(cam.focal * 12 / sp[2], 14, 80)
                pygame.draw.circle(surf, (246, 243, 228), (int(sp[0]), int(sp[1])), int(r), 3)
        if bf:
            self._draw_brackets(surf, bf)
        for p in self.popups:
            sp = cam.project(p["pos"])
            if sp:
                label = self.font.render(p["text"], True, (255, 236, 180))
                label.set_alpha(max(0, min(255, int(p["life"] * 260))))
                surf.blit(label, label.get_rect(midbottom=(int(sp[0]), int(sp[1]))))
        surf.blit(self.vignette, (0, 0))
        self._draw_hud()

    def _paint_bank_base(self, painter, cam):
        """岸上静态布景（堤壁/草地/卵石/岩石/灌木）按机位缓存成一张贴图。

        这些内容只跟机位有关，机位不动时每帧只 blit 一次，省下上千次多边形/圆形绘制；
        会摇摆的草丛与芦苇仍走每帧实时绘制。
        """
        key = (round(self.cam_yaw, 4), round(self.cam_elev, 4), round(self.dist, 2),
               round(self.cam_target.x, 1), round(self.cam_target.y, 1),
               self.shake > 0)
        if self._bank_key != key:
            cache = pygame.Surface((W, H), pygame.SRCALPHA)
            sub = Painter()
            scenery.draw_bank_base(sub, cam, self.bank_props)
            sub.flush(cache)
            self._bank_key, self._bank_surf = key, cache
        img = self._bank_surf
        # 深度取最大 → 在 BANK 层里最先画, 荷叶/生物仍然照常盖在它上面
        painter.add(1e6, lambda s, img=img: s.blit(img, (0, 0)), Painter.BANK)

    def _draw_brackets(self, surf, bf):
        """金色方框 + 状态标注：标出唯一的神经元个体。"""
        sp = self.cam.project(V3(bf.pos.x, bf.pos.y, bf.z + 6))
        if sp is None:
            return
        sx, sy, depth = sp
        r = clamp(self.cam.focal * 22 / depth, 20, 130) * (1 + 0.05 * math.sin(self.t * 5))
        corner = r * 0.45
        for cx, cyy, dx, dy in ((sx - r, sy - r, 1, 1), (sx + r, sy - r, -1, 1),
                                (sx - r, sy + r, 1, -1), (sx + r, sy + r, -1, -1)):
            pygame.draw.line(surf, GOLD, (cx, cyy), (cx + dx * corner, cyy), 3)
            pygame.draw.line(surf, GOLD, (cx, cyy), (cx, cyy + dy * corner), 3)
        st = "梳洗" if bf.groom_t > 0 else bf.state_name()
        label = self.font_small.render(f"神经元个体 · {st}", True, GOLD)
        surf.blit(label, label.get_rect(midbottom=(int(sx), int(sy - r - 4))))

    def _draw_hud(self):
        surf = self.screen
        pygame.draw.rect(surf, (6, 24, 20), (0, 0, W, 66), border_bottom_left_radius=14,
                         border_bottom_right_radius=14)
        title = self.font_big.render(f"吃掉 {self.frog.eaten} 只果蝇", True, (240, 248, 238))
        surf.blit(title, (18, 8))
        cd = self.frog.cooldown
        cd_txt = "舌头就绪" if cd <= 0 else f"舌头 {cd:.1f}s"
        info = self.font.render(
            f"存活 {len(self.flies)}/{TARGET_FLIES}（8 脚本 + 1 神经元） · 跳跃 {self.frog.hops} · "
            f"{cd_txt} · 神经面板{'开' if self.show_brain else '关'}(B)", True, (168, 210, 190))
        surf.blit(info, (20, 42))
        hint = self.font.render("方向键/WASD 游动 · 空格 跳跃 · 单击/F 吐舌 · 左键拖平移 · 右键拖旋转 · 滚轮缩放 · B 面板",
                                True, (205, 226, 210))
        surf.blit(hint, hint.get_rect(midbottom=(W / 2, H - 14)))
        if self.show_brain:
            bf = self.brain_fly()
            if bf:
                panel = pygame.Rect(1000, 76, 264, 238)
                pygame.draw.rect(surf, (20, 34, 26), panel, border_radius=10)
                pygame.draw.rect(surf, GOLD, panel, 2, border_radius=10)
                st = "梳洗" if bf.groom_t > 0 else bf.state_name()
                title = self.font_small.render(f"神经元个体 · {st}", True, GOLD)
                surf.blit(title, (panel.x + 12, panel.y + 8))
                for i, (name, act, fired) in enumerate(bf.neurons()):
                    yy = panel.y + 34 + i * 22
                    lab = self.font_small.render(name, True, (200, 224, 205))
                    surf.blit(lab, (panel.x + 12, yy))
                    pygame.draw.rect(surf, (40, 58, 48), (panel.x + 70, yy, 150, 12), border_radius=3)
                    color = (255, 90, 70) if fired else (120, 220, 160)
                    pygame.draw.rect(surf, color, (panel.x + 70, yy, max(2, int(150 * act)), 12),
                                     border_radius=3)
                notes = [
                    "GF 巨纤维·逃逸: 蛙近175触发",
                    "CX 中央复合体·巡航: 左右竞争",
                    "REST 歇息: 悬停食饵→降落进食",
                    "超阈值(-52mV)发放; 条=距发放",
                    "tau: GF.05 CX.25 REST.6秒",
                ]
                ny = panel.y + 130
                pygame.draw.line(surf, (60, 84, 66), (panel.x + 12, ny - 8),
                                 (panel.x + panel.w - 12, ny - 8), 1)
                for i, s in enumerate(notes):
                    note = self.font_small.render(s, True, (186, 208, 192))
                    surf.blit(note, (panel.x + 12, ny + i * 17))

    # ---------- 主循环 ----------
    def _apply_display(self):
        """窗口模式与全屏之间切换；SCALED 保证 1280x800 画面等比铺满。"""
        flags = pygame.SCALED | pygame.FULLSCREEN if self.fullscreen \
            else pygame.SCALED | pygame.RESIZABLE
        self.screen = pygame.display.set_mode((W, H), flags)

    def run(self):
        clock = pygame.time.Clock()
        running = True
        while running:
            dt = min(clock.tick(FPS) / 1000.0, 1 / 20)
            tongue_cmd = False
            quit_req = False
            for e in pygame.event.get():
                tongue_cmd, quit_req = self.handle_event(e)
                if quit_req:
                    running = False
            keys = pygame.key.get_pressed()
            ix = (keys[pygame.K_RIGHT] + keys[pygame.K_d]
                  - keys[pygame.K_LEFT] - keys[pygame.K_a])
            iy = (keys[pygame.K_UP] + keys[pygame.K_w]
                  - keys[pygame.K_DOWN] - keys[pygame.K_s])
            # 屏幕方向 → 世界方向（固定镜头）：按上=往画面深处游
            ff = V2(self.cam.fwd.x, self.cam.fwd.y)
            if ff.length_squared() < 1e-6:
                ff = V2(0, 1)
            ff.normalize_ip()
            fr = V2(ff.y, -ff.x)                     # 镜头水平右方向
            move = ff * iy + fr * ix
            if move.length_squared() > 1:
                move = move.normalize()
            self.simulate(dt, move, keys[pygame.K_SPACE], tongue_cmd)
            self.draw()
            pygame.display.flip()
        pygame.quit()


def selftest(frames=1800):
    """无头冒烟测试：自动驾驶必须能用舌头/跳跃吃到虫，神经元果蝇必须触发过逃逸。"""
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    pygame.init()
    g = Game(headless=True)
    gf_events = 0
    for i in range(frames):
        g.simulate(1 / 60, V2(0), False, False)
        bf = g.brain_fly()
        if bf and bf.brain.gf_count:
            gf_events = max(gf_events, bf.brain.gf_count)
        if g.frog.eaten >= 3 and i > 300:
            break
    eaten, hops, alive = g.frog.eaten, g.frog.hops, len(g.flies)
    pygame.quit()
    print(f"[selftest] 吃掉={eaten} 跳跃={hops} 存活={alive} "
          f"神经元果蝇GF逃逸反射={gf_events}次")
    assert eaten >= 1, "自动驾驶没吃到虫"
    print("[selftest] PASS ✓")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        pygame.init()
        Game().run()
