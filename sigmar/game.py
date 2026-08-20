"""Interactive game state: selection, clicking, undo.

The front end owns no rules at all -- every click comes back here, so playing
the board in a browser exercises exactly the same freedom and matching code the
solver does.  When a click is refused, the reason is returned as text, which is
what makes the UI useful for checking the rules rather than just playing them.
"""

from __future__ import annotations

from .board import ROWCOL, Board
from .marbles import METALS, NAMES, Marble, is_solo, lowest_metal, matches
from .solver import Move


class Game:
    def __init__(self, board: Board, name: str = "board"):
        self.name = name
        self.start = board.copy()
        self.board = board.copy()
        self.moves: list[Move] = []
        self.selected: int | None = None

    # ---- queries --------------------------------------------------------

    def counts(self) -> list[int]:
        tally = [0] * (int(Marble.GOLD) + 1)
        for m in self.board.cells:
            if m:
                tally[m] += 1
        return tally

    def free(self) -> set[int]:
        return set(self.board.free_cells())

    def partners(self, i: int) -> set[int]:
        """Free marbles that `i` may legally be cleared with, right now."""
        if not self.board.cells[i] or not self.board.is_free(i):
            return set()
        counts = self.counts()
        a = Marble(self.board.cells[i])
        return {
            j
            for j in self.free()
            if j != i and matches(a, Marble(self.board.cells[j]), counts)
        }

    def playable(self) -> set[int]:
        """Marbles the game would draw highlighted: free *and* has a move."""
        counts = self.counts()
        out = set()
        for i in self.free():
            marble = Marble(self.board.cells[i])
            if is_solo(marble, counts) or self.partners(i):
                out.add(i)
        return out

    def won(self) -> bool:
        return self.board.marble_count() == 0

    # ---- why a click was refused ---------------------------------------

    def _refusal(self, i: int) -> str | None:
        """Explain why marble `i` cannot be picked up, or None if it can."""
        marble = Marble(self.board.cells[i])
        if not self.board.is_free(i):
            return f"{NAMES[marble]} is blocked -- it needs 3 contiguous empty neighbours."
        counts = self.counts()
        if marble in METALS:
            lowest = lowest_metal(counts)
            if marble != lowest:
                return (
                    f"{NAMES[marble]} cannot go yet -- metals transmute in order, "
                    f"and {NAMES[lowest]} is still on the board."
                )
            if marble is not Marble.GOLD and not self.partners(i):
                return f"No free quicksilver to pair with {NAMES[marble]}."
        elif not self.partners(i):
            return f"Nothing on the board currently matches this {NAMES[marble]}."
        return None

    # ---- mutation -------------------------------------------------------

    def _clear(self, a: int, b: int | None) -> None:
        kinds = (
            Marble(self.board.cells[a]),
            None if b is None else Marble(self.board.cells[b]),
        )
        self.moves.append(Move(a, b, kinds[0], kinds[1]))
        self.board.cells[a] = 0
        if b is not None:
            self.board.cells[b] = 0
        self.selected = None

    def click(self, i: int) -> dict:
        """Apply a click. Returns {ok, message, cleared}."""
        if not 0 <= i < len(self.board.cells):
            return {"ok": False, "message": "No such cell."}
        if not self.board.cells[i]:
            self.selected = None
            return {"ok": True, "message": "", "cleared": None}

        marble = Marble(self.board.cells[i])
        if self.selected == i:
            self.selected = None
            return {"ok": True, "message": "", "cleared": None}

        # Second click: try to complete the pair.
        if self.selected is not None:
            first = self.selected
            if i in self.partners(first):
                other = Marble(self.board.cells[first])
                self._clear(first, i)
                return {
                    "ok": True,
                    "cleared": [first, i],
                    "message": f"Cleared {NAMES[other]} + {NAMES[marble]}.",
                }
            # Not a match -- fall through and treat this as a fresh selection,
            # which is what the game does.

        refusal = self._refusal(i)
        if refusal:
            self.selected = None
            return {"ok": False, "message": refusal, "cleared": None}

        if is_solo(marble, self.counts()):
            self._clear(i, None)
            return {"ok": True, "cleared": [i], "message": "Gold cleared on its own."}

        self.selected = i
        row, col = ROWCOL[i]
        return {
            "ok": True,
            "cleared": None,
            "message": f"{NAMES[marble]} (r{row + 1},c{col + 1}) selected.",
        }

    def undo(self) -> bool:
        if not self.moves:
            return False
        move = self.moves.pop()
        self.board.cells[move.a] = move.kind_a
        if move.b is not None:
            self.board.cells[move.b] = move.kind_b
        self.selected = None
        return True

    def reset(self) -> None:
        self.board = self.start.copy()
        self.moves = []
        self.selected = None
