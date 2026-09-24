"""端到端验证桌面版窗口行为（真实 exe + 真实系统光标）

覆盖：
  1. 每次按设计尺寸打开（CSS 1360×900），客户区够高（不够高页签会出滚动条）
  2. 改过大小再关闭不留下记录文件；再启动仍回到设计尺寸
  3. 朗读小窗随主窗创建、真正隐藏（hidden=True 只是透明度 0，会挡鼠标）、尺寸偏小
  4. ⚠️ 拖动/缩放用**真实系统光标**驱动（SetCursorPos），每步回读真实窗口矩形。
     只断言"JS 调了接口"是不够的（v1.32 就是这么漏过 bug 的）。
  5. ⚠️ 核心回归（v1.30 定位）：**拉伸之后再拉伸 / 再拖动**，基准必须是"当前矩形"。
     老代码拿 pywebview 缓存的尺寸当基准，而它不跟踪我们的 SetWindowPos →
     用户拉过一次之后：再拉就跳回初始尺寸（甲方"试图拉伸就自动缩小了"）、
     再拖就不跟手（"拉伸完之后又不能正常移动窗口位置了"）。

⚠️ 单位：本脚本把自己升成 PER_MONITOR_AWARE，所以 GetWindowRect / SetCursorPos /
   GetCursorPos 读写的都是**物理像素**（不升级的话拿到的是被虚拟化过的逻辑值，
   这正是 v1.32/1.33 单位反复的起因）。
"""
import ctypes
import ctypes.wintypes as wintypes
import importlib.util
import os
import subprocess
import sys
import time

EXE_NAME = "词枢记词器.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(os.path.dirname(HERE), "apps", "word-hub", "词枢记词器.exe")
LAUNCHER = os.path.join(os.path.dirname(HERE), "_build", "wordhub_launcher.py")
STATE = os.path.join(os.environ.get("APPDATA", ""), "WordHub", "window.json")

user32 = ctypes.windll.user32

# ⚠️ 必须最先做：升级 DPI 感知级别，之后所有坐标才是真实物理像素
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

RES = []
def check(name, ok, detail=""):
    RES.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def cursor_pos():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def cursor_to(x, y):
    """把真实光标挪过去（这就是"真实鼠标输入"，比合成事件可信）"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)


def pids_of(name):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="gbk", errors="replace").stdout
    got = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == name.lower():
            got.append(int(parts[1]))
    return got


def main_window(pids, tries=30):
    for _ in range(tries):
        hit = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 400 and r.bottom - r.top > 300:
                    hit.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        if hit:
            return hit[0]
        time.sleep(1.0)
    return 0


def rect_of(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return {"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top}


def window_by_title(pids, title_part):
    """按标题找本进程的窗口，返回 (hwnd, 是否可见, 宽, 高)；找不到返回 None。
    注意要把隐藏窗口也找出来 —— 朗读小窗平时就是隐藏的。"""
    hit = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if title_part in buf.value:
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            hit.append((hwnd, bool(user32.IsWindowVisible(hwnd)),
                        r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(cb, 0)
    return hit[0] if hit else None


def launch_and_wait():
    subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
    time.sleep(10)
    hwnd = main_window(pids_of(EXE_NAME))
    return hwnd, (rect_of(hwnd) if hwnd else None)


def kill():
    subprocess.run(["taskkill", "/IM", EXE_NAME, "/F"], capture_output=True)
    for _ in range(40):                    # 两套 WebView2 环境销毁要约 20 秒
        if not pids_of(EXE_NAME):
            break
        time.sleep(1)


CUR_SAVED = cursor_pos()                   # 测试结束把用户的光标放回去
# ⚠️ 每次探针前先把光标放到这个"家"位置再量起点：否则光标可能已经贴在屏幕边缘，
#    SetCursorPos 会被系统夹住 → 实际位移小于要的位移，断言就会假失败
#    （第一版就是这么挂的：上一条探针把光标推到了 2559，下一条 +200 位移变成 0）。
CURSOR_HOME = (600, 500)


# ===================== 用真实光标驱动 WindowAPI =====================
class _Nat:
    def __init__(self, hwnd):
        self.Handle = hwnd


class RealWindow:
    """假窗口对象：读写真实 Win32 窗口，用来驱动 launcher 的拖动/缩放逻辑。

    ⚠️ 单位要和真 pywebview 一致：`x/y/width/height` 报的是 **CSS 像素**（= 物理/1.5）。
    （v1.30 实测：pywebview 报 1345×862，而同一进程 GetWindowRect 报 2018×1294。）
    本脚本自身是 DPI 感知的，所以 rect_of 给的是物理像素 → 这里显式除以 scale 才对得上。
    """

    def __init__(self, hwnd, scale=1.0):
        self.hwnd = hwnd
        self.scale = scale
        self.native = _Nat(hwnd)
        self.restored = 0

    @property
    def x(self):
        return round(rect_of(self.hwnd)["x"] / self.scale)

    @property
    def y(self):
        return round(rect_of(self.hwnd)["y"] / self.scale)

    @property
    def width(self):
        return round(rect_of(self.hwnd)["w"] / self.scale)

    @property
    def height(self):
        return round(rect_of(self.hwnd)["h"] / self.scale)

    def restore(self):
        self.restored += 1


def load_launcher():
    spec = importlib.util.spec_from_file_location("whl", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    return mod


MOD = load_launcher()


def api_for(title, min_w, min_h):
    hit = window_by_title(pids_of(EXE_NAME), title)
    if not hit:
        return None, None, None
    hwnd = hit[0]
    scale = user32.GetDpiForWindow(hwnd) / 96.0
    api = MOD.WindowAPI(hide_on_close=(min_w != MOD.MIN_W), title=title,
                        min_w=min_w, min_h=min_h)
    api.bind(RealWindow(hwnd, scale))
    return api, hwnd, scale


def resize_probe(title, min_w, min_h, edge, dx, dy):
    """拖一次边缘（真实光标位移 dx/dy 物理像素），返回真实窗口矩形的变化。"""
    api, hwnd, scale = api_for(title, min_w, min_h)
    if not api:
        return None
    before = rect_of(hwnd)
    cursor_to(*CURSOR_HOME)
    api.begin_resize(edge)
    c0 = cursor_pos()
    cursor_to(c0[0] + dx, c0[1] + dy)
    api.update_resize()
    api.end_resize()
    time.sleep(0.2)
    after = rect_of(hwnd)
    return {"scale": scale, "before": before, "after": after,
            "dw": after["w"] - before["w"], "dh": after["h"] - before["h"],
            "dx": after["x"] - before["x"], "dy": after["y"] - before["y"],
            "handle_ok": api._handle() == hwnd,
            "min_phys": api._min_phys()}


def drag_probe(title, min_w, min_h, move_x, move_y):
    """拖标题栏（真实光标位移 move_x/move_y 物理像素），返回真实位置变化。"""
    api, hwnd, scale = api_for(title, min_w, min_h)
    if not api:
        return None
    before = rect_of(hwnd)
    cursor_to(*CURSOR_HOME)
    api.begin_drag()
    c0 = cursor_pos()
    cursor_to(c0[0] + move_x, c0[1] + move_y)
    api.drag_to()
    api.end_drag()
    time.sleep(0.2)
    after = rect_of(hwnd)
    return {"scale": scale, "before": before, "after": after,
            "dmove": (after["x"] - before["x"], after["y"] - before["y"]),
            "dw": after["w"] - before["w"], "dh": after["h"] - before["h"],
            "handle_ok": api._handle() == hwnd}


# 用户数据目录（学习记录 localStorage 就在这）—— 关窗后必须还在
PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "WordHub", "webview")
SENTINEL = os.path.join(PROFILE, "_sentinel_keep.txt")
os.makedirs(PROFILE, exist_ok=True)
with open(SENTINEL, "w", encoding="utf-8") as f:
    f.write("keep me")

kill()
if os.path.exists(STATE):
    os.remove(STATE)
    print("已清掉历史记录文件")

# ---- 第一次启动：应为设计尺寸（CSS 1360×900 → 物理 2040×1350）----
hwnd, first = launch_and_wait()
check("第一次启动拿到窗口", bool(hwnd), str(first))
check("按设计尺寸打开（CSS 1360×900 → 物理约 2040×1350）",
      bool(first) and 2000 <= first["w"] <= 2080 and 1320 <= first["h"] <= 1380, str(first))

# 客户区：应用里 100vh 对应的就是它（本机 150% → 客户区物理 ≈ 设计 CSS × 1.5）
cr = wintypes.RECT()
user32.GetClientRect(hwnd, ctypes.byref(cr))
client = (cr.right - cr.left, cr.bottom - cr.top)
check("客户区尺寸约 2040×1350 物理（页面按这个排版，必须放得下，否则出滚动条）",
      2000 <= client[0] <= 2080 and 1320 <= client[1] <= 1380, str(client))

# ---- 朗读小窗：随主窗一起建好，但必须真正隐藏（否则会挡住鼠标点击）----
mini = window_by_title(pids_of(EXE_NAME), "朗读小窗")
check("朗读小窗随主窗一起创建（开机即在，随时可秒开）", mini is not None, str(mini))
check("小窗处于隐藏状态（pywebview 的 hidden 只是「透明+Show」，不真正 hide 会挡点击）",
      mini is not None and mini[1] is False, str(mini))
check("小窗是个小窗口（CSS 420×330 → 物理约 630×495）",
      mini is not None and 600 <= mini[2] <= 660 and 460 <= mini[3] <= 520, str(mini))

# ---- OS 不该在硬钳窗口尺寸（撤掉 OS 级最小尺寸后，想设多大就多大）----
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
user32.SetWindowPos(hwnd, 0, 160, 120, 1500, 1000, SWP_NOZORDER | SWP_NOACTIVATE)
time.sleep(1.0)
resized = rect_of(hwnd)
check("窗口能自由缩放（设 1500×1000 就真的是 1500×1000，OS 没有硬钳）",
      abs(resized["w"] - 1500) <= 4 and abs(resized["h"] - 1000) <= 4, str(resized))

# ================= 拉伸/拖动：真实光标 + 真窗口回读 =================
print()
print("----- 拉伸 / 拖动（真实光标驱动，每步回读真实矩形）-----")

# 先把窗口摆到一个"远高于下限"的位置和尺寸：下限是物理 1612×1312，
# 保持在上限之上，拉伸断言里的 1:1 才不会被下限夹取干扰。
user32.SetWindowPos(hwnd, 0, 40, 40, 1700, 1400, SWP_NOZORDER | SWP_NOACTIVATE)
time.sleep(0.6)

r1 = resize_probe("词枢 · 英语记词器", MOD.MIN_W, MOD.MIN_H, "e", 200, 0)
check("拖右边缘 +200 物理 → 窗口真的宽 200（1:1 跟手，句柄也没取错）",
      bool(r1) and abs(r1["dw"] - 200) <= 4 and r1["dh"] == 0 and r1["handle_ok"], str(r1))

# ⚠️ 核心回归：拉伸过之后**再拉一次**，基准必须是当前矩形。
#    老代码用 pywebview 缓存的宽度当基准 → 这里会"跳回初始尺寸"（甲方的"自动缩小"）。
r2 = resize_probe("词枢 · 英语记词器", MOD.MIN_W, MOD.MIN_H, "e", 150, 0)
check("★ 拉伸之后再拉伸：仍然 1:1（+150 就是 +150，不会跳回初始宽度）",
      bool(r2) and abs(r2["dw"] - 150) <= 4 and r2["before"]["w"] == r1["after"]["w"],
      f"第一次后 {r1['after']['w']} → 第二次前 {r2['before']['w']} → 后 {r2['after']['w']}")

r3 = resize_probe("词枢 · 英语记词器", MOD.MIN_W, MOD.MIN_H, "s", 0, 200)
check("拖下边缘 +200 物理 → 高度真的 +200",
      bool(r3) and abs(r3["dh"] - 200) <= 4 and r3["dw"] == 0, str(r3))

d1 = drag_probe("词枢 · 英语记词器", MOD.MIN_W, MOD.MIN_H, 150, 90)
check("★ 拉伸之后拖标题栏：位移 1:1 跟手（+150/+90），尺寸不变",
      bool(d1) and d1["dmove"] == (150, 90) and d1["dw"] == 0 and d1["dh"] == 0, str(d1))

d2 = drag_probe("词枢 · 英语记词器", MOD.MIN_W, MOD.MIN_H, -220, -140)
check("反方向拖动同样 1:1（-220/-140）",
      bool(d2) and d2["dmove"] == (-220, -140), str(d2))

# ---- 下限：常量是 CSS 像素，物理下限 = CSS × DPI 比例 ----
cl = resize_probe("词枢 · 英语记词器", MOD.MIN_W, MOD.MIN_H, "e", -8000, 0)
exp_min_w = cl["min_phys"][0] if cl else 0
check("用力往回拖（-8000）会被夹在自己的最小尺寸（CSS 1075 × 1.5 = 1612 物理）",
      bool(cl) and abs(cl["after"]["w"] - exp_min_w) <= 4,
      f"拖完宽 {cl['after']['w'] if cl else '?'}，期望 {exp_min_w}")

cl2 = resize_probe("词枢 · 英语记词器", MOD.MIN_W, MOD.MIN_H, "w", 8000, 0)
check("往左拖到极限时右边缘钉住不动（窗口不会整体漂移）",
      bool(cl2) and abs((cl2["after"]["x"] + cl2["after"]["w"]) - (cl2["before"]["x"] + cl2["before"]["w"])) <= 6,
      f"右边缘 {cl2['before']['x'] + cl2['before']['w']} → {cl2['after']['x'] + cl2['after']['w']}")

mini_res = resize_probe("词枢 · 朗读小窗", MOD.MINI_MIN_W, MOD.MINI_MIN_H, "e", 200, 0)
check("小窗拖右边缘 +200 物理 → 宽 200（下限是小窗自己的，不会被钳成主窗的 1612）",
      bool(mini_res) and abs(mini_res["dw"] - 200) <= 4
      and mini_res["min_phys"][0] < 1000, str(mini_res))

check("小窗与主窗拿到的是各自的窗口句柄（不是同一个）",
      bool(r1) and bool(mini_res) and mini_res["before"]["w"] < 800,
      f"主窗 {r1['before'] if r1 else '?'} / 小窗 {mini_res['before'] if mini_res else '?'}")

# ---- 关窗：进程要退出，学习记录留得住，且不留尺寸记忆文件 ----
user32.SetWindowPos(hwnd, 0, 160, 120, 2040, 1350, SWP_NOZORDER | SWP_NOACTIVATE)
time.sleep(0.5)
user32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE
kill()
check("关闭后进程退出", not pids_of(EXE_NAME), f"剩余进程 {pids_of(EXE_NAME)}")
check("不再产生窗口记录文件（尺寸记忆已撤）", not os.path.exists(STATE),
      STATE if os.path.exists(STATE) else "无")
check("关闭后用户数据目录仍在，学习记录留得住（不会被 rmtree）",
      os.path.isdir(PROFILE) and os.path.isfile(SENTINEL),
      f"{PROFILE}  目录={os.path.isdir(PROFILE)}  哨兵={os.path.isfile(SENTINEL)}")

# ---- 第二次启动：仍应回到设计尺寸 ----
hwnd2, second = launch_and_wait()
check("第二次启动窗口存在", bool(hwnd2), str(second))
check("重启后回到设计尺寸（没有被上次的小尺寸影响）",
      bool(second) and 2000 <= second["w"] <= 2080 and 1320 <= second["h"] <= 1380, str(second))

kill()
cursor_to(*CUR_SAVED)                      # 把用户的光标放回原处

print()
print("通过 %d / %d" % (sum(RES), len(RES)))
raise SystemExit(0 if all(RES) else 1)
