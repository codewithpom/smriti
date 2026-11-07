"""Convert a browser-extension cookie export (JSON list) to the Netscape file gallery-dl reads.

  python cookies_json_to_netscape.py <in.json> <out.txt>

Prints only the cookie count, never names or values. Overwrites <out.txt>.
"""
import json
import sys
from pathlib import Path


def convert(cookies):
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c["domain"]
        sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = int(c.get("expirationDate", 0))
        lines.append("\t".join([domain, sub, c.get("path", "/"), secure, str(expiry), c["name"], c["value"]]))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    src, dst = sys.argv[1:3]
    cookies = json.loads(Path(src).read_text(encoding="utf-8"))
    Path(dst).write_text(convert(cookies), encoding="utf-8", newline="\n")
    print(f"cookies written: {len(cookies)}")
