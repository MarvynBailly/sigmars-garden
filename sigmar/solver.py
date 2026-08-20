"""Depth-first solver for Sigmar's Garden.

The search is exhaustive but memoised: a board state is exactly the set of
occupied cells (a marble never moves, so position implies type), which packs
into a single integer bitmask.  Any state proven unsolvable is recorded, so the
same dead end is never explored twice regardless of the move order that led
there.

Two facts about the game shape the search:

* Removing marbles never locks anything -- freedom only ever increases.  So the
  hard part is not the order of moves but the *pairing*: spending a salt on a
  cardinal that had a same-element partner available can orphan a marble later.
* Metals are forced.  Only the lowest metal still on the board can be cleared,
  and only against quicksilver, so those moves have no real alternatives.

Move ordering exploits both, and a parity check on the salt/cardinal budget
prunes states that are already doomed before the search notices.
"""

from __future__ import annotations

import random
from typing import Iterator, NamedTuple

from .board import FREE_TABLE, NEIGHBOUR_MASK, NEIGHBOURS, ROWCOL, Board
from .marbles import CARDINALS, METALS, NAMES, Marble

FIRE, WATER, EARTH, AIR = CARDINALS
SALT = Marble.SALT
VITAE = Marble.VITAE
MORS = Marble.MORS
QUICKSILVER = Marble.QUICKSILVER
GOLD = Marble.GOLD

# Move-ordering priorities. Higher goes first. Metals are forced moves with no
# alternative pairing, so they cost nothing to take early; salt is the scarce
# flexible resource, so it is spent last.
PRI_GOLD = 6
PRI_METAL = 5
PRI_CARDINAL = 4
PRI_VITAE = 3
PRI_SALT_CARDINAL = 1
PRI_SALT_SALT = 0

# How hard restarts shake up the ordering.  Priority classes sit 16 apart, so a
# jitter this large lets a restart reconsider which *kind* of move it opens
# with, not merely which marbles of the same kind it picks.  That distinction
# is what makes restarts work: with a small jitter the pathological boards kept
# re-walking the same doomed opening and needed 10x the states to crack.
RESTART_JITTER = 120.0


class Move(NamedTuple):
    """One click-pair. `b` is None for gold, which clears on its own."""

    a: int
    b: int | None
    kind_a: Marble
    kind_b: Marble | None

    def describe(self) -> str:
        ra, ca = ROWCOL[self.a]
        left = f"{NAMES[self.kind_a]:<11} (r{ra + 1},c{ca + 1})"
        if self.b is None:
            return f"{left}   [cleared alone]"
        rb, cb = ROWCOL[self.b]
        return f"{left}  +  {NAMES[self.kind_b]:<11} (r{rb + 1},c{cb + 1})"


class SolveResult(NamedTuple):
    moves: list[Move] | None
    nodes: int
    exhausted: bool  # True if the whole reachable state space was searched

    @property
    def solved(self) -> bool:
        return self.moves is not None


class Solver:
    def __init__(self, board: Board):
        self.board = board
        self.types = list(board.cells)
        self.start = board.occupancy()
        self._counts = [0] * (int(GOLD) + 1)
        for m in self.types:
            if m:
                self._counts[m] += 1

    # ---- board queries -------------------------------------------------

    def _free_cells(self, occ: int) -> list[int]:
        """Indices of marbles with three contiguous empty neighbours."""
        free = []
        neighbours = NEIGHBOURS
        table = FREE_TABLE
        rest = occ
        while rest:
            low = rest & -rest
            i = low.bit_length() - 1
            rest ^= low
            n0, n1, n2, n3, n4, n5 = neighbours[i]
            pattern = (
                ((occ >> n0) & 1)
                | (((occ >> n1) & 1) << 1)
                | (((occ >> n2) & 1) << 2)
                | (((occ >> n3) & 1) << 3)
                | (((occ >> n4) & 1) << 4)
                | (((occ >> n5) & 1) << 5)
            )
            if table[pattern]:
                free.append(i)
        return free

    def _feasible(self, counts) -> bool:
        """Cheap proof that the remaining multiset can still be paired off.

        Every cardinal element must end up matched with its own kind or with
        salt, so each element with an odd count consumes one salt, and whatever
        salt is left over has to pair with itself.
        """
        salt = counts[SALT]
        odd = (counts[FIRE] & 1) + (counts[WATER] & 1) + (counts[EARTH] & 1) + (counts[AIR] & 1)
        return odd <= salt and not ((salt - odd) & 1)

    # ---- move generation -----------------------------------------------

    def _moves(self, occ: int, counts) -> list[tuple[int, int, int | None]]:
        """All legal moves as (priority-score, a, b), best first."""
        by_kind: dict[int, list[int]] = {}
        for i in self._free_cells(occ):
            by_kind.setdefault(self.types[i], []).append(i)

        scored = []
        salts = by_kind.get(SALT, ())

        def add(pri, a, b):
            # Digging matters: clearing a marble hemmed in by neighbours opens
            # up the densest part of the board, so break priority ties that way.
            # (Scoring by how many marbles a move actually unlocks was tried and
            # searched fewer states but cost more wall-clock than it saved.)
            tie = bin(occ & NEIGHBOUR_MASK[a]).count("1")
            if b is not None:
                tie += bin(occ & NEIGHBOUR_MASK[b]).count("1")
            scored.append((pri * 16 + tie, a, b))

        for cardinal in CARDINALS:
            same = by_kind.get(cardinal, ())
            for x in range(len(same)):
                for y in range(x + 1, len(same)):
                    add(PRI_CARDINAL, same[x], same[y])
            for a in same:
                for s in salts:
                    add(PRI_SALT_CARDINAL, a, s)

        for x in range(len(salts)):
            for y in range(x + 1, len(salts)):
                add(PRI_SALT_SALT, salts[x], salts[y])

        for v in by_kind.get(VITAE, ()):
            for m in by_kind.get(MORS, ()):
                add(PRI_VITAE, v, m)

        for metal in METALS:
            if not counts[metal]:
                continue
            # Only the lowest remaining metal is eligible.
            if metal == GOLD:
                for g in by_kind.get(GOLD, ()):
                    add(PRI_GOLD, g, None)
            else:
                for m in by_kind.get(metal, ()):
                    for q in by_kind.get(QUICKSILVER, ()):
                        add(PRI_METAL, m, q)
            break

        scored.sort(key=lambda t: -t[0])
        return scored

    # ---- search ---------------------------------------------------------

    def solve(
        self,
        max_nodes: int = 2_000_000,
        first_pass: int = 20_000,
        seed: int = 0,
    ) -> SolveResult:
        """Search for a full clear, with restarts.

        Runtimes here are heavy-tailed: nearly every board falls in a few dozen
        states, but occasionally one move-ordering choice near the root sends
        the search into a huge barren subtree.  So the search runs in rounds of
        doubling node budgets, re-shuffling tie-breaks each round.

        The set of states proven unsolvable is *kept* across rounds.  Deadness
        does not depend on the order a state was reached in, so a restart never
        repeats the previous round's work -- it only re-picks its gambles.  That
        keeps the search complete: `exhausted` still means "no solution exists".

        Returns the move list, or None if the board is unsolvable (`exhausted`
        True) or the node budget ran out first (`exhausted` False).
        """
        dead: set[int] = set()
        total = 0
        budget = min(first_pass, max_nodes)
        attempt = 0

        while True:
            counts = list(self._counts)
            path: list[tuple[int, int | None]] = []
            # The first round runs on the plain heuristic; later rounds jitter it.
            rng = random.Random(seed + attempt) if attempt else None
            self._nodes = 0
            self._budget = budget
            self._out_of_budget = False

            found = self._search(self.start, counts, dead, path, rng)
            total += self._nodes

            if found:
                moves = [
                    Move(
                        a,
                        b,
                        Marble(self.types[a]),
                        None if b is None else Marble(self.types[b]),
                    )
                    for a, b in path
                ]
                return SolveResult(moves, total, False)

            if not self._out_of_budget:
                return SolveResult(None, total, True)  # searched everything
            if total >= max_nodes:
                return SolveResult(None, total, False)

            attempt += 1
            budget = min(budget * 2, max_nodes - total)

    def _search(self, occ, counts, dead, path, rng) -> bool:
        if occ == 0:
            return True
        if occ in dead:
            return False
        if self._nodes >= self._budget:
            self._out_of_budget = True
            return False
        self._nodes += 1

        if not self._feasible(counts):
            dead.add(occ)
            return False

        moves = self._moves(occ, counts)
        if rng is not None:
            # Jitter the ordering so restarts explore a different subtree first.
            moves = [(score + rng.random() * RESTART_JITTER, a, b) for score, a, b in moves]
            moves.sort(key=lambda t: -t[0])

        types = self.types
        for _, a, b in moves:
            counts[types[a]] -= 1
            next_occ = occ & ~(1 << a)
            if b is not None:
                counts[types[b]] -= 1
                next_occ &= ~(1 << b)
            path.append((a, b))

            if self._search(next_occ, counts, dead, path, rng):
                return True

            path.pop()
            counts[types[a]] += 1
            if b is not None:
                counts[types[b]] += 1

            if self._out_of_budget:
                return False

        dead.add(occ)
        return False


def solve(board: Board, max_nodes: int = 2_000_000, seed: int = 0) -> SolveResult:
    """Solve `board`. Convenience wrapper around `Solver.solve`."""
    return Solver(board).solve(max_nodes=max_nodes, seed=seed)


# ---- verification --------------------------------------------------------


def verify(board: Board, moves: list[Move]) -> None:
    """Replay `moves` against the rules; raises ValueError on any violation."""
    from .marbles import is_solo, matches

    work = board.copy()
    counts = [0] * (int(GOLD) + 1)
    for m in work.cells:
        if m:
            counts[m] += 1

    for n, move in enumerate(moves, 1):
        cells = [move.a] + ([] if move.b is None else [move.b])
        for i in cells:
            if not work.cells[i]:
                raise ValueError(f"move {n}: cell {ROWCOL[i]} is empty")
            if not work.is_free(i):
                raise ValueError(f"move {n}: marble at {ROWCOL[i]} is not free")
        a = Marble(work.cells[move.a])
        if move.b is None:
            if not is_solo(a, counts):
                raise ValueError(f"move {n}: {NAMES[a]} cannot be cleared alone")
        else:
            b = Marble(work.cells[move.b])
            if move.a == move.b:
                raise ValueError(f"move {n}: same marble picked twice")
            if not matches(a, b, counts):
                raise ValueError(f"move {n}: {NAMES[a]} does not match {NAMES[b]}")
        for i in cells:
            counts[work.cells[i]] -= 1
            work.cells[i] = 0

    if work.marble_count():
        raise ValueError(f"{work.marble_count()} marbles left on the board")


def replay(board: Board, moves: list[Move]) -> Iterator[Board]:
    """Yield the board state before each move, then the final empty board."""
    work = board.copy()
    for move in moves:
        yield work.copy()
        work.cells[move.a] = 0
        if move.b is not None:
            work.cells[move.b] = 0
    yield work
