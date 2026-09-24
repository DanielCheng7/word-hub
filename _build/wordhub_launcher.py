"""词枢记词器 · 桌面版启动器

做法：起一个只监听 127.0.0.1 的本地 HTTP 服务（保证 localStorage / 相对路径脚本都正常），
再用 WebView2 开一个**无边框**原生窗口，窗口栏由页面自己画成 macOS 风格（红黄绿）。

用法：
  python wordhub_launcher.py            正常启动窗口
  python wordhub_launcher.py --selftest 只起服务并逐个核对内置文件，验证打包完整性后退出
"""
import ctypes
import ctypes.wintypes
import functools
import http.server
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request

import webview

WEBAPP = "webapp"
# 应用运行必需的文件（打包后用于自检）
NEEDED = [
    "index.html",
    "data_kids.js", "data_primary.js",
    "data_cet4.js", "data_cet6.js", "data_ky.js",
    "data_ielts.js", "data_toefl.js", "data_gre.js",
    "data_sent.js",
]
WINDOW_TITLE = "词枢 · 英语记词器"
MINI_TITLE = "词枢 · 朗读小窗"
# ===================== 窗口尺寸的「单位」约定（v1.30 用实验钉死，改这里前必读）=====================
# 本文件里所有尺寸常量都是**页面 CSS 像素**（= 网页里 100vw/100vh 的单位），不是物理像素。
#   实测（2560×1600 屏 / 150% 缩放）：CSS 像素 × 1.5 = 物理像素；而窗口的 DPI 比例
#   _scale() = GetDpiForWindow/96 = 1.5，正好就是 CSS → 物理的换算系数。
# ⚠️ pywebview 额外有一层坑（v1.30 进程内实测，见 _verify/probe_inproc_units.py）：
#   `Window.x/y/width/height` 报的是 CSS 像素（物理/1.5），但它是**建窗时缓存下来的**，
#   我们自己调 SetWindowPos 改过大小之后它**不会更新** —— 拿它当"当前尺寸"用，
#   就会出现「拉伸一次之后再拉就跳回原尺寸」「拉伸完拖动不跟手」。
#   所以：**任何要"当前几何"的地方一律 GetWindowRect 现读**，别信 _w.width/_w.x。
#   （pywebview 的 resize()/move() 也是两个坑：resize 会乘 DPI 但基准同样过期，
#     move() 这个版本直接抛 ArgumentError —— 见 probe_inproc_units.json 的 C2 记录。）
MINI_W, MINI_H = 420, 330          # 朗读小窗的默认尺寸（CSS）
MINI_MIN_W, MINI_MIN_H = 400, 280  # 小窗最小尺寸（CSS）
# 朗读音频：优先用在线真人发音（有道的词典读音，美音 type=2 / 英音 type=1），
# 下回来按词缓存到本地；连不上或没有该词时，退回系统语音（见 WindowAPI._speak_loop）
AUDIO_URL = "https://dict.youdao.com/dictvoice?audio={word}&type={type}"
AUDIO_CACHE_MAX = 4000          # 缓存文件数上限，超了按最久未用删
DEFAULT_W, DEFAULT_H = 1360, 900   # 默认窗口（CSS）：正好能放下所有页签，不出现滚动条
MIN_W, MIN_H = 1075, 875   # 最小窗口（CSS）：再小（默认字号下）六个页签就会出滚动条

# ShowWindow 的三个命令（红黄绿按钮与"最大化时先还原"都用它，不走 pywebview 的包装）
SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE = 3, 6, 9
def resource_dir():
    """打包后资源在 sys._MEIPASS/webapp；源码运行时用脚本同级目录（或上级的 apps/word-hub）"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, WEBAPP), base,
                 os.path.join(os.path.dirname(base), "apps", "word-hub")):
        if os.path.isfile(os.path.join(cand, "index.html")):
            return cand
    raise SystemExit("找不到 index.html（资源不完整）")


# 没有 WebView2 运行时的官方安装地址（约 2 MB）
WEBVIEW2_DOWNLOAD = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def dpi_scale():
    """系统 DPI 比例（150% → 1.5）。

    ⚠️ pywebview 的 `min_size` 会被它**再乘一次这个比例**才交给 WinForms 的 MinimumSize
    （winforms.py: `MinimumSize = Size(min_size[0]*scale, ...)`）。所以想得到"物理 1075×875 的
    下限"，必须传逻辑值 1075/scale —— 否则 OS 强制的最小尺寸比窗口本身还大，
    **任何移动/缩放都会被往上钳**（v1.34 实测：主窗强制最小 1612×1312 物理 > 窗口实际 1345×875）。
    """
    try:
        u = ctypes.windll.user32
        u.GetDpiForSystem.restype = ctypes.c_uint
        return max(1.0, (u.GetDpiForSystem() or 96) / 96.0)
    except Exception:
        return 1.0


def profile_dir():
    """WebView2 的用户数据目录 —— 学习记录（localStorage）就存在这里。

    ⚠️ 必须固定在一个稳定位置，并且 `private_mode` 要关掉：
    pywebview 默认 `private_mode=True`，会把用户数据放进**临时目录**，
    并在窗口关闭时把它整个 rmtree 掉 —— 结果就是"一关程序，学过的进度全没了"。
    （2026-09-22 查出来：winforms.on_close → browser.clear_user_data() → rmtree）
    """
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
            or os.path.expanduser("~"))
    return os.path.join(base, "WordHub", "webview")


def webview2_missing():
    """是否缺少 WebView2 运行时。缺了 pywebview 会**静默退回 IE11 内核**，界面又慢又乱。"""
    try:
        from webview.platforms import winforms
        return not bool(winforms.is_chromium)
    except Exception:
        return False          # 判断不出来就别打扰用户


def warn_if_no_webview2():
    """缺 WebView2 时先把话说清楚，并给下载地址（否则用户只会看到"界面坏了"）。"""
    if not webview2_missing():
        return
    msg = ("没有检测到 Microsoft Edge WebView2 运行时。\n\n"
           "缺少它时，本程序只能用系统自带的旧版内核渲染，界面会明显变慢、排版也会错乱。\n\n"
           "请先安装（免费，约 2 MB），装完再打开本程序：\n"
           + WEBVIEW2_DOWNLOAD + "\n\n现在仍要继续启动吗？")
    try:
        # MB_YESNO(4) | MB_ICONWARNING(0x30) | MB_TOPMOST(0x40000)；返回 6 = 点了"是"
        r = ctypes.windll.user32.MessageBoxW(None, msg, WINDOW_TITLE, 4 | 0x30 | 0x40000)
    except Exception:
        return
    if r != 6:
        raise SystemExit(1)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ⚠️ 固定端口（v1.32 定位到的根因）：页面地址是 http://127.0.0.1:<端口>/index.html，
#    而 **localStorage 是按 origin 隔离的，origin 里含端口** —— 端口一变，
#    用户上次存的主题/字号/词库/**学习进度**在下次启动就全都"看不见"了
#    （实测 WebView2 的用户数据里躺着 16 个不同端口各一份）。
#    所以端口必须稳定；偶尔被占用就先等一会儿（多半是上一个实例还在退出）。
STABLE_PORT = 17831
PORT_WAIT_SECONDS = 4.0


def storage_path():
    """localStorage 的镜像备份（桌面壳替页面存的一份，见 Serving 的注入逻辑）。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return os.path.join(base, "WordHub", "storage.json")


def read_storage_backup():
    try:
        with open(storage_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d else None
    except (OSError, ValueError):
        return None


def seed_script(seed):
    """生成一段在页面**最早期**运行的小脚本：把备份里「当前缺失」的键补进 localStorage。

    为什么用"注入 HTML"而不是让页面自己调接口：应用一上来就在解析期读 Settings
    （主题/字号/词库），等 JS 桥就绪再恢复就晚了 —— 会先闪一下浅色，甚至按错的词库建队列。
    ⚠️ 只补 `getItem(k) === null` 的键，**绝不覆盖**页面里已有的值。
    """
    payload = json.dumps(seed, ensure_ascii=True).replace("</", "<\\/")
    return ("<script id=\"wh-seed\">(function(){try{var S=" + payload + ",n=0,k;"
            "for(k in S){if(localStorage.getItem(k)===null){localStorage.setItem(k,S[k]);n++;}}"
            "window.__whSeeded=n;}catch(e){window.__whSeeded=-1;}})();</script>\n")


def build_index(directory):
    """读 index.html，并把"存储种子"注入到 <head> 之后（备份为空就原样返回）。"""
    raw = open(os.path.join(directory, "index.html"), "rb").read()
    seed = read_storage_backup()
    if not seed:
        return raw
    html = raw.decode("utf-8")
    if "<head>" not in html:
        return raw
    return html.replace("<head>", "<head>\n" + seed_script(seed), 1).encode("utf-8")


class Serving(http.server.SimpleHTTPRequestHandler):
    """静态文件服务。index.html 走"现读现注入"，其余交给父类。"""

    def log_message(self, *args):
        pass

    def do_GET(self):
        if urllib.parse.urlparse(self.path).path in ("/", "/index.html"):
            try:
                body = build_index(self.directory)
            except OSError:
                body = None
            if body is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
        return super().do_GET()


Quiet = Serving          # 老探针脚本按这个名字引用过，留个别名


def start_server(directory, prefer=None):
    """起本地服务。**优先固定端口**（见 STABLE_PORT 的注释），实在占不到才退回随机端口。"""
    handler = functools.partial(Serving, directory=directory)
    httpd = None
    if prefer:
        deadline = time.time() + PORT_WAIT_SECONDS
        while httpd is None:
            try:
                httpd = http.server.ThreadingHTTPServer(("127.0.0.1", prefer), handler)
            except OSError:
                if time.time() >= deadline:
                    break
                time.sleep(0.3)          # 多半是上一个实例还在退出，等一会
    if httpd is None:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", free_port()), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def own_window(hwnd):
    """这个句柄是不是**本文档进程自己的**窗口？

    ⚠️ 凡是"按标题找窗口"的地方都要拿这个验一下：`FindWindowW` 只按标题取 Z 序第一个，
    同名窗口可能不止一个 —— 拿错了就会把 SetWindowPos / PostMessage 打到别人的窗口上。
    """
    try:
        if not hwnd:
            return False
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(
            ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return False


class WindowAPI:
    """暴露给页面调用的窗口能力：红黄绿三颗按钮 + 拖动 + 8 个边缘缩放热区。

    去边框之后系统既不给标题栏也不再提供边缘拖拽，所以拖动和缩放都由页面驱动、
    这里负责算位置和调 SetWindowPos。

    ⚠️ 窗口对象必须挂在**私有**属性上（_w）：pywebview 会把 js_api 对象的公开属性
    递归遍历一遍来生成 JS 桥（util.get_functions），一旦让 Window 暴露成公开属性，
    它会顺着 native → browser → webview → COM 对象图一路爬到底，
    日志里就会刷 "maximum recursion depth exceeded"，加载期白白浪费大量时间。
    """

    def __init__(self, hide_on_close=False, title=None, min_w=None, min_h=None, port=None):
        # 端口只用来记日志（存储镜像里能看出"这份数据是从哪个 origin 捞的"）
        self._port = port
        # ⚠️ pywebview 把 on_close（内部 Application.Exit()）绑在每个窗口的 FormClosed 上，
        # 所以小窗的"关闭"绝不能 destroy —— 会把主程序一起退掉，只能 hide。
        self._hide_on_close = hide_on_close
        self._title = title or WINDOW_TITLE   # 本实例所属窗口的标题（找句柄用，各找各的）
        self._mini = None
        self._w = None
        self._max = False
        self._drag = None      # 边缘缩放会话
        self._mv = None        # 拖动会话
        self._hwnd = 0
        # 最小尺寸按窗口各给各的（**CSS 像素**）：主窗 1075×875，朗读小窗 400×280
        self._min_w = min_w or MIN_W
        self._min_h = min_h or MIN_H
        # 朗读：单独一个线程 + 一个"只保留最新"的槽位，避免连点 🔊 时叠着念
        self._speak_slot = None
        self._speak_evt = threading.Event()
        self._speak_thread = None
        self._mci_alias = None          # 正在播的 MCI 别名（换词时先关掉，避免叠着念）
        self._sapi_voice = None         # 系统语音实例（stop_speak 要拿它来打断）
        self._prefetching = set()       # 正在后台预取的词，避免重复下载
        self._audio_dir = None

    def bind(self, window):
        self._w = window

    # ---- 窗口句柄 / 缩放比例（缩放时合并成一次 SetWindowPos 要用）----
    def _own_window(self, hwnd):
        """这个句柄是不是**本文档进程自己的**窗口？（兜底路径必须验）"""
        return own_window(hwnd)

    def _find_by_enum(self):
        """枚举顶层窗口，找「本进程 + 标题完全匹配」的那一个。

        ⚠️ 比 `FindWindowW` 可靠：后者只按标题取 Z 序第一个，**可能是别人的窗口**
        （v1.32 实测 FindWindowW 给的 41353842 根本不是真实主窗 86578632）。
        拿错句柄的后果是所有 SetWindowPos 都打在别人身上 ——
        表现就是「主窗怎么拖都不动，而小窗一切正常」（小窗标题唯一所以碰巧对）。
        """
        try:
            u = ctypes.windll.user32
            u.EnumWindows.restype = ctypes.c_bool
            u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM), ctypes.wintypes.LPARAM]
            u.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
            u.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND,
                                                   ctypes.POINTER(ctypes.wintypes.DWORD)]
            me = os.getpid()
            found = []

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            def cb(hwnd, _l):
                pid = ctypes.wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != me:
                    return True
                buf = ctypes.create_unicode_buffer(512)
                u.GetWindowTextW(hwnd, buf, 512)
                if buf.value == self._title:
                    found.append(int(hwnd))
                return True

            u.EnumWindows(cb, 0)
            return found[0] if found else 0
        except Exception:
            return 0

    def _find_by_title(self):
        """最后兜底：FindWindowW 按标题找，**必须验明是本进程的窗口**才认。"""
        try:
            u = ctypes.windll.user32
            u.FindWindowW.restype = ctypes.c_void_p
            u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            h = int(u.FindWindowW(None, self._title) or 0)
            return h if self._own_window(h) else 0
        except Exception:
            return 0

    def _handle(self):
        """当前 API 实例所属窗口的句柄；拿不到就返回 0（**绝不猜、绝不缓存猜的**）。

        三级解析（越靠后越要验）：
          ① `self._w.native.Handle` —— pywebview 建窗时挂上来的 WinForms 句柄，
             按定义就是本窗口的，可信；
          ② 枚举顶层窗口，找本进程里标题完全匹配的那个（比 FindWindowW 可靠）；
          ③ FindWindowW 兜底，但**验明属于本进程**才认。
        ⚠️ 认不出来就返回 0 且**不写进缓存**，下次调用重新解析 —— 启动早期窗口还没
        建好时第一次解析必然失败，如果那时把"猜到的"句柄缓存下来，
        之后每一次拖动/缩放都会打在错的窗口上（v1.30 就是这么让主窗"永久失灵"的）。
        """
        if self._hwnd:
            return self._hwnd
        try:
            if self._w is not None:
                h = self._w.native.Handle
                # ⚠️ 不能写 int(h)：pythonnet 的 System.IntPtr 不支持 int()，会抛 TypeError
                # （v1.32 真窗口实测：一抛就退回按标题找 → 主窗完全缩放不了。必须 ToInt64()）
                if hasattr(h, "ToInt64"):
                    h = h.ToInt64()
                h = int(h)
                if h:
                    self._hwnd = h
                    return self._hwnd
        except Exception:
            pass
        for h in (self._find_by_enum(), self._find_by_title()):
            if h:
                self._hwnd = h
                return self._hwnd
        return 0

    def _scale(self):
        h = self._handle()
        if not h:
            return 1.0
        try:
            u = ctypes.windll.user32
            u.GetDpiForWindow.restype = ctypes.c_uint
            u.GetDpiForWindow.argtypes = [ctypes.c_void_p]
            return (u.GetDpiForWindow(h) or 96) / 96.0
        except Exception:
            return 1.0

    def _rect_phys(self):
        """**现读**窗口的真实矩形（物理像素）。

        ⚠️ 别改用 pywebview 的 `_w.x/_w.y/_w.width/_w.height`：那是建窗时缓存的值，
        我们自己 SetWindowPos 改过之后它不更新 —— 拿它当"当前几何"就会出现
        「拉伸一次之后再拉就跳回原尺寸」「拉伸完拖动不跟手」（v1.30 定位到的根因）。
        """
        hwnd = self._handle()
        if not hwnd:
            return None
        try:
            u = ctypes.windll.user32
            u.GetWindowRect.restype = ctypes.c_bool
            u.GetWindowRect.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.wintypes.RECT)]
            rc = ctypes.wintypes.RECT()
            if not u.GetWindowRect(hwnd, ctypes.byref(rc)):
                return None
            return (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
        except Exception:
            return None

    def _cursor_phys(self):
        """系统光标位置（物理像素）。

        ⚠️ 为什么不用页面传过来的 `ev.screenX/screenY`：那是 CSS 像素，换算系数取决于
        页面的 devicePixelRatio —— 而**沙箱里 WebView2 渲染进程起不来，这个假设没法验证**。
        GetCursorPos 在本进程里读出来就是物理像素（v1.30 进程内实测：外部感知进程把光标
        设到 (1000,800)，本进程读回正是 (1000,800)），于是拖动/缩放全程只用一套单位。
        """
        try:
            u = ctypes.windll.user32
            u.GetCursorPos.restype = ctypes.c_bool
            u.GetCursorPos.argtypes = [ctypes.POINTER(ctypes.wintypes.POINT)]
            p = ctypes.wintypes.POINT()
            if not u.GetCursorPos(ctypes.byref(p)):
                return None
            return (p.x, p.y)
        except Exception:
            return None

    def _zoomed(self):
        """窗口当前是不是最大化状态（问系统，不问我们自己的标志位）。"""
        hwnd = self._handle()
        if not hwnd:
            return False
        try:
            u = ctypes.windll.user32
            u.IsZoomed.restype = ctypes.c_bool
            u.IsZoomed.argtypes = [ctypes.c_void_p]
            return bool(u.IsZoomed(hwnd))
        except Exception:
            return False

    def _show(self, cmd):
        hwnd = self._handle()
        if not hwnd:
            return False
        try:
            u = ctypes.windll.user32
            u.ShowWindow.restype = ctypes.c_bool
            u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            u.ShowWindow(hwnd, cmd)
            return True
        except Exception:
            return False

    def _restore(self):
        """从最大化还原，并**等到真的是还原态**才返回。

        ⚠️ 为什么必须做这件事：**最大化状态下 Windows 会忽略对窗口的移动和尺寸修改**
        （最大化窗口本来就不能被拖、被拉伸）。而启动/拖动时用的那个 `self._max` 标志位
        只是个估计值 —— 一旦它跟系统真实状态对不上，拖动和缩放就会"点了完全没反应"，
        而小窗因为从不最大化一直好好的（甲方 v1.37 反馈的正是"主窗完全不能拖/不能拉伸、
        小窗非常完美"）。所以这里改用 `IsZoomed` 问系统，并用 `ShowWindow(SW_RESTORE)`
        而不是 pywebview 的包装方法（这个版本 pywebview 的 `move()` 实测直接抛
        ArgumentError，同一批方法都不可靠）。
        """
        hwnd = self._handle()
        if hwnd:
            if self._show(SW_RESTORE):
                for _ in range(25):              # 等它真的退出最大化（最多 ~0.5 秒）
                    if not self._zoomed():
                        return True
                    time.sleep(0.02)
                return True
        try:
            self._w.restore()                    # 拿不到句柄才退回 pywebview
        except Exception:
            pass
        return False

    def _min_phys(self):
        """最小尺寸换算成物理像素（常量是 CSS 像素，要乘 DPI 比例）。"""
        sc = self._scale()
        return (int(round(self._min_w * sc)), int(round(self._min_h * sc)))

    def fit_to_design(self, css_w=None, css_h=None):
        """把窗口调成设计尺寸（**默认尺寸不准的兜底**）。

        ⚠️ 实测 `create_window(width=1360, height=900)` 出来的窗口比要的小一圈
        （宽少 15、高少 38 CSS 像素，小窗同样少这些）—— 高度矮 12px 就可能让某个页签
        冒出滚动条。所以开窗后显式再摆一次，位置保持不动，只把尺寸摆正。
        """
        rect = self._rect_phys()
        if rect is None:
            return False
        sc = self._scale()
        w = int(round((css_w or DEFAULT_W) * sc))
        h = int(round((css_h or DEFAULT_H) * sc))
        # 保持视觉居中，并确保整窗留在屏幕工作区内（否则新尺寸可能把窗口顶出屏幕）
        try:
            u = ctypes.windll.user32
            work_w = u.GetSystemMetrics(0)
            work_h = u.GetSystemMetrics(1)
            cx = rect[0] + rect[2] // 2
            cy = rect[1] + rect[3] // 2
            x = max(0, min(cx - w // 2, max(0, work_w - w)))
            y = max(0, min(cy - h // 2, max(0, work_h - h)))
        except Exception:
            x, y = rect[0], rect[1]
        return self._set_rect(x, y, w, h)

    def _set_rect(self, x, y, w, h):
        """一次 SetWindowPos 同时改位置和大小（**坐标是物理像素**）。

        pywebview 的 resize()+move() 是两次调用 = 两次全窗重排，缩放时会明显发涩。"""
        hwnd = self._handle()
        if not hwnd:
            return False
        try:
            u = ctypes.windll.user32
            u.SetWindowPos.restype = ctypes.c_bool
            u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_uint]
            SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
            u.SetWindowPos(hwnd, None, int(x), int(y), int(w), int(h), SWP_NOZORDER | SWP_NOACTIVATE)
            return True
        except Exception:
            return False

    def _move_phys(self, x, y):
        """只改位置、不动尺寸的 SetWindowPos（坐标是物理像素）。

        ⚠️ 拖动**不能**用 pywebview 的 `_w.move()`：它在 DPI > 100% 时换算不准
        （pywebview issue #1645 就是在报这个"scale factor 处理不对"）。
        这里和缩放走同一套已实测精确的单位（本进程 DPI 感知 → SetWindowPos 收物理像素）。
        """
        hwnd = self._handle()
        if not hwnd:
            return False
        try:
            u = ctypes.windll.user32
            u.SetWindowPos.restype = ctypes.c_bool
            u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_uint]
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
            return bool(u.SetWindowPos(hwnd, None, int(x), int(y), 0, 0,
                                       SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))
        except Exception:
            return False

    # ---- 存储镜像（localStorage 的备用副本）----
    def save_storage(self, data):
        """页面把 localStorage 快照交上来，落成 storage.json。

        为什么需要：页面源是 `http://127.0.0.1:<端口>`，**localStorage 按 origin 隔离** ——
        万一端口变了（被别的程序占了等），用户上次的主题/字号/词库/学习进度就"看不见"了。
        有了这份副本，下次启动时桌面壳会把**缺失的键**注入回去（见 seed_script）。
        顺手也成了迁移工具：用 `--port <老端口>` 起一次，就能把散在那个 origin 里的数据捞出来。
        """
        try:
            d = json.loads(data) if isinstance(data, str) else data
            if not isinstance(d, dict) or not d:
                return False
            if not any(k.startswith("wh_") for k in d):
                return False          # 空壳快照不落盘，免得把好备份覆盖掉
            p = storage_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.isfile(p):     # 留一代旧备份，万一新快照不完整还能捞回来
                try:
                    with open(p, "rb") as f:
                        prev = f.read()
                    with open(p + ".bak", "wb") as f:
                        f.write(prev)
                except OSError:
                    pass
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            os.replace(tmp, p)
            log_interact(f"存储镜像 端口={self._port} 键={len(d)} "
                         f"主题={d.get('wh_setting_theme')!r} 词库={d.get('wh_setting_list')!r}")
            return True
        except Exception:
            return False

    # ---- 红黄绿 ----
    def minimize(self):
        if not self._show(SW_MINIMIZE):
            self._w.minimize()
        return True

    def zoom(self):
        """最大化 / 还原。返回值 = 切换后是否处于最大化（页面据此显示"还原/缩放"）。"""
        if self._zoomed():
            self._restore()
        else:
            if not self._show(SW_MAXIMIZE):
                try:
                    self._w.maximize()
                except Exception:
                    pass
        self._max = self._zoomed()
        return self._max

    def close(self):
        if self._hide_on_close:
            self._w.hide()
        else:
            self._destroy_mini()          # 关主窗要顺手把小窗也关掉，否则程序退不出去
            self._w.destroy()
        return True

    def _destroy_mini(self):
        """把小窗也关掉。⚠️ pywebview 的 on_close 里只有 `len(BrowserView.instances) == 0`
        时才真正 Application.Exit() —— 也就是**必须所有窗口都关闭，程序才会退出**；
        主窗关了而小窗还在，进程会一直留在后台。

        这里用 PostMessage(WM_CLOSE) 而不是 Window.destroy()：
        destroy() 内部走 Control.Invoke 同步马歇尔到 UI 线程，而本函数会在主窗关闭的
        回调（另一个线程）里被调用，UI 线程正在销毁 WebView2 —— 同步等待会卡很久。
        PostMessage 是异步投递，立刻返回。
        """
        if self._mini is None:
            return
        try:
            if self._mini not in webview.windows:
                return
            u = ctypes.windll.user32
            # ⚠️ 优先用小窗自己的句柄；按标题找的话必须验明是本进程的窗口
            hwnd = 0
            try:
                h = self._mini.native.Handle
                if hasattr(h, "ToInt64"):
                    h = h.ToInt64()
                hwnd = int(h)
            except Exception:
                hwnd = 0
            if not hwnd:
                u.FindWindowW.restype = ctypes.c_void_p
                u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
                hwnd = int(u.FindWindowW(None, MINI_TITLE) or 0)
                if not own_window(hwnd):
                    hwnd = 0
            if hwnd:
                u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_void_p, ctypes.c_void_p]
                u.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE，异步
        except Exception:
            pass

    # ---- 朗读小窗 ----
    def attach_mini(self, window):
        """主窗 API 记住小窗句柄，用于开关它。"""
        self._mini = window

    def open_mini(self):
        """把朗读小窗显示出来（窗口在建主窗时已一起建好）。

        ⚠️ 甲方要求：打开小窗**默认不自动开始朗读**，要播放请自己按 ▶。
        """
        # 小窗可能已经被用户关掉（比如 Alt+F4）—— 那时它已不在 webview.windows 里，
        # 这里返回 False，页面会退回「浏览器弹窗」方案
        if self._mini is None or self._mini not in webview.windows:
            return False
        try:
            self._mini.show()
            self._mini.evaluate_js("window.__miniOpened && window.__miniOpened()")
        except Exception:
            return False
        return True

    def set_on_top(self, on=True):
        """小窗置顶开关（页面上的 📌）。"""
        if self._w is None:
            return False
        try:
            self._w.on_top = bool(on)
        except Exception:
            return False
        return True


    # ---- 拖动窗口（页面自己实现拖动，不用 pywebview 内置的 drag region）----
    def begin_drag(self, *_ignored):
        """按下瞬间记下「窗口矩形 + 光标位置」（都是物理像素），之后按光标位移搬窗口。

        两种"官方做法"都试过、都不行：
          a) 用 `_w.x/_w.y` 读当前位置 → 那是建窗时的缓存值，用户拉伸过之后就是错的；
          b) pywebview 官方的「位置式」（传 `ev.screenX - ev.clientX`）→ 页面给的坐标是
             CSS 像素，换算系数依赖 devicePixelRatio，而沙箱里验证不了；
          最终改成：**位置和位移全部现读现算**——`GetWindowRect` 拿当前矩形、
          `GetCursorPos` 拿当前光标，两者在同一进程里都是物理像素（已实测），
          于是"拉伸完再拖不跟手""先缩再拖不行"这类不一致从根上没有了。
        """
        if self._w is None:
            return False
        # 最大化状态下拖：先还原（和系统标题栏行为一致）。
        # ⚠️ 必须问系统 `IsZoomed`，不能只看 self._max —— 标志位一旦跟真实状态脱节
        # （比如窗口被别的途径最大化过），拖动就会"点了完全没反应"（最大化窗口不可移动）。
        was_zoom = bool(self._max or self._zoomed())
        if was_zoom:
            self._restore()
        self._max = False
        rect = self._rect_phys()
        cur = self._cursor_phys()
        if rect is None or cur is None:
            log_interact(f"拖动 begin 失败：句柄={self._handle()} 矩形={rect} 光标={cur} [{self._title}]")
            return False
        self._mv = {"rect": rect, "cur": cur, "last": (rect[0], rect[1])}
        log_interact(f"拖动 begin 句柄={self._handle()} 起点={rect} 最大化过={was_zoom} [{self._title}]")
        return True

    def drag_to(self, *_ignored):
        """按「光标从按下到现在走了多远」平移窗口（全程物理像素，不做任何比例换算）。"""
        d = self._mv
        if not d:
            return False
        cur = self._cursor_phys()
        if cur is None:
            return False
        x = d["rect"][0] + (cur[0] - d["cur"][0])
        y = d["rect"][1] + (cur[1] - d["cur"][1])
        if (x, y) == d.get("last"):      # 光标没动就别白调一次 SetWindowPos
            return True
        d["last"] = (x, y)
        return self._move_phys(x, y)

    def end_drag(self):
        if self._mv:
            log_interact(f"拖动 end  终点={self._rect_phys()} [{self._title}]")
        self._mv = None
        return True

    # ---- 朗读：优先在线真人发音，退回系统 SAPI ----
    def speak(self, word, accent="en-US"):
        """念一个单词（异步，不阻塞页面）。

        为什么要绕开浏览器：新版 Edge/WebView2 改成枚举 OneCore 语音后，机器上没装英文语音包时
        speechSynthesis.getVoices() 里一个英文语音都没有，浏览器就会拿中文语音去念英文。
        这里改成：**先用在线真人发音**（词典读音，最自然）→ 缓存到本地 → 拿不到才用系统语音。
        """
        text = str(word or "").strip()
        if not text:
            return False
        self._speak_slot = (text, str(accent or "en-US"))
        self._ensure_speaker()
        self._speak_evt.set()          # 只保留最新一条，旧的直接丢掉
        return True

    def prefetch(self, word, accent="en-US"):
        """后台把音频下下来放缓存，让后面点 🔊 立刻出声（失败就算了，不影响任何功能）。"""
        text = str(word or "").strip()
        if not text:
            return False
        key = (text.lower(), "gb" if "GB" in str(accent).upper() else "us")
        if key in self._prefetching:
            return True
        self._prefetching.add(key)

        def _job():
            try:
                self._audio_file(text, accent)
            except Exception:
                pass
            finally:
                self._prefetching.discard(key)

        threading.Thread(target=_job, daemon=True).start()
        return True

    def _ensure_speaker(self):
        if self._speak_thread is not None:
            return
        self._speak_thread = threading.Thread(target=self._speak_loop, daemon=True)
        self._speak_thread.start()

    # ---- 在线真人发音 + 本地缓存 ----
    def _cache_dir(self):
        if self._audio_dir:
            return self._audio_dir
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
        d = os.path.join(base, "WordHub", "audio")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            d = os.environ.get("TEMP", ".")
        self._audio_dir = d
        return d

    def _audio_file(self, word, accent):
        """返回已经落盘的音频路径；需要就先下载。拿不到返回 None。"""
        book = "gb" if "GB" in str(accent).upper() else "us"
        safe = "".join(ch for ch in word.lower() if ch.isalnum() or ch in "-_'")
        if not safe:
            return None
        path = os.path.join(self._cache_dir(), f"{safe}.{book}.mp3")
        if os.path.isfile(path) and os.path.getsize(path) > 1024:
            try:
                os.utime(path, None)          # 刷新访问时间，配合 LRU 清理
            except OSError:
                pass
            return path
        url = AUDIO_URL.format(word=urllib.parse.quote(word), type=1 if book == "gb" else 2)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
                "Referer": "https://www.youdao.com/",
            })
            with urllib.request.urlopen(req, timeout=6) as r:
                body = r.read()
            # 至少要像个音频：ID3 头或 MPEG 帧同步字，且不能太小
            if len(body) < 1024 or not (body[:3] == b"ID3" or body[0] == 0xFF):
                return None
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
            self._trim_cache()
            return path
        except Exception:
            return None

    def _trim_cache(self):
        """缓存只留最近用到的若干条，别让它无限长大（一条约 15 KB）。"""
        try:
            d = self._cache_dir()
            files = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".mp3")]
            if len(files) <= AUDIO_CACHE_MAX:
                return
            files.sort(key=lambda p: os.path.getmtime(p))
            for p in files[:len(files) - AUDIO_CACHE_MAX]:
                try:
                    os.remove(p)
                except OSError:
                    pass
        except Exception:
            pass

    def _play_mp3(self, path):
        """用 Windows 的 MCI 播 MP3（系统自带，不需要任何额外依赖）。"""
        try:
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                            ctypes.c_uint, ctypes.c_void_p]
            winmm.mciSendStringW.restype = ctypes.c_uint
            if self._mci_alias:                       # 先停掉上一句，否则会叠着念
                winmm.mciSendStringW("close " + self._mci_alias, None, 0, None)
                self._mci_alias = None
            alias = "whvoice"
            if winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, None) != 0:
                return False
            self._mci_alias = alias
            if winmm.mciSendStringW(f"play {alias}", None, 0, None) != 0:
                winmm.mciSendStringW("close " + alias, None, 0, None)
                self._mci_alias = None
                return False
            return True
        except Exception:
            return False

    def _speak_loop(self):
        """朗读都在这条常驻线程里串行完成（系统语音 SAPI 是 STA 对象，必须固定线程用）。"""
        try:
            import comtypes
            import comtypes.client
        except ImportError:
            comtypes = None
        else:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass
        voice = None
        while True:
            self._speak_evt.wait()
            self._speak_evt.clear()
            slot = self._speak_slot
            self._speak_slot = None
            if not slot:
                continue
            text, accent = slot
            # 一、在线真人发音（最自然）
            path = self._audio_file(text, accent)
            if path and self._play_mp3(path):
                continue
            # 二、退回系统语音
            if comtypes is None:
                continue
            try:
                if voice is None:
                    voice = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
                    voice.Rate = -1      # SAPI 语速 -10..10；-1 大致对应浏览器里的 rate 0.9
                    self._sapi_voice = voice
                self._pick_voice(voice, accent)
                # 3 = SVSFlagsAsync(1) | SVSFPurgeBeforeSpeak(2)：
                # 异步返回（这样下面的 stop_speak 才有机会掐断它），且先清掉上一句，不会叠着念
                voice.Speak(text, 3)
            except Exception:
                voice = None             # 出问题就下次重建
                self._sapi_voice = None

    def stop_speak(self):
        """把手上正在念的内容掐掉（关掉朗读小窗时用）。

        三件事都要做：丢掉排队中的下一个词、停掉在线音频（MCI）、打断系统语音（SAPI）。
        """
        try:
            self._speak_slot = None      # 排队中的下一个词不念了
            self._speak_evt.clear()
        except Exception:
            pass
        try:
            winmm = ctypes.windll.winmm
            winmm.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                             ctypes.c_uint, ctypes.c_void_p]
            winmm.mciSendStringW.restype = ctypes.c_uint
            if self._mci_alias:
                winmm.mciSendStringW("close " + self._mci_alias, None, 0, None)
                self._mci_alias = None
        except Exception:
            pass
        try:
            v = getattr(self, "_sapi_voice", None)
            if v is not None:
                v.Speak("", 2)           # SPF_PURGEBEFORESPEAK：清掉正在念的
        except Exception:
            pass
        return True

    @staticmethod
    def _pick_voice(voice, accent):
        """在 SAPI 语音里挑英文的那个（优先匹配口音：en-US / en-GB）。"""
        try:
            tokens = voice.GetVoices()
        except Exception:
            return
        # 注意：SAPI 的语音描述里英音写作 "English (United Kingdom)" / "English (Great Britain)"，
        # 并没有 "GB" 字样，所以不能只拿 "GB" 去匹配，否则选英音会失效、悄悄退回美音。
        want_gb = "GB" in str(accent).upper()
        is_gb = lambda d: ("UNITED KINGDOM" in d) or ("GREAT BRITAIN" in d) or ("-GB" in d)
        is_us = lambda d: ("UNITED STATES" in d) or ("-US" in d)
        exact, any_en = None, None
        for i in range(tokens.Count):
            desc = tokens.Item(i).GetDescription().upper()
            if "ENGLISH" not in desc and "-EN" not in desc:
                continue
            if any_en is None:
                any_en = i
            if want_gb and is_gb(desc):
                exact = i
                break
            if (not want_gb) and is_us(desc):
                exact = i
                break
        pick = exact if exact is not None else any_en
        if pick is not None:
            voice.Voice = tokens.Item(pick)

    # ---- 边缘缩放 ----
    def begin_resize(self, edge):
        """按下瞬间记下「窗口矩形 + 光标位置」（物理像素）与要拖的边。

        ⚠️ 基准**必须现读**（v1.30 定位到的真根因）：原来用 `int(self._w.width)` 当基准，
        而 pywebview 那个值是**建窗时缓存的**，用户拉伸过一次之后就一直是初始尺寸 →
        再拉就按初始宽度重算 → 窗口"自己跳回去变小"（甲方原话："试图拉伸就自动缩小了"）。
        """
        if self._w is None:
            return False
        # 最大化时拖边缘：先还原，否则 Windows 会忽略尺寸修改（见 _restore 的注释）
        was_zoom = bool(self._max or self._zoomed())
        if was_zoom:
            self._restore()
        self._max = False
        rect = self._rect_phys()
        cur = self._cursor_phys()
        if rect is None or cur is None:
            log_interact(f"缩放 begin 失败：句柄={self._handle()} 矩形={rect} 光标={cur} 边={edge} [{self._title}]")
            return False
        self._drag = {"edge": str(edge), "rect": rect, "cur": cur}
        self._max = False
        log_interact(f"缩放 begin 句柄={self._handle()} 边={edge} 起点={rect} 最大化过={was_zoom} [{self._title}]")
        return True

    def update_resize(self, *_ignored):
        """按光标位移改窗口尺寸（全程物理像素）。

        换算链只剩一条、且每个量都是现读的：
            光标物理位移 → 加在「按下时的真实矩形」上 → 夹到最小尺寸 → SetWindowPos
        不再有 `_w.width`（会过期）、`_scale()` 双向换算（易混）、页面 CSS 坐标（验证不了）。
        """
        d = self._drag
        if not d:
            return False
        cur = self._cursor_phys()
        if cur is None:
            return False
        dx = cur[0] - d["cur"][0]
        dy = cur[1] - d["cur"][1]
        edge = d["edge"]
        x0, y0, w0, h0 = d["rect"]
        x, y, w, h = x0, y0, w0, h0
        if "e" in edge:
            w = w0 + dx
        if "s" in edge:
            h = h0 + dy
        if "w" in edge:
            w = w0 - dx
            x = x0 + dx
        if "n" in edge:
            h = h0 - dy
            y = y0 + dy
        # 下限：常量是 CSS 像素 → 换成物理再比（v1.33 曾拿物理常量去比 CSS 尺寸，差 1.5 倍）
        min_w, min_h = self._min_phys()
        if w < min_w:
            if "w" in edge:
                x = x0 + w0 - min_w      # 往左拉到底时，右边要钉住不动
            w = min_w
        if h < min_h:
            if "n" in edge:
                y = y0 + h0 - min_h
            h = min_h
        return self._set_rect(x, y, w, h)

    def end_resize(self):
        if self._drag:
            log_interact(f"缩放 end  终点={self._rect_phys()} 边={self._drag['edge']} [{self._title}]")
        self._drag = None
        return True

    def maximized(self):
        return bool(self._max)


def selftest(directory, port):
    lines = []

    def out(msg):
        lines.append(msg)
        print(msg)

    out(f"资源目录：{directory}")
    ok = True


    # ---- 朗读链路自检：comtypes + 系统 SAPI 的英文语音 ----
    # 不放声，只确认包装后能拿到 comtypes、能选中英文语音
    speak_ok = False
    try:
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitialize()
        except Exception:
            pass
        voice = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
        tokens = voice.GetVoices()
        names = [tokens.Item(k).GetDescription() for k in range(tokens.Count)]
        WindowAPI._pick_voice(voice, "en-US")
        picked = None
        try:
            picked = voice.Voice.GetDescription()
        except Exception:
            pass
        out(f"  朗读：comtypes 可用，SAPI 语音 {len(names)} 个，选中 {picked}")
        speak_ok = bool(picked) and any("ENGLISH" in n.upper() for n in names)
        if not speak_ok:
            out("  朗读：没选中英文语音（桌面版会退回浏览器语音）")
    except Exception as e:
        out(f"  朗读链路不可用: {type(e).__name__} {e}   —— 桌面版会退回浏览器语音")
    ok = ok and speak_ok

    # ---- 在线真人发音源可达性（只报告，不影响自检结论：断网会自动退回系统语音）----
    try:
        req = urllib.request.Request(
            AUDIO_URL.format(word="test", type=2),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.youdao.com/"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            sample = r.read()
        if sample[:3] == b"ID3" or (sample and sample[0] == 0xFF):
            out(f"  在线发音源：可达，示例音频 {len(sample)} B（真人读音，优先使用）")
        else:
            out(f"  在线发音源：返回的不是音频（{len(sample)} B）—— 会改用系统语音")
    except Exception as e:
        out(f"  在线发音源：不可达（{type(e).__name__}）—— 会改用系统语音")

    for name in NEEDED:
        path = os.path.join(directory, name)
        exists = os.path.isfile(path)
        size = os.path.getsize(path) if exists else 0
        out(f"  {'OK  ' if exists and size else 'MISS'} {name:16s} {size:>9,d} B")
        ok = ok and exists and size > 0
    for name in NEEDED:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/{name}", timeout=10) as r:
            body = r.read()
        out(f"  SERVED {name:16s} HTTP {r.status}  {len(body):>9,d} B")
        ok = ok and r.status == 200 and len(body) > 0
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html?desktop=1", timeout=10) as r:
        html = r.read().decode("utf-8", "replace")
    for probe in ("词枢", "macbar", "accentSel"):
        hit = probe in html
        out(f"  页面包含「{probe}」：{hit}")
        ok = ok and hit
    out("自检结果：" + ("全部通过" if ok else "有缺失"))
    # --windowed 打包后没有控制台，把结果同时写到磁盘，便于外部核对
    for target in (os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "selftest.log"),
                   os.path.join(os.environ.get("TEMP", "."), "wordhub_selftest.log")):
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            break
        except OSError:
            continue
    return 0 if ok else 1


def server_port_from_args():
    """页面服务端口覆盖（迁移/测试用）：优先 --port N，其次环境变量 WORDHUB_PORT。
    传了就**只用这个端口**（不退回随机），这样才能在指定 origin 上把老数据捞出来。"""
    if "--port" in sys.argv:
        i = sys.argv.index("--port")
        if i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
    env = os.environ.get("WORDHUB_PORT")
    return int(env) if env else None


def debug_port_from_args():
    """远程调试端口（仅供自动化测试连进 WebView2 量帧）：
    优先 --debug-port N，其次环境变量 WORDHUB_DEBUG_PORT，都没有就返回 None（正常启动不开端口）。"""
    if "--debug-port" in sys.argv:
        i = sys.argv.index("--debug-port")
        if i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
    env = os.environ.get("WORDHUB_DEBUG_PORT")
    return int(env) if env else None


def log_interact(msg):
    """把每次拖动/缩放的关键事实追加到 interact.log（只留最近 300 行）。

    用途：万一甲方还反馈"拖不动/拉不动"，看一眼这个日志就能立刻分清是哪一类问题 ——
      ① 页面根本没发事件（日志里没有新的 begin 行）→ 是页面侧（热区被挡/JS 报错）；
      ② 发了但句柄=0 → 是句柄没解析出来；
      ③ 发了、句柄对、矩形也对，但 end 里的矩形没变 → 是系统层面忽略了这次修改
         （例如窗口处于最大化：最大化窗口既不能被拖也不能被拉伸）。
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    path = os.path.join(base, "WordHub", "interact.log")
    try:
        lines = []
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()[-299:]
        lines.append(time.strftime("%m-%d %H:%M:%S ") + msg)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def log_startup(lines):
    """把启动期的关键事实写到磁盘。exe 是 --windowed（没控制台），
    出问题时这是唯一能拿到的现场线索：两个窗口各自的句柄、DPI 比例、真实物理矩形。"""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    target = os.path.join(base, "WordHub", "startup.log")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
    try:                       # 每次启动清掉上一轮交互日志，避免把历史混在一起看
        os.remove(os.path.join(base, "WordHub", "interact.log"))
    except OSError:
        pass


def prime_windows(api, mini_api, mini_window):
    """GUI 起来后：① 把两个窗口摆成设计尺寸；② 真正隐藏小窗。

    ⚠️ ① 这个回调**跑得比窗口建好还早**（实测此时 SetWindowPos 拿不到句柄、静默失败），
    所以要重试到尺寸真对上为止；否则默认尺寸会一直是 pywebview 那个「少 15×38 CSS 像素」
    的值（高度矮 12px 就可能让某个页签冒出滚动条）。
    ⚠️ ② pywebview 的 hidden=True 只是「透明度设 0 再 Show()」——窗口其实还在屏幕上，
    会**吃掉鼠标点击**（看不见但点不动），必须再真正 hide() 一次。
    窗口本身保持创建状态（页面已加载好），之后 open_mini() 秒开。
    """
    targets = ((api, DEFAULT_W, DEFAULT_H, "主窗"), (mini_api, MINI_W, MINI_H, "小窗"))
    for _ in range(40):                          # 最多试 ~6 秒
        done = 0
        for a, cw, ch, _tag in targets:
            try:
                if not a.fit_to_design(cw, ch):
                    continue
                r = a._rect_phys()
                sc = a._scale()
                if r and abs(r[2] - int(round(cw * sc))) <= 2 \
                        and abs(r[3] - int(round(ch * sc))) <= 2:
                    done += 1
            except Exception:
                pass
        if done == len(targets):
            break
        time.sleep(0.15)
    lines = [f"页面源 http://127.0.0.1:{getattr(api, '_port', None)}/"
             f"（localStorage 按 origin 隔离，端口变了就等于换了新存储）",
             f"存储镜像 {storage_path()}（存在={os.path.isfile(storage_path())}）"]
    for a, cw, ch, tag in targets:
        try:
            sc = a._scale()
            lines.append(f"{tag}  标题={a._title!r}  句柄={a._handle()}  "
                         f"DPI比例={sc:.3f}  物理矩形={a._rect_phys()}  "
                         f"期望={int(round(cw * sc))}×{int(round(ch * sc))}")
        except Exception as e:
            lines.append(f"{tag}  出错: {type(e).__name__} {e}")
    log_startup(lines)
    try:
        mini_window.hide()
    except Exception:
        pass


def main():
    directory = resource_dir()
    debug_port = debug_port_from_args()
    if debug_port:
        webview.settings["REMOTE_DEBUGGING_PORT"] = debug_port
        print(f"[debug] WebView2 远程调试端口 {debug_port}", file=sys.stderr, flush=True)

    httpd, port = start_server(directory, prefer=server_port_from_args() or STABLE_PORT)
    print(f"[info] 页面源 http://127.0.0.1:{port}/（localStorage 按 origin 隔离，端口固定才能记住设置）",
          file=sys.stderr, flush=True)
    if "--selftest" in sys.argv:
        code = selftest(directory, port)
        httpd.shutdown()
        raise SystemExit(code)

    warn_if_no_webview2()

    api = WindowAPI(port=port)
    # 拖动改由页面自己实现（指针捕获 + rAF 合帧），不再用 pywebview 内置的 drag region。
    # 这里仍把开关打开只是兜底：内置处理器没有可匹配的元素（页面里已经没有
    # .pywebview-drag-region 这个类了），所以根本不会挂上去。
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = True
    # 固定按默认尺寸开窗：默认大小正好能放下所有页签而不出现滚动条
    window = webview.create_window(
        WINDOW_TITLE,
        f"http://127.0.0.1:{port}/index.html?desktop=1",
        js_api=api,
        width=DEFAULT_W, height=DEFAULT_H,
        # ⚠️ 这里刻意**不设** OS 级最小尺寸，下限由 update_resize 自己钳（实测可用）。
        #    原因：pywebview 的 min_size 会被它乘一次 DPI 比例（v1.30 实测传 1075 CSS
        #    → 系统强制最小 1612×1312 物理），而它乘的那个比例是**开窗瞬间**取的，
        #    可能还是 96dpi（= 1.0）→ 下限时有时无，不可靠；我们自己的钳制用的是
        #    GetDpiForWindow，永远是对的。
        min_size=(1, 1),
        frameless=True,        # 去掉系统标题栏，改由页面画 macOS 风格窗口栏
        easy_drag=False,       # 不整页可拖，只认 drag-region
        resizable=True,
        background_color="#f5f5f7", text_select=True,
    )
    # ---- 朗读小窗：启动时一起建好（hidden），之后靠 show/hide 开关 ----
    mini_api = WindowAPI(hide_on_close=True, title=MINI_TITLE,
                         min_w=MINI_MIN_W, min_h=MINI_MIN_H, port=port)
    mini_window = webview.create_window(
        MINI_TITLE,
        f"http://127.0.0.1:{port}/index.html?desktop=1&mini=1",
        js_api=mini_api,
        width=MINI_W, height=MINI_H,
        min_size=(1, 1),          # 同上：不用 OS 级最小值，小窗下限由 update_resize 钳
        frameless=True, easy_drag=False, resizable=True,
        on_top=True, hidden=True,
        background_color="#f5f5f7", text_select=True,
    )
    mini_api.bind(mini_window)
    api.attach_mini(mini_window)
    # 主窗被系统关掉（Alt+F4、任务栏右键关闭）时也要把小窗带走，否则进程退不出
    window.events.closed += api._destroy_mini

    api.bind(window)

    # ⚠️ private_mode 必须关掉，并把用户数据目录固定下来，否则学习记录关窗即丢（见 profile_dir）
    webview.start(private_mode=False, storage_path=profile_dir(),
                  func=functools.partial(prime_windows, api, mini_api, mini_window))
    httpd.shutdown()


if __name__ == "__main__":
    main()
