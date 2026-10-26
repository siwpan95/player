#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无头自检（不创建 Tk 窗口）。

player.py 的 WMPVisualizer 在 __init__ 里才 tk.Tk()，模块顶层只 import tkinter，
所以可以安全 import 而不弹窗。本自检用 object.__new__ 绕开 __init__，
再用一个记录调用的假 canvas 驱动绘制逻辑，验证三件事：

  1. HSV→RGB 颜色换算结果正确、取值合法；
  2. 音频模拟数据推进：取值始终夹在 [0,1]，帧号自增，历史环形缓冲不超长；
  3. 条形图 / 尖峰两种效果的绘制算法产出正确的图元数量与坐标范围。

用法：python selfcheck.py
退出码：0 成功 / 1 失败。
"""

import math
import os
import random
import re
import sys
import tkinter  # noqa: F401  仅加载库，不创建 Tk()，不会开窗口

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # 统一按 UTF-8 输出，避免 GBK 控制台把中文自检结果打成乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

from player import WMPVisualizer  # noqa: E402


class StubCanvas:
    """记录图元调用的假 canvas（替代 tk.Canvas）。"""

    def __init__(self):
        self.calls = []

    def delete(self, *a, **kw):
        self.calls.append(("delete", a, kw))

    def create_rectangle(self, *a, **kw):
        self.calls.append(("rectangle", a, kw))

    def create_line(self, *a, **kw):
        self.calls.append(("line", a, kw))

    def create_oval(self, *a, **kw):
        self.calls.append(("oval", a, kw))

    def create_polygon(self, *a, **kw):
        self.calls.append(("polygon", a, kw))

    def create_text(self, *a, **kw):
        self.calls.append(("text", a, kw))

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)


def fail(msg):
    print("FAIL: " + msg)
    sys.exit(1)


def make_visualizer(width=800, height=600):
    """构造一个不依附 Tk 的视觉器实例：绕过 __init__，手工装状态。"""
    v = object.__new__(WMPVisualizer)
    v.width = width
    v.height = height
    v.effect_type = "bars"
    v.frame_count = 0
    v.freq_data = [0.5] * 1024
    from collections import deque
    v.history = deque(maxlen=50)
    v.canvas = StubCanvas()
    return v


def check_hsv():
    v = make_visualizer()
    cases = [
        ((0.0, 0.0, 1.0), "#ffffff"),
        ((0.0, 1.0, 1.0), "#ff0000"),
        ((1 / 3, 1.0, 1.0), "#00ff00"),
        ((2 / 3, 1.0, 1.0), "#0000ff"),
        ((0.0, 0.0, 0.0), "#000000"),
    ]
    for args, want in cases:
        got = v.hsv_to_rgb(*args)
        if got != want:
            fail("hsv_to_rgb%r = %s，期望 %s" % (args, got, want))
    for i in range(0, 100):
        h = i / 100
        for s in (0.0, 0.35, 0.8, 1.0):
            c = v.hsv_to_rgb(h, s, 1.0)
            if not re.fullmatch(r"#[0-9a-f]{6}", c):
                fail("颜色格式非法：h=%s s=%s → %r" % (h, s, c))
            rgb = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            if max(rgb) != 255:
                fail("v=1 时亮度未打满：h=%s s=%s → %s" % (h, s, c))
    return len(cases)


def check_audio_state():
    v = make_visualizer()
    random.seed(1234)
    v.freq_data = [random.random() for _ in range(1024)]
    before = v.frame_count
    for _ in range(60):
        v.update_audio_data()
    if v.frame_count != before + 60:
        fail("帧号未按帧推进：%d" % v.frame_count)
    if len(v.history) != v.history.maxlen:
        fail("历史缓冲未满/超长：%d" % len(v.history))
    if len(v.freq_data) != 1024:
        fail("频谱长度被改变：%d" % len(v.freq_data))
    for x in v.freq_data:
        if not (0.0 <= x <= 1.0):
            fail("频谱值越界：%r" % x)
    snap = list(v.freq_data)
    v.update_audio_data()
    if list(v.freq_data) == snap:
        fail("音频数据没有变化（状态未推进）")
    for row in v.history:
        if len(row) != 1024:
            fail("历史快照长度不对：%d" % len(row))
    return v.frame_count


def check_draw_bars():
    v = make_visualizer()
    v.freq_data = [0.5] * 1024
    v.draw_bars()
    n = v.canvas.count("rectangle")
    if n != 64:
        fail("条形图应画 64 根，实际 %d" % n)
    bar_w = v.width // 64
    for kind, args, kw in v.canvas.calls:
        if kind != "rectangle":
            continue
        x1, y1, x2, y2 = args
        if not (0 <= x1 < x2 <= v.width):
            fail("条形 x 越界：%r" % (args,))
        if not (0 <= y1 <= y2 <= v.height):
            fail("条形 y 越界：%r" % (args,))
        if x2 - x1 != bar_w - 2:
            fail("条宽不对：%d" % (x2 - x1))
        if not re.fullmatch(r"#[0-9a-f]{6}", kw.get("fill", "")):
            fail("条形颜色非法：%r" % (kw,))
    # 数据全 0.5 时应画在高度一半
    x1, y1, x2, y2 = v.canvas.calls[0][1]
    if abs(y1 - (v.height - int(0.5 * v.height * 0.8))) > 1:
        fail("条形高度与数据不符：y1=%s" % y1)
    return n


def check_draw_spikes():
    v = make_visualizer()
    v.freq_data = [0.5] * 1024
    v.draw_spikes()
    n = v.canvas.count("line")
    if n != 128:
        fail("尖峰应画 128 条，实际 %d" % n)
    cx, cy = v.width // 2, v.height // 2
    max_r = min(cx, cy) - 20
    for kind, args, kw in v.canvas.calls:
        if kind != "line":
            continue
        x1, y1, x2, y2 = args
        if (x1, y1) != (cx, cy):
            fail("尖峰起点不在中心：%r" % (args,))
        r = math.hypot(x2 - cx, y2 - cy)
        if not (20 - 1e-6 <= r <= 20 + max_r + 1e-6):
            fail("尖峰半径越界：%.3f" % r)
    return n


def main():
    n_color = check_hsv()
    frames = check_audio_state()
    bars = check_draw_bars()
    spikes = check_draw_spikes()
    print("selfcheck ok: HSV→RGB %d 组定点 + 100 色域扫描 / 音频推进 %d 帧"
          "（[0,1] 夹紧、history 上限 50） / 条形图 %d 根 + 尖峰 %d 条几何正确"
          % (n_color, frames, bars, spikes))


if __name__ == "__main__":
    main()
