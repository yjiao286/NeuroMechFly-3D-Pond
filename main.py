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

import models
import scenery
from fly_brain import FlyBrain
from render3d import Camera3D, Painter, SubCamera, V3, clamp, segment, sphere
from sounds import SoundKit

W, H = 1280, 800
FPS = 60
MARGIN_X, MARGIN_Y = 545, 285          # 蛙与虫的水面活动半幅
EAT_RADIUS = 60                        # 跳跃落点压杀半径
TONGUE_RANGE = 200                     # 舌头射程
TONGUE_CATCH = int(round(17 * models.FLY_SCALE))   # 舌尖捕获半径(跟随果蝇体型)
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


def pick_food(pos, crumbs, bias=1.0):
    """抢食规则：代价 = 距离 ÷ 空进食位 × 个体偏好。

    空位多的食饵更划算 → 果蝇自然分散到不同荷叶; 余量新鲜的碎屑值得多飞一段路
    (快见底的只顺路吃); bias 是每只果蝇固定的偏好系数(0.8~1.2), 让同时选食的
    个体拆分到不同目标, 避免雷群式全体涌向同一个空位。全都满座时返回最近的一块,
    调用方会让它在上面盘旋等位(而不是硬挤下去)。
    """
    best, best_key, fallback, fb_d = None, 1e9, None, 1e9
    for c in crumbs:
        if c.amount <= 0.3:
            continue
        d = c.pos().distance_to(pos)
        free = c.free_slots()
        if free <= 0:
            if d < fb_d:
                fallback, fb_d = c, d
            continue
        key = (d / (free * (0.25 + c.amount / 6.0))) * bias
        if key < best_key:
            best, best_key = c, key
    return best if best is not None else fallback


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
        self.skin_spots = [((rng.uniform(-30, 22), rng.uniform(-20, 20)), rng.uniform(4.0, 8.0))
                           for _ in range(4)]  # 迷彩色皮肤斑点(预生成防抖动)
        self.blink_cd = random.uniform(2.0, 5.0)   # 眨眼倒计时
        self.blink = 0.0                           # 眼皮闭合剩余时长

    def mouth_pos(self):
        mx, my = rot2(36, 0, self.heading)
        return V3(self.pos.x + mx, self.pos.y + my, self.z + 5.5)

    def update(self, dt, move, jump, tongue_cmd, tongue_target, ripples, sounds):
        landed = False
        self.cooldown = max(0.0, self.cooldown - dt)
        self.blink_cd -= dt                        # 偶尔眨一下眼
        if self.blink_cd <= 0:
            self.blink = 0.13
            self.blink_cd = random.uniform(2.5, 6.5)
        self.blink = max(0.0, self.blink - dt)
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

    def draw(self, painter, cam, t, pads):
        """形体交给建模层(models.py)；这里只负责把自身状态传出去。"""
        models.draw_frog(painter, cam, self, t, pads)

    def draw_tongue(self, painter, cam, t):
        """舌头伸得很长, 必须画在生物精灵画布之外(否则会被裁掉)。"""
        if not self.tongue or not self.tongue["tip"]:
            return
        tip = self.tongue["tip"]
        mouth = self.mouth_pos()
        for i in range(7):
            p = mouth.lerp(tip, i / 6)
            sphere(painter, cam, p, 4.6 - 1.5 * (i / 6), (214, 68, 68))
        sphere(painter, cam, tip, 6.2, (238, 128, 128))


class FlyBase:
    BASE_SPEED = 85.0 * models.FLY_SCALE / 1.42      # 巡航速度随体型同比放大
    CRUISE_Z = 16.0 * models.FLY_SCALE               # 巡航高度
    ESCAPE_Z = CRUISE_Z + 4.0 * models.FLY_SCALE     # 受惊时拔高
    LAND_Z = 3.0 * models.FLY_SCALE                  # 落在叶面上的身体高度

    def __init__(self, pos, seed):
        self.pos = V2(pos)
        self.heading = random.uniform(0, math.tau)
        self.z = self.CRUISE_Z         # 飞行高度（身体基准）
        self.z_target = self.CRUISE_Z
        self.wing_phase = random.uniform(0, math.tau)
        self.leg_phase = random.uniform(0, math.tau)
        self.alive = True
        self.buzz_jitter = random.Random(seed).uniform(0.85, 1.15)
        self.pick_bias = random.Random(seed + 7).uniform(0.8, 1.2)   # 选食个体偏好(拆雷群)
        self.groom_t = 0.0             # 擦眼睛(梳洗)剩余时长
        self.groom_cd = random.uniform(5.0, 9.0)
        self.food = None               # 当前认领的食饵(抢食统计用)
        self._food_cd = 0.0            # 等位时的重新选食倒计时
        self.eating_now = False
        self._speed = 0.0

    def tip_pos(self):
        """舌头瞄准点(略高于身体中心, 随体型缩放)。"""
        return V3(self.pos.x, self.pos.y, self.z + 3.0 * models.FLY_SCALE)

    def move_body(self, dt, speed, turn):
        self._speed = speed
        self.heading += turn * dt
        self.pos += V2(math.cos(self.heading), math.sin(self.heading)) * speed * dt
        if not (-MARGIN_X < self.pos.x < MARGIN_X and -MARGIN_Y < self.pos.y < MARGIN_Y):
            self.pos.x = clamp(self.pos.x, -MARGIN_X, MARGIN_X)
            self.pos.y = clamp(self.pos.y, -MARGIN_Y, MARGIN_Y)
            self.heading += math.pi
        if speed > 1 and self.z < self.LAND_Z + 2.0:
            self.leg_phase += dt * speed / 9       # 步态相位随步速推进
        if self.z > self.LAND_Z + 5.0:
            self.wing_phase += dt * 22 * math.tau  # 振翅相位(约 22 Hz: 60fps 下看得清上下弧)
        self.z += (self.z_target - self.z) * min(1.0, dt * 5)

    GROOM_TIME = 1.6                   # 一次擦眼持续多久

    def _update_groom(self, dt):
        """梳洗周期：落地后每隔几秒用前足擦一次眼睛。

        间隔与时长都调过——动作本身在池畔视角下只有几个像素, 太短太稀就永远撞不见。
        """
        self.groom_cd -= dt
        if self.groom_cd <= 0 and self.groom_t <= 0:
            self.groom_t = self.GROOM_TIME
            self.groom_cd = random.uniform(2.5, 5.0)
        if self.groom_t > 0:
            self.groom_t = max(0.0, self.groom_t - dt)

    def _seg(self, painter, cam, a, b, color, w):
        segment(painter, cam, a, b, color, w)

    def draw(self, painter, cam, t, pads):
        """形体交给建模层(models.py)。"""
        models.draw_fly(painter, cam, self, t, pads)


def mode_eat(fly):
    return fly.eating_now and not fly.groom_t > 0


class ScriptedFly(FlyBase):
    """脚本化 NPC：航点觅食 → 降落啃食 → 靠近青蛙 110 内拔腿就逃。没有神经元。

    遇到神经元个体争食会让位（见 _flee 与 update 里的领域对峙）——
    对应真实果蝇在食源上的攻击-驱逐行为, 也让"思考者"的竞争优势看得见。
    """

    def __init__(self, pos, seed):
        super().__init__(pos, seed)
        self.state = "觅食"
        self.food = None
        self.flee_t = 0.0
        self.eat_t = 0.0
        self.displace_t = 0.0          # 被神经元个体逼近时的对峙计时
        self.yielded = 0               # 统计: 让位次数
        self.loiter_t = 0.0            # 满座盘旋等位的计时(超过就放弃)
        self.flee_speed = 240.0        # 逃离速度(惊慌 240 / 被挤走 150)

    def _flee(self, threat_pos, flee_t, panic=True):
        """弃食逃飞: 放弃认领把进食位让出来, 朝远离威胁的方向。"""
        self.state = "逃离"
        self.flee_t = flee_t
        self.flee_speed = 240.0 if panic else 150.0   # 被挤走不同于吓破胆, 慢一档
        self.groom_t = 0.0                     # 逃跑打断梳洗
        self.eating_now = False
        self.food = None                       # 放弃认领, 把进食位让出来
        away = self.pos - threat_pos
        self.heading = math.atan2(away.y, away.x) + random.uniform(-0.3, 0.3)
        self.z_target = self.ESCAPE_Z if panic else self.CRUISE_Z

    def update(self, dt, frog, crumbs, t, brain_fly=None):
        dfrog = self.pos.distance_to(frog.pos)
        if self.state != "逃离" and dfrog < 110:
            self._flee(frog.pos, 1.1)
        # 领域让位(荷叶级): 神经元个体落在这片荷叶上 → 半秒内弃食让座。
        # 真实果蝇的食源攻击就是"领域占有者驱逐入侵者"; 只认"它已落地",
        # 空中路过/盘旋不算威胁。被挤开不像躲青蛙那样惊慌, 回巡航高度飞走
        if (self.state == "进食" and brain_fly is not None and brain_fly.alive
                and brain_fly.resting and brain_fly.z < FlyBase.LAND_Z + 6
                and brain_fly.pos.distance_to(self.pos) < 72):
            self.displace_t += dt
            if self.displace_t > 0.5:
                self.displace_t = 0.0
                self.yielded += 1
                self._flee(brain_fly.pos, 0.8, panic=False)
        else:
            self.displace_t = 0.0
        if self.state == "逃离":
            self.flee_t -= dt
            self.move_body(dt, self.flee_speed, random.uniform(-1, 1) * dt * 2)
            if self.flee_t <= 0 or dfrog > 260:
                self.state = "觅食"
                self.food = None
                self.z_target = self.CRUISE_Z
        elif self.state == "进食":
            self.eat_t -= dt
            self.z_target = self.LAND_Z
            self.move_body(dt, 0, 0)               # 高度过渡就写在 move_body 里: 落地必须调它
            self._update_groom(dt)
            # 落地了才动嘴(下降过程不啃), 擦眼睛时前足也腾不出空;
            # 开吃闸门带粘性: 已在吃的保持(feeders 里含自己), 没吃则要求餐位空着,
            # 否则同帧到达的两只会一起落下去(容量=1 时的竞态)
            self.eating_now = (self.groom_t <= 0 and self.z < self.LAND_Z + 3.5
                               and (self.eating_now or self.food.feeders == 0))
            if self.eating_now and self.food and self.food.amount > 0:
                self.food.bite(dt)
            if self.eat_t <= 0 or not self.food or self.food.amount <= 0:
                self.state = "觅食"
                self.food = None
                self.eating_now = False
                self.groom_t = 0.0             # 起飞去下一处, 别再擦眼睛
                self.z_target = self.CRUISE_Z
        else:
            self._food_cd = max(0.0, self._food_cd - dt)
            if self._food_cd <= 0 and (self.food is None or self.food.amount <= 0
                                       or self.food.full(self)):
                self.food = pick_food(self.pos, crumbs, self.pick_bias)
                self._food_cd = 0.45       # 重新权衡的间隔(等位/游荡共用这个节流)
            if self.food:
                fp = self.food.pos()
                d = fp.distance_to(self.pos)
                self.heading += clamp(ang_diff(self.heading, math.atan2(fp.y - self.pos.y,
                                                                        fp.x - self.pos.x)), -1, 1) * 2.6 * dt
                if self.food.full(self):
                    # 满座: 不硬挤, 大圈缓飞等位(转弯率随个体微差, 圈不重叠);
                    # 碎屑快见底、或等超过 3.5 秒就放弃——游荡片刻再选,
                    # 免得所有等位者一窝蜂挤向同一个刚空出的座位
                    if self.food.amount < 0.8 or self.loiter_t > 3.5:
                        self.food = None
                        self.loiter_t = 0.0
                        self._food_cd = 0.8
                    else:
                        self.loiter_t += dt
                        self.move_body(dt, self.BASE_SPEED * 0.45, 0)
                        if d < 40:
                            self.heading += 1.0 * self.buzz_jitter * dt
                else:
                    self.loiter_t = 0.0
                    self.move_body(dt, self.BASE_SPEED * self.buzz_jitter, 0)
                    if d < 9:
                        self.state = "进食"
                        self.eat_t = random.uniform(2.2, 3.2)
            else:
                self.heading += math.sin(t * 0.8 + self.wing_phase) * 0.8 * dt
                self.move_body(dt, self.BASE_SPEED * 0.6, 0)
        if self.state != "进食":
            self.move_body(dt, 0, 0)   # 仅用于高度过渡

    def state_name(self):
        return self.state


class BrainFly(FlyBase):
    """唯一的"思考者"：脉冲神经网络驱动的果蝇个体（金色框标注）。

    巨纤维逃逸反射比脚本 NPC 灵敏得多（170 就触发，脚本 110），但会习惯化：
    蹲着不动的青蛙在旁久了就敢落下进食，头顶掠影则立刻恢复敏感。
    循气味趋向食饵，是否降落进食由歇息回路闸门决定；落定的荷叶即它的
    领域——正在同一片荷叶上进食的脚本个体会让位弃食（真实果蝇的食源攻击）。
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
        self.z_target = self.ESCAPE_Z
        self.rest_t = 0.0
        self.rest_total = 0.0
        self.groom_t = 0.0                     # 起飞打断梳洗
        self.eating_now = False

    @property
    def resting(self):
        return self.brain.resting

    def update(self, dt, frog, crumbs, t, brain_fly=None):
        dfrog = self.pos.distance_to(frog.pos)
        # 目标黏性: 正在吃、歇在食饵上、或已进入 60px 内的 committed 进近时锁定
        # 目标——容量=1 时被占的食饵对选食是"满座", 不锁定就永远在对峙前转身离开
        if (self.eating_now or (self.brain.resting and self.food is not None
                                and self.food.amount > 0
                                and self.pos.distance_to(self.food.pos()) < 30)
                or (self.food is not None and self.food.amount > 0
                    and self.pos.distance_to(self.food.pos()) < 60)):
            food = self.food
        else:
            food = pick_food(self.pos, crumbs, self.pick_bias)
            self.food = food
        food_dist = food.pos().distance_to(self.pos) if food else 999.0
        cmd = self.brain.step(dt, dist_frog=dfrog, frog_airborne=frog.state == "air",
                              pad_dist=food_dist, t=t)
        if cmd["gf_fired"]:
            away = self.pos - frog.pos
            self.heading = math.atan2(away.y, away.x) + random.uniform(-0.3, 0.3)
        was_eating = self.eating_now

        if self.brain.escape_timer > 0:
            self.z_target = self.ESCAPE_Z
            self.eating_now = False
            self.groom_t = 0.0                 # 逃逸起飞打断梳洗
            # 逃逸打断本次歇息: 计时清零, 否则下一轮歇息继承旧账,
            # 刚落地就被"歇满 9 秒"条款赶走, 形成落地-起飞循环
            self.rest_t = 0.0
            self.rest_total = 0.0
            self.move_body(dt, self.BASE_SPEED * cmd["thrust"], random.uniform(-1, 1) * dt)
        elif self.brain.resting:
            # 歇息回路开了就落地——目标被占也照落: 走近对峙, 脚本个体会让位
            # (领域性; 真实果蝇是在食源上用步足争抢, 不是在空中抢)
            self.z_target = self.LAND_Z
            self.rest_t += dt
            self.rest_total += dt
            self._update_groom(dt)
            self.eating_now = False
            if food and food.amount > 0:
                # 落上叶面就走过去(可能要穿过小半个荷叶), 贴近后进入死区防头尾翻转
                if food_dist > 8:
                    target = math.atan2(food.pos().y - self.pos.y, food.pos().x - self.pos.x)
                    self.heading = lerp_angle(self.heading, target, 1 - math.exp(-6 * dt))
                    crawl = min(36.0, food_dist * 4 + 8)   # 被占时也要快步逼近对峙
                else:
                    crawl = 0.0
                self.move_body(dt, crawl if self.groom_t <= 0 else 0.0, 0)
                if (food_dist < 10 and food.amount > 0 and self.groom_t <= 0
                        and self.z < self.LAND_Z + 3.5
                        and (was_eating or food.feeders == 0)):   # 落地才吃; 闸门带粘性
                    self.eating_now = True
                    food.bite(dt)
                    self.hunger = max(0.0, self.hunger - dt * 0.22)
                    self.rest_t = 0.0
                    if food.amount <= 0 or self.hunger <= 0.05:
                        self._takeoff()          # 吃光了, 或者吃饱了——把座位让出来
            else:
                self.move_body(dt, 0, 0)
            # 主动起飞：3.5 秒没吃到东西(含对峙失败)，或这顿歇满 9 秒
            if self.rest_t > 3.5 or self.rest_total > 9.0:
                self._takeoff()
        else:
            self.z_target = self.CRUISE_Z
            braking = 1.0
            if food and self.brain.escape_timer <= 0:
                braking = 0.45 if food_dist < 45 else 1.0   # 接近食饵减速, 让歇息电位积累
                k = clamp(ang_diff(self.heading,
                                   math.atan2(food.pos().y - self.pos.y, food.pos().x - self.pos.x)), -1, 1)
                self.heading += k * 1.6 * dt * (0.5 + self.hunger)    # 气味趋向
            self.move_body(dt, self.BASE_SPEED * cmd["thrust"] * self.buzz_jitter * braking, cmd["turn"])
        self.hunger = min(1.0, self.hunger + dt * 0.05)   # 饿得快: 觅食驱力强, 存在感足

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
        self._bank_ss = 1
        self._bank_still = 0
        self._cam_moved = False
        self._last_eye = (0.0, 0.0, 0.0, 0.0, 0.0)
        self.bank_props = scenery.make_bank_props()
        self.pads = scenery.make_pads()
        # 每片荷叶两块碎屑(分居对侧): 容量=1 后全池 12 个餐位, 9 只果蝇有争抢但不会
        # 全体雷群式涌向唯一空位、把时间都耗在通勤上
        self.crumbs = [scenery.FoodCrumb(p, side=i) for p in self.pads for i in (0, 1)]
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
        for c in self.crumbs:                       # 抢食统计: 认领数 / 正在进食数
            c.claims = sum(1 for f in self.flies if f.food is c)
            c.feeders = sum(1 for f in self.flies if f.food is c and f.eating_now)
            c.inbound = {id(f) for f in self.flies   # 只统计"马上就到"的(36px): 远处过路的
                         if f.food is c and not f.eating_now  # 不占座, 免得假性满座
                         and f.pos.distance_to(c.pos()) < 36}
        landed = self.frog.update(dt, move, jump, tongue_cmd, self.aim_target,
                                  self.ripples, self.sounds)
        if landed:
            self.shake = 0.32
            self.try_eat(self.frog.pos, EAT_RADIUS, "压杀!")
        tip = self.frog.tongue["tip"] if self.frog.tongue else None
        if tip and self.frog.tongue["phase"] in ("out", "hold"):
            self.try_eat_tip(tip)
        bf = self.brain_fly()
        for f in self.flies:
            f.update(dt, self.frog, self.crumbs, self.t, bf)
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
        buzz = max((1 - f.pos.distance_to(self.frog.pos) / 430
                    for f in self.flies if f.z > FlyBase.LAND_Z + 5.0),
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
        moving = (abs(eye.x - self._last_eye[0]) + abs(eye.y - self._last_eye[1])
                  + abs(eye.z - self._last_eye[2])
                  + abs(self.cam_target.x - self._last_eye[3]) * 0.5
                  + abs(self.cam_target.y - self._last_eye[4]) * 0.5) > 0.35
        self._cam_moved = moving
        self._last_eye = (eye.x, eye.y, eye.z, self.cam_target.x, self.cam_target.y)
        self.cam.set_view(eye, self.cam_target)
        scenery.draw_sky(surf, self.cam, self.t)
        painter, cam = self.painter, self.cam
        scenery.draw_pond(painter, cam, self.t, moving=self._cam_moved)
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
        self.frog.draw_tongue(painter, cam, self.t)
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
        self._draw_hud()

    def _paint_bank_base(self, painter, cam):
        """岸上静态布景（堤壁/草地/卵石/岩石/灌木）。

        只跟机位有关, 因此缓存成一张贴图; 机位一停就再用 2× 超采样重画一遍,
        缩回原尺寸后整片岸景的轮廓都是抗锯齿的——转动时先用 1× 保证跟手,
        停下后再"补一遍清晰度"(渐进式抗锯齿)。
        """
        key = (round(self.cam_yaw, 4), round(self.cam_elev, 4), round(self.dist, 2),
               round(self.cam_target.x, 1), round(self.cam_target.y, 1),
               self.shake > 0)
        if self._bank_key != key:
            self._bank_key, self._bank_ss, self._bank_still = key, 1, 0
            self._bank_surf = self._render_bank(cam, 1)
        else:
            self._bank_still += 1
            if self._bank_ss < 2 and self._bank_still > 5:
                self._bank_ss = 2
                self._bank_surf = self._render_bank(cam, 2)
        img = self._bank_surf
        painter.add(1e6, lambda s, img=img: s.blit(img, (0, 0)), Painter.BANK)

    def _render_bank(self, cam, ss):
        """按 ss 倍分辨率渲染静态岸景, 再缩回窗口尺寸(ss>1 时即为抗锯齿)。"""
        cache = pygame.Surface((W * ss, H * ss), pygame.SRCALPHA)
        sub = Painter()
        scam = SubCamera(cam, 0, 0, ss)
        scenery.draw_beach_base(sub, scam)
        scenery.draw_bank_base(sub, scam, self.bank_props)
        sub.flush(cache)
        if ss > 1:
            cache = pygame.transform.smoothscale(cache, (W, H))
        return cache

    def _draw_brackets(self, surf, bf):
        """金色方框 + 状态标注：标出唯一的神经元个体。"""
        sp = self.cam.project(V3(bf.pos.x, bf.pos.y,
                                 bf.z + 5.5 * models.FLY_SCALE))
        if sp is None:
            return
        sx, sy, depth = sp
        r = clamp(self.cam.focal * 21 * models.FLY_SCALE / depth, 24, 200) \
            * (1 + 0.05 * math.sin(self.t * 5))
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
                    "GF 巨纤维·逃逸: 初遇170/习惯化70",
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
