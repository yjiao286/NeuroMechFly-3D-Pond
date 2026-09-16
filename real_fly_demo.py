"""真·果蝇模型（NeuroMechFly 2.1.0 + MuJoCo）渲染验证。

用法: .venv-flygym/bin/python real_fly_demo.py
输出: real_fly.png —— EPFL NeuroMechFly 形态仿真果蝇（简化网格版）静置在平面上的侧视渲染。
"""

from pathlib import Path

import numpy as np
from flygym import Renderer, Simulation
from flygym.compose import FlatGroundWorld, NeuroMechFly
from flygym.utils.math import Rotation3D

OUT = Path(__file__).parent / "real_fly.png"

world = FlatGroundWorld()
fly = NeuroMechFly()
world.add_fly(fly, spawn_position=np.array([0.0, 0.0, 1.0]),
              spawn_rotation=Rotation3D("quat", [1, 0, 0, 0]))
sim = Simulation(world)
sim.reset()

# 让物理跑一小段，使果蝇稳定落在地面上
for _ in range(300):
    sim.step()

cameras = [c.name for c in world.mj_model.cameras] if hasattr(world, "mj_model") else []
print("可用相机:", cameras[:8])
cam = next((c for c in cameras if "side" in c.lower()), cameras[0] if cameras else "side")
renderer = Renderer(world.mj_model, cameras=[cam], camera_res=(600, 800))
img = renderer.render(sim.mj_data, cam)
if isinstance(img, list):
    img = img[0]
from PIL import Image
Image.fromarray(img).save(OUT)
print("已保存:", OUT)
