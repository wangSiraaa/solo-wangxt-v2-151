"""演示用经验刀具负载模型。

重要说明
--------
本模块所有参数均为**演示取值**，未对任何真实机床/刀具做标定。
输出只是"经验负载"的相对量，用于演示从工序记录估计刀具消耗的流程，
不构成、也不能替代安全换刀决策；剩余寿命只以消耗区间（band）表达，
绝不输出精确的失效倒计时。

模型（每个切削区段独立计算，再按时间累计）
-------------------------------------------
    load_i = t_i [min] * (rpm_i / REF_RPM) ** SPEED_EXPONENT
             * k_material(workpiece_material_i) * k_interrupted_i

- 空转(idle)与暂停(pause)区段负载恒为 0，但其时长单独统计，不与切削混淆。
- 转速或工件材料变化必须切成不同区段，分别计算后累计（调用方负责切段，
  本模型按区段数组向量化计算）。
- 切削区段缺少转速或工件材料时，该区段负载为 **未知(None)**，绝不当零；
  汇总时未知区段单独列出，不参与已知负载合计。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

import numpy as np

KIND_CUT = "cut"
KIND_IDLE = "idle"
KIND_PAUSE = "pause"
KINDS = (KIND_CUT, KIND_IDLE, KIND_PAUSE)

# ---- 演示参数（非真实标定值，仅供演示流程使用） ----
REF_RPM = 8000.0            # 参考转速
SPEED_EXPONENT = 4.0        # 速度经验指数（Taylor 类，演示值）
INTERRUPTED_FACTOR = 1.25   # 断续切削冲击系数（演示值）
MATERIAL_FACTORS = {        # 工件材料系数（演示值）
    "aluminum": 0.6,
    "steel": 1.0,
    "stainless": 1.5,
    "titanium": 2.2,
}

MODEL_ID = "demo-empirical-v1"

DISCLAIMER = (
    "演示经验模型，参数未标定；结果仅反映相对消耗区间，"
    "不构成安全换刀依据，也不是失效倒计时。"
)


@dataclass
class SegmentInput:
    """参与负载计算的区段视图（与持久层解耦）。"""

    kind: str
    started_at: datetime
    ended_at: datetime
    rpm: Optional[float] = None
    workpiece_material: Optional[str] = None
    interrupted: bool = False


def compute_loads(segments: Sequence[SegmentInput]) -> list[Optional[float]]:
    """向量化计算每个区段的负载。

    返回与输入等长的列表：切削段为负载值，缺工况的切削段为 None（未知），
    空转/暂停段为 0.0。
    """
    n = len(segments)
    if n == 0:
        return []

    kinds = np.array([s.kind for s in segments], dtype=object)
    duration_min = np.array(
        [(s.ended_at - s.started_at).total_seconds() / 60.0 for s in segments],
        dtype=float,
    )
    rpm = np.array(
        [s.rpm if s.rpm is not None else np.nan for s in segments], dtype=float
    )
    material = np.array(
        [MATERIAL_FACTORS.get(s.workpiece_material or "", np.nan) for s in segments],
        dtype=float,
    )
    impact = np.where(
        np.array([bool(s.interrupted) for s in segments]), INTERRUPTED_FACTOR, 1.0
    )

    is_cut = kinds == KIND_CUT
    with np.errstate(invalid="ignore"):
        loads = (
            duration_min
            * np.power(rpm / REF_RPM, SPEED_EXPONENT)
            * material
            * impact
        )
    # 空转/暂停不产生刀具负载
    loads = np.where(is_cut, loads, 0.0)

    # 切削段缺转速或材料 -> 未知负载（None），不是零
    unknown = is_cut & (np.isnan(rpm) | np.isnan(material))
    return [None if u else float(v) for u, v in zip(unknown, loads)]


def summarize(loads: Sequence[Optional[float]]) -> dict:
    """已知负载合计 + 未知区段数。未知不计入合计。"""
    arr = np.array([v for v in loads if v is not None], dtype=float)
    return {
        "total_known_load": float(arr.sum()) if arr.size else 0.0,
        "unknown_count": sum(1 for v in loads if v is None),
    }


def consumption_band(ratio_known: Optional[float]) -> tuple[str, str]:
    """把已知消耗比例映射为定性的消耗区间（不是剩余寿命倒计时）。"""
    if ratio_known is None:
        return "unknown", "无法估计（无已知负载）"
    if ratio_known < 0.5:
        return "low", "低消耗"
    if ratio_known < 0.8:
        return "medium", "中等消耗"
    if ratio_known < 1.0:
        return "high", "高消耗，建议按计划检查"
    return "beyond", "已达经验上限，模型不再外推"
