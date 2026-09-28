"""The whole site builds from the local records without an exception, and every page links only to pages that exist.
Run: python3 tests/test_site.py"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from llmbox import site  # noqa: E402

out = tempfile.mkdtemp(prefix="llmbox-site-")
pages = site.build(out)
names = {os.path.basename(p) for p in pages}
bad = []
for p in pages:
    html = open(p).read()
    for href in re.findall(r'href="([^"#?]+\.html)"', html):
        if href not in names and "${" not in href:   # a link a page script fills in (recipe-${id}.html)
            bad.append(f"{os.path.basename(p)} -> {href}")
print(f"{len(pages)} pages built in {out}")
print("broken links:", bad or "none")
sys.exit(1 if bad or "index.html" not in names or "method.html" not in names else 0)
