# 统一四个应用的默认窗口尺寸：给桌面快捷方式的命令行加上 --window-size
# 说明：coord-kit / life-map 走 msedge --app，fund-desk 走启动脚本里的 msedge --app，
#       word-hub 是 pywebview 自绘窗口（自己按 1360×900 开窗），不需要改。
# 改之前先把原快捷方式备份到 _backup/lnk_2026-09-23/。
from pathlib import Path
import shutil

import win32com.client

DESKTOP = Path.home() / "Desktop"
BACKUP = Path(r"D:\Agent Project\GLMtest\_backup\lnk_2026-09-23")
WINDOW_SIZE = "--window-size=1360,900"      # 与词枢记词器一致的默认开窗尺寸（逻辑像素）

APPLY = True                                 # False = 只看不改

TARGETS = ["坐标转换瑞士军刀", "生活半径地图"]

sh = win32com.client.Dispatch("WScript.Shell")
BACKUP.mkdir(parents=True, exist_ok=True)

for name in TARGETS:
    lnk = DESKTOP / f"{name}.lnk"
    if not lnk.exists():
        print(f"跳过（不存在）：{lnk}")
        continue
    sc = sh.CreateShortcut(str(lnk))
    print(f"[{name}]")
    print(f"  Target : {sc.TargetPath}")
    print(f"  Args   : {sc.Arguments}")
    print(f"  WorkDir: {sc.WorkingDirectory}")
    print(f"  Icon   : {sc.IconLocation}")
    if not APPLY:
        continue
    if WINDOW_SIZE in sc.Arguments:
        print("  已是目标参数，跳过")
        continue
    shutil.copy2(lnk, BACKUP / lnk.name)                  # 先备份原件
    sc.Arguments = (sc.Arguments + " " + WINDOW_SIZE).strip()
    sc.Save()
    sc2 = sh.CreateShortcut(str(lnk))
    print(f"  新Args : {sc2.Arguments}")

print("\n备份目录：", BACKUP)
