"""Render a recorded test log as a terminal-style HTML page for a screenshot.

    python assets/test-evidence/render.py <name>.log "<title>" "<command>" [--tail N] [--head N]

The text is the log file's content, unedited (only a line range is chosen, and the command
that produced it is shown on top). The HTML is then captured with a headless browser at
1600 px width. Logs live next to the images, so every image can be checked against its log.
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#0D1117;width:1600px;font-family:"Cascadia Code",Consolas,monospace}}
.win{{margin:40px;border:2px solid #30363D;border-radius:16px;overflow:hidden;background:#0D1117;
box-shadow:0 20px 50px rgba(1,4,9,.6)}}
.bar{{background:#161B22;padding:14px 20px;display:flex;gap:10px;align-items:center;
border-bottom:2px solid #21262D}}
.bar i{{width:13px;height:13px;border-radius:50%;background:#3d444d;display:inline-block}}
.bar b{{margin-left:14px;color:#C9D1D9;font:600 20px "Segoe UI",Inter,sans-serif}}
.cmd{{padding:18px 26px 0;color:#3FB950;font-size:18px}}
.cmd span{{color:#9DA7B3}}
pre{{margin:0;padding:14px 26px 26px;color:#C9D1D9;font-size:17px;line-height:1.45;
white-space:pre-wrap;word-break:break-word}}
.ok{{color:#3FB950}}.bad{{color:#F85149}}.dim{{color:#6E7681}}
</style></head><body><div class="win"><div class="bar"><i></i><i></i><i></i><b>{title}</b></div>
<div class="cmd"><span>$</span> {command}</div><pre>{body}</pre></div></body></html>"""


def colour(line: str) -> str:
    text = html.escape(line)
    if (line.lstrip().startswith("✔") or re.search(
            r"\b(passed|PASS|ok|All checks passed|Success)\b", line)) and not re.search(
            r"\b(failed|FAIL|BAD)\b", line):
        return f'<span class="ok">{text}</span>'
    if re.search(r"\b(failed|FAILED|FAIL|BAD|error)\b", line):
        return f'<span class="bad">{text}</span>'
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("title")
    parser.add_argument("command")
    parser.add_argument("--head", type=int)
    parser.add_argument("--tail", type=int)
    args = parser.parse_args()
    lines = ANSI.sub("", args.log.read_text(encoding="utf-8", errors="replace")).splitlines()
    if args.head:
        lines = lines[: args.head]
    if args.tail:
        lines = lines[-args.tail:]
    body = "\n".join(colour(line) for line in lines)
    out = args.log.with_suffix(".html")
    out.write_text(PAGE.format(title=html.escape(args.title), command=html.escape(args.command),
                               body=body), encoding="utf-8", newline="\n")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
