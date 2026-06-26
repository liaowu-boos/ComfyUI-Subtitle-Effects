"""
缓动曲线库 - 所有动效节点共享
所有函数输入 t ∈ [0, 1]，输出变换系数
"""
import math


def linear(t: float) -> float:
    return max(0.0, min(1.0, t))


def clamp01(t: float) -> float:
    return max(0.0, min(1.0, t))


# ---------------------------------------------------------------------------
# Spring / Overshoot  (动效 01, 05)
# ---------------------------------------------------------------------------

def spring_overshoot(t: float, decay: float = 8.0, freq: float = 4.0) -> float:
    """
    阻尼回弹：f(t) = 1 - exp(-decay * t) * cos(freq * π * t)
    t=0 → 0,  t→1 → 1  (带超调后回弹)
    """
    t = clamp01(t)
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return 1.0 - math.exp(-decay * t) * math.cos(freq * math.pi * t)


def spring_scale(t: float, overshoot: float = 1.3,
                 decay: float = 8.0, freq: float = 4.0) -> float:
    """
    用于 Scale 变换的弹簧函数：0 → overshoot → 1.0
    """
    raw = spring_overshoot(t, decay, freq)
    # raw 在 0→1 之间会超过 1 然后回来
    # 我们映射到 0→overshoot→1
    if t >= 1:
        return 1.0
    return raw * (1.0 + (overshoot - 1.0) * (1.0 - t))


def spring_rotation(t: float, start_deg: float = -15.0, overshoot_deg: float = 5.0,
                    decay: float = 6.0, freq: float = 3.0) -> float:
    """
    旋转弹簧：start_deg → overshoot_deg → 0
    返回角度值（度）
    """
    t = clamp01(t)
    if t >= 1:
        return 0.0
    damped = math.exp(-decay * t) * math.cos(freq * math.pi * t)
    return start_deg * damped


# ---------------------------------------------------------------------------
# Cubic Bezier  (动效 02, 03, 04)
# ---------------------------------------------------------------------------

def _bezier_sample(t: float, p1: float, p2: float) -> float:
    """单轴三次贝塞尔采样 B(t) = 3(1-t)^2*t*p1 + 3(1-t)*t^2*p2 + t^3"""
    u = 1.0 - t
    return 3.0 * u * u * t * p1 + 3.0 * u * t * t * p2 + t * t * t


def cubic_bezier(t: float, x1: float, y1: float, x2: float, y2: float,
                 iterations: int = 16) -> float:
    """
    CSS cubic-bezier(x1, y1, x2, y2) 的 Python 实现
    通过二分法求解 x(s)=t 对应的 s，再返回 y(s)
    """
    t = clamp01(t)
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0

    # 二分法求 s 使得 bezier_x(s) ≈ t
    lo, hi = 0.0, 1.0
    for _ in range(iterations):
        mid = (lo + hi) * 0.5
        x_mid = _bezier_sample(mid, x1, x2)
        if x_mid < t:
            lo = mid
        else:
            hi = mid
    s = (lo + hi) * 0.5
    return _bezier_sample(s, y1, y2)


def ease_out(t: float) -> float:
    """平滑缓出 cubic-bezier(0.25, 1, 0.5, 1)"""
    return cubic_bezier(t, 0.25, 1.0, 0.5, 1.0)


def ease_out_expo(t: float) -> float:
    """指数急停 cubic-bezier(0.16, 1, 0.3, 1)"""
    return cubic_bezier(t, 0.16, 1.0, 0.3, 1.0)


def ease_in_out_hard(t: float) -> float:
    """硬性缓入缓出 cubic-bezier(0.87, 0, 0.13, 1)"""
    return cubic_bezier(t, 0.87, 0.0, 0.13, 1.0)


# v1.3 暴露给 SpeedStretch easing_curve 下拉的命名别名：避免节点里写实现
def ease_out_cubic(t: float) -> float:
    """平滑缓出 cubic-bezier(0.25, 1, 0.5, 1)，等同 ease_out。"""
    return cubic_bezier(t, 0.25, 1.0, 0.5, 1.0)


def ease_out_quint(t: float) -> float:
    """更陡的缓出 cubic-bezier(0.22, 1, 0.36, 1)，接近指数。"""
    return cubic_bezier(t, 0.22, 1.0, 0.36, 1.0)


def ease_in_out_cubic(t: float) -> float:
    """对称缓入缓出 cubic-bezier(0.65, 0, 0.35, 1)。"""
    return cubic_bezier(t, 0.65, 0.0, 0.35, 1.0)


# ---------------------------------------------------------------------------
# 工具：将 progress 映射到分段时间线
# ---------------------------------------------------------------------------

def remap(t: float, t_start: float, t_end: float) -> float:
    """将全局 t 映射到 [t_start, t_end] 区间内的局部 progress (0-1)"""
    if t_end <= t_start:
        return 1.0 if t >= t_start else 0.0
    return clamp01((t - t_start) / (t_end - t_start))
