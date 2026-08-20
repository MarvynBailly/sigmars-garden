"""Play the real game: screenshot the monitor, solve, click the marbles.

The loop is deliberately not "solve once, then fire 56 clicks and hope". After
every pair it re-checks that those two cells actually went empty, and stops if
they did not. A click that misses -- because the window moved, or the grid was a
pixel out, or the game was mid-animation -- otherwise leaves the plan and the
board disagreeing, and every later click lands on the wrong marble.

Three ways to stop it: press Escape, move the mouse yourself, or let it fail its
own verification.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import desktop
from .board import ROWCOL, Board
from .marbles import NAMES, Marble, matches
from .solver import Move, solve
from .vision import (
    VisionError,
    cell_centres,
    find_grid,
    read_board,
    read_cells,
)

GAME_TITLE = "Opus Magnum"

# Where the panel's other controls sit relative to the board centre, in units of
# the hex size. The whole Sigmar's Garden panel scales as one piece, so these
# hold at any window size: measured on a 904px screenshot, they land within a
# couple of pixels on a 4K one.
NEW_GAME_OFFSET = (-9.048, 9.966)
WINS_BOX_OFFSET = (9.678, 10.425)
WINS_BOX_HALF = (1.5, 0.75)

# Mean per-pixel change below which the board counts as no longer animating.
# An idle board measures exactly zero, so this only has to clear capture noise.
STILL_TOLERANCE = 0.05
# How many cells the known deal counts may overrule before the read is suspect.
MAX_CORRECTIONS = 2
# Each retry of a move waits this much longer than the attempt before it.
RETRY_SLOWDOWN = 4.0
# How long to allow for an animation to get going before deciding there is none.
START_GRACE = 1.2


class AutoplayError(RuntimeError):
    pass


class Aborted(AutoplayError):
    pass


class _Unsolvable(AutoplayError):
    """This particular board cannot be won -- deal another rather than stop."""


class _BadDeal(AutoplayError):
    """This deal would not read cleanly -- deal another rather than stop."""


class _MoveFailed(AutoplayError):
    """A move would not go through -- abandon this board, not the whole run."""


def _tally(board):
    counts = [0] * (int(Marble.GOLD) + 1)
    for marble in board.cells:
        if marble:
            counts[marble] += 1
    return counts


@dataclass
class Options:
    monitor: int = 1
    dry_run: bool = False
    countdown: float = 3.0
    click_delay: float = 0.05      # between the two clicks of a pair
    click_settle: float = 0.015    # after the pointer lands, before pressing
    clear_timeout: float = 1.2     # longest to wait for a pair to vanish
    poll: float = 0.02             # how often to look while waiting
    settle: float = 0.9            # after focusing, before the first capture
    verify: bool = True
    max_retries: int = 2
    focus_game: bool = True
    max_moves: int | None = None   # stop early; handy for trying it out
    games: int = 1                 # how many wins to play for
    deal_timeout: float = 10.0     # how long a new board may take to appear
    max_deals: int | None = None   # give up after this many boards, however they went


@dataclass
class Progress:
    played: int = 0
    planned: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class Session:
    """The result of playing for a number of wins."""

    wins: int = 0
    target: int = 0
    deals: int = 0
    unsolvable: int = 0
    abandoned: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.wins >= self.target


def _monitor_rect(index: int) -> tuple[int, int, int, int]:
    desktop.require()
    screens = desktop.monitors()
    if not 1 <= index <= len(screens):
        raise AutoplayError(
            f"No monitor {index}: this machine has {len(screens)} "
            f"({', '.join(str(s) for s in screens)})."
        )
    return screens[index - 1]


class Autoplayer:
    """Drives one game. Holds the board's screen geometry once it is found."""

    def __init__(self, options: Options | None = None, log=print):
        self.options = options or Options()
        self.log = log
        self.rect = _monitor_rect(self.options.monitor)
        self.grid: tuple[float, float, float] | None = None
        self._commanded: tuple[int, int] | None = None
        self._started = False
        self._board_capture = None
        self._board_grid: tuple[float, float, float] | None = None

    # ---- screen ---------------------------------------------------------

    def capture(self):
        return desktop.grab(self.rect)

    def locate(self, image=None, force: bool = False):  # noqa: D401
        """Find the board once and keep it: the window does not move mid-game.

        Refining the grid costs a couple of seconds, which is fine once and
        absurd 28 times.
        """
        if self.grid is not None and not force:
            return self.grid
        image = image if image is not None else self.capture()
        self.grid = find_grid(image)
        cx, cy, size = self.grid
        self.log(
            f"board found at ({cx:.0f}, {cy:.0f}) on monitor {self.options.monitor}, "
            f"cells {2 * size:.0f}px across"
        )
        return self.grid

    def screen_point(self, cell: int) -> tuple[int, int]:
        """Screen coordinates of a cell's centre."""
        if self.grid is None:
            raise AutoplayError("board position not established yet")
        x, y = cell_centres(*self.grid)[cell]
        return int(round(self.rect[0] + x)), int(round(self.rect[1] + y))

    def _open_board_capture(self) -> None:
        """Set up a reusable grab of just the board, in board-local coordinates.

        Everything after the board is found only ever looks at the board, and a
        cached capture of that region costs a few milliseconds against ImageGrab's
        110-odd for any region at all.
        """
        cx, cy, size = self.grid
        half_w, half_h = 10.0 * size, 9.3 * size
        width = self.rect[2] - self.rect[0]
        height = self.rect[3] - self.rect[1]
        left = max(0, int(cx - half_w))
        top = max(0, int(cy - half_h))
        right = min(width, int(cx + half_w))
        bottom = min(height, int(cy + half_h))
        self._board_capture = desktop.RegionCapture(
            (self.rect[0] + left, self.rect[1] + top,
             self.rect[0] + right, self.rect[1] + bottom)
        )
        self._board_grid = (cx - left, cy - top, size)

    def capture_board(self):
        if self._board_capture is None:
            self._open_board_capture()
        return self._board_capture.grab()

    def read(self, image=None, expect_fresh: bool = False) -> tuple[Board, dict]:
        if image is not None:
            self.locate(image)
            return read_board(image, grid=self.grid, expect_fresh=expect_fresh)
        self.locate()
        return read_board(
            self.capture_board(), grid=self._board_grid, expect_fresh=expect_fresh
        )

    def wait_until_still(self, timeout: float, settle: int = 3) -> bool:
        """Wait for an animation to run its course, by comparing successive grabs.

        Used while a new board deals in. Reading the board to find out whether
        it has finished costs ~370ms a go and gives nonsense mid-animation;
        comparing two pictures costs a few milliseconds and answers exactly the
        question being asked. A settled board measures a frame-to-frame
        difference of exactly zero -- the game animates nothing while idle --
        so "still" is unambiguous.

        It waits for motion to *begin* first. The deal does not start for about
        400ms after the click, and without that the board is trivially still the
        moment it is asked, and the wait returns before anything has happened.
        """
        import numpy as np

        self.locate()
        deadline = time.time() + timeout
        started = False
        previous, stable = None, 0

        while time.time() < deadline:
            self._check_abort()
            frame = np.asarray(self.capture_board().convert("L"), dtype=np.int16)[::4, ::4]
            if previous is not None:
                moving = np.abs(frame - previous).mean() >= STILL_TOLERANCE
                if moving:
                    started, stable = True, 0
                elif started:
                    stable += 1
                    if stable >= settle:
                        return True
                elif time.time() > deadline - timeout + START_GRACE:
                    return True  # nothing ever moved; nothing to wait for
            previous = frame
            time.sleep(self.options.poll)
        return False

    def close(self) -> None:
        if self._board_capture is not None:
            self._board_capture.close()
            self._board_capture = None

    # ---- safety ---------------------------------------------------------

    def _check_abort(self) -> None:
        if desktop.escape_pressed():
            raise Aborted("Escape pressed.")
        if self._commanded is not None:
            x, y = desktop.cursor_position()
            drift = abs(x - self._commanded[0]) + abs(y - self._commanded[1])
            if drift > 60:
                raise Aborted(
                    f"the mouse moved {drift}px on its own -- assuming you took over."
                )

    def _click_cell(self, cell: int, slowdown: float = 1.0) -> None:
        x, y = self.screen_point(cell)
        self._check_abort()
        desktop.click(x, y, settle=self.options.click_settle * slowdown)
        self._commanded = (x, y)

    # ---- playing --------------------------------------------------------

    def _cells_are_empty(self, cells) -> bool:
        self.locate()
        found = read_cells(self.capture_board(), self._board_grid, cells)
        return all(marble == 0 for marble in found.values())

    def _wait_cleared(self, cells) -> bool:
        """Watch until the pair vanishes, rather than sleeping a fixed guess.

        The clearing animation is much shorter than a delay safe enough to cover
        it, and polling costs almost nothing now that a board grab is a few
        milliseconds. This is both quicker on average and more patient when the
        game is briefly slow.
        """
        deadline = time.time() + self.options.clear_timeout
        while True:
            if self._cells_are_empty(cells):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(self.options.poll)

    def play_move(self, move: Move, number: int) -> None:
        cells = [move.a] + ([] if move.b is None else [move.b])
        for attempt in range(self.options.max_retries + 1):
            # Retrying at the speed that just failed tends to fail the same way.
            # Over 1792 live moves exactly one click went missing, and the
            # same-speed retry missed it too; each attempt now goes slower.
            slowdown = RETRY_SLOWDOWN**attempt
            gap = self.options.click_delay * slowdown

            self._click_cell(move.a, slowdown)
            if move.b is not None:
                time.sleep(gap)
                self._click_cell(move.b, slowdown)

            if not self.options.verify:
                time.sleep(gap)
                return
            if self._wait_cleared(cells):
                return
            if attempt < self.options.max_retries:
                self.log(f"  move {number} did not take; retrying more slowly")
                # A stuck half-selection would poison the retry, so clear it by
                # clicking the same marble again to toggle it off.
                self._click_cell(move.a, slowdown)
                time.sleep(gap)

        # Say which of the two it was, because the fixes differ: a pair that no
        # longer matches means the board was misread and the plan was wrong from
        # the start; a pair that still matches means the clicks are outrunning
        # the game.
        try:
            board, _ = self.read()
            kinds = [board.cells[c] for c in cells]
            if not all(kinds):
                why = "one of the two now reads empty, so the board and the plan disagree"
            elif move.b is not None and not matches(
                Marble(kinds[0]), Marble(kinds[1]), _tally(board)
            ):
                why = (
                    f"they read as {NAMES[Marble(kinds[0])]} and "
                    f"{NAMES[Marble(kinds[1])]}, which do not match -- misread"
                )
            else:
                why = "the pair still matches, so the click did not register"
        except Exception as exc:  # pragma: no cover - diagnosis is best effort
            why = f"could not re-read the board ({exc})"

        raise _MoveFailed(
            f"move {number} ({move.describe().strip()}) did not clear: {why}"
        )

    def focus_game_window(self) -> None:
        window = desktop.find_window(GAME_TITLE)
        if window:
            self.log(f"focusing '{window[1]}' at {window[2]}")
            desktop.focus(window[0])
            time.sleep(self.options.settle)
        else:
            self.log(f"no window titled '{GAME_TITLE}' found; using the screen as-is")

    def play(self) -> Progress:
        if self.options.focus_game:
            self.focus_game_window()
        board, report = self.read()
        return self.play_board(board, report)

    def play_board(self, board: Board, report: dict) -> Progress:
        """Solve and click out an already-read board."""
        options = self.options
        progress = Progress()

        if board.marble_count() == 0:
            raise AutoplayError(
                "the board on screen is already empty -- nothing to play. "
                "Click NEW GAME, or use --games to deal one automatically."
            )

        self.log(
            f"read {report['marbles']} marbles"
            + (f", {len(report['uncertain'])} uncertain" if report["uncertain"] else "")
        )
        if report["countMismatches"]:
            detail = ", ".join(
                f"{k} {v[0]}/{v[1]}" for k, v in report["countMismatches"].items()
            )
            self.log(f"note: counts differ from a fresh deal ({detail})")

        result = solve(board)
        if not result.solved:
            raise _Unsolvable(
                "no solution from this position"
                + (" -- this board is lost" if result.exhausted else " within the search budget")
            )
        progress.planned = len(result.moves)
        self.log(f"solved in {len(result.moves)} moves ({result.nodes} states)")

        if options.dry_run:
            for n, move in enumerate(result.moves, 1):
                targets = " -> ".join(
                    f"({x},{y})" for x, y in (self.screen_point(c) for c in
                                              [move.a] + ([] if move.b is None else [move.b]))
                )
                self.log(f"{n:3}. {move.describe()}   would click {targets}")
            progress.notes.append("dry run: nothing was clicked")
            return progress

        if options.countdown and not self._started:
            self.log(
                f"starting in {options.countdown:.0f}s -- "
                "Escape or move the mouse to stop"
            )
            time.sleep(options.countdown)
            self._commanded = None
        self._started = True

        plan = result.moves
        if options.max_moves is not None:
            plan = plan[: options.max_moves]
        for n, move in enumerate(plan, 1):
            self.play_move(move, n)
            progress.played = n
            self.log(f"{n:3}/{len(plan)}  {move.describe()}")

        if len(plan) < len(result.moves):
            progress.notes.append(
                f"stopped after {len(plan)} of {len(result.moves)} moves as asked"
            )
            self.log(progress.notes[-1])
        else:
            self.log("board cleared")
        return progress


def _panel_point(grid, offset) -> tuple[float, float]:
    cx, cy, size = grid
    return cx + offset[0] * size, cy + offset[1] * size


class GameLoop:
    """Plays boards until a target number of wins, dealing a new one each time."""

    def __init__(self, player: Autoplayer, log=print):
        self.player = player
        self.log = log

    # ---- the panel's other controls -------------------------------------

    def new_game_point(self) -> tuple[int, int]:
        grid = self.player.locate()
        x, y = _panel_point(grid, NEW_GAME_OFFSET)
        return (
            int(round(self.player.rect[0] + x)),
            int(round(self.player.rect[1] + y)),
        )

    def wins_box(self) -> tuple[int, int, int, int]:
        grid = self.player.locate()
        x, y = _panel_point(grid, WINS_BOX_OFFSET)
        left, top = self.player.rect[0], self.player.rect[1]
        half_x, half_y = WINS_BOX_HALF[0] * grid[2], WINS_BOX_HALF[1] * grid[2]
        return (
            int(left + x - half_x), int(top + y - half_y),
            int(left + x + half_x), int(top + y + half_y),
        )

    def wins_snapshot(self):
        try:
            return desktop.grab(self.wins_box())
        except Exception:  # pragma: no cover - box off screen
            return None

    @staticmethod
    def _changed(before, after) -> bool:
        """Did the game's own win counter tick over?

        Read as a picture rather than a number -- the digits do not need
        recognising, only to be seen changing, which is enough to confirm the
        game agreed the board was won.
        """
        if before is None or after is None or before.size != after.size:
            return False
        from PIL import ImageChops

        return ImageChops.difference(before, after).getbbox() is not None

    # ---- dealing ---------------------------------------------------------

    def deal(self):
        """Click NEW GAME and wait for a full board to appear."""
        options = self.player.options
        x, y = self.new_game_point()
        self.player._check_abort()
        desktop.click(x, y, settle=options.click_settle)
        self.player._commanded = (x, y)

        # Watch for the deal animation to finish before reading. Comparing two
        # grabs costs a few milliseconds where a full 91-cell read costs ~370,
        # so waiting for the picture to stop changing is far cheaper than
        # re-reading the board until it happens to make sense.
        self.player.wait_until_still(options.deal_timeout)

        deadline = time.time() + options.deal_timeout
        last = None
        while time.time() < deadline:
            # A just-dealt board holds a known multiset, which is a far stronger
            # constraint than judging each cell alone. See read_board's
            # expect_fresh: lead read as mors ended an 82-game run at one win.
            board, report = self.player.read(expect_fresh=True)
            # Judge the deal on the *unconstrained* read. Imposing the counts
            # always yields a standard deal, so it cannot tell a finished deal
            # from a board still animating in -- one that is still empty comes
            # back as a complete and entirely fictional board.
            settled = report["rawMarbles"] == 55
            plausible = len(report["corrected"]) <= MAX_CORRECTIONS
            if settled and plausible and not report["countMismatches"]:
                if report["corrected"]:
                    self.log(
                        f"  {len(report['corrected'])} cell(s) resolved by the "
                        "deal's known marble counts"
                    )
                return board, report
            last = (board, report)
            time.sleep(0.15)

        if last and last[1]["rawMarbles"] == 55:
            # A full board, but too many cells had to be overruled to make it a
            # standard deal. Say so rather than playing a board we may have
            # misread.
            detail = ", ".join(
                f"{k} {v[0]}/{v[1]}" for k, v in last[1]["rawMismatches"].items()
            ) or f"{len(last[1]['corrected'])} cells overruled"
            raise _BadDeal(f"new board read badly ({detail})")
        raise _BadDeal(
            f"no new board appeared within {self.player.options.deal_timeout:.0f}s "
            f"after clicking NEW GAME at ({x}, {y})"
        )

    def deal_retrying(self, attempts: int = 3):
        """Deal, and if the board will not read cleanly, deal another.

        One unreadable board should not end a long run -- there are plenty more
        where it came from, and the next is a fresh sample.
        """
        problem = None
        for attempt in range(attempts):
            try:
                return self.deal()
            except _BadDeal as exc:
                problem = exc
                self.log(f"  {exc}; dealing again ({attempt + 1}/{attempts})")
        raise AutoplayError(
            f"could not get a readable board in {attempts} deals: {problem}"
        )

    # ---- the loop --------------------------------------------------------

    def play_session(self, target: int | None = None) -> Session:
        target = target if target is not None else self.player.options.games
        session = Session(target=target)
        options = self.player.options

        if options.focus_game:
            self.player.focus_game_window()

        board, report = self.player.read()
        while not session.complete:
            if options.max_deals is not None and session.deals >= options.max_deals:
                session.notes.append(f"stopped after {session.deals} boards")
                break
            session.deals += 1

            self.log(
                f"--- board {session.deals}: {session.wins}/{target} wins so far ---"
            )

            # An already-cleared board solves in zero moves and would otherwise
            # be counted as a win -- which it is not, as the game's own counter
            # confirms by not moving. Deal instead.
            if board.marble_count() == 0:
                self.log("  board is already empty; dealing a fresh one")
                session.deals -= 1
                board, report = self.deal_retrying()
                continue

            before = self.wins_snapshot()
            try:
                self.player.play_board(board, report)
            except _Unsolvable as exc:
                session.unsolvable += 1
                self.log(f"  {exc}; dealing another")
                board, report = self.deal_retrying()
                continue
            except _MoveFailed as exc:
                # Abandoning one board is much better than ending a long run.
                session.abandoned += 1
                self.log(f"  {exc}; abandoning this board")
                session.notes.append(f"board {session.deals}: {exc}")
                board, report = self.deal_retrying()
                continue

            session.wins += 1
            after = self.wins_snapshot()
            confirmed = self._changed(before, after)
            self.log(
                f"  board cleared -- {session.wins}/{target}"
                + ("  (game's win counter ticked over)" if confirmed
                   else "  (could not confirm the game's counter)")
            )
            if not confirmed:
                session.notes.append(
                    f"board {session.deals} cleared but the WINS box did not change"
                )

            if not session.complete:
                board, report = self.deal_retrying()

        return session


def annotate(image, grid, moves, path) -> None:
    """Save the capture with the planned clicks drawn on it."""
    from PIL import ImageDraw

    canvas = image.copy()
    draw = ImageDraw.Draw(canvas)
    centres = cell_centres(*grid)
    radius = grid[2] * 0.55
    for n, move in enumerate(moves, 1):
        for cell in [move.a] + ([] if move.b is None else [move.b]):
            x, y = centres[cell]
            draw.ellipse(
                [x - radius, y - radius, x + radius, y + radius],
                outline=(255, 0, 255), width=3,
            )
            draw.text((x - radius + 4, y - radius + 2), str(n), fill=(255, 255, 0))
    canvas.save(path)


def describe_targets(player: Autoplayer, board: Board) -> list[str]:
    lines = []
    for i, marble in enumerate(board.cells):
        if marble:
            row, col = ROWCOL[i]
            x, y = player.screen_point(i)
            lines.append(f"r{row + 1}c{col + 1} {NAMES[Marble(marble)]:<11} -> ({x}, {y})")
    return lines
