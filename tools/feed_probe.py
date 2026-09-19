"""无头进食探针 v4：青蛙不动，量化进食竞争的全貌。

用法: python tools/feed_probe.py [秒数]
指标:
  bite秒/顿数     累计进食时长与用餐次数(顿数多=周转快)
  吃过的果蝇      至少进食过一次的果蝇数
  等位时长        "目标满座且距食饵<45px 非进食"的连续区段: 条数 / 均值 / 最大
  僵局区段        某食饵"有认领者滞留(≤40px)却 0 进食"连续 >8s 的次数
"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame  # noqa: E402

import main  # noqa: E402

V2 = pygame.math.Vector2


def run(seconds=240.0):
    pygame.init()
    g = main.Game(headless=True)
    g.autopilot = False                      # 青蛙蹲着不动: 纯看果蝇觅食竞争
    dt = 1 / 60
    frames = int(seconds / dt)
    bite_time = 0.0
    meals = 0
    ate = set()
    bf_bite = 0.0
    bf_meals = 0
    yields_total = 0
    yields_seen = {}                          # id(fly) -> 上次读到的 yielded
    bf = g.brain_fly()
    bf_states = {}                            # 神经元个体各状态时长
    for f in g.flies:
        bf_states[f.state_name() if f is bf else ""] = 0.0
    was_eating = {}                          # id(fly) -> 上帧是否 eating_now
    loiter = {}                              # id(fly) -> 当前连续等位时长
    loiter_spans = []
    idle = {id(c): 0.0 for c in g.crumbs}    # 每颗食饵"有认领者滞留但0进食"连续时长
    deadlocks = 0
    overfill_frames = 0                      # 同一食饵上 eating_now 超过 CAPACITY 的帧数
    max_on_crumb = 0
    for _ in range(frames):
        g.simulate(dt, V2(0), False, False)
        bf = g.brain_fly()
        if bf is not None:
            bf_states[bf.state_name()] = bf_states.get(bf.state_name(), 0.0) + dt
        for f in g.flies:
            key = id(f)
            if isinstance(f, main.ScriptedFly):
                cur = f.yielded
                yields_total += cur - yields_seen.get(key, 0)
                yields_seen[key] = cur
            if f.eating_now:
                bite_time += dt
                ate.add(key)
                if not was_eating.get(key, False):
                    meals += 1
                    if f is bf:
                        bf_meals += 1
                was_eating[key] = True
                if f is bf:
                    bf_bite += dt
                loiter[key] = 0.0
                continue
            was_eating[key] = False
            if f.food is not None and f.food.full(f) and f.pos.distance_to(f.food.pos()) < 45:
                loiter[key] = loiter.get(key, 0.0) + dt
            else:
                if loiter.get(key, 0.0) > 0.4:
                    loiter_spans.append(loiter[key])
                loiter[key] = 0.0
        for c in g.crumbs:
            on = sum(1 for f in g.flies if f.food is c and f.eating_now)
            max_on_crumb = max(max_on_crumb, on)
            if on > c.CAPACITY:
                overfill_frames += 1
            claimers = [f for f in g.flies if f.food is c]
            feeding = any(f.eating_now for f in claimers)
            near = [f for f in claimers if f.pos.distance_to(c.pos()) < 40]
            if near and not feeding:
                idle[id(c)] += dt
                if idle[id(c)] > 8.0:
                    deadlocks += 1
                    idle[id(c)] = 0.0
            else:
                idle[id(c)] = 0.0
    if any(v > 0.4 for v in loiter.values()):
        loiter_spans.extend(v for v in loiter.values() if v > 0.4)
    loiter_spans.sort()
    mean_lo = sum(loiter_spans) / len(loiter_spans) if loiter_spans else 0.0
    max_lo = loiter_spans[-1] if loiter_spans else 0.0
    med_lo = loiter_spans[len(loiter_spans) // 2] if loiter_spans else 0.0
    pygame.quit()
    print(f"bite秒={bite_time:6.1f} 顿数={meals:4d} 吃过的果蝇={len(ate)}/9 "
          f"单饵最多同时进食={max_on_crumb} 超员帧={overfill_frames}")
    share = 100.0 * bf_bite / bite_time if bite_time > 0 else 0.0
    states = " ".join(f"{k}={v:.0f}s" for k, v in sorted(bf_states.items()) if k)
    print(f"神经元个体: 进食={bf_bite:5.1f}s {bf_meals:3d}顿 (占总进食 {share:.0f}%, 9只均值为11%)"
          f"   脚本个体让位={yields_total}次")
    print(f"神经元个体状态: {states}")
    print(f"等位: {len(loiter_spans):4d} 段, 中位={med_lo:4.1f}s 均值={mean_lo:4.1f}s "
          f"最长={max_lo:4.1f}s   僵局区段(>8s)={deadlocks}")


if __name__ == "__main__":
    run(float(sys.argv[1]) if len(sys.argv) > 1 else 240.0)
