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


def launch_and_wait():
    subprocess.Popen([EXE], cwd=os.path.dirname(EXE))
    time.sleep(10)
    hwnd = main_window(pids_of(EXE_NAME))
    return hwnd, (rect_of(hwnd) if hwnd else None)


def kill():
    subprocess.run(["taskkill", "/IM", EXE_NAME, "/F"], capture_output=True)
    time.sleep(2.5)


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
time.sleep(4)
check("关闭后进程退出", not pids_of(EXE_NAME))
check("不再产生窗口记录文件（尺寸记忆已撤）", not os.path.exists(STATE),
      STATE if os.path.exists(STATE) else "无")

# ---- 第二次启动：应回到默认尺寸 ----
hwnd2, second = launch_and_wait()
check("第二次启动窗口存在", bool(hwnd2), str(second))
ok_back = second and 1300 <= second["w"] <= 1400 and 830 <= second["h"] <= 920
check("重启后回到默认尺寸（没有被上次的小尺寸影响）", ok_back, str(second))
kill()

print()
print("通过 %d / %d" % (sum(RES), len(RES)))
raise SystemExit(0 if all(RES) else 1)
