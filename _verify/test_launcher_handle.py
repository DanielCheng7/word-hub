"""在【同一个进程里】把桌面版真跑起来，验证"应用自己解析窗口句柄"这条路径。

为什么必须单独立这一套（v1.30）：
  之前的 test_exe_window.py 是把一个「读写真实窗口的假对象」塞给 WindowAPI，
  句柄是我自己给的 —— 所以它**永远测不出"应用自己找错窗口"**。
  甲方这次的反馈恰好就是这一类：「主窗完全不能拖/不能拉伸，小窗一切正常」——
  两个窗口共用同一套 JS 和同一个 WindowAPI，唯一能差的就是"解析到哪个句柄"。

本脚本断言：
  ① 启动早期（GUI 刚起来）解析不到句柄时返回 0，**绝不返回别人的窗口**；
  ② prime_windows 之后，主窗/小窗各自是设计尺寸（CSS 1360×900 / 420×330）；
  ③ 主窗和小窗拿到的是两个不同的、都属于本进程的句柄；
  ④ 用应用自己的句柄解析，真实拖一次边缘 → 窗口真的变宽（尺寸 1:1）；
  ⑤ 真实拖一次标题栏 → 窗口位置 1:1 跟着光标走。

用法：python _verify/test_launcher_handle.py
"""
import ctypes
import ctypes.wintypes as wt
import functools
import http.server
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_verify", "downloads")
os.makedirs(OUT, exist_ok=True)

sys.path.insert(0, os.path.join(ROOT, "_build"))
import webview                                        # noqa: E402
import wordhub_launcher as L                          # noqa: E402

user32 = ctypes.windll.user32
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowTextW.argtypes = [wt.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.IsZoomed.argtypes = [wt.HWND]
user32.IsZoomed.restype = ctypes.c_bool
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = ctypes.c_bool

RES = []
def check(name, ok, detail=""):
    RES.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


HOME = (600, 500)


def cursor_pos():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def cursor_to(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)


def title_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def pid_of(hwnd):
    d = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(d))
    return d.value


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


directory = L.resource_dir()
port = L.free_port()
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                      functools.partial(Quiet, directory=directory))
threading.Thread(target=httpd.serve_forever, daemon=True).start()

api = L.WindowAPI()
win = webview.create_window(
    L.WINDOW_TITLE, f"http://127.0.0.1:{port}/index.html?desktop=1",
    js_api=api, width=L.DEFAULT_W, height=L.DEFAULT_H, min_size=(1, 1),
    frameless=True, easy_drag=False, resizable=True, background_color="#f5f5f5")
mini_api = L.WindowAPI(hide_on_close=True, title=L.MINI_TITLE,
                       min_w=L.MINI_MIN_W, min_h=L.MINI_MIN_H)
mini_win = webview.create_window(
    L.MINI_TITLE, f"http://127.0.0.1:{port}/index.html?desktop=1&mini=1",
    js_api=mini_api, width=L.MINI_W, height=L.MINI_H, min_size=(1, 1),
    frameless=True, easy_drag=False, resizable=True, on_top=True, hidden=True,
    background_color="#f5f5f5")
mini_api.bind(mini_win)
api.attach_mini(mini_win)
api.bind(win)

EARLY = {}


def run():
    """模拟"GUI 刚起来"那一刻 + 之后的完整流程。"""
    # ① 最早的一次解析：此时窗口多半还没建好
    t0 = time.time()
    while time.time() - t0 < 2.0:                 # 反复试 2 秒，记录第一次非 0 的结果
        h = api._handle()
        if h:
            EARLY["first_hwnd"] = h
            EARLY["first_ok"] = (pid_of(h) == os.getpid())
            EARLY["first_title"] = title_of(h)
            break
        time.sleep(0.02)
    user32.SetCursorPos(*HOME)                    # 别把用户的光标留在奇怪的地方

    # ② 走真实启动路径
    L.prime_windows(api, mini_api, mini_win)
    time.sleep(0.8)

    mh, xh = api._handle(), mini_api._handle()
    mr, xr = api._rect_phys(), mini_api._rect_phys()
    sc_m, sc_x = api._scale(), mini_api._scale()

    out = {"early": EARLY, "main_hwnd": mh, "main_title": title_of(mh) if mh else "",
           "main_rect": mr, "main_scale": sc_m,
           "mini_hwnd": xh, "mini_title": title_of(xh) if xh else "",
           "mini_rect": xr, "mini_scale": sc_x,
           "want_main": (int(L.DEFAULT_W * sc_m), int(L.DEFAULT_H * sc_m)),
           "want_mini": (int(L.MINI_W * sc_x), int(L.MINI_H * sc_x)),
           "pid_ok": (mh and xh and pid_of(mh) == os.getpid() == pid_of(xh)),
           "same_hwnd": bool(mh) and mh == xh}

    # ④⑤ 用**应用自己的句柄**真拖一次边缘 / 拖一次标题栏
    def real_resize(a, edge, dx, dy):
        cursor_to(*HOME)
        c0 = cursor_pos()
        a.begin_resize(edge)
        # ⚠️ 基准必须在 begin_resize 之后读：窗口若处于最大化，begin 里会先把它还原，
        #    尺寸随之变化（读早了就会拿"最大化时的尺寸"去比，差值全是假的）
        before = a._rect_phys()
        cursor_to(c0[0] + dx, c0[1] + dy)
        c1 = cursor_pos()
        a.update_resize()
        a.end_resize()
        time.sleep(0.25)
        return before, a._rect_phys(), (c1[0] - c0[0], c1[1] - c0[1])

    def real_drag(a, dx, dy):
        cursor_to(*HOME)
        c0 = cursor_pos()
        a.begin_drag()
        before = a._rect_phys()
        cursor_to(c0[0] + dx, c0[1] + dy)
        c1 = cursor_pos()
        a.drag_to()
        a.end_drag()
        time.sleep(0.25)
        return before, a._rect_phys(), (c1[0] - c0[0], c1[1] - c0[1])

    b, a2, mv = real_resize(api, "e", 200, 0)
    out["resize"] = {"before": b, "after": a2, "cursor_moved": mv,
                     "dw": a2[2] - b[2], "dh": a2[3] - b[3]}
    b, a3, mv = real_drag(api, 120, 70)
    out["drag"] = {"before": b, "after": a3, "cursor_moved": mv,
                   "dmove": (a3[0] - b[0], a3[1] - b[1]),
                   "dw": a3[2] - b[2]}
    b, a4, mv = real_resize(mini_api, "e", 150, 0)
    out["mini_resize"] = {"before": b, "after": a4, "cursor_moved": mv,
                          "dw": a4[2] - b[2]}

    # ⑥ 最大化状态下拖/拉伸（甲方 v1.37 症状：主窗完全不能拖不能拉、小窗却完美）。
    #    根因候选：**最大化状态下 Windows 会忽略对窗口的移动与尺寸修改**，
    #    而我们原来只看自己那个 self._max 标志位（跟真实状态脱节时就彻底失灵）。
    h = api._handle()
    user32.ShowWindow(h, 3)                       # 3 = SW_MAXIMIZE
    time.sleep(0.8)
    out["zoomed_before"] = bool(user32.IsZoomed(h))
    b, a5, mv = real_resize(api, "e", 180, 0)
    out["resize_after_max"] = {"before": b, "after": a5, "cursor_moved": mv,
                               "dw": a5[2] - b[2],
                               "zoomed": bool(user32.IsZoomed(api._handle()))}
    b, a6, mv = real_drag(api, 100, 60)
    out["drag_after_max"] = {"before": b, "after": a6, "cursor_moved": mv,
                             "dmove": (a6[0] - b[0], a6[1] - b[1]),
                             "zoomed": bool(user32.IsZoomed(api._handle()))}

    import json
    with open(os.path.join(OUT, "launcher_handle.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))

    # ---------------- 断言 ----------------
    print()
    print("----- 结果 -----")
    early = out["early"]
    check("启动早期解析不到窗口时返回 0 或自己的窗口（绝不返回别人的窗口）",
          ("first_hwnd" not in early) or early.get("first_ok") is True,
          f"首次解析={early.get('first_hwnd')} 属于本进程={early.get('first_ok')} "
          f"标题={early.get('first_title')!r}")

    check("prime 之后主窗句柄可解析、且是本文档进程的窗口",
          bool(mh) and pid_of(mh) == os.getpid(),
          f"hwnd={mh} 标题={out['main_title']!r}")
    check("主窗句柄指向的确实是主窗（标题对得上）",
          out["main_title"] == L.WINDOW_TITLE, out["main_title"])
    check("小窗句柄指向的确实是小窗（标题对得上）",
          out["mini_title"] == L.MINI_TITLE, out["mini_title"])
    check("主窗/小窗拿到的是两个不同的句柄（不会互相串）",
          bool(mh) and bool(xh) and mh != xh, f"主窗 {mh} / 小窗 {xh}")

    check("主窗被摆成设计尺寸（CSS 1360×900）",
          bool(mr) and abs(mr[2] - out["want_main"][0]) <= 2
          and abs(mr[3] - out["want_main"][1]) <= 2,
          f"{mr}，期望 {out['want_main']}")
    check("小窗被摆成设计尺寸（CSS 420×330）",
          bool(xr) and abs(xr[2] - out["want_mini"][0]) <= 2
          and abs(xr[3] - out["want_mini"][1]) <= 2,
          f"{xr}，期望 {out['want_mini']}")

    rz = out["resize"]
    check("★ 走应用自己的句柄：拖右边缘 → 主窗真的宽了（1:1，全程物理像素）",
          rz["dw"] == rz["cursor_moved"][0] and rz["dw"] > 0,
          f"光标移动 {rz['cursor_moved'][0]} → 宽度变化 {rz['dw']}"
          f"（前 {rz['before'][2]} → 后 {rz['after'][2]}）")
    dr = out["drag"]
    check("★ 走应用自己的句柄：拖标题栏 → 主窗位置 1:1 跟着光标走，尺寸不变",
          dr["dmove"] == dr["cursor_moved"] and dr["dw"] == 0,
          f"光标移动 {dr['cursor_moved']} → 窗口位移 {dr['dmove']}，宽度变化 {dr['dw']}")
    mz = out["mini_resize"]
    check("小窗同样能 1:1 拉伸（顺便确认小窗没被钳成主窗的下限）",
          mz["dw"] == mz["cursor_moved"][0] and mz["dw"] > 0,
          f"光标移动 {mz['cursor_moved'][0]} → 宽度变化 {mz['dw']}")

    rz2 = out["resize_after_max"]
    check("★ 主窗已最大化时拖右边缘：先自动还原，再 1:1 拉伸",
          out["zoomed_before"] is True and rz2["zoomed"] is False
          and rz2["dw"] == rz2["cursor_moved"][0] and rz2["dw"] > 0,
          f"拉伸前是否最大化={out['zoomed_before']} → 之后={rz2['zoomed']}；"
          f"光标移动 {rz2['cursor_moved'][0]} → 宽度变化 {rz2['dw']}")
    dr2 = out["drag_after_max"]
    check("★ 主窗已最大化时拖标题栏：先自动还原，再 1:1 移动",
          dr2["dmove"] == dr2["cursor_moved"] and dr2["zoomed"] is False,
          f"光标移动 {dr2['cursor_moved']} → 窗口位移 {dr2['dmove']}")

    # 日志里要能看到两个窗口的句柄（exe 出问题时靠它定位）
    log = os.path.join(os.environ.get("LOCALAPPDATA", ""), "WordHub", "startup.log")
    try:
        content = open(log, encoding="utf-8").read()
    except OSError:
        content = ""
    check("启动日志落盘且含两个窗口的句柄与真实矩形",
          "主窗" in content and "小窗" in content and "句柄=" in content,
          content.replace("\n", " | ")[:160] or "（没有日志）")

    ilog = os.path.join(os.environ.get("LOCALAPPDATA", ""), "WordHub", "interact.log")
    try:
        icontent = open(ilog, encoding="utf-8").read()
    except OSError:
        icontent = ""
    check("交互日志记下每次拖动/缩放（万一还拖不动，一眼能分清是页面没发还是窗口没动）",
          "拖动 begin" in icontent and "拖动 end" in icontent
          and "缩放 begin" in icontent and "端点" not in icontent,
          icontent.replace("\n", " | ")[:200] or "（没有日志）")

    print()
    print("通过 %d / %d" % (sum(RES), len(RES)))
    sys.stdout.flush()          # ⚠️ os._exit 不刷新缓冲，不 flush 就一个字都看不到
    os._exit(0 if all(RES) else 1)


threading.Timer(90, lambda: (sys.stdout.flush(), os._exit(5))).start()
threading.Thread(target=run, daemon=True).start()
webview.start(private_mode=False, storage_path=L.profile_dir())
httpd.shutdown()
