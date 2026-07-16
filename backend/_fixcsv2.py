import ast
import pathlib

p = pathlib.Path("otomo/tools/user_analysis/tool.py")
lines = p.read_text(encoding="utf-8").splitlines()
for i in range(1246, 1254):
    print(i + 1, repr(lines[i]))

out = []
i = 0
while i < len(lines):
    stripped = lines[i].strip()
    if stripped.startswith("if any(ch in s for ch in") and not stripped.endswith("):"):
        out.append("    if any(ch in s for ch in ',\"\\n\\r'):")
        i += 2  # 跳过被断开的下一行 '):
        continue
    if stripped.startswith('csv_text=') and not stripped.endswith(","):
        out.append('                csv_text="\\ufeff" + "\\r\\n".join(rows),')
        i += 2  # 跳过 ".join(rows),
        continue
    out.append(lines[i])
    i += 1

p.write_text("\n".join(out) + "\n", encoding="utf-8")
ast.parse(p.read_text(encoding="utf-8"))
print("syntax ok")
