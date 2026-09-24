"""桌面壳窗口逻辑的专项验证（不需要真的开窗口）

覆盖：
  1. js_api 自省：WindowAPI 不能再有「公开的、非可调用对象属性」——
     否则 pywebview 会顺着 Window → native → browser → COM 一路递归，
     日志里刷 "maximum recursion depth exceeded"，加载期白白卡住。
  2. 拖动数学：基准（窗口矩形）和位移（光标）都必须**现读**，且拉伸过之后基准要跟着变
     —— v1.30 定位到的根因：拿 pywebview 缓存的尺寸当基准，用户拉过一次就全错；
  3. 缩放：update_resize 必须**只调一次** SetWindowPos（位置+尺寸一起改），
     全程物理像素，不能退回 pywebview 的 resize()+move() 两次全窗重排；
  4. 最小尺寸夹取（CSS 常量 → 物理）与「往左上拖时右下边钉住」。
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
print("=== 2. 拖动：基准与位移全部现读（专挡 v1.30 的根因）===")
w = FakeWindow(x=100, y=80, w=1345, h=874)
api = mod.WindowAPI()
api.bind(w)
api._scale = lambda: 1.0
api._move_phys = lambda x, y: (w.moves.append((x, y)), True)[1]
cur = [1000, 500]
api._cursor_phys = lambda: tuple(cur)
api._rect_phys = lambda: (100, 80, 1345, 874)

ok = api.begin_drag()
check("begin_drag 现读「按下时的窗口矩形 + 光标位置」（不再信 pywebview 的缓存值）",
      ok is True and api._mv["rect"] == (100, 80, 1345, 874) and api._mv["cur"] == (1000, 500),
      str(api._mv))

cur[:] = [1300, 620]                      # 光标走了 +300/+120
api.drag_to()
check("drag_to 按光标位移平移窗口（+300/+120 → 位置 100/80 变 400/200）",
      w.moves[-1] == (400, 200), f"moves={w.moves[-1]}")

cur[:] = [1000, 500]                      # 拖回原点
api.drag_to()
check("拖回原点就回到原位（位移式，不累加、不漂移）",
      w.moves[-1] == (100, 80), f"moves={w.moves[-1]}")

# ⚠️ 核心回归：用户拉伸过之后，基准必须是「当前矩形」而不是建窗时的尺寸。
#    老代码拿 int(self._w.width) 当基准，而 pywebview 那个值在拉伸后**不更新** →
#    再拉就按初始宽度重算（窗口"自己跳回去变小"）、再拖比例就错（不跟手）。
#    甲方三轮反馈（"试图拉伸就自动缩小""拉伸完不能移动窗口"）的真身就在这里。
api._rect_phys = lambda: (700, 600, 2000, 1400)   # 模拟窗口已被拉到 2000×1400 且移到 (700,600)
cur[:] = [500, 400]
api.begin_drag()
cur[:] = [550, 425]
api.drag_to()
check("★ 拉伸之后再拖动：基准取的是当前矩形 (700,600)，位移 1:1 跟手",
      w.moves[-1] == (750, 625), f"得到 {w.moves[-1]}，期望 (750,625)")
api.end_drag()

n = len(w.moves)
after = api.drag_to()
check("end_drag 之后 drag_to 彻底失效（不会再动窗口）",
      after is False and len(w.moves) == n, f"after={after}, moves={len(w.moves)}")

# 最大化状态下拖动：先还原
w2 = FakeWindow()
api2 = mod.WindowAPI()
api2.bind(w2)
api2._cursor_phys = lambda: (10, 10)
api2._rect_phys = lambda: (0, 0, 1000, 800)
api2._max = True
api2.begin_drag()
check("最大化状态下开始拖动会先还原窗口", w2.restored == 1 and api2._max is False,
      f"restored={w2.restored}")

print()
print("=== 3. 缩放：一次 SetWindowPos（不能退回 resize+move 两次）===")
# 基准取启动后的真实尺寸：CSS 1360×900 → 物理 2040×1350（fit_to_design 摆正后的样子）
w4 = FakeWindow(x=100, y=80, w=2040, h=1350)
api4 = mod.WindowAPI()
api4.bind(w4)
rects = []
api4._set_rect = lambda x, y, wd, ht: (rects.append((x, y, wd, ht)), True)[1]
api4._scale = lambda: 1.5
api4._rect_phys = lambda: (100, 80, 2040, 1350)
cur4 = [2000, 1400]
api4._cursor_phys = lambda: tuple(cur4)
api4.begin_resize("se")
cur4[:] = [2060, 1440]
api4.update_resize()
check("update_resize 只调一次 _set_rect（位置+尺寸一次改完）",
      len(rects) == 1, f"_set_rect 调用 {len(rects)} 次")
check("且完全没有退回 pywebview 的 resize()/move()",
      w4.sizes == [] and w4.moves == [], f"resize={w4.sizes} move={w4.moves}")
if rects:
    check("缩放是纯物理运算：光标 +60/+40 → 尺寸 +60/+40，位置不变（右下角拉伸）",
          rects[0] == (100, 80, 2100, 1390), f"rect={rects[0]}，期望 (100,80,2100,1390)")

# ⚠️ 核心回归：拉伸过之后再拉，基准必须是当前矩形（老代码在这里跳回初始尺寸）
rects.clear()
api4._rect_phys = lambda: (100, 80, 2000, 1400)    # 用户已经把它拉到 2000×1400
cur4[:] = [3000, 2000]
api4.begin_resize("e")
cur4[:] = [3050, 2000]
api4.update_resize()
check("★ 拉伸之后再拉伸：基准是当前宽度 2000 → 结果 2050（不会跳回 1360）",
      bool(rects) and rects[0][2] == 2050, f"rect={rects[0] if rects else None}")
api4.end_resize()

# 拿不到窗口句柄时不能崩（_set_rect 直接失败即可；不再退回已确认有坑的 pywebview 路径）
w5 = FakeWindow()
api5 = mod.WindowAPI()
api5.bind(w5)
api5._set_rect = lambda *a: False
api5._rect_phys = lambda: (0, 0, 1345, 874)
api5._cursor_phys = lambda: (2000, 1400)
api5.begin_resize("se")
ok5 = api5.update_resize()
check("拿不到窗口句柄时不抛异常（也不误用 pywebview 的 resize/move）",
      ok5 is False and w5.sizes == [] and w5.moves == [], f"ret={ok5}")

print()
print("=== 4. 最小尺寸夹取（常量是 CSS 像素，先换算成物理再比）===")
w6 = FakeWindow(x=100, y=80, w=1345, h=874)
api6 = mod.WindowAPI()
api6.bind(w6)
small = []
api6._set_rect = lambda x, y, wd, ht: (small.append((x, y, wd, ht)), True)[1]
api6._scale = lambda: 1.5
api6._rect_phys = lambda: (100, 80, 1345, 874)
cur6 = [2000, 1400]
api6._cursor_phys = lambda: tuple(cur6)
api6.begin_resize("se")
cur6[:] = [2000 - 5000, 1400 - 5000]        # 往小拖很多
api6.update_resize()
exp_w, exp_h = round(mod.MIN_W * 1.5), round(mod.MIN_H * 1.5)
check("拖到极小仍被夹在最小尺寸（CSS 1075×875 → 物理 1612×1312）",
      small[-1][2] == exp_w and small[-1][3] == exp_h,
      f"得到 {small[-1][2]}×{small[-1][3]}，期望 {exp_w}×{exp_h}")

# 往左/往上拖到底时，右/下边必须钉住不动（否则窗口会整体漂移）
small.clear()
api6._rect_phys = lambda: (1000, 900, 2000, 1400)     # 右=3000，下=2300
cur6[:] = [2000, 1400]
api6.begin_resize("nw")
cur6[:] = [2000 - 9000, 1400 - 9000]
api6.update_resize()
x, y, wd, ht = small[-1]
check("往左上拖到底时右/下边钉住不动（x+w=3000, y+h=2300）",
      x + wd == 3000 and y + ht == 2300, f"rect={small[-1]}")

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
      "def prime_windows(" in src and "mini_window.hide()" in src
      and "func=functools.partial(prime_windows" in src)
check("启动回调里带重试（回调跑得比窗口建好还早，一次不成就得再试）",
      "for _ in range(40)" in src and "a.fit_to_design(cw, ch)" in src)
check("句柄解析：兜底路径只认「本文档进程自己的窗口」，且绝不缓存猜来的句柄",
      "def own_window(" in src and "pid.value == os.getpid()" in src
      and "def _find_by_enum(" in src and "return 0" in src)
check("最大化状态问系统（IsZoomed），还原用 ShowWindow(SW_RESTORE)，不依赖 self._max 标志位",
      "def _zoomed(" in src and "IsZoomed" in src and "SW_RESTORE" in src
      and "self._max or self._zoomed()" in src)

print("=== 9. 固定端口 + 存储镜像（localStorage 按 origin 隔离，端口必须稳定）===")
import tempfile
import json as _json
import urllib.request as _urlreq
import functools as _ft

check("有一个固定的起始端口（端口是 origin 的一部分，随机端口会让设置和进度换 origin 而丢失）",
      isinstance(mod.STABLE_PORT, int) and 1024 < mod.STABLE_PORT < 65535, mod.STABLE_PORT)

_tmp = tempfile.mkdtemp(prefix="wh_store_")
_real_storage_path = mod.storage_path
mod.storage_path = lambda: os.path.join(_tmp, "storage.json")
try:
    # ---- save_storage / read_storage_backup ----
    a = mod.WindowAPI(port=17831)
    ok = a.save_storage(_json.dumps({"wh_setting_theme": '"dark"', "wh_setting_list": '"cet4"'}))
    back = mod.read_storage_backup()
    check("页面交上来的 localStorage 快照能落盘、能读回",
          ok is True and back == {"wh_setting_theme": '"dark"', "wh_setting_list": '"cet4"'}, str(back))
    check("空快照/坏数据不落盘（不覆盖已有的好备份）",
          a.save_storage("{}") is False and a.save_storage("不是 JSON") is False
          and mod.read_storage_backup() == back)

    # ---- seed_script ----
    sc = mod.seed_script({"wh_setting_theme": '"dark"', "x": "evil</script><b>hi</b>"})
    check("种子里把 </ 转义掉了（否则 JSON 里的 </script> 会提前闭合掉整段脚本）",
          sc.count("</script>") == 1, f"出现 {sc.count('</script>')} 次 </script>")
    check("种子只补「当前缺失」的键（=== null 才写），绝不覆盖页面已有的值",
          "localStorage.getItem(k)===null" in sc and "localStorage.setItem(k,S[k])" in sc)

    # ---- build_index：注入点必须在任何其它 <script> 之前 ----
    appdir = os.path.join(_tmp, "app")
    os.makedirs(appdir, exist_ok=True)
    with open(os.path.join(appdir, "index.html"), "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html><html><head><meta charset=\"UTF-8\">"
                "<title>t</title></head><body><script>var a=1;</script></body></html>")
    body = mod.build_index(appdir).decode("utf-8")
    check("index.html 被注入了种子脚本，且它是文档里的第一个 script（应用读到设置前就已恢复）",
          "<script id=\"wh-seed\">" in body and body.index("<script") == body.index("<script id=\"wh-seed\">"),
          body[:100])

    mod.storage_path = lambda: os.path.join(_tmp, "none.json")
    raw2 = mod.build_index(appdir)
    check("没有备份时原样返回（不多此一举地改 HTML）",
          raw2 == open(os.path.join(appdir, "index.html"), "rb").read())
    mod.storage_path = lambda: os.path.join(_tmp, "storage.json")

    # ---- start_server：端口始终保持固定（这就是"设置能记住"的前提）----
    mod.PORT_WAIT_SECONDS = 0.3
    httpd, port = mod.start_server(appdir, prefer=mod.STABLE_PORT)
    check("start_server 优先用固定端口", port == mod.STABLE_PORT, f"拿到 {port}")
    with _urlreq.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=8) as r:
        served = r.read().decode("utf-8")
    check("服务出去的 index.html 里真的带上了种子（走 HTTP 这条路，不是只有函数能跑）",
          "wh-seed" in served and "wh_setting_theme" in served, served[:90])
    # Windows 上 http.server 默认带 SO_REUSEADDR，重复绑定同一端口会成功 —— 对我们反而是好事：
    # 端口永远拿得到，origin 永远稳定（即便上一个实例还没退干净）
    httpd2, port2 = mod.start_server(appdir, prefer=mod.STABLE_PORT)
    check("再起一个服务也还落在同一个端口（origin 不会漂）",
          port2 == mod.STABLE_PORT, f"第二个拿到 {port2}")
    httpd.shutdown()
    httpd2.shutdown()
finally:
    mod.storage_path = _real_storage_path
    import shutil as _sh
    _sh.rmtree(_tmp, ignore_errors=True)

print("=== 10. 源码里不该再有 self.w（公开窗口属性）===")
src = open(LAUNCHER, encoding="utf-8").read()
bad = [l.strip() for l in src.splitlines()
       if "self.w." in l or "self.w " in l or "self.w=" in l]
check("启动器源码里没有残留的 self.w（公开窗口属性）", bad == [], "; ".join(bad[:3]) or "干净")

print()
print("通过 %d / %d" % (sum(RES), len(RES)))
sys.exit(0 if all(RES) else 1)
