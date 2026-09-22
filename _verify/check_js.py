"""临时工具：抽取 HTML 内联脚本并用 node --check 做语法校验"""
import os
import re
import shutil
import subprocess
import sys

# node 优先取环境变量，其次取 PATH 里的，最后才回落到本机开发用的绝对路径
NODE = (os.environ.get("NODE_BIN")
        or shutil.which("node")
        or r"C://Users//Hao//.workbuddy//binaries//node//versions//22.22.2-3//node.exe")
TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_syntax_check.js")


def check(path):
    html = open(path, encoding="utf-8").read()
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    print(f"{os.path.basename(path)}: 内联脚本 {len(blocks)} 段")
    ok = True
    for i, b in enumerate(blocks):
        with open(TMP, "w", encoding="utf-8") as f:
            f.write(b)
        r = subprocess.run([NODE, "--check", TMP], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  段{i}: OK（{len(b.splitlines())} 行）")
        else:
            print(f"  段{i}: 失败\n{r.stderr.strip()[:600]}")
            ok = False
    return ok


if __name__ == "__main__":
    allok = all(check(p) for p in sys.argv[1:])
    print("总体:", "全部通过" if allok else "存在语法错误")
    sys.exit(0 if allok else 1)
