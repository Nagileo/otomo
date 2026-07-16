import pathlib

p = pathlib.Path("otomo/tools/user_analysis/tool.py")
lines = p.read_text(encoding="utf-8").splitlines()

# 修 _csv_escape 里被展开的换行（1249-1251 附近，0-indexed 搜索定位更稳）
out = []
i = 0
while i < len(lines):
    if lines[i].strip() == 'if any(ch in s for ch in \',"' and i + 1 < len(lines) and lines[i + 1].strip() == "'):":
        out.append("    if any(ch in s for ch in ',\"\\n\\r'):")
        i += 2
        continue
    if lines[i].strip() == 'csv_text="﻿" + "' and i + 1 < len(lines) and lines[i + 1].strip() == '".join(rows),':
        out.append('                csv_text="\\ufeff" + "\\r\\n".join(rows),')
        i += 2
        continue
    out.append(lines[i])
    i += 1

p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("fixed")
import ast
ast.parse(p.read_text(encoding="utf-8"))
print("syntax ok")
