"""桌面壳窗口逻辑的专项验证（不需要真的开窗口）

覆盖：
  1. js_api 自省：WindowAPI 不能再有「公开的、非可调用对象属性」——
     否则 pywebview 会顺着 Window → native → browser → COM 一路递归，
     日志里刷 "maximum recursion depth exceeded"，加载期白白卡住。
  2. 拖动数学：begin_drag 记锚点、drag_move 发增量、end_drag 之后彻底失效；
     最大化时先还原；HiDPI 下按比例换算。
  3. 缩放：update_resize 必须**只调一次** SetWindowPos（位置+尺寸一起改），
     不能退回 pywebview 的 resize()+move() 两次全窗重排。
  4. 最小尺寸夹取仍然有效。
"""
import ctypes
import importlib.util
import os
import sys

RES = []
USER32 = ctypes.windll.user32


def check(name, ok, detail=""):
    RES.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


# ---- 载入启动器模块（它只在 __main__ 下才开窗口，导入是安全的）----
HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(os.path.dirname(HERE), "_build", "wordhub_launcher.py")
spec = importlib.util.spec_from_file_location("wordhub_launcher", LAUNCHER)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeWindow:
    """够用的假窗口：记录 move/resize，模拟 pywebview 的 Window 接口。"""

    def __init__(self, x=100, y=80, w=1345, h=874):
        self._x, self._y, self._w, self._h = x, y, w, h
        self.moves = []
        self.sizes = []
        self.restored = 0
        self.maximized = 0
        self.destroyed = 0

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    @property
    def width(self):
        return self._w

    @property
    def height(self):
        return self._h

    def move(self, x, y):
        self.moves.append((x, y))

    def resize(self, w, h):
        self.sizes.append((w, h))

    def restore(self):
        self.restored += 1

    def maximize(self):
        self.maximized += 1

    def minimize(self):
        self.minimized = getattr(self, "minimized", 0) + 1

    def destroy(self):
        self.destroyed += 1


print("=== 1. js_api 自省：不能再有会被递归的公开属性 ===")
api = mod.WindowAPI()
api.bind(FakeWindow())

# 复刻 pywebview util.get_functions 的判定逻辑：公开名 + 非可调用 + 有 __module__ → 递归进去
risky = []
for name in dir(api):
    if name.startswith("_"):
        continue
    attr = getattr(api, name, None)
    if callable(attr):
        continue
    if hasattr(attr, "__module__"):
        risky.append(name)
check("WindowAPI 没有「公开且非可调用」的属性（不会触发 pywebview 递归自省）",
      risky == [], f"可疑属性: {risky}")

# 反向验证：这条检查是有牙齿的 —— 把窗口挂成公开属性就该被抓出来
class Leaky(mod.WindowAPI):
    pass


leaky = Leaky()
leaky.w = FakeWindow()          # 老写法（v1.10 之前）
found = [n for n in dir(leaky) if not n.startswith("_")
         and not callable(getattr(leaky, n, None)) and hasattr(getattr(leaky, n, None), "__module__")]
check("反向验证：老的「公开属性放窗口」写法确实会被这条检查抓到",
      "w" in found, f"抓到: {found}")

print()
print("=== 2. 拖动数学（位置式，官方 customize.js 同款）===")
w = FakeWindow(x=100, y=80, w=1345, h=874)
api = mod.WindowAPI()
api.bind(w)
api._scale = lambda: 1.0                      # 隔离掉 DPI，只看逻辑
api._move_phys = lambda x, y: (w.moves.append((x, y)), True)[1]   # 隔离真实窗口

ok = api.begin_drag(120, 60, 1345, 874)
check("begin_drag 记下「光标在客户区的位置」和 CSS↔逻辑换算比",
      ok is True and api._mv == {"cx": 120.0, "cy": 60.0, "k": 1.0}, str(api._mv))

api.drag_to(500, 300)
check("drag_to 把「客户区原点应在的屏幕坐标」直接当窗口位置（不做增量累加）",
      w.moves[-1] == (500, 300), f"moves={w.moves}")

api.drag_to(100, 80)
check("再拖回去就回到原位（不依赖窗口当前位置 → 刚缩放完也不会跳）",
      w.moves[-1] == (100, 80), f"moves={w.moves}")

api.end_drag()
n = len(w.moves)
after = api.drag_to(999, 999)
check("end_drag 之后 drag_to 彻底失效（不会再动窗口）",
      after is False and len(w.moves) == n, f"after={after}, moves={len(w.moves)}")

# 最大化状态下拖动：先还原
w2 = FakeWindow()
api2 = mod.WindowAPI()
api2.bind(w2)
api2._max = True
api2.begin_drag(500, 500, 1345, 874)
check("最大化状态下开始拖动会先还原窗口", w2.restored == 1 and api2._max is False,
      f"restored={w2.restored}")

# HiDPI：窗口逻辑宽 2018 vs 页面 CSS 宽 1345 → k≈1.5
w3 = FakeWindow(x=0, y=0, w=2018, h=1312)
api3 = mod.WindowAPI()
api3.bind(w3)
api3._scale = lambda: 1.0
api3._move_phys = lambda x, y: (w3.moves.append((x, y)), True)[1]
api3.begin_drag(100, 50, 1345, 874)
api3.drag_to(700, 500)
check("HiDPI：目标屏幕坐标按 CSS↔逻辑比例换算（k≈1.5004 → 700/500 变 1050/750）",
      abs(w3.moves[-1][0] - 1050) <= 2 and abs(w3.moves[-1][1] - 750) <= 2, f"moves={w3.moves}")

print("=== 3. 缩放：一次 SetWindowPos（不能退回 resize+move 两次）===")
w4 = FakeWindow(x=100, y=80, w=1345, h=874)
api4 = mod.WindowAPI()
api4.bind(w4)
rects = []
api4._set_rect = lambda x, y, wd, ht: (rects.append((x, y, wd, ht)), True)[1]
api4._scale = lambda: 1.5
api4.begin_resize("se", 2000, 1400, 1345, 874)
api4.update_resize(2060, 1440)
check("update_resize 只调一次 _set_rect（位置+尺寸一次改完）",
      len(rects) == 1, f"_set_rect 调用 {len(rects)} 次")
check("且完全没有退回 pywebview 的 resize()/move()",
      w4.sizes == [] and w4.moves == [], f"resize={w4.sizes} move={w4.moves}")
if rects:
    x, y, wd, ht = rects[0]
    # 起点 2000,1400 → 终点 2060,1440：逻辑增量 +60/+40 → 逻辑尺寸 (1345+60, 874+40)
    # ⚠️ _set_rect 收的是**物理像素**（本进程 DPI 感知，SetWindowPos 走物理），
    #    而 pywebview 报的逻辑尺寸要乘 _scale()=1.5 才等于物理 → 所以期望值也是 ×1.5
    #    （真窗口实测：主窗拖 +200 逻辑 = +300 物理，恢复这条乘法后才精确到位）
    exp_w, exp_h = (1345 + 60) * 1.5, (874 + 40) * 1.5
    check("缩放把逻辑尺寸换算成物理像素（×DPI 比例）后一次设完，且不改位置（右下角拉伸）",
          abs(wd - exp_w) < 1 and abs(ht - exp_h) < 1 and x == 100 * 1.5 and y == 80 * 1.5,
          f"rect={rects[0]}，期望 {exp_w}×{exp_h} @ ({100 * 1.5},{80 * 1.5})")

# 拿不到句柄时必须能退回 pywebview 的老路（不能把缩放搞坏）
w5 = FakeWindow()
api5 = mod.WindowAPI()
api5.bind(w5)
api5._set_rect = lambda *a: False            # 模拟 FindWindow 失败
api5.begin_resize("se", 2000, 1400, 1345, 874)
api5.update_resize(2060, 1440)
check("拿不到窗口句柄时会退回 pywebview 的 resize()+move()（功能不丢）",
      len(w5.sizes) == 1, f"sizes={w5.sizes}")

print()
print("=== 4. 最小尺寸夹取 ===")
w6 = FakeWindow(x=100, y=80, w=1345, h=874)
api6 = mod.WindowAPI()
api6.bind(w6)
small = []
api6._set_rect = lambda x, y, wd, ht: (small.append((wd, ht)), True)[1]
api6._scale = lambda: 1.0
api6.begin_resize("se", 2000, 1400, 1345, 874)
api6.update_resize(2000 - 900, 1400 - 700)    # 往小拖很多
wd, ht = small[-1]
check("拖到极小仍被夹在最小尺寸（逻辑像素 ≥ 1075×875）",
      wd >= mod.MIN_W and ht >= mod.MIN_H, f"得到 {wd}×{ht}，下限 {mod.MIN_W}×{mod.MIN_H}")

print()
print()
print("=== 5. 朗读：从 SAPI 里挑英文语音 ===")


class FakeTok:
    def __init__(self, d):
        self._d = d

    def GetDescription(self):
        return self._d


class FakeToks:
    def __init__(self, ds):
        self._ds = ds

    @property
    def Count(self):
        return len(self._ds)

    def Item(self, i):
        return FakeTok(self._ds[i])


class FakeVoice:
    def __init__(self, ds):
        self._toks = FakeToks(ds)
        self.voice = None
        self.rate = None

    def GetVoices(self):
        return self._toks

    @property
    def Voice(self):
        return self.voice

    @Voice.setter
    def Voice(self, v):
        self.voice = v


# 本机真实情况：SAPI 里 1 个中文 + 1 个英文（Zira）
fv = FakeVoice(["Microsoft Huihui Desktop - Chinese (Simplified)",
                "Microsoft Zira Desktop - English (United States)"])
mod.WindowAPI._pick_voice(fv, "en-US")
check("SAPI 里挑到的是英文语音（不是中文那个）",
      fv.voice is not None and "ENGLISH" in fv.voice.GetDescription().upper(),
      fv.voice.GetDescription() if fv.voice else "(没选中)")

fv2 = FakeVoice(["Microsoft Huihui Desktop - Chinese (Simplified)",
                 "Microsoft Zira Desktop - English (United States)"])
mod.WindowAPI._pick_voice(fv2, "en-GB")          # 想要英音但没有 → 退回任一英文
check("想要英音但系统只有美音时，退回任一英文语音（绝不落回中文）",
      fv2.voice is not None and "ENGLISH" in fv2.voice.GetDescription().upper(),
      fv2.voice.GetDescription() if fv2.voice else "(没选中)")

# 同时有美音和英音时，要真的按口音挑（SAPI 里英音描述是 "English (United Kingdom)"）
fv3 = FakeVoice(["Microsoft Huihui Desktop - Chinese (Simplified)",
                 "Microsoft Zira Desktop - English (United States)",
                 "Microsoft George - English (United Kingdom)"])
mod.WindowAPI._pick_voice(fv3, "en-GB")
check("系统同时有美音和英音、选英音时挑到英音",
      fv3.voice is not None and "UNITED KINGDOM" in fv3.voice.GetDescription().upper(),
      fv3.voice.GetDescription() if fv3.voice else "(没选中)")

fv4 = FakeVoice(["Microsoft George - English (United Kingdom)",
                 "Microsoft Zira Desktop - English (United States)"])
mod.WindowAPI._pick_voice(fv4, "en-US")
check("选美音时挑到美音（不会被排在前面的英音抢走）",
      fv4.voice is not None and "UNITED STATES" in fv4.voice.GetDescription().upper(),
      fv4.voice.GetDescription() if fv4.voice else "(没选中)")

api7 = mod.WindowAPI()
check("空词不触发朗读", api7.speak("") is False and api7.speak("   ") is False)
check("正常单词放进槽位（真正发音在常驻线程里做，不阻塞调用方）",
      api7.speak("vocabulary") is True and api7._speak_slot is not None,
      str(api7._speak_slot))

print()
print("=== 6. 朗读音频：在线真人发音 + 缓存 + 兜底 ===")

import tempfile

TMP = tempfile.mkdtemp(prefix="wh_audio_test_")
GOOD_MP3 = b"ID3\x04\x00\x00" + b"\x00" * 4096          # 够大 + ID3 头
api_a = mod.WindowAPI()
api_a._audio_dir = TMP

_calls = []
_real_urlopen = mod.urllib.request.urlopen


class FakeResp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    _calls.append(url)
    if "zzznope" in url:
        raise OSError("404")
    return FakeResp(GOOD_MP3)


mod.urllib.request.urlopen = fake_urlopen
try:
    # ---- 1) 首次朗读：真的下载并落盘 ----
    _calls.clear()
    path = api_a._audio_file("vocabulary", "en-US")
    ok = bool(path) and os.path.isfile(path) and os.path.getsize(path) == len(GOOD_MP3)
    check("首次朗读会下载音频并落盘", ok and len(_calls) == 1,
          f"path={os.path.basename(path) if path else None}, 请求 {len(_calls)} 次")

    # ---- 2) 美音/英音走不同参数（type=2 / type=1）----
    check("美音用 type=2、英音用 type=1",
          _calls and "type=2" in _calls[0],
          _calls[0] if _calls else "(无)")
    _calls.clear()
    gb = api_a._audio_file("vocabulary", "en-GB")
    check("英音请求带 type=1 且另存一个文件",
          _calls and "type=1" in _calls[0] and gb != path, _calls[0] if _calls else "(无)")

    # ---- 3) 缓存命中：不再发请求（断网也能播）----
    _calls.clear()

    def dead(*a, **k):
        raise OSError("network disabled")

    mod.urllib.request.urlopen = dead
    p2 = api_a._audio_file("vocabulary", "en-US")
    mod.urllib.request.urlopen = fake_urlopen
    check("已有缓存时不再联网（断网也能取到，LRU 只刷新访问时间）",
          p2 == path and not _calls, f"path 一致={p2 == path}, 请求 {len(_calls)} 次")

    # ---- 4) 返回的不是音频要拒绝（避免把 HTML 错误页当音频播）----
    _calls.clear()
    mod.urllib.request.urlopen = lambda req, timeout=None: FakeResp(b"<html>not audio</html>" * 100)
    bad = api_a._audio_file("htmlpage", "en-US")
    check("服务端返回的不是音频时拒绝落盘", bad is None and not os.path.isfile(os.path.join(TMP, "htmlpage.us.mp3")),
          str(bad))

    # ---- 5) 太小的响应也拒绝 ----
    mod.urllib.request.urlopen = lambda req, timeout=None: FakeResp(b"ID3\x04tiny")
    tiny = api_a._audio_file("tinyword", "en-US")
    check("响应过小时拒绝落盘", tiny is None, str(tiny))

    # ---- 6) 下载失败不抛异常，返回 None（调用方据此退回系统语音）----
    mod.urllib.request.urlopen = fake_urlopen
    gone = api_a._audio_file("zzznope", "en-US")
    check("查不到的词返回 None（不抛异常）", gone is None, str(gone))

    # ---- 7) 非字母词名不会跑到缓存目录外 ----
    weird = api_a._audio_file("../../evil", "en-US")
    check("词名里的路径字符被清掉（不会写出缓存目录）",
          weird is None or os.path.dirname(os.path.abspath(weird)) == os.path.abspath(TMP),
          str(weird))
finally:
    mod.urllib.request.urlopen = _real_urlopen


# ---- 8) 播放：换词时必须先 close 上一个别名（否则两句叠着念）----
class FakeFunc:
    """模拟 ctypes 的函数指针：可赋值 argtypes，也可调用（绑定方法就不行）。"""

    def __init__(self, rec):
        self.rec = rec
        self.argtypes = None
        self.restype = None

    def __call__(self, cmd, *a):
        self.rec.append(cmd)
        return 0


class FakeWinmm:
    def __init__(self):
        self.cmds = []
        self.mciSendStringW = FakeFunc(self.cmds)


class FakeCtypes:
    c_wchar_p = ctypes.c_wchar_p
    c_uint = ctypes.c_uint
    c_void_p = ctypes.c_void_p

    def __init__(self, winmm):
        self.windll = type("W", (), {"winmm": winmm})()


api_b = mod.WindowAPI()
api_b._audio_dir = TMP
fm = FakeWinmm()
_saved_ctypes = mod.ctypes
mod.ctypes = FakeCtypes(fm)
try:
    r1 = api_b._play_mp3(path)
    first = list(fm.cmds)
    fm.cmds.clear()
    r2 = api_b._play_mp3(gb)
    second = list(fm.cmds)
finally:
    mod.ctypes = _saved_ctypes
check("播放走 open + play（MCI，系统自带，不需要额外依赖）",
      r1 and len(first) == 2 and first[0].startswith("open") and first[1].startswith("play"),
      str(first))
check("换词时先 close 上一个别名，不会叠着念",
      r2 and second and second[0].startswith("close"),
      str(second[:2]))


# ---- 9) 缓存有上限，超了删最旧的 ----
api_c = mod.WindowAPI()
api_c._audio_dir = tempfile.mkdtemp(prefix="wh_audio_trim_")
old_cap = mod.AUDIO_CACHE_MAX
try:
    mod.AUDIO_CACHE_MAX = 5
    for i in range(8):
        fp = os.path.join(api_c._audio_dir, f"w{i}.us.mp3")
        with open(fp, "wb") as f:
            f.write(GOOD_MP3)
        os.utime(fp, (1000 + i, 1000 + i))          # 人为拉开新旧
    api_c._trim_cache()
    left = sorted(os.listdir(api_c._audio_dir))
    check("缓存超过上限时按最久未用清理",
          len(left) == 5 and "w7.us.mp3" in left and "w0.us.mp3" not in left,
          f"剩 {len(left)} 个: {left}")
finally:
    mod.AUDIO_CACHE_MAX = old_cap


# ---- 10) speak / prefetch 的入参校验 ----
api_d = mod.WindowAPI()
check("空词不触发朗读", api_d.speak("") is False and api_d.speak("   ") is False)
check("正常词放进槽位（实际下载与播放都在常驻线程里，不阻塞调用方）",
      api_d.speak("vocabulary") is True and api_d._speak_slot is not None, str(api_d._speak_slot))
check("预取只对非空词生效", api_d.prefetch("hello") is True and api_d.prefetch("") is False)

print()
print("=== 7. 桌面版进度持久化 + WebView2 检测 ===")
# 背景：pywebview 默认 private_mode=True → 用户数据放进临时目录，且关窗时 rmtree 掉，
# 结果"一关程序学习记录全没"。这里直接调 pywebview 自己的落盘/清理逻辑来验证修复。

from webview import _state                                    # noqa: E402
from webview.platforms import winforms                        # noqa: E402
from webview.platforms.edgechromium import EdgeChrome         # noqa: E402


class Probe:
    """假窗口对象：一旦有人访问 .webview 就记下来（说明它真去删数据了）。"""

    touched = False

    @property
    def webview(self):
        Probe.touched = True
        raise RuntimeError("probe: 不该走到这里")


old_pm, old_sp = _state['private_mode'], _state['storage_path']
old_cache = winforms.cache_dir
try:
    # ---- 1) 现在的写法：profile_dir() 固定目录 + private_mode=False ----
    target = mod.profile_dir()
    _state['private_mode'] = False
    _state['storage_path'] = target
    winforms.cache_dir = None
    winforms.init_storage()
    check("用户数据落在固定的用户目录里（不是临时目录）",
          winforms.cache_dir == target and os.path.isdir(target)
          and os.environ.get("TEMP", "\0") not in target,
          winforms.cache_dir)

    Probe.touched = False
    EdgeChrome.clear_user_data(Probe())
    check("关窗时不会再删用户数据目录（学习记录留得住）", Probe.touched is False,
          "未访问 webview，直接返回")

    # ---- 2) 对照：老写法 private_mode=True + 无 storage_path ----
    _state['private_mode'] = True
    _state['storage_path'] = None
    winforms.cache_dir = None
    winforms.init_storage()
    tmp = winforms.cache_dir
    check("对照：默认 private_mode 下用户数据落在临时目录，而且那个目录根本不存在（所以存不住）",
          bool(tmp) and not os.path.exists(tmp),
          f"{tmp}  存在={os.path.exists(tmp)}")

    Probe.touched = False
    EdgeChrome.clear_user_data(Probe())
    check("对照：默认 private_mode 下关窗确实会去删用户数据目录（这就是丢进度的原因）",
          Probe.touched is True, "访问了 webview → 进入删除分支")

    # ---- 3) WebView2 运行时检测 ----
    check("能判断出本机装了 WebView2（所以启动时不会弹提示）",
          mod.webview2_missing() is False, f"missing={mod.webview2_missing()}")
    orig = winforms.is_chromium
    try:
        winforms.is_chromium = False
        check("缺少 WebView2 时能识别出来（会弹提示给下载地址，而不是静默用旧内核）",
              mod.webview2_missing() is True, "missing=True")
        check("提示里的下载地址是微软官方短链",
              mod.WEBVIEW2_DOWNLOAD.startswith("https://go.microsoft.com/"), mod.WEBVIEW2_DOWNLOAD)
    finally:
        winforms.is_chromium = orig
finally:
    _state['private_mode'], _state['storage_path'] = old_pm, old_sp
    winforms.cache_dir = old_cache

print()
print("=== 8. 朗读小窗的窗口逻辑 ===")


class FakeW:
    def __init__(self):
        self.calls = []
        self.on_top = False

    def hide(self):
        self.calls.append('hide')

    def destroy(self):
        self.calls.append('destroy')

    def show(self):
        self.calls.append('show')

    def evaluate_js(self, js):
        self.calls.append('js:' + js[:40])
        return True


fw = FakeW()
main_api = mod.WindowAPI()
main_api.bind(fw)
main_api.close()
check("主窗的「关闭」仍是 destroy（关掉主窗 = 退出程序）", fw.calls == ['destroy'], str(fw.calls))

fm = FakeW()
mini_api = mod.WindowAPI(hide_on_close=True)
mini_api.bind(fm)
mini_api.close()
check("小窗的「关闭」是 hide —— 不能 destroy（pywebview 把 Application.Exit() 绑在每个窗口的 "
      "FormClosed 上，destroy 会把主程序一起退掉）", fm.calls == ['hide'], str(fm.calls))

import webview as _wv   # noqa: E402

m2 = FakeW()
api2 = mod.WindowAPI()
api2.attach_mini(m2)
_wv.windows.append(m2)          # open_mini 会先确认小窗还在 webview.windows 里（没被关掉）
try:
    ok_open = api2.open_mini()
finally:
    _wv.windows.remove(m2)
check("打开小窗：show + 通知页面开始播放（__miniOpened）",
      ok_open is True and 'show' in m2.calls
      and any(c.startswith('js:') and '__miniOpened' in c for c in m2.calls), str(m2.calls))

# 小窗被用户关掉（Alt+F4）后，open_mini 要能识别出来，让页面退回「浏览器弹窗」方案
a5 = mod.WindowAPI()
a5.attach_mini(FakeW())         # 这个假窗口不在 webview.windows 里 = 已经被关掉
check("小窗已被关掉时 open_mini 返回 False（页面据此退回弹窗方案）", a5.open_mini() is False)

f3 = FakeW()
a3 = mod.WindowAPI()
a3.bind(f3)
r1, r2 = a3.set_on_top(False), a3.set_on_top(True)
check("置顶开关能改到窗口上（📌）", r1 and r2 and f3.on_top is True, f"on_top={f3.on_top}")

a4 = mod.WindowAPI()
check("没绑定窗口时各接口安全返回 False（不抛异常）",
      a4.open_mini() is False and a4.set_on_top(True) is False)

src = open(LAUNCHER, encoding="utf-8").read()
check("小窗是「启动时一起建好 + 启动后真正 hide」——hidden=True 只是透明度 0，仍会挡住鼠标点击",
      "func=_hide_mini_on_start" in src and "mini_window.hide()" in src)

print("=== 9. 源码里不该再有 self.w（公开窗口属性）===")
src = open(LAUNCHER, encoding="utf-8").read()
bad = [l.strip() for l in src.splitlines()
       if "self.w." in l or "self.w " in l or "self.w=" in l]
check("启动器源码里没有残留的 self.w（公开窗口属性）", bad == [], "; ".join(bad[:3]) or "干净")

print()
print("通过 %d / %d" % (sum(RES), len(RES)))
sys.exit(0 if all(RES) else 1)
