#!/usr/bin/env python3
"""Bump v1.4.NNN by one and stamp dev_history.LAST_UPDATED (the per-change rule)."""
import re, sys, time
from pathlib import Path
root = Path(__file__).resolve().parents[1]
main = root / "src/main.py"
s = main.read_text()
cur = re.search(r'APP_VERSION = "1\.4\.(\d+)"', s)
new = f"1.4.{int(cur.group(1)) + 1:03d}"
main.write_text(s.replace(cur.group(0), f'APP_VERSION = "{new}"'))
about = root / "src/about_tab.py"
a = about.read_text()
about.write_text(re.sub(r'"1\.4\.\d{3}"', f'"{new}"', a).replace("'1.4.004'", f"'{new}'"))
dh = root / "src/dev_history.py"
d = dh.read_text()
dh.write_text(re.sub(r'LAST_UPDATED = "[^"]*"', f'LAST_UPDATED = "{time.strftime("%Y-%m-%d %H:%M")}"', d))
print(new)
