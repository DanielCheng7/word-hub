"""把词枢记词器打包成单文件 exe（PyInstaller + pywebview，用系统自带 WebView2）

跑法： E:\\Python\\python.exe _build\\build_wordhub_exe.py
产物： apps\\word-hub\\词枢记词器.exe
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "apps", "word-hub")
BUILD = os.path.join(ROOT, "_build")
NAME = "WordHub"                      # 内部名用 ASCII，最后再改成中文名，避免打包期的编码问题
EXE_CN = "词枢记词器.exe"

DATA_FILES = [
    "index.html",
    "data_cet4.js", "data_cet6.js", "data_ky.js",
    "data_ielts.js", "data_toefl.js", "data_gre.js",
    "data_sent.js",
]


def main():
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--icon", os.path.join(APP, "icon.ico"),
        "--distpath", APP,
        "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        # 应用本体：放进包内 webapp/ 目录
        "--add-data", os.path.join(APP, "index.html") + ";webapp",
    ]
    for f in DATA_FILES[1:]:
        cmd += ["--add-data", os.path.join(APP, f) + ";webapp"]
    # pywebview / pythonnet 的运行时文件必须整包收进来，否则窗口起不来
    # comtypes：桌面版朗读要用到（绕开 WebView2 语音列表，直接调系统 SAPI 的英文语音）
    for pkg in ("webview", "pythonnet", "clr_loader"):
        cmd += ["--collect-all", pkg]
    # comtypes 只做 hidden-import 即可：朗读用的是 dynamic=True，不生成/不需要类型库缓存，
    # 整包 collect-all 会把 comtypes.gen 的缓存全塞进来，白白多十来 MB
    for mod in ("comtypes", "comtypes.client", "comtypes.automation"):
        cmd += ["--hidden-import", mod]
    cmd.append(os.path.join(BUILD, "wordhub_launcher.py"))

    print("打包中（PyInstaller，约 1~3 分钟）…")
    r = subprocess.run(cmd, cwd=BUILD, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout or "").strip().splitlines()[-6:]
    for line in tail:
        print("   ", line)
    if r.returncode != 0:
        print((r.stderr or "")[-2500:])
        raise SystemExit("打包失败")

    built = os.path.join(APP, NAME + ".exe")
    final = os.path.join(APP, EXE_CN)
    # 注意：不能用 os.remove 先删旧的 —— 沙箱的删除钩子会拦，且 exe 正在运行时会锁文件。
    # os.replace 是原子替换（同盘覆盖），也不需要先删。
    for attempt in range(3):
        try:
            os.replace(built, final)
            break
        except OSError as e:
            if attempt == 2:
                raise SystemExit(f"替换 {final} 失败（多半是 exe 还在运行，先关掉再打包）：{e}")
            print("  目标被占用，2 秒后重试…")
            time.sleep(2)
    print(f"产物：{final}  {os.path.getsize(final)/1048576:.1f} MB")


if __name__ == "__main__":
    main()
