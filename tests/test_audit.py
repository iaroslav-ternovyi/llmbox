"""llmbox audit (llmbox/audit.py) on a scratch home and a scratch site: an unreadable result is an error, results
newer than the last backup a warning; a site page with a home folder is an error, one model with two speeds an error,
a link to a missing page a warning, a torn HTML entity or a figure labelled 32k but predicted at another depth an error;
a model with a caveat gets its findings as notes.
Run: python3 tests/test_audit.py"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
home = tempfile.mkdtemp()
os.environ["LLMBOX_NO_DB"] = "1"
from llmbox import audit  # noqa: E402

os.makedirs(os.path.join(home, "results", "box"))
json.dump({"kind": "speed"}, open(os.path.join(home, "results", "box", "a.json"), "w"))
open(os.path.join(home, "results", "box", "b.json"), "w").write("{broken")
f = audit.check_data(home)
assert any(x["level"] == "error" and "do not read" in x["what"] for x in f), f
assert any(x["level"] == "warn" and "last backup (never)" in x["what"] for x in f), f
time.sleep(0.05)
open(os.path.join(home, audit.BACKUP_MARK), "w").write("now")
assert not any("backup" in x["what"] for x in audit.check_data(home)), "a backup newer than the results: no warning"

site = tempfile.mkdtemp()
open(os.path.join(site, "index.html"), "w").write("<tr data-rid='m1'><td class='spd r'><b>56</b></td></tr><tr data-rid='m2'><td class='spd r'>—</td></tr>"
                                                  "<a href=\"gone.html\">x</a>")
open(os.path.join(site, "recipe-m1.html"), "w").write("<div id='vspd'><span class='sc'>Speed</span><b>59<small>")
open(os.path.join(site, "recipe-m2.html"), "w").write("<div id='vspd'><span class='sc'>Speed</span><b>—")
open(os.path.join(site, "hw-rtx-5070-12gb.html"), "w").write('<a href="recipe-m1.html">M1</a></td><td data-h="tok/s"><b>56</b>'
                                                             '<tr><a href="recipe-m2.html">M2</a></td><td data-h="tok/s"><b>28</b></tr>')
os.makedirs(os.path.join(site, "r"))
open(os.path.join(site, "r", "x.html"), "w").write("-m /home/mixer9292/models/a.gguf")
f = audit.check_site(site)
what = " | ".join(x["what"] for x in f)
assert any(x["level"] == "error" and "home folder" in x["what"] for x in f), what
assert any(x["level"] == "error" and "m1: one model, different speeds: model page 59, home 56" in x["what"] for x in f), what
assert not any("m2: one model" in x["what"] for x in f), "a model with no figure on home is not compared with the next row's figure"
assert any(x["level"] == "warn" and "gone.html" in x["what"] for x in f), what

# a torn entity (an &-less "ldquo;") and a speed labelled 32k but predicted at another depth are errors
site2 = tempfile.mkdtemp()
open(os.path.join(site2, "index.html"), "w").write('<p>slower (the speed)ldquo;at 32k</p><script>const DATA = {"recipes": {"a": {"deepK": 32}, '
                                                   '"ling": {"deepK": 96}}};</script><p>&ldquo;fine&rdquo;</p>')
w2 = " | ".join(x["what"] for x in audit.check_site(site2))
assert "torn HTML entity: index.html" in w2 and "ling 96k" in w2 and "a 32k" not in w2, w2
open(os.path.join(site2, "index.html"), "w").write('<p>&ldquo;fine&rdquo; &amp; &#8220;ok&#8221;</p>')
assert not any("torn" in x["what"] for x in audit.check_site(site2)), "real entities are fine"

ex = audit._Explained(lambda rid: "flawed" if rid == "k2" else "")
ex.append(audit._f("warn", "tests", "k2: never tuned"))
ex.append(audit._f("warn", "tests", "qwen: never tuned"))
assert [x["level"] for x in ex] == ["info", "warn"] and "caveat" in ex[0]["what"]
print("all passed")
