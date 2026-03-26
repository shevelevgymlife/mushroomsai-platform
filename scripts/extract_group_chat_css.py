from pathlib import Path

p = Path("web/templates/dashboard/user.html")
lines = p.read_text(encoding="utf-8").splitlines()
# 374-639 (0-based 373-638), plus keyframes igMsgIn at 843
css = "\n".join(lines[373:639]) + "\n" + lines[842] + "\n"
Path("static/css/community-groups.css").write_text(
    "/* From dashboard/user.html — group chats */\n" + css, encoding="utf-8"
)
print("ok", len(css))
