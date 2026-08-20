"""Random board generation, used to test the solver.

Boards are built backwards.  Starting from an empty board we place the pairs of
a solution in reverse order, requiring each pair to be free the moment it lands.
That is exactly the condition the forward removal would have needed, so every
generated board comes with a solution by construction.
"""

from __future__ import annotations

import random

from .board import N_CELLS, Board
from .marbles import CARDINALS, METALS, Marble

SALT = Marble.SALT
QUICKSILVER = Marble.QUICKSILVER
GOLD = Marble.GOLD


def _salt_plan(rng: random.Random) -> list[tuple[Marble, Marble]]:
    """Pair up 32 cardinals and 4 salts.

    Each cardinal element must give up an even number of its marbles to salt,
    otherwise one is left with no partner.
    """
    salts_left = 4
    pairs: list[tuple[Marble, Marble]] = []
    remaining = {c: 8 for c in CARDINALS}
    for cardinal in rng.sample(CARDINALS, len(CARDINALS)):
        if salts_left >= 2 and rng.random() < 0.35:
            take = 2 * rng.randint(1, salts_left // 2)
            salts_left -= take
            remaining[cardinal] -= take
            pairs.extend([(cardinal, SALT)] * take)
    pairs.extend([(SALT, SALT)] * (salts_left // 2))
    for cardinal, count in remaining.items():
        pairs.extend([(cardinal, cardinal)] * (count // 2))
    return pairs


def _pair_plan(rng: random.Random) -> list[tuple[Marble, Marble | None]]:
    """A full solution's worth of pairs, in *reverse* removal order."""
    # Metals are forced: lead is removed first and gold last, so in reverse the
    # chain is placed gold-first. Everything else may interleave freely.
    chain: list[tuple[Marble, Marble | None]] = [(GOLD, None)]
    chain += [
        (metal, QUICKSILVER)
        for metal in reversed(METALS[:-1])  # silver, copper, iron, tin, lead
    ]

    others = _salt_plan(rng) + [(Marble.VITAE, Marble.MORS)] * 4
    rng.shuffle(others)

    # Merge, keeping the chain's relative order intact.
    slots = sorted(rng.sample(range(len(chain) + len(others)), len(chain)))
    merged: list[tuple[Marble, Marble | None]] = []
    chain_iter, other_iter = iter(chain), iter(others)
    slot_set = set(slots)
    for position in range(len(chain) + len(others)):
        merged.append(next(chain_iter) if position in slot_set else next(other_iter))
    return merged


def random_solvable_board(rng: random.Random | None = None, attempts: int = 60) -> Board:
    """Generate a standard 55-marble board that is guaranteed solvable."""
    rng = rng or random.Random()
    for _ in range(attempts):
        board = _try_build(rng)
        if board is not None:
            return board
    raise RuntimeError("board generation failed; try a different seed")


def _try_build(rng: random.Random) -> Board | None:
    board = Board.empty()
    empties = list(range(N_CELLS))

    for kind_a, kind_b in _pair_plan(rng):
        placed = _place(board, empties, kind_a, kind_b, rng)
        if not placed:
            return None
    return board


def _candidates(board, empties, rng, exclude=None) -> list[int]:
    """Empty cells where a marble would land free, most central first.

    `is_free` only inspects a cell's neighbours, so it answers the question for
    an empty cell too.  The central bias keeps generated boards clustered like a
    real deal instead of smeared across the whole hexagon.
    """
    from .board import CELLS

    def depth(i):
        q, r = CELLS[i]
        return max(abs(q), abs(r), abs(q + r))

    cand = [i for i in empties if i != exclude and board.is_free(i)]
    cand.sort(key=lambda i: (depth(i), rng.random()))
    return cand


def _place(board, empties, kind_a, kind_b, rng, tries: int = 30) -> bool:
    """Drop one pair onto empty cells such that both land free."""
    cells = board.cells
    for a in _candidates(board, empties, rng)[:tries]:
        cells[a] = kind_a
        if kind_b is None:
            empties.remove(a)
            return True
        # Placing `b` can block `a` if they end up adjacent, so re-check both.
        for b in _candidates(board, empties, rng, exclude=a)[:tries]:
            cells[b] = kind_b
            if board.is_free(a) and board.is_free(b):
                empties.remove(a)
                empties.remove(b)
                return True
            cells[b] = 0
        cells[a] = 0
    return False


def random_board(rng: random.Random | None = None) -> Board:
    """Deal the standard 55 marbles onto random cells, solvable or not.

    Placement is biased towards the middle of the board, which is roughly how
    the real game deals, and makes for a more honest difficulty test.
    """
    from .board import CELLS

    rng = rng or random.Random()
    from .marbles import STANDARD_COUNTS

    bag = [m for m, n in STANDARD_COUNTS.items() for _ in range(n)]
    rng.shuffle(bag)

    order = sorted(range(N_CELLS), key=lambda i: (max(abs(CELLS[i][0]), abs(CELLS[i][1]), abs(CELLS[i][0] + CELLS[i][1])), rng.random()))
    board = Board.empty()
    for cell, marble in zip(order[: len(bag)], bag):
        board.cells[cell] = marble
    return board
