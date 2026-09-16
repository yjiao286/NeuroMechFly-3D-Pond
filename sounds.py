"""程序化音效：蛙鸣、落水、咔嚓、振翅嗡嗡声 —— 全部用 numpy 合成，无需素材文件。"""

from __future__ import annotations

import numpy as np
import pygame

SR = 22050


def _to_sound(wave, gain=0.55):
    peak = np.max(np.abs(wave)) or 1.0
    pcm = (wave / peak * 32767 * gain).astype(np.int16)
    return pygame.sndarray.make_sound(np.ascontiguousarray(pcm))


def _croak():
    """三声低频蛙鸣，带 30Hz 颤音。"""
    out = np.zeros(int(0.52 * SR))
    for k, start in enumerate((0.0, 0.17, 0.34)):
        n = int(0.15 * SR)
        t = np.arange(n) / SR
        f = 96 + 10 * k + 14 * np.sin(2 * np.pi * 30 * t)
        phase = 2 * np.pi * np.cumsum(f) / SR
        env = np.exp(-t * 20) * np.minimum(1.0, t * 400)
        out[int(start * SR):int(start * SR) + n] += np.sin(phase) * env * 0.9
    return out


def _splash():
    """低频闷响 + 滤波噪声，模拟落水。"""
    n = int(0.42 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(3)
    noise = np.convolve(rng.standard_normal(n), np.ones(9) / 9, mode="same")
    f = np.linspace(175, 65, n)
    thump = np.sin(2 * np.pi * np.cumsum(f) / SR)
    return noise * np.exp(-t * 8) * 0.7 + thump * np.exp(-t * 10) * 0.65


def _crunch():
    """短促的咔嚓：噪声脆响 + 高频泛音。"""
    n = int(0.09 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(n) * np.exp(-t * 55)
    ping = np.sin(2 * np.pi * 880 * t) * np.exp(-t * 70)
    return noise * 0.85 + ping * 0.4


def _buzz():
    """振翅嗡嗡声：190Hz 谐波 + 28Hz 幅度颤动；整 190 个周期保证无缝循环。"""
    n = SR  # 1 秒
    t = np.arange(n) / SR
    wave = (0.6 * np.sin(2 * np.pi * 190 * t)
            + 0.25 * np.sin(2 * np.pi * 380 * t)
            + 0.12 * np.sin(2 * np.pi * 570 * t))
    tremolo = 0.72 + 0.28 * np.sin(2 * np.pi * 28 * t)
    return wave * tremolo


def _tongue():
    """吐舌：由低到高的快速扫频 + 一点脆响。"""
    n = int(0.09 * SR)
    t = np.arange(n) / SR
    f = np.linspace(280, 950, n)
    sweep = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 26)
    rng = np.random.default_rng(5)
    click = rng.standard_normal(n) * np.exp(-t * 90)
    return sweep * 0.8 + click * 0.3


class SoundKit:
    """所有方法在混音器不可用或静音时自动变为无操作。"""

    def __init__(self, enabled=True):
        self.ok = False
        self.muted = False
        self._buzz_ch = None
        if not enabled:
            return
        try:
            pygame.mixer.init(frequency=SR, size=-16, channels=1, buffer=512)
            self.croak = _to_sound(_croak(), 0.5)
            self.splash = _to_sound(_splash(), 0.45)
            self.crunch = _to_sound(_crunch(), 0.6)
            self.tongue = _to_sound(_tongue(), 0.5)
            self.buzz = _to_sound(_buzz(), 0.5)
            self.ok = True
        except Exception:
            self.ok = False

    def toggle_mute(self):
        self.muted = not self.muted
        if self.ok and self.muted:
            pygame.mixer.set_volume(0.0)
        elif self.ok:
            pygame.mixer.set_volume(1.0)
        return self.muted

    def croak_(self):
        if self.ok:
            self.croak.play()

    def splash_(self):
        if self.ok:
            self.splash.play()

    def crunch_(self):
        if self.ok:
            self.crunch.play()

    def tongue_(self):
        if self.ok:
            self.tongue.play()

    def set_buzz(self, level):
        """level ∈ [0,1]：离得最近的飞行果蝇有多近，越近嗡嗡声越大。"""
        if not self.ok or self.muted:
            return
        if level > 0.03:
            if self._buzz_ch is None or not self._buzz_ch.get_busy():
                self._buzz_ch = self.buzz.play(loops=-1, fade_ms=300)
            if self._buzz_ch:
                self._buzz_ch.set_volume(min(1.0, level))
        elif self._buzz_ch and self._buzz_ch.get_busy() and level < 0.01:
            self._buzz_ch.fadeout(600)
