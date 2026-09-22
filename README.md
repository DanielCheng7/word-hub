# 词枢 · 英语记词器（WordHub）

一个**离线优先**的本地背单词应用：单文件 HTML + 一个把网页打包成 Windows 桌面程序的壳。
间隔重复算法、六个内置词库（CET4 / CET6 / 考研 / 雅思 / 托福 / GRE）、真人读音、拼写模式、
统计热力图 —— 全部跑在本机，不注册、不上传任何学习记录。

![界面](https://img.shields.io/badge/平台-Windows%20%7C%20浏览器-0071e3)
![依赖](https://img.shields.io/badge/依赖-零构建%20%2F%20零框架-34c759)

## 快速开始

**桌面版**：从 [Releases](https://github.com/DanielCheng7/word-hub/releases) 下载 `词枢记词器.exe` 双击即可（单文件，约 34 MB，自带 WebView2 调用）。

**网页版**：直接用浏览器打开 `apps/word-hub/index.html`。

## 仓库结构

| 路径 | 说明 |
|---|---|
| `apps/word-hub/index.html` | 应用本体（单文件，含全部界面与逻辑） |
| `apps/word-hub/data_*.js` | 六个词库 + 例句数据（CET4/CET6/考研/雅思/托福/GRE） |
| `apps/word-hub/README.md` | **功能说明与使用文档（先看这个）** |
| `apps/word-hub/icon.ico` | 桌面版图标 |
| `_build/wordhub_launcher.py` | 桌面壳：本地 HTTP 服务 + WebView2 无边框窗口 + 窗口/朗读 API |
| `_build/build_wordhub_exe.py` | 用 PyInstaller 把上面两个打包成单文件 exe |
| `_build/make_shortcut.ps1` | 在桌面建快捷方式 |
| `_verify/test_word*.js` | 浏览器端自动化测试（Playwright + 本机 Edge） |
| `_verify/test_launcher_perf.py` | 桌面壳窗口逻辑的纯逻辑测试（不需要开窗口） |
| `_verify/test_exe_window.py` | exe 的真机窗口行为测试 |
| `_verify/check_js.py` | 抽取 HTML 内联脚本做语法校验 |

> 本仓库是从一个更大的工作区里筛出来的，只包含这一个应用；
> 仓库根目录下的其他应用与缓存都在 `.gitignore` 里排除了。

## 功能一览

- **卡片 / 拼写两种模式**：卡片看词翻面自评；拼写模式据释义拼英文，回车判对错
- **复习节奏可切换**：默认「每天复习」（学完第二天即可再复习），另有经典 SM-2 渐进间隔（1→6→15→38→95…天）
- **「正在学」与「复习」是两个独立板块**：前者是今日新词，后者只放学过且到期的词
- **真人读音**：优先取在线词典真人发音（按词缓存到本地，断网自动退回系统语音）
- **收藏夹 / 学习统计**：收藏生词、打卡热力图、词库进度
- **完全离线**：进度存在浏览器 localStorage（桌面版在 WebView2 用户目录），可导出 / 导入 JSON

## 构建 exe

```bash
pip install pywebview pyinstaller comtypes
python _build/build_wordhub_exe.py        # 产物：apps/word-hub/词枢记词器.exe
apps/word-hub/词枢记词器.exe --selftest    # 自检：资源完整性 + HTTP + 朗读链路 + 在线发音源
```

## 跑测试

需要 Node + Playwright（用本机 Edge 当浏览器）：

```bash
cd _verify
node test_word.js          # 主流程与算法
node test_word_ui.js       # 界面与交互
node test_word_desktop.js  # 桌面窗口栏、无滚动条、翻面动画
node test_word_perf.js     # 窗口拖动/缩放的节流与命中区
node test_word_blocks.js   # 「正在学/复习」板块 + 发音链路

python test_launcher_perf.py   # 桌面壳窗口逻辑（拖动、缩放、SAPI 选音）
python test_exe_window.py      # exe 真机窗口行为
python check_js.py ../apps/word-hub/index.html
```

## 数据来源与致谢

- 词库释义取自 **[ECDICT](https://github.com/skywind3000/ECDICT)** 开源英汉词典（MIT 协议）
- 例句取自 **OPUS TED2013 中英平行语料**
- 在线真人读音走的是词典发音接口；离线兜底用系统自带的英文语音（SAPI）
- 界面右上角三颗圆点的做法参考 macOS 窗口栏
