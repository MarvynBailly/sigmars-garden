"""Build a browser page that checks the JavaScript reader against known boards.

    python tools/build_vision_test.py && <chrome> --headless --dump-dom <page>

The reference screenshots are inlined as data URLs: a file:// image would taint
the canvas and make getImageData throw, and data URLs do not.
"""

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "vision_test.html"

CASES = [
    ("screenshot", "boards/screenshot.png", "boards/screenshot.txt"),
    ("screenshot2", "boards/screenshot2.png", "boards/screenshot2.txt"),
]


def data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def main() -> int:
    extra = sys.argv[1:]
    cases = list(CASES)
    for i, path in enumerate(extra):
        cases.append((f"extra{i + 1}", path, None))

    images = []
    for name, image_path, board_path in cases:
        board = (ROOT / board_path).read_text(encoding="utf-8") if board_path else ""
        images.append(
            "{name: %r, url: %r, truth: %r}" % (name, data_url(Path(ROOT / image_path)), board)
        )

    html = f"""<!doctype html>
<meta charset="utf-8">
<title>vision check</title>
<pre id="out">running…</pre>
<script>{(ROOT / "web" / "sigmar.js").read_text(encoding="utf-8")}</script>
<script>{(ROOT / "web" / "templates.js").read_text(encoding="utf-8")}</script>
<script>{(ROOT / "web" / "vision.js").read_text(encoding="utf-8")}</script>
<script>
const CASES = [{",".join(images)}];
const out = document.getElementById("out");
const log = (line) => {{ out.textContent += "\\n" + line; }};

function loadImage(url) {{
  return new Promise((resolve, reject) => {{
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  }});
}}

(async () => {{
  out.textContent = "";
  loadTemplates(TEMPLATE_DATA);
  for (const testCase of CASES) {{
    try {{
      const image = await loadImage(testCase.url);
      const started = performance.now();
      const result = readBoardFromImage(image, {{
        cells: CELLS, fromChar: FROM_CHAR, standardCounts: STANDARD_COUNTS, chars: CHARS,
      }});
      const elapsed = Math.round(performance.now() - started);
      const board = new Board(result.cells);
      const counts = board.counts();
      const bad = Object.entries(STANDARD_COUNTS)
        .filter(([k, v]) => (counts[k] || 0) !== v)
        .map(([k, v]) => `${{NAMES[k]}} ${{counts[k] || 0}}/${{v}}`);
      let wrong = "n/a";
      if (testCase.truth) {{
        const truth = parseBoard(testCase.truth);
        wrong = String(result.cells.filter((c, i) => c !== truth.cells[i]).length);
      }}
      const solved = solve(board);
      log(`${{testCase.name}}: ${{image.width}}x${{image.height}} grid=(`
        + `${{result.grid.cx.toFixed(1)}},${{result.grid.cy.toFixed(1)}},`
        + `${{result.grid.size.toFixed(2)}}) marbles=${{board.marbleCount()}} `
        + `wrongCells=${{wrong}} counts=${{bad.length ? bad.join(",") : "ok"}} `
        + `uncertain=${{result.uncertain.length}} solved=${{solved.solved}} `
        + `moves=${{solved.moves ? solved.moves.length : "-"}} ${{elapsed}}ms`);
    }} catch (err) {{
      log(`${{testCase.name}}: ERROR ${{err.message}}`);
    }}
  }}
  log("done");
}})();
</script>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
