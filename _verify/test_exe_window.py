"""端到端验证桌面版窗口行为：

1. 每次都按默认尺寸打开（已经撤掉「记住上次大小」）
2. 改过大小再关闭，也不会留下任何记录文件
3. 再启动仍然回到默认尺寸
"""
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

import importlib.util
import sys

EXE_NAME = "词枢记词器.exe"
HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(os.path.dirname(HERE), "apps", "word-hub", "词枢记词器.exe")
STATE = os.path.join(os.environ.get("APPDATA", ""), "WordHub", "window.json")

user32 = ctypes.windll.user32
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]


RES = []
def check(name, ok, detail=""):
    RES.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


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
    time.sleep(2.5)


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

# ---- 第一次启动：应为默认尺寸 ----
hwnd, first = launch_and_wait()
check("第一次启动拿到窗口", bool(hwnd), str(first))
ok_default = first and 1300 <= first["w"] <= 1400 and 830 <= first["h"] <= 920
check("默认尺寸打开（约 1360×900）", ok_default, str(first))

# 客户区尺寸（应用里 100vh 对应的就是它，直接决定会不会出滚动条）
cr = wintypes.RECT()
user32.GetClientRect(hwnd, ctypes.byref(cr))
client = (cr.right - cr.left, cr.bottom - cr.top)
# 本机 150% 缩放：窗口物理 2018×1312 → 页面/客户区逻辑 1345×874（Playwright 就是按这个尺寸测的）
check("客户区尺寸约 1345×874（页面按这个高度排版，必须放得下）",
      1330 <= client[0] <= 1360 and 860 <= client[1] <= 890, str(client))

# ---- 朗读小窗：随主窗一起建好，但必须真正隐藏（否则会挡住鼠标点击）----
mini = window_by_title(pids_of(EXE_NAME), "朗读小窗")
check("朗读小窗随主窗一起创建（开机即在，随时可秒开）", mini is not None, str(mini))
check("小窗处于隐藏状态（pywebview 的 hidden 只是「透明+Show」，不真正 hide 会挡点击）",
      mini is not None and mini[1] is False, str(mini))
# 注意：无边框窗口 WinForms 会扣掉边框，请求 420×330 实际约 405×294
check("小窗是个小窗口（约 405×294，比之前的 445×383 明显小一圈）",
      mini is not None and 370 <= mini[2] <= 440 and 250 <= mini[3] <= 330, str(mini))

# ---- 改成小尺寸后关闭 ----
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
user32.SetWindowPos(hwnd, 0, 160, 120, 1120, 830, SWP_NOZORDER | SWP_NOACTIVATE)
time.sleep(2)
resized = rect_of(hwnd)
check("窗口能自由缩放", resized["w"] < 1200, str(resized))

# 试图拖到比最小尺寸还小 → 应被系统夹住（否则页签又会出现滚动条）
user32.SetWindowPos(hwnd, 0, 120, 100, 800, 600, SWP_NOZORDER | SWP_NOACTIVATE)
time.sleep(2)
clamped_outer = rect_of(hwnd)
cr2 = wintypes.RECT()
user32.GetClientRect(hwnd, ctypes.byref(cr2))
clamped_client = (cr2.right - cr2.left, cr2.bottom - cr2.top)
check("拖到 800×600 会被夹在最小尺寸（客户区 ≥ 1070×870）",
      clamped_client[0] >= 1070 and clamped_client[1] >= 870, f"外框 {clamped_outer} 客户区 {clamped_client}")

user32.SetWindowPos(hwnd, 0, 160, 120, 1120, 830, SWP_NOZORDER | SWP_NOACTIVATE)
time.sleep(2)
user32.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE
# ⚠️ 退出要等：程序有两个 WebView2 环境（主窗 + 朗读小窗），
# 窗口瞬间就没了，但进程后台销毁 WebView2 还要 ~20 秒，所以这里轮询而不是死等 4 秒
for _ in range(40):
    if not pids_of(EXE_NAME):
        break
    time.sleep(1)
check("关闭后进程退出", not pids_of(EXE_NAME), f"剩余进程 {pids_of(EXE_NAME)}")
check("不再产生窗口记录文件（尺寸记忆已撤）", not os.path.exists(STATE),
      STATE if os.path.exists(STATE) else "无")
check("关闭后用户数据目录仍在，学习记录留得住（不会被 rmtree）",
      os.path.isdir(PROFILE) and os.path.isfile(SENTINEL),
      f"{PROFILE}  目录={os.path.isdir(PROFILE)}  哨兵={os.path.isfile(SENTINEL)}")

# ---- 第二次启动：应回到默认尺寸 ----
hwnd2, second = launch_and_wait()
check("第二次启动窗口存在", bool(hwnd2), str(second))
ok_back = second and 1300 <= second["w"] <= 1400 and 830 <= second["h"] <= 920
check("重启后回到默认尺寸（没有被上次的小尺寸影响）", ok_back, str(second))
# ================= 拉伸缩放：真窗口实测 =================
# ⚠️ 这一节专门挡「句柄取错窗口」这类 bug：只断言"JS 调了 begin_resize"是不够的，
#    必须真的读一次窗口尺寸，确认它变了（v1.32 就是句柄取错窗口 → 主窗完全缩放不了）。
LAUNCHER = os.path.join(os.path.dirname(HERE), "_build", "wordhub_launcher.py")


class _Nat:
    def __init__(self, hwnd):
        self.Handle = hwnd


class RealWindow:
    """假窗口对象：读写真实 Win32 窗口，用来驱动 launcher 的缩放逻辑。"""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.native = _Nat(hwnd)

    @property
    def x(self):
        return rect_of(self.hwnd)["x"]

    @property
    def y(self):
        return rect_of(self.hwnd)["y"]

    @property
    def width(self):
        return rect_of(self.hwnd)["w"]

    @property
    def height(self):
        return rect_of(self.hwnd)["h"]

    def resize(self, w, h):
        user32.SetWindowPos(self.hwnd, None, 0, 0, int(w), int(h), 0x0004 | 0x0010)

    def move(self, x, y):
        user32.SetWindowPos(self.hwnd, None, int(x), int(y), 0, 0, 0x0004 | 0x0010)

    def restore(self):
        pass


def resize_probe(title, min_w, min_h, edge, dx, dy):
    """用 launcher 的 WindowAPI 拖一次边缘，返回真实窗口的尺寸变化。"""
    spec = importlib.util.spec_from_file_location("whl", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["x"]
    spec.loader.exec_module(mod)
    hwnd = window_by_title(pids_of(EXE_NAME), title)
    if not hwnd:
        return None
    api = mod.WindowAPI(hide_on_close=(min_w != mod.MIN_W), title=title, min_w=min_w, min_h=min_h)
    rw = RealWindow(hwnd[0])
    api.bind(rw)
    before = rect_of(hwnd[0])
    api.begin_resize(edge, 1000, 500, before["w"], before["h"])
    api.update_resize(1000 + dx, 500 + dy)
    time.sleep(0.4)
    after = rect_of(hwnd[0])
    api.end_resize()
    return {"before": before, "after": after, "dw": after["w"] - before["w"], "dh": after["h"] - before["h"],
            "handle_ok": api._handle() == hwnd[0], "min": (api._min_w, api._min_h)}


main_res = resize_probe("词枢 · 英语记词器", 1075, 875, "e", 200, 0)
check("真窗口实测：主窗拖右边缘 +200 就真的宽了 200（句柄没取错窗口）",
      main_res is not None and abs(main_res["dw"] - 200) <= 6 and main_res["handle_ok"],
      str(main_res))

mini_res = resize_probe("词枢 · 朗读小窗", 320, 250, "e", 200, 0)
check("真窗口实测：小窗拖右边缘 +200 就真的宽了 200（且没被钳成主窗的 1075）",
      mini_res is not None and abs(mini_res["dw"] - 200) <= 6 and mini_res["min"] == (320, 250),
      str(mini_res))
check("小窗与主窗拿到的是各自的窗口句柄（不是同一个）",
      main_res is not None and mini_res is not None and mini_res["before"]["w"] < 500,
      f"主窗 {main_res['before'] if main_res else '?'} / 小窗 {mini_res['before'] if mini_res else '?'}")


kill()

print()
print("通过 %d / %d" % (sum(RES), len(RES)))
raise SystemExit(0 if all(RES) else 1)
