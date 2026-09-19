"""果蝇神经元大脑 —— 受真实黑腹果蝇神经环路启发的脉冲神经网络 (SNN)。

池塘里每只 NPC 果蝇都由一组泄漏积分发放 (LIF) 神经元驱动，环路设计对应
真实果蝇 (Drosophila melanogaster) 的经典神经科学发现：

  环路                    果蝇中的真实对应                          本模型中的作用
  巨纤维系统 (GF)         视叶→巨轴突→逃逸反射，数毫秒内出手        感知青蛙逼近/头顶阴影，
                                                                   膜电位超过阈值立即发放→逃逸；
                                                                   对反复无害的静止威胁会习惯化
                                                                   (真实 GF 环路的经典性质)，蹲着
                                                                   不动的青蛙久了就敢落下进食
  中央复合体 (CX)         航向整合、巡航行进控制                     左右两个慢振荡器竞争产生
                                                                   平滑、有惯性的随机游走
  歇息/觉醒切换           果蝇间歇性的停歇-起飞行为                  悬停在荷叶上空时积累电位，
                                                                   超过阈值降落；受扰立即起飞

发放模型: dv/dt = (-(v - v_rest) + I) / tau，v >= v_thr 时发放一个脉冲并复位。
为数值稳定，每帧按 tau/2 细分为多个子步积分。
"""

from __future__ import annotations

import math
import random


class LIFNeuron:
    """泄漏积分发放神经元。activation ∈ [0,1] 供可视化，step() 返回是否发放。"""

    def __init__(self, tau=0.05, v_rest=-70.0, v_thr=-52.0, v_reset=-76.0):
        self.v = v_rest
        self.tau = tau
        self.v_rest = v_rest
        self.v_thr = v_thr
        self.v_reset = v_reset
        self.fired = False
        self.activation = 0.0

    def step(self, dt, current):
        self.fired = False
        n = max(1, math.ceil(dt / (self.tau * 0.5)))
        h = dt / n
        for _ in range(n):
            self.v += h * (-(self.v - self.v_rest) + current) / self.tau
            if self.v >= self.v_thr:
                self.v = self.v_reset
                self.fired = True
                break
        span = self.v_thr - self.v_rest
        self.activation = max(0.0, min(1.0, (self.v - self.v_rest) / span))
        return self.fired


class FlyBrain:
    """一只果蝇的完整神经环路。身体提供感觉输入，环路输出运动指令。"""

    WANDER, ESCAPE, REST, TAKEOFF = "巡航", "逃逸", "歇息", "起飞"

    def __init__(self, seed=None):
        rng = random.Random(seed)
        self.gf = LIFNeuron(tau=0.05)     # 巨纤维：威胁越近电流越大，越过阈值即发放
        self.cx_l = LIFNeuron(tau=0.25)   # 中央复合体左振荡器（只调幅，不发放）
        self.cx_r = LIFNeuron(tau=0.25)   # 中央复合体右振荡器
        self.rest = LIFNeuron(tau=0.6)    # 歇息驱动：很慢，悬停荷叶上空才积累

        # 每只果蝇的"个性"：转弯节奏、游走幅度、振翅频率都略有不同
        self.phase = rng.uniform(0, math.tau)
        self.freq = rng.uniform(0.25, 0.5)
        self.wander_gain = rng.uniform(0.8, 1.3)
        self.buzz_jitter = rng.uniform(0.85, 1.15)

        self.escape_timer = 0.0
        self.refractory = 0.0             # GF 不应期，防止逃逸连发
        self.hab = 0.0                    # GF 习惯化程度 0(全敏感)→1(全脱敏)
        self.threat_eff = 0.0             # 最近一帧的有效威胁(供身体层决策)
        self.resting = False
        self.state = self.WANDER
        self.gf_count = 0                 # 统计：一生逃逸反射次数
        self.rest_count = 0

    def step(self, dt, *, dist_frog, frog_airborne, pad_dist, t):
        """感觉输入 → 环路更新 → 运动输出。返回 {turn, thrust, gf_fired}。"""
        # --- 感觉电流 ---
        threat = max(0.0, 1.0 - dist_frog / 260.0)
        if frog_airborne and dist_frog < 160.0:
            threat = min(1.0, threat * 3.0)      # 头顶掠过的阴影 = 巨纤维的最强刺激
            self.hab = 0.0                       # 掠影无法被习惯化: 恢复全部敏感度
        elif threat > 0.3:
            self.hab = min(1.0, self.hab + dt * 0.5 * threat)   # 持续暴露→逐渐脱敏感
        else:
            self.hab = max(0.0, self.hab - dt * 0.06)           # 威胁远去→慢慢恢复敏感
        # 习惯化后的有效威胁: 蹲着不动的青蛙再近也只是"背景", 但衰减有下限,
        # 贴脸 (<~70px) 仍会触发逃逸——比脚本果蝇的 110 更难骗, 但不再永久锁死进食
        threat_eff = threat * (1.0 - 0.55 * self.hab)
        self.threat_eff = threat_eff          # 供身体层判断"现在敢不敢落地抢食"
        rest_fill = max(0.0, 1.0 - pad_dist / 40.0)
        # 歇息闸门比逃逸宽松(0.32): 习惯化后 75px 外就敢落下来吃, 但贴脸逃逸仍在
        rest_drive = 45.0 * rest_fill if (threat_eff < 0.32 and not self.resting) else 0.0

        # --- 环路更新 ---
        gf_fired = False
        if self.refractory <= 0:
            gf_fired = self.gf.step(dt, 55.0 * threat_eff)
        else:
            self.gf.step(dt, 0.0)
        osc_l = 0.5 + 0.45 * math.sin(t * self.freq + self.phase)
        osc_r = 0.5 + 0.45 * math.sin(t * self.freq + self.phase + math.pi * 0.9)
        self.cx_l.step(dt, 12.0 * osc_l)
        self.cx_r.step(dt, 12.0 * osc_r)
        rest_fired = self.rest.step(dt, rest_drive)

        self.refractory = max(0.0, self.refractory - dt)
        was_resting = self.resting

        # --- 逃逸反射：GF 一旦发放，全功率起飞逃离 ---
        if gf_fired:
            self.gf_count += 1
            self.escape_timer = 0.85
            self.refractory = 1.6
            self.resting = False
            self.rest.v = self.rest.v_reset
            self.state = self.TAKEOFF if was_resting else self.ESCAPE

        if self.escape_timer > 0:
            self.escape_timer -= dt
            turn = 0.0
            thrust = 3.1
            self.state = self.ESCAPE
        elif self.resting:
            turn = 0.0
            thrust = 0.0
            self.state = self.REST
            if threat_eff >= 0.32:               # 被惊扰：立刻起飞
                self.resting = False
                self.escape_timer = 0.4
                self.state = self.TAKEOFF
        else:
            # 中央复合体左右振荡器的支配差 → 平滑转弯；游走有惯性，不抖动
            dominance = self.cx_l.activation - self.cx_r.activation
            turn = dominance * 2.6 * self.wander_gain + random.gauss(0, 0.12)
            thrust = 1.0
            self.state = self.WANDER
            if rest_fired:
                self.resting = True
                self.rest_count += 1
                self.state = self.REST

        return {"turn": turn, "thrust": thrust, "gf_fired": gf_fired}

    def neurons(self):
        """供调试可视化：(名字, 激活度, 本帧是否发放)。"""
        return [("GF", self.gf.activation, self.gf.fired),
                ("CX_L", self.cx_l.activation, self.cx_l.fired),
                ("CX_R", self.cx_r.activation, self.cx_r.fired),
                ("REST", self.rest.activation, self.rest.fired)]


if __name__ == "__main__":
    # 单独自测：青蛙以 60px/s 逐渐逼近一只悬停的果蝇，观察巨纤维何时发放
    brain = FlyBrain(seed=1)
    dt = 1 / 60
    dist = 400.0
    for i in range(600):
        cmd = brain.step(dt, dist_frog=dist, frog_airborne=False, pad_dist=999, t=i * dt)
        dist -= 60 * dt
        if cmd["gf_fired"]:
            print(f"t={i*dt:.2f}s 距离={dist:.0f}px → 巨纤维发放，状态={brain.state}")
            break
    else:
        print("警告：巨纤维始终未发放")
