"""Cut marble templates from reference screenshots with verified boards.

    python tools/build_templates.py <shot.png> <board.txt> [<shot2.png> <board2.txt> ...]

Each board file must be a correct transcription of its screenshot; every cell
becomes a labelled example. More reference boards is strictly better -- each
adds a second rendering of every marble, and the metals appear only once per
board. Writes sigmar/templates.npz.
"""

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sigmar.board import load_board
from sigmar.vision import build_templates, find_grid


def main(argv):
    if len(argv) < 2 or len(argv) % 2:
        print(__doc__)
        return 2

    pairs = []
    for image_path, board_path in zip(argv[::2], argv[1::2]):
        image = Image.open(image_path)
        board = load_board(board_path)
        cx, cy, size = find_grid(image)
        print(f"{Path(image_path).name}: {image.width}x{image.height}, "
              f"grid ({cx:.1f}, {cy:.1f}) cell {size:.2f}px, "
              f"{board.marble_count()} marbles")
        pairs.append((image, board))

    data = build_templates(pairs)
    total = sum(data["templates"].values())
    print(f"\n{total} templates over {len(data['templates'])} kinds:")
    for char, count in sorted(data["templates"].items()):
        print(f"  {char}: {count}")
    print(f"tile={data['tile']} sigma={data['sigma']} radius={data['radius']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
