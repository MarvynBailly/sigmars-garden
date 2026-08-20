"""Assemble the single-file web page from web/page.html.in and the scripts.

    python tools/build_artifact.py

Writes three files from the same source:

  web/artifact.html      content only -- no <!doctype>, <html>, <head> or <body>,
                         which the Artifact host supplies. No webfonts: that host
                         blocks font CDNs, and a silent fallback is worse than a
                         chosen system stack.
  web/preview.html       the same content as a full document, for opening locally
                         or driving with a headless browser
  docs/index.html        what GitHub Pages serves at marvyn.com/sigmars-garden/.
                         A full document that does load marvyn.com's own
                         typefaces, so the page reads as part of that site rather
                         than a visitor on it. Pages is pointed at docs/ so the
                         published site is this page and nothing else.

Everything else is inlined, because the page has to work with no network at all.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

SCRIPTS = [
    ("{{SIGMAR_JS}}", "sigmar.js"),
    ("{{TEMPLATE_JS}}", "templates.js"),
    ("{{VISION_JS}}", "vision.js"),
    ("{{UI_JS}}", "ui.js"),
]

# An emoji favicon, inline, so the page stays a single file.
FAVICON = (
    "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<text y='.9em' font-size='90'>%E2%9A%97%EF%B8%8F</text></svg>"
)

SITE_FONTS = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&family=IBM+Plex+Serif:wght@500;600&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  /* marvyn.com's own faces, so this page belongs to that site. The stacks in
     the page below stay as the fallback if the fonts do not arrive. */
  :root {
    --display: "IBM Plex Serif", "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
    --body: "IBM Plex Sans", ui-sans-serif, "Segoe UI", system-ui, sans-serif;
    --mono: "JetBrains Mono", "Cascadia Mono", Consolas, ui-monospace, monospace;
  }
</style>"""

DESCRIPTION = (
    "Play Sigmar's Garden from Opus Magnum, let the solver clear it, or paste a "
    "screenshot of the real game and watch it read all 55 marbles back."
)


def document(body: str, head_extra: str = "") -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="description" content="{DESCRIPTION}">\n'
        "<title>Sigmar's Garden — a solver you can play</title>\n"
        f'<link rel="icon" href="{FAVICON}">\n'
        f"{head_extra}\n</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def main() -> int:
    template = (WEB / "page.html.in").read_text(encoding="utf-8")
    for token, name in SCRIPTS:
        if token not in template:
            print(f"error: {token} missing from page.html.in", file=sys.stderr)
            return 1
        template = template.replace(token, (WEB / name).read_text(encoding="utf-8"))

    if "{{HOME_LINK}}" not in template:
        print("error: {{HOME_LINK}} missing from page.html.in", file=sys.stderr)
        return 1

    standalone = template.replace("{{HOME_LINK}}", "")
    on_site = template.replace(
        "{{HOME_LINK}}", '<a href="https://marvyn.com/">marvyn.com</a>'
    )

    outputs = [
        (WEB / "artifact.html", standalone),
        (WEB / "preview.html", document(standalone)),
        (ROOT / "docs" / "index.html", document(on_site, SITE_FONTS)),
    ]
    for path, text in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"{path.relative_to(ROOT).as_posix()}: {path.stat().st_size / 1024 / 1024:.2f} MB")

    # Without this, Pages runs the folder through Jekyll, which ignores files
    # beginning with an underscore and rewrites what it feels like.
    (ROOT / "docs" / ".nojekyll").write_text("", encoding="utf-8")

    artifact = outputs[0][0]
    if artifact.stat().st_size > 16 * 1024 * 1024:
        print("error: over the 16MB artifact limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
