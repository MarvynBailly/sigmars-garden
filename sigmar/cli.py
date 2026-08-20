"""Command line interface: solve a board file, or generate one."""

from __future__ import annotations

import argparse
import random
import sys
import time

from .board import ROWCOL, load_board
from .generator import random_board, random_solvable_board
from .marbles import CHARS, NAMES, STANDARD_COUNTS, Marble
from .solver import replay, solve, verify
from .vision import VisionError


def _check_counts(board, stream) -> None:
    actual = board.counts()
    problems = []
    for marble, expected in STANDARD_COUNTS.items():
        got = actual.get(marble, 0)
        if got != expected:
            problems.append(f"  {NAMES[marble]}: {got} (standard board has {expected})")
    extra = set(actual) - set(STANDARD_COUNTS)
    for marble in extra:
        problems.append(f"  {NAMES[marble]}: unexpected marble")
    if problems:
        print(
            "warning: marble counts differ from a standard deal -- "
            "check the transcription:",
            file=stream,
        )
        print("\n".join(problems), file=stream)
        print(file=stream)


def _print_solution(board, moves, show_steps: bool) -> None:
    states = list(replay(board, moves)) if show_steps else []
    for n, move in enumerate(moves, 1):
        if show_steps:
            marks = {move.a: "A"}
            if move.b is not None:
                marks[move.b] = "B"
            print(states[n - 1].render(marks))
        print(f"{n:3}. {move.describe()}")
        if show_steps:
            print()


def cmd_solve(args) -> int:
    board = load_board(args.board)
    _check_counts(board, sys.stderr)

    print(board.render())
    print(f"\n{board.marble_count()} marbles on the board.\n")

    start = time.perf_counter()
    result = solve(board, max_nodes=args.max_nodes, seed=args.seed)
    elapsed = time.perf_counter() - start

    if not result.solved:
        if result.exhausted:
            print(f"No solution exists. ({result.nodes} states searched, {elapsed:.2f}s)")
        else:
            print(
                f"No solution found within {result.nodes} states ({elapsed:.2f}s). "
                "Raise --max-nodes, or try a different --seed."
            )
        return 1

    verify(board, result.moves)
    print(f"Solved in {len(result.moves)} moves "
          f"({result.nodes} states, {elapsed:.2f}s):\n")
    _print_solution(board, result.moves, args.steps)
    print("\nRow/col are 1-based: row 1 is the top row of 6, "
          "col 1 the leftmost cell of that row.")
    return 0


def cmd_gen(args) -> int:
    rng = random.Random(args.seed)
    board = random_board(rng) if args.any else random_solvable_board(rng)
    text = board.render()
    if args.out:
        header = f"# Sigmar's Garden board (seed {args.seed})\n"
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(header + text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_check(args) -> int:
    """Parse a board and report which marbles are currently free."""
    board = load_board(args.board)
    _check_counts(board, sys.stderr)
    free = board.free_cells()
    # Free marbles show as uppercase, the way the game highlights them.
    marks = {i: CHARS[Marble(board[i])].upper() for i in free}
    print(board.render(marks))
    print()
    print(f"{board.marble_count()} marbles, {len(free)} free:")
    for i in sorted(free, key=lambda i: ROWCOL[i]):
        row, col = ROWCOL[i]
        print(f"  {NAMES[Marble(board[i])]:<11} (r{row + 1},c{col + 1})")
    return 0


def cmd_read(args) -> int:
    """Read a board out of a screenshot image."""
    from PIL import Image

    from .vision import read_board

    board, report = read_board(Image.open(args.image))
    grid = report["grid"]
    print(f"# read from {args.image}", file=sys.stderr)
    print(f"# board centre ({grid['cx']}, {grid['cy']}), cell size {grid['size']}px",
          file=sys.stderr)
    if report["uncertain"]:
        cells = ", ".join(
            f"r{ROWCOL[i][0] + 1}c{ROWCOL[i][1] + 1}" for i in report["uncertain"]
        )
        print(f"# close calls, worth checking: {cells}", file=sys.stderr)
    print(board.render())
    _check_counts(board, sys.stderr)
    if args.solve:
        result = solve(board)
        if not result.solved:
            print("\nNo solution from this board.", file=sys.stderr)
            return 1
        print(f"\nSolved in {len(result.moves)} moves:", file=sys.stderr)
        for n, move in enumerate(result.moves, 1):
            print(f"{n:3}. {move.describe()}", file=sys.stderr)
    return 0


def cmd_autoplay(args) -> int:
    """Solve the board on screen and click it out."""
    from .autoplay import (
        Aborted, Autoplayer, AutoplayError, GameLoop, Options, annotate,
    )

    options = Options(
        monitor=args.monitor,
        dry_run=args.dry_run,
        countdown=args.countdown,
        clear_timeout=args.delay,
        verify=not args.no_verify,
        focus_game=not args.no_focus,
        max_moves=args.moves,
        games=args.games,
        max_deals=args.max_deals,
    )
    player = Autoplayer(options)
    try:
        if args.shot:
            image = player.capture()
            player.locate(image)
            board, _ = player.read(image)
            result = solve(board)
            if result.solved:
                annotate(image, player.grid, result.moves, args.shot)
                print(f"wrote {args.shot}", file=sys.stderr)
        if args.games > 1:
            session = GameLoop(player).play_session()
            print(
                f"{session.wins}/{session.target} wins over {session.deals} boards"
                + (f", {session.unsolvable} unsolvable" if session.unsolvable else "")
                + (f", {session.abandoned} abandoned" if session.abandoned else ""),
                file=sys.stderr,
            )
            for note in session.notes:
                print(f"note: {note}", file=sys.stderr)
            return 0 if session.complete else 1
        progress = player.play()
    except Aborted as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    except (AutoplayError, VisionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if progress.notes:
        print("; ".join(progress.notes), file=sys.stderr)
    return 0


def cmd_play(args) -> int:
    """Serve the browser front end, backed by this package's rules."""
    from .server import serve

    board = load_board(args.board) if args.board else None
    if board is not None:
        _check_counts(board, sys.stderr)
    serve(port=args.port, open_browser=not args.no_browser, board=board)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sigmar", description="Solve a Sigmar's Garden board."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_solve = sub.add_parser("solve", help="solve a board file")
    p_solve.add_argument("board", help="path to a board file")
    p_solve.add_argument(
        "--steps", action="store_true", help="print the board before every move"
    )
    p_solve.add_argument("--max-nodes", type=int, default=2_000_000)
    p_solve.add_argument(
        "--seed", type=int, default=0, help="seed for restart tie-breaking"
    )
    p_solve.set_defaults(func=cmd_solve)

    p_gen = sub.add_parser("gen", help="generate a random board")
    p_gen.add_argument("--seed", type=int, default=None)
    p_gen.add_argument("-o", "--out", help="write to this file instead of stdout")
    p_gen.add_argument(
        "--any",
        action="store_true",
        help="deal at random (may be unsolvable) instead of building a solvable board",
    )
    p_gen.set_defaults(func=cmd_gen)

    p_check = sub.add_parser("check", help="parse a board and list the free marbles")
    p_check.add_argument("board")
    p_check.set_defaults(func=cmd_check)

    p_read = sub.add_parser("read", help="read a board out of a screenshot")
    p_read.add_argument("image", help="PNG/JPG screenshot of the game")
    p_read.add_argument("--solve", action="store_true", help="also solve what it read")
    p_read.set_defaults(func=cmd_read)

    p_auto = sub.add_parser(
        "autoplay", help="solve the board on screen and click it out"
    )
    p_auto.add_argument("--monitor", type=int, default=1, help="which monitor (1-based)")
    p_auto.add_argument(
        "--dry-run", action="store_true",
        help="print the clicks it would make, without moving the mouse",
    )
    p_auto.add_argument("--countdown", type=float, default=3.0)
    p_auto.add_argument(
        "--delay", type=float, default=1.2,
        help="longest to wait for a pair to disappear, in seconds"
    )
    p_auto.add_argument(
        "--no-verify", action="store_true",
        help="do not re-check that each pair actually cleared (not recommended)",
    )
    p_auto.add_argument(
        "--no-focus", action="store_true", help="do not bring the game window forward"
    )
    p_auto.add_argument(
        "--moves", type=int, default=None,
        help="stop after this many moves instead of clearing the board",
    )
    p_auto.add_argument(
        "--games", type=int, default=1,
        help="keep playing until this many boards have been won, "
             "clicking NEW GAME between them",
    )
    p_auto.add_argument(
        "--max-deals", type=int, default=None,
        help="give up after this many boards however they went (default: unlimited)",
    )
    p_auto.add_argument("--shot", help="save an annotated screenshot of the plan here")
    p_auto.set_defaults(func=cmd_autoplay)

    p_play = sub.add_parser("play", help="play in a browser, backed by the solver")
    p_play.add_argument("board", nargs="?", help="board file to start from")
    p_play.add_argument("--port", type=int, default=8765)
    p_play.add_argument("--no-browser", action="store_true")
    p_play.set_defaults(func=cmd_play)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
