"""诊断: 每 4s 打一次全池快照——每只果蝇的状态/目标/距离, 每颗食饵的血量/占用。"""
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pygame  # noqa: E402

import main  # noqa: E402

V2 = pygame.math.Vector2


def run(seconds=60.0):
    pygame.init()
    g = main.Game(headless=True)
    g.autopilot = False
    dt = 1 / 60
    crumbs = {id(c): i for i, c in enumerate(g.crumbs)}
    for fr in range(int(seconds / dt)):
        g.simulate(dt, V2(0), False, False)
        if fr % int(4 / dt) == 0:
            print(f"--- t={fr*dt:4.0f}s")
            for c in g.crumbs:
                print(f"  食饵#{crumbs[id(c)]} amount={c.amount:4.1f} "
                      f"feeders={c.feeders} inbound={len(c.inbound)} claims={c.claims}")
            for f in g.flies:
                tgt = crumbs.get(id(f.food), "-") if f.food is not None else "-"
                d = f"{f.pos.distance_to(f.food.pos()):4.0f}" if f.food else "  -"
                print(f"  {type(f).__name__:11s} {f.state_name():3s} z={f.z:4.1f} "
                      f"目标#{tgt} d={d} eat={int(f.eating_now)} "
                      f"cd={getattr(f, '_food_cd', 0):.2f} loiter={getattr(f, 'loiter_t', 0):.1f}")
    pygame.quit()


if __name__ == "__main__":
    run(float(sys.argv[1]) if len(sys.argv) > 1 else 60.0)
