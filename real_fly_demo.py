"""真·果蝇模型：用 EPFL 的 FlyGym / NeuroMechFly 组装并渲染。

游戏里的果蝇是手写的卡通模型；这个脚本用的是**真实果蝇**的神经力学模型：
micro-CT 扫描出来的身体、带刚度和阻尼的关节、足端附着执行器、复眼相机。

用法：
    python3.12 -m venv .venv-flygym
    .venv-flygym/bin/pip install -r requirements-flygym.txt
    .venv-flygym/bin/python real_fly_demo.py

输出（默认写到 docs/images/）：
    neuromechfly.png  整只果蝇的 MuJoCo 离屏渲染
    fly-vision.png    果蝇复眼看到的世界（原始鱼眼图 + 小眼采样）
"""

from __future__ import annotations

import argparse
from pathlib import Path

import mujoco as mj
import numpy as np
from PIL import Image, ImageDraw

import flygym
from flygym import Simulation
from flygym.anatomy import AxisOrder, JointPreset, Skeleton
from flygym.compose import BlocksTerrainWorld, FlatGroundWorld, NeuroMechFly
from flygym.compose.fly.base_fly import ActuatorType
from flygym.compose.pose import KinematicPosePreset
from flygym.utils.math import Rotation3D

VISUALS_CONFIG = flygym.assets_dir / "model/neuromechfly/visuals.yaml"
NEUTRAL_QUAT = Rotation3D("quat", [1, 0, 0, 0])


def build_fly(name: str = "nmf") -> tuple[NeuroMechFly, list]:
    """按 FlyGym 2.x 的组装式 API 把真果蝇装起来。

    身体网格自带，但关节 / 执行器 / 附着 / 视觉都要显式添加：

    * ``JointPreset.ALL_BIOLOGICAL`` —— 生物学上存在的关节自由度（每条腿 11 个：
      coxa 3、trochanterfemur 2、tibia 1、tarsus1~5 各 1）；
    * 位置执行器 —— 每个自由度一个，等效于"肌肉-肌腱"的位置控制；
    * ``add_leg_adhesion()`` —— 六条腿 tarsus5 的附着执行器（0~1 抓地）；
    * ``add_vision()`` —— 左右复眼相机。
    """
    fly = NeuroMechFly(name=name)
    fly.colorize(VISUALS_CONFIG)                      # 上色：体表/翅/复眼材质
    skeleton = Skeleton(
        axis_order=AxisOrder.PITCH_YAW_ROLL,
        joint_preset=JointPreset.ALL_BIOLOGICAL,
    )
    fly.add_joints(skeleton, neutral_pose=KinematicPosePreset.NEUTRAL)
    dofs = list(skeleton.iter_jointdofs())
    fly.add_actuators(
        dofs,
        ActuatorType.POSITION,
        neutral_input=KinematicPosePreset.NEUTRAL,
        kp=1.0,
        kv=0.1,
    )
    fly.add_leg_adhesion()
    fly.add_vision()
    return fly, dofs


def build_simulation(world=None, spawn_z: float = 1.6) -> Simulation:
    """把果蝇放进世界并编译成 MuJoCo 模型。

    注意 ``add_ground_contact_sensors=False``：FlyGym 2.1.0 里逐腿接地传感器会去
    引用 ``nmf/lf_coxa`` 这类并不存在的元素名，编译时报
    "unrecognized name ... of sensorized object"（上游 bug）。关掉它即可正常编译；
    需要接触力时可以用 ``Simulation.get_bodysegment_contact_forces()`` 读接触列表。
    """
    fly, _ = build_fly()
    world = FlatGroundWorld() if world is None else world
    world.add_fly(
        fly,
        spawn_position=np.array([0.0, 0.0, spawn_z]),
        spawn_rotation=NEUTRAL_QUAT,
        add_ground_contact_sensors=False,
    )
    return Simulation(world)


def settle(sim: Simulation, steps: int = 500) -> None:
    """让果蝇从初始高度落到地面并稳定下来（不施加控制信号）。"""
    mj.mj_resetDataKeyframe(sim.mj_model, sim.mj_data, 0)
    for _ in range(steps):
        mj.mj_step(sim.mj_model, sim.mj_data)


def render_fly(sim: Simulation, width: int = 1280, height: int = 800) -> Image.Image:
    """离屏渲染整只果蝇（含阴影）。"""
    model, data = sim.mj_model, sim.mj_data
    renderer = mj.Renderer(model, height=height, width=width)
    camera = mj.MjvCamera()
    mj.mjv_defaultFreeCamera(model, camera)
    body_ids = [model.body(f"nmf/{seg}").id for seg in
                ("c_thorax", "c_head", "c_abdomen4", "lf_tibia", "rf_tibia",
                 "lm_tibia", "rm_tibia", "lh_tibia", "rh_tibia")]
    camera.lookat[:] = data.xpos[body_ids].mean(axis=0)
    camera.distance = 5.2
    camera.elevation = 21.0
    camera.azimuth = 137.0
    renderer.update_scene(data, camera=camera)
    renderer.scene.flags[mj.mjtRndFlag.mjRND_SHADOW] = True
    image = Image.fromarray(renderer.render())
    # 裁掉画面底部空场的一小条，让果蝇在 README 里占更大比例
    return image.crop((0, 0, image.width, int(image.height * 0.90)))


def render_vision(sim: Simulation) -> Image.Image:
    """拼一张"果蝇复眼看到的世界"：左边原始鱼眼图，右边小眼采样读数。"""
    sim.warmup(0.5)
    raw = sim.get_raw_vision("nmf")               # (2, H, W, 3) 左右眼
    readouts = sim.get_ommatidia_readouts("nmf")  # (2, n_ommatidia, 2) 黄型/淡型小眼
    retina = sim.retina
    hex_img = retina.hex_pxls_to_human_readable(readouts[0])   # (rows, cols, 2)

    eye = Image.fromarray(raw[0]).resize((520, 590), Image.LANCZOS)
    sparse = np.zeros((*hex_img.shape[:2], 3), dtype=np.uint8)
    sparse[..., 0] = np.clip(hex_img[..., 0], 0, 1) * 255      # 黄型小眼
    sparse[..., 1] = np.clip(hex_img[..., 1], 0, 1) * 255      # 淡型小眼
    ommatidia = Image.fromarray(sparse).resize((520, 590), Image.NEAREST)

    canvas = Image.new("RGB", (1120, 660), (14, 24, 20))
    canvas.paste(eye, (20, 50))
    canvas.paste(ommatidia, (580, 50))
    draw = ImageDraw.Draw(canvas)
    draw.text((20, 20), f"left eye camera, fisheye corrected  "
                        f"({retina.nrows}x{retina.ncols} px, 157 deg FOV)",
              fill=(210, 230, 215))
    draw.text((580, 20), f"ommatidia readout ({retina.num_ommatidia_per_eye} per eye, "
                         f"R=yellow type, G=pale type)",
              fill=(210, 230, 215))
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=Path(__file__).parent / "docs/images",
                        type=Path, help="输出目录（默认 docs/images）")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    fly_sim = build_simulation()
    model = fly_sim.mj_model
    print(f"NeuroMechFly: {model.nbody - 1} 个体节 / {model.njnt - 1} 个关节自由度 / "
          f"{model.nu} 个执行器（含 6 个足端附着）/ {model.ncam} 个复眼相机")
    settle(fly_sim)
    out = args.out_dir / "neuromechfly.png"
    render_fly(fly_sim).save(out)
    print("写入", out)

    vision_sim = build_simulation(
        BlocksTerrainWorld(height_range=(0.25, 0.25), ground_alpha=1.0)
    )
    out = args.out_dir / "fly-vision.png"
    render_vision(vision_sim).save(out)
    print("写入", out)


if __name__ == "__main__":
    main()
