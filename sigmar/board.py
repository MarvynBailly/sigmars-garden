"""The hexagonal board: geometry, freedom rule, parsing and rendering.

Coordinates are axial (q, r) over a hexagon of radius 5 -- 91 cells, laid out
as 11 rows of 6, 7, 8, 9, 10, 11, 10, 9, 8, 7, 6.  Cells are also addressed by
(row, col) with row 0 at the top and col 0 at the left of that row, which is
what the file format and the printed solution use.
"""

from __future__ import annotations

from .marbles import CHARS, EMPTY, EMPTY_CHAR, FROM_CHAR, Marble

RADIUS = 5
N_CELLS = 3 * RADIUS * (RADIUS + 1) + 1  # 91

# The six axial neighbour directions, in cyclic order around a cell. Adjacent
# entries are themselves adjacent, which is what makes "3 contiguous empty
# neighbours" a check on a circular 6-bit window.
DIRS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))

ROW_LENGTHS = tuple(2 * RADIUS + 1 - abs(r) for r in range(-RADIUS, RADIUS + 1))


def _q_range(r: int) -> range:
    return range(max(-RADIUS, -RADIUS - r), min(RADIUS, RADIUS - r) + 1)


# Cell tables, built once at import.
CELLS: list[tuple[int, int]] = []          # index -> (q, r)
ROWCOL: list[tuple[int, int]] = []         # index -> (row, col)
_INDEX: dict[tuple[int, int], int] = {}    # (q, r) -> index

for _r in range(-RADIUS, RADIUS + 1):
    for _q in _q_range(_r):
        _INDEX[(_q, _r)] = len(CELLS)
        ROWCOL.append((_r + RADIUS, _q - _q_range(_r).start))
        CELLS.append((_q, _r))

# index -> 6 neighbour indices in cyclic order; N_CELLS marks "off the board",
# a bit position that is never set so it always reads as empty.
OFF_BOARD = N_CELLS
NEIGHBOURS: tuple[tuple[int, ...], ...] = tuple(
    tuple(_INDEX.get((q + dq, r + dr), OFF_BOARD) for dq, dr in DIRS)
    for q, r in CELLS
)
# index -> bitmask of its on-board neighbours (used for move ordering).
NEIGHBOUR_MASK: tuple[int, ...] = tuple(
    sum(1 << n for n in nbrs if n != OFF_BOARD) for nbrs in NEIGHBOURS
)


def _three_contiguous_empty(pattern: int) -> bool:
    """True if the 6-bit occupancy ring has three adjacent zero bits."""
    ring = pattern | (pattern << 6)
    return any(((ring >> i) & 0b111) == 0 for i in range(6))


# pattern of occupied neighbours (bit j = direction j) -> is the marble free?
FREE_TABLE: tuple[bool, ...] = tuple(_three_contiguous_empty(p) for p in range(64))


def index_of(row: int, col: int) -> int:
    """Index of a (row, col) address; raises IndexError if out of range."""
    if not 0 <= row < len(ROW_LENGTHS) or not 0 <= col < ROW_LENGTHS[row]:
        raise IndexError(f"no cell at row {row}, col {col}")
    r = row - RADIUS
    return _INDEX[(_q_range(r).start + col, r)]


class Board:
    """An immutable-ish snapshot of which marble sits on each of the 91 cells."""

    __slots__ = ("cells",)

    def __init__(self, cells):
        cells = list(cells)
        if len(cells) != N_CELLS:
            raise ValueError(f"expected {N_CELLS} cells, got {len(cells)}")
        self.cells = cells

    @classmethod
    def empty(cls) -> "Board":
        return cls([EMPTY] * N_CELLS)

    def __getitem__(self, i: int) -> int:
        return self.cells[i]

    def copy(self) -> "Board":
        return Board(self.cells)

    def occupancy(self) -> int:
        """Bitmask of occupied cells."""
        return sum(1 << i for i, m in enumerate(self.cells) if m)

    def counts(self) -> dict:
        tally = {}
        for m in self.cells:
            if m:
                tally[Marble(m)] = tally.get(Marble(m), 0) + 1
        return tally

    def marble_count(self) -> int:
        return sum(1 for m in self.cells if m)

    def is_free(self, i: int) -> bool:
        """A marble is free when three contiguous neighbouring spaces are empty.

        Spaces off the edge of the board count as empty.
        """
        cells = self.cells
        pattern = 0
        for j, n in enumerate(NEIGHBOURS[i]):
            if n != OFF_BOARD and cells[n]:
                pattern |= 1 << j
        return FREE_TABLE[pattern]

    def free_cells(self) -> list[int]:
        return [i for i, m in enumerate(self.cells) if m and self.is_free(i)]

    def render(self, marks: dict | None = None) -> str:
        """Pretty-print the board; `marks` maps cell index -> replacement char."""
        marks = marks or {}
        lines = []
        i = 0
        for row, length in enumerate(ROW_LENGTHS):
            pad = " " * (len(ROW_LENGTHS) - length)
            glyphs = []
            for _ in range(length):
                glyphs.append(marks.get(i) or CHARS.get(self.cells[i], EMPTY_CHAR))
                i += 1
            lines.append(f"{pad}{' '.join(glyphs)}")
        return "\n".join(lines)

    def to_text(self) -> str:
        return self.render()

    def __str__(self) -> str:
        return self.render()


def parse_board(text: str) -> Board:
    """Read the board file format: 11 rows of single-character marble codes.

    Blank lines and `#` comments are ignored, as is whitespace inside a row.
    """
    rows = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if line:
            rows.append((lineno, line))

    if len(rows) != len(ROW_LENGTHS):
        raise ValueError(
            f"expected {len(ROW_LENGTHS)} board rows, got {len(rows)}"
        )

    cells = []
    for (lineno, line), (row, length) in zip(rows, enumerate(ROW_LENGTHS)):
        tokens = [c for c in line if not c.isspace()]
        if len(tokens) != length:
            raise ValueError(
                f"line {lineno}: row {row} needs {length} cells, got {len(tokens)}"
            )
        for col, token in enumerate(tokens):
            if token not in FROM_CHAR:
                raise ValueError(f"line {lineno}, col {col}: unknown marble {token!r}")
            cells.append(FROM_CHAR[token])
    return Board(cells)


def load_board(path) -> Board:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_board(fh.read())
