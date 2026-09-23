"""词枢记词器 · 桌面版启动器

做法：起一个只监听 127.0.0.1 的本地 HTTP 服务（保证 localStorage / 相对路径脚本都正常），
再用 WebView2 开一个**无边框**原生窗口，窗口栏由页面自己画成 macOS 风格（红黄绿）。

用法：
  python wordhub_launcher.py            正常启动窗口
  python wordhub_launcher.py --selftest 只起服务并逐个核对内置文件，验证打包完整性后退出
"""
import ctypes
import functools
import http.server
import os
import socket
import sys
import threading
import urllib.parse
import urllib.request

import webview

WEBAPP = "webapp"
# 应用运行必需的文件（打包后用于自检）
NEEDED = [
    "index.html",
    "data_cet4.js", "data_cet6.js", "data_ky.js",
    "data_ielts.js", "data_toefl.js", "data_gre.js",
    "data_sent.js",
]
WINDOW_TITLE = "词枢 · 英语记词器"
MINI_TITLE = "词枢 · 朗读小窗"
MINI_W, MINI_H = 420, 330          # 朗读小窗的默认尺寸（控件上移 + 间距收紧后才压得下来）
# 朗读音频：优先用在线真人发音（有道的词典读音，美音 type=2 / 英音 type=1），
# 下回来按词缓存到本地；连不上或没有该词时，退回系统语音（见 WindowAPI._speak_loop）
AUDIO_URL = "https://dict.youdao.com/dictvoice?audio={word}&type={type}"
AUDIO_CACHE_MAX = 4000          # 缓存文件数上限，超了按最久未用删
DEFAULT_W, DEFAULT_H = 1360, 900   # 默认窗口：正好能放下所有页签，不出现滚动条
# 窗口尺寸的注释要点：本机显示器 150% 缩放，所以窗口 2018×1312 物理 = 1345×874 CSS，
# 页面（WebView2）拿到的就是 1345×874 CSS 像素 —— 测试要按这个尺寸测，不能按外框数字测。
MIN_W, MIN_H = 1075, 875   # 最小窗口也要保证（默认字号下）六个页签都不出现滚动条
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


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_server(directory):
    port = free_port()
    handler = functools.partial(Quiet, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


class WindowAPI:
    """暴露给页面调用的窗口能力：红黄绿三颗按钮 + 拖动 + 8 个边缘缩放热区。

    去边框之后系统既不给标题栏也不再提供边缘拖拽，所以拖动和缩放都由页面驱动、
    这里负责算位置和调 SetWindowPos。

    ⚠️ 窗口对象必须挂在**私有**属性上（_w）：pywebview 会把 js_api 对象的公开属性
    递归遍历一遍来生成 JS 桥（util.get_functions），一旦让 Window 暴露成公开属性，
    它会顺着 native → browser → webview → COM 对象图一路爬到底，
    日志里就会刷 "maximum recursion depth exceeded"，加载期白白浪费大量时间。
    """

    def __init__(self, hide_on_close=False):
        # ⚠️ pywebview 把 on_close（内部 Application.Exit()）绑在每个窗口的 FormClosed 上，
        # 所以小窗的"关闭"绝不能 destroy —— 会把主程序一起退掉，只能 hide。
        self._hide_on_close = hide_on_close
        self._mini = None
        self._w = None
        self._max = False
        self._drag = None      # 边缘缩放会话
        self._mv = None        # 拖动会话
        self._hwnd = 0
        # 朗读：单独一个线程 + 一个"只保留最新"的槽位，避免连点 🔊 时叠着念
        self._speak_slot = None
        self._speak_evt = threading.Event()
        self._speak_thread = None
        self._mci_alias = None          # 正在播的 MCI 别名（换词时先关掉，避免叠着念）
        self._prefetching = set()       # 正在后台预取的词，避免重复下载
        self._audio_dir = None

    def bind(self, window):
        self._w = window

    # ---- 窗口句柄 / 缩放比例（缩放时合并成一次 SetWindowPos 要用）----
    def _handle(self):
        if self._hwnd:
            return self._hwnd
        try:
            u = ctypes.windll.user32
            u.FindWindowW.restype = ctypes.c_void_p
            u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            h = u.FindWindowW(None, WINDOW_TITLE)
            if h:
                self._hwnd = h
        except Exception:
            pass
        return self._hwnd

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

    def _set_rect(self, x, y, w, h):
        """一次 SetWindowPos 同时改位置和大小。
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

    # ---- 红黄绿 ----
    def minimize(self):
        self._w.minimize()
        return True

    def zoom(self):
        if self._max:
            self._w.restore()
        else:
            self._w.maximize()
        self._max = not self._max
        return True

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
            u.FindWindowW.restype = ctypes.c_void_p
            u.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            hwnd = u.FindWindowW(None, MINI_TITLE)
            if hwnd:
                u.PostMessageW(hwnd, 0x0010, 0, 0)      # WM_CLOSE，异步
        except Exception:
            pass

    # ---- 朗读小窗 ----
    def attach_mini(self, window):
        """主窗 API 记住小窗句柄，用于开关它。"""
        self._mini = window

    def open_mini(self):
        """把朗读小窗显示出来（窗口在建主窗时已一起建好，这里只是 Show + 通知页面开始播）。"""
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


    # ---- 边缘缩放 ----
    # ---- 拖动窗口（页面自己实现拖动，不用 pywebview 内置的 drag region）----
    def begin_drag(self, screen_x, screen_y, css_w, css_h):
        """记下「按下时光标位置 + 当时窗口位置」，之后每次移动只传光标位置。
        用「起始位置 + 位移」算，而不是每帧读一次窗口位置，避免累积误差与多余的系统调用。"""
        if self._w is None:
            return False
        if self._max:                 # 最大化状态下拖：先还原（和系统标题栏行为一致）
            self._w.restore()
            self._max = False
        css_w = float(css_w) or 1.0
        self._mv = {
            "sx": float(screen_x), "sy": float(screen_y),
            "x": int(self._w.x), "y": int(self._w.y),
            "k": (float(self._w.width) / css_w) or 1.0,   # 屏幕坐标是 CSS 像素，窗口坐标是逻辑像素
        }
        return True

    def drag_move(self, screen_x, screen_y):
        d = self._mv
        if not d or self._w is None:
            return False
        dx = (float(screen_x) - d["sx"]) * d["k"]
        dy = (float(screen_y) - d["sy"]) * d["k"]
        self._w.move(int(d["x"] + dx), int(d["y"] + dy))
        return True

    def end_drag(self):
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
                self._pick_voice(voice, accent)
                voice.Speak(text, 0)     # 同步：念完再取下一个，避免叠着念
            except Exception:
                voice = None             # 出问题就下次重建

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
    def begin_resize(self, edge, screen_x, screen_y, css_w, css_h):
        if self._w is None:
            return False
        if self._max:                 # 最大化时拖边缘：先还原，否则算出来的尺寸是错的
            self._w.restore()
            self._max = False
        css_w = float(css_w) or 1.0
        self._drag = {
            "edge": str(edge),
            "sx": float(screen_x), "sy": float(screen_y),
            "x": int(self._w.x), "y": int(self._w.y),
            "w": int(self._w.width), "h": int(self._w.height),
            # 屏幕坐标是 CSS 像素、窗口坐标是逻辑像素；HiDPI 缩放下要对上比例
            "k": (float(self._w.width) / css_w) or 1.0,
        }
        self._max = False
        return True

    def update_resize(self, screen_x, screen_y):
        d = self._drag
        if not d or self._w is None:
            return False
        dx = (float(screen_x) - d["sx"]) * d["k"]
        dy = (float(screen_y) - d["sy"]) * d["k"]
        edge = d["edge"]
        x, y, w, h = d["x"], d["y"], d["w"], d["h"]
        if "e" in edge:
            w = d["w"] + dx
        if "s" in edge:
            h = d["h"] + dy
        if "w" in edge:
            w = d["w"] - dx
            x = d["x"] + dx
            if w < MIN_W:
                x = d["x"] + (d["w"] - MIN_W)
                w = MIN_W
        if "n" in edge:
            h = d["h"] - dy
            y = d["y"] + dy
            if h < MIN_H:
                y = d["y"] + (d["h"] - MIN_H)
                h = MIN_H
        w, h = max(MIN_W, int(w)), max(MIN_H, int(h))
        # 位置和尺寸一次改完（拿不到句柄才退回 pywebview 的两次调用）
        sc = self._scale()
        if not self._set_rect(int(x) * sc, int(y) * sc, w * sc, h * sc):
            self._w.resize(w, h)
            if ("w" in edge) or ("n" in edge):
                self._w.move(int(x), int(y))
        return True

    def end_resize(self):
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


def debug_port_from_args():
    """远程调试端口（仅供自动化测试连进 WebView2 量帧）：
    优先 --debug-port N，其次环境变量 WORDHUB_DEBUG_PORT，都没有就返回 None（正常启动不开端口）。"""
    if "--debug-port" in sys.argv:
        i = sys.argv.index("--debug-port")
        if i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])
    env = os.environ.get("WORDHUB_DEBUG_PORT")
    return int(env) if env else None


def main():
    directory = resource_dir()
    debug_port = debug_port_from_args()
    if debug_port:
        webview.settings["REMOTE_DEBUGGING_PORT"] = debug_port
        print(f"[debug] WebView2 远程调试端口 {debug_port}", file=sys.stderr, flush=True)

    httpd, port = start_server(directory)
    if "--selftest" in sys.argv:
        code = selftest(directory, port)
        httpd.shutdown()
        raise SystemExit(code)

    warn_if_no_webview2()

    api = WindowAPI()
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
        min_size=(MIN_W, MIN_H),
        frameless=True,        # 去掉系统标题栏，改由页面画 macOS 风格窗口栏
        easy_drag=False,       # 不整页可拖，只认 drag-region
        resizable=True,
        background_color="#f5f5f7", text_select=True,
    )
    # ---- 朗读小窗：启动时一起建好（hidden），之后靠 show/hide 开关 ----
    mini_api = WindowAPI(hide_on_close=True)
    mini_window = webview.create_window(
        MINI_TITLE,
        f"http://127.0.0.1:{port}/index.html?desktop=1&mini=1",
        js_api=mini_api,
        width=MINI_W, height=MINI_H,
        min_size=(320, 250),
        frameless=True, easy_drag=False, resizable=True,
        on_top=True, hidden=True,
        background_color="#f5f5f7", text_select=True,
    )
    mini_api.bind(mini_window)
    api.attach_mini(mini_window)
    # 主窗被系统关掉（Alt+F4、任务栏右键关闭）时也要把小窗带走，否则进程退不出
    window.events.closed += api._destroy_mini

    api.bind(window)

    def _hide_mini_on_start():
        """⚠️ pywebview 的 hidden=True 只是「透明度设 0 再 Show()」——
        那个窗口其实还在屏幕上，会**吃掉鼠标点击**（看不见但点不动）。
        所以启动后再真正 hide() 一次，让它彻底不挡事。
        窗口本身仍保持创建状态（WebView2 里页面已加载好），之后 open_mini() 秒开。"""
        try:
            mini_window.hide()
        except Exception:
            pass

    # ⚠️ private_mode 必须关掉，并把用户数据目录固定下来，否则学习记录关窗即丢（见 profile_dir）
    webview.start(private_mode=False, storage_path=profile_dir(), func=_hide_mini_on_start)
    httpd.shutdown()


if __name__ == "__main__":
    main()
