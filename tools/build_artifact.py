"""Assemble the single-file web page from web/page.html.in and the scripts.

    python tools/build_artifact.py

Writes two files from the same source:

  web/artifact.html   the page in Artifact form -- content only, no <!doctype>,
                      <html>, <head> or <body>, which the host supplies
  web/preview.html    the same content wrapped in a full document, for opening
                      locally or driving with a headless browser

Everything is inlined because the page has to work with no network at all.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def main() -> int:
    template = (WEB / "page.html.in").read_text(encoding="utf-8")
    for token, name in [
        ("{{SIGMAR_JS}}", "sigmar.js"),
        ("{{TEMPLATE_JS}}", "templates.js"),
        ("{{VISION_JS}}", "vision.js"),
        ("{{UI_JS}}", "ui.js"),
    ]:
        source = (WEB / name).read_text(encoding="utf-8")
        if token not in template:
            print(f"error: {token} missing from page.html.in", file=sys.stderr)
            return 1
        template = template.replace(token, source)

    artifact = WEB / "artifact.html"
    artifact.write_text(template, encoding="utf-8")

    preview = WEB / "preview.html"
    preview.write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Sigmar's Garden</title>\n</head>\n<body>\n" + template + "\n</body>\n</html>\n",
        encoding="utf-8",
    )

    for path in (artifact, preview):
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size / 1024 / 1024:.2f} MB")
    if artifact.stat().st_size > 16 * 1024 * 1024:
        print("error: over the 16MB artifact limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
