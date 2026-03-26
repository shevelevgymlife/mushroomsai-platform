from pathlib import Path

p = Path("web/templates/dashboard/user.html")
lines = p.read_text(encoding="utf-8").splitlines()
parts = [
    "\n".join(lines[2765:2782]),
    "\n".join(lines[2965:3902]),
]
js = "\n\n".join(parts)
js = js.replace("goSec('groups');", "")
js = js.replace('goSec("groups");', "")
out = Path("static/js/community-groups-chat.js")
out.write_text(
    "// Extracted from dashboard/user.html — community group chats\n" + js + "\n",
    encoding="utf-8",
)
print("Wrote", out, "lines", len(js.splitlines()))
