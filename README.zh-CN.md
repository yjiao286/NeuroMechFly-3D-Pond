# 🧠 NeuroMechFly-3D-Pond

**约 2.3k 行 Python 实现的 60 FPS 无引擎 3D 池塘**——自行完成投影、着色与光栅化，不用
OpenGL，除源码外不依赖任何美术/音频素材文件。池塘里有 9 个果蝇智能体，其中**恰好 1 个**由
泄漏整合发放（LIF）脉冲神经网络驱动（巨纤维逃逸、中央复合体转向、歇息回路控制进食）。
同一仓库还通过 MuJoCo 组装、仿真并渲染**真实的 NeuroMechFly** 身体模型——micro-CT 网格、
126 个转动自由度、复眼读数。

![python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![pygame-ce](https://img.shields.io/badge/pygame--ce-2.5-6cbf4a)
![renderer](https://img.shields.io/badge/3D%20renderer-software%20rasteriser-2f6f5f)
![loc](https://img.shields.io/badge/code-~2.3k%20lines-informational)
![FlyGym](https://img.shields.io/badge/FlyGym-NeuroMechFly%202.1%20%2F%20MuJoCo%203.9-8a5cf6)
![license](https://img.shields.io/badge/license-MIT-4c8f4c)

[English](README.md) · [简体中文](README.zh-CN.md)

![NeuroMechFly：用 micro-CT 网格在 MuJoCo 中渲染的真实果蝇模型](docs/images/neuromechfly.png)

*[`real_fly_demo.py`](real_fly_demo.py) 组装出的模型在 MuJoCo 中的离屏渲染：70 个体节、
126 个转动自由度、132 个执行器（126 位置 + 6 足端附着）、2 个复眼相机。不是游戏素材，
详见 [§5](#5-真果蝇flygym--neuromechfly-流水线)。*

![果蝇复眼读数：鱼眼相机视图与 721 小眼阵列](docs/images/fly-vision.png)

*同一模型的视觉接口。左：左眼相机（512 × 450 px，视场角 157°，已做鱼眼校正）。
右：同一帧重采样到六边形小眼阵列——每只眼 721 个小眼——并按小眼类型拆分
（R = 黄型，G = 淡型）。视网膜真正输出给大脑的是这张图，而不是一张矩形图像。*

---

## 目录

| 章节 | 内容 |
|---|---|
| 1 | [系统总览](#1-系统总览) |
| 2 | [脉冲神经智能体](#2-脉冲神经智能体) |
| 3 | [渲染器](#3-渲染器) |
| 4 | [场景、建模与材质](#4-场景建模与材质) |
| 5 | [真·果蝇：FlyGym / NeuroMechFly 流水线](#5-真果蝇flygym--neuromechfly-流水线) |
| 6 | [相关工作](#6-相关工作) |
| 7 | [构建、运行、操作](#7-构建运行操作) |
| 8 | [验证](#8-验证) |
| 9 | [已知限制](#9-已知限制) |

---

## 1 · 系统总览

仓库里是两套彼此独立的栈：A 栈是游戏本体——确定性仿真循环 + 手写软件光栅器；
B 栈是 FlyGym 桥接层——组装科学计算用的 MuJoCo 模型并离线渲染。两者除
`V3 = pygame.math.Vector3` 外不共享代码。

| 子系统 | 文件 | 行数 | 职责 |
|---|---|---|---|
| 主循环 / 智能体 / HUD | [`main.py`](main.py) | 1016 | 60 FPS 定步长循环、青蛙运动学、两类果蝇、HUD、无头自测 |
| 软件渲染器 | [`render3d.py`](render3d.py) | 333 | `Camera3D` 透视投影、`Painter` 深度排序、材质图元 |
| 神经智能体 | [`fly_brain.py`](fly_brain.py) | 155 | LIF 神经元、GF / CX / REST 环路、感觉整合、运动指令 |
| 场景 | [`scenery.py`](scenery.py) | 654 | 水面、焦散、涟漪、荷叶、食饵、岸景、布景、天空、按机位缓存 |
| 音频 | [`sounds.py`](sounds.py) | 127 | 22.05 kHz 程序化合成（蛙鸣/落水/咔嚓/振翅/吐舌） |
| FlyGym 桥 | [`real_fly_demo.py`](real_fly_demo.py) | 166 | 在 MuJoCo 中组装 NeuroMechFly，渲染本体与视网膜读数 |

运行期约定：窗口 1280 × 800（`SCALED │ RESIZABLE`），帧率上限 60 FPS，单步 dt 上限 1/20 s
（防卡顿后穿模），世界单位为池塘单位，所有随机布局均带固定种子。

## 2 · 脉冲神经智能体

`fly_brain.py` 实现 `dv/dt = (−(v − v_rest) + I)/τ` 的阈值复位动力学，为数值稳定按 τ/2
细分子步积分。默认参数 `v_rest = −70 mV`、`v_thr = −52 mV`、`v_reset = −76 mV`；发放即
`fired` 置位一帧，`activation ∈ [0,1]` 是归一化"距阈值距离"——也就是 `B` 面板里画的条。

| 环路 | 对应生物学结构 | τ | 驱动量 | 触发条件 |
|---|---|---|---|---|
| **GF** 巨纤维 | 逃逸反射 | 0.05 s | `55 · max(0, 1 − d_frog/260)`，跳跃时 ×3 | 威胁进入约 170 单位或头顶有跳跃阴影 → 0.85 s 逃逸推力 ×3.1，随后 1.6 s 不应期 |
| **CX_L / CX_R** 中央复合体 | 航向控制 | 0.25 s | 两个慢振荡器竞争 | 以幅度差读出，产生平滑非周期的巡航 |
| **REST** 歇息/觉醒 | 进食闸门 | 0.6 s | 悬停且威胁 < 0.25 时 `45 · rest_fill` | 超阈值才释放降落动作；2.5 s 没吃到或歇满 12 s 主动起飞 |
| 气味趋向 | 趋化 | — | 朝最近食饵偏航 | 饥饿状态 |

其余 8 个智能体（`ScriptedFly`）是有限状态航点控制器：觅食 → 降落 → 啃食，110 单位内逃跑
（260 单位后解除）。神经元个体约 170 单位就逃——这就是"阈值检测器"与"脚本触发器"的可观测差异。

| | `ScriptedFly` | `BrainFly` |
|---|---|---|
| 决策来源 | 状态机 + 调参 | LIF 膜电位 |
| 逃逸触发 | 距离 < 110 | GF 越阈（约 170） |
| 进食 | 计时器 | REST 闸门 |
| 可观测性 | 无 | `B` 面板逐神经元实时激活 |
| 池塘中数量 | 8 | 1（金色方框标注） |

## 3 · 渲染器

无 GPU、无深度缓冲。流水线为：世界坐标 → 视空间变换 → 近平面裁剪（Sutherland–Hodgman，
`NEAR = 14`）→ 透视投影（`focal = 1050 px`）→ 延迟提交给 painter → 逐层深度排序 → 光栅化。
多边形以闭包形式入队、由 `Painter.flush` 统一执行，这正是能做显式排序控制的原因。

| 层 | 内容 | 排序规则 |
|---|---|---|
| 0 `WATER` | 水体（40 × 8 色格） | 平面物，恒最先 |
| 1 `FX` | 细波纹、焦散、岸边泡沫、涟漪、浮萍 | 只在水面之上 |
| 2 `PAD` | 预留给荷叶贴花 | — |
| 3 `BANK` | 堤壁、草地、卵石、岩石、灌木（缓存）、芦苇、草丛 | 深度排序，静态物按机位缓存 |
| 4 `MAIN` | 青蛙、果蝇、粒子、舌头 | 深度排序，最后绘制 |

**为什么必须分层。** 用"多边形平均深度"把小型生物与大片地面一起排序，只要生物站在地面
多边形的远半侧就会被整片地面吞掉。拆成"纯平面恒垫底"与"立体物按深度排序"两层即可消除该
失效模式；同层的共面部件（叶面/叶脉/叶背、影子与本体）再用显式 `bias` 错开。

**材质。** 全场景共用一盏世界空间主光（`LIGHT_XY`），镜头旋转时高光方向保持一致：

| 图元 | 用途 | 着色模型 |
|---|---|---|
| `sphere()` | 灌木、岩石、复眼、食饵、关节 | 边缘压暗 → 向光侧内缩提亮 → 镜面点 → 描边；小半径 LOD |
| `dome()` | 蛙背/头、荷叶、卵石、翅面 | 边缘压暗 → 向光侧内缩高光 → 光泽 |
| `soft_shadow()` | 所有落地物 | 预生成径向衰减贴图按投影椭圆缩放 |
| `flat_polygon()` / `segment()` / `polyline()` | 水面、叶脉、茎、舌头 | 平涂 + 深度偏置 |

实测开销：整池尽收眼底时，无头软件渲染 **≈ 17.7 ms/帧**（1280 × 800 dummy SDL，
Python 3.14 + pygame-ce 2.5.8）。静态岸景图层只在机位变化时重绘，这是主要的性能余量来源。

## 4 · 场景、建模与材质

所有几何在运行时由带种子的 RNG 生成，仓库内没有任何网格、贴图或音频文件。

| 元素 | 建模 | 贴图 / 着色 |
|---|---|---|
| 水面 | 池塘平面 ±620 × ±350，活动区 ±545 × ±285 | 三层：大气渐变（40 × 8 格）+ 漂移细波纹短划 + 焦散闪点；岸边泡沫环 |
| 荷叶 | 6 片，半径 46–66，缺口 + 叶脉 + 可选荷花，按 `sin(1.2t + φ) · 1.6` 起伏 | 放射状渐细叶脉、近侧叶缘高光、叶背厚度、柔光接触影 |
| 青蛙 | 圆顶身体 + 头、6 条关节腿（各 3 趾）、金眼 + 瞳孔高光、7 颗圆珠串联的舌头 | 背脊高光带、迷彩斑点、腹部阴影、接触影 |
| 果蝇 | 三节腹部、带刚毛的胸部、头 + 触角 + 平衡棒、6 条两段式腿、带翅脉双翅 | 逐部件着色、翅脉与前缘高光、柔光阴影、落地时半透明收翅 |
| 岸景 | 4 面堤壁 × 8 段、44 单位岸顶、草地延伸至 2600 单位 | 湿沙→干沙渐变、卵石、岩石斑点、灌木簇、雾化草地、飘动云、地平线暖光 |

姿态与步态覆盖四种果蝇状态：飞行（振翅、收腿）、行走（三角步态，步频 ∝ 速度）、
进食（口器对着食饵、前足搓动）、梳洗（前足在复眼上画圈 1.2 s，即真实果蝇的清洁行为）。

## 5 · 真·果蝇：FlyGym / NeuroMechFly 流水线

[`real_fly_demo.py`](real_fly_demo.py) 通过 FlyGym 2.x 的组装式 API 构建 EPFL 的
[NeuroMechFly](https://neuromechfly.org) 并离屏渲染——本文最上方两张图就是它的输出。

| 阶段 | API 调用 | 结果 |
|---|---|---|
| 身体 | `NeuroMechFly()` + `colorize(visuals.yaml)` | 70 个 micro-CT 体节，应用材质 |
| 关节 | `Skeleton(AxisOrder.PITCH_YAW_ROLL, JointPreset.ALL_BIOLOGICAL)` + `add_joints()` | 126 个转动自由度，逐关节刚度/阻尼/armature |
| 驱动 | `add_actuators(..., ActuatorType.POSITION)` | 126 个位置执行器（肌肉代理） |
| 附着 | `add_leg_adhesion()` | 6 个 tarsus5 附着执行器，`ctrl ∈ [0,1]` |
| 视觉 | `add_vision()` | 2 个复眼相机 → `get_raw_vision()` 与 `get_ommatidia_readouts()` |
| 物理 | `FlatGroundWorld` / `BlocksTerrainWorld` + `Simulation` | MuJoCo 3.9，1 ms 步长 |

暴露给控制器的接口（即神经环路可读写的通道）：

| 方向 | 通道 | 分辨率 |
|---|---|---|
| 感觉 | 视觉 | 2 × 721 个小眼，黄型/淡型分离，单眼视场角 157°，含鱼眼模型 |
| 感觉 | 本体感觉 | 全部 126 个自由度的关节角与角速度 |
| 感觉 | 机械感觉 | 逐体节接触力（简单世界中含逐腿接地传感器） |
| 运动 | 驱动 | 126 路关节目标 + 6 路附着通道 |

```bash
python3.12 -m venv .venv-flygym                  # FlyGym 2.1.0 需要 Python 3.12+
.venv-flygym/bin/pip install -r requirements-flygym.txt
.venv-flygym/bin/python real_fly_demo.py         # → docs/images/{neuromechfly,fly-vision}.png
```

```
NeuroMechFly: 70 个体节 / 126 个关节自由度 / 132 个执行器（含 6 个足端附着）/ 2 个复眼相机
```

首次运行主要耗时在视网膜通路的 Numba JIT（约 1 分钟），之后渲染只需数秒。
`pip install "flygym[warp]"` 可启用 MuJoCo-Warp 批量仿真后端；`"flygym[rl]"` 提供
Gymnasium + Stable-Baselines3，用于训练运动策略。

### 游戏智能体 vs 科学模型

| | 游戏果蝇（[`fly_brain.py`](fly_brain.py)） | NeuroMechFly |
|---|---|---|
| 身体 | 数十个运行时生成的多边形 | 70 个 micro-CT 网格 |
| 关节 | 运动学式、脚本步态 | 126 个带关节动力学的自由度 |
| 控制器 | 4 个手工整定 LIF 环路 | 由使用者提供的环路 / CPG / RL 策略 |
| 视觉 | 距离判定 | 每只眼 721 个小眼的视网膜 |
| 步长 | 1/60 s，纯 Python | 1 ms，MuJoCo |
| 用途 | 交互与教学 | 感运动神经科学 |

## 6 · 相关工作

脉冲智能体的背景：*Drosophila* 是目前唯一拥有完整成体大脑接线图的动物，而这些接线图正在
被接入仿真身体。

| 工作 | 贡献 | 链接 |
|---|---|---|
| FlyWire 全脑连接组 | 139,255 个神经元、约 5 × 10⁷ 个化学突触 | [Dorkenwald 等，*Nature* 634, 124–138 (2024)](https://doi.org/10.1038/s41586-024-07558-y) · [flywire.ai](https://flywire.ai) · [注释仓库](https://github.com/flyconnectome/flywire_annotations) |
| 全脑注释与细胞分型 | 细胞类型与跨个体一致性 | [Schlegel 等，*Nature* 634, 139–152 (2024)](https://doi.org/10.1038/s41586-024-07686-5) |
| 计算整脑模型 | 由连接组约束、复现糖感知与进食 | [Shiu 等，*Nature* 634, 210–219 (2024)](https://doi.org/10.1038/s41586-024-07763-9) |
| 半脑（Hemibrain）连接组 | 开启该领域的中央脑重建 | [Scheffer 等，*eLife* 9:e57443 (2020)](https://doi.org/10.7554/eLife.57443) |
| 雄性腹神经索连接组 | 下行指令 → 运动输出 | [Takemura 等，*eLife* (2024)](https://doi.org/10.7554/eLife.97769) |
| **NeuroMechFly v2 / FlyGym** | 有身体的神经力学模型——本仓库安装并渲染的即此 | [Wang-Chen 等，*Nature Methods* 21, 2353–2362 (2024)](https://doi.org/10.1038/s41592-024-02497-y) · [neuromechfly.org](https://neuromechfly.org) · [NeLy-EPFL/flygym](https://github.com/NeLy-EPFL/flygym) |
| FlyBody | 全身物理仿真 + 连接组推导的神经肌肉接线 | [Vaxenburg 等，*Nature* (2025)](https://doi.org/10.1038/s41586-025-09029-4) · [TuragaLab/flybody](https://github.com/TuragaLab/flybody) |
| 连接组约束深度机制网络 | 由视叶接线预测单神经元反应 | [Lappalainen 等，*Nature* 634 (2024)](https://doi.org/10.1038/s41586-024-07939-3) · [TuragaLab/flyvis](https://github.com/TuragaLab/flyvis) |

## 7 · 构建、运行、操作

```bash
git clone https://github.com/yjiao286/NeuroMechFly-3D-Pond.git
cd NeuroMechFly-3D-Pond

python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip

.venv/bin/python main.py
```

需要 Python 3.10+（开发环境 3.14）；依赖仅 `pygame-ce` 与 `numpy`。

| 输入 | 功能 | 实现细节 |
|---|---|---|
| 方向键 / `WASD` | 游动 | 速度 175 单位/秒，朝向按指数转向率平滑 |
| `空格` | 跳跃 | 0.62 s 内跨越 310 单位、抛物线峰值 62 单位；落点 60 单位内压杀 |
| 单击左键 / `F` | 吐舌 | 射程 200、舌尖捕获半径 17、锥角 ±1.15 rad、周期 0.30 s |
| 按住左键拖动 | 平移视角 | 抓取式平移，速度随缩放自适应 |
| 按住右键拖动 | 旋转视角 | 方位角自由，俯仰限制 18°–62° |
| 滚轮 | 缩放 | 距离限制 420–3200，默认 1526 |
| `B` | 神经面板 | GF / CX_L / CX_R / REST 激活条 + 图例 |
| `M` / `F11` / `Esc` | 静音 / 全屏 / 退出 | — |

## 8 · 验证

```bash
.venv/bin/python main.py --selftest      # 1800 帧无头自动驾驶，断言至少吃到 1 只
.venv/bin/python fly_brain.py            # 单环路探针：打印 GF 发放时刻与距离
```

```
[selftest] 吃掉=5 跳跃=1 存活=5 神经元果蝇GF逃逸反射=0次
[selftest] PASS ✓
t=3.80s 距离=171px → 巨纤维发放，状态=逃逸
```

自测在 `SDL_VIDEODRIVER=dummy` 下以脚本策略驱动青蛙，因此无需显示器即可覆盖渲染与物理
路径。上文帧耗时数据用同样方式测得（`Game.draw()` 循环 60 次、缓存预热后取均值）。

## 9 · 已知限制

- **界面文案为中文**。字体依次在 macOS 系统字体与 `pygame.font.match_font` 中解析；
  在 Windows / Linux 上请把中文字体 `.ttf` 加入 `FONT_PATHS`（`main.py:39`）。
- **音效为尽力而为**：由 numpy 合成后经 `pygame.sndarray` 播放，无音频设备时静音运行。
- **游戏智能体是行为模型，不是生物物理模型**：4 个手工整定的 LIF 环路，为可读性与交互
  延迟取舍。科学模型见 [§5](#5-真果蝇flygym--neuromechfly-流水线)。
- **FlyGym 2.1.0 的传感器命名缺陷**：逐腿接地传感器引用了不存在的元素名，因此
  `real_fly_demo.py` 以 `add_ground_contact_sensors=False` 组装；接触力仍可通过
  `get_bodysegment_contact_forces()` 获取。属上游问题。
- 已在 macOS 验证（Python 3.14 + pygame-ce 2.5.8；FlyGym 2.1.0 / MuJoCo 3.9 跑在
  Python 3.12 上），其余平台未测试。

## 许可证

[MIT](LICENSE)。文中引用的第三方项目与数据集遵循各自的许可协议。
