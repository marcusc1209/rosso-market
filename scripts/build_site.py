"""Wrap site/index.html in a full HTML document and copy the data, ready for any static host.

    python scripts/build_site.py      ->  public/
"""
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
src, out = root / "site", root / "public"
if out.exists():
    shutil.rmtree(out)
shutil.copytree(src, out)
body = (src / "index.html").read_text(encoding="utf-8")
head_end = body.index("<style>")
(out / "index.html").write_text(
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
    + body[:head_end] + body[head_end:body.index("</style>") + 8] + "\n</head>\n<body>\n"
    + body[body.index("</style>") + 8:] + "\n</body>\n</html>\n",
    encoding="utf-8",
)
print("built", out)
