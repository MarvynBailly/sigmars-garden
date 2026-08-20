"""A local web front end for playing and checking the solver.

Deliberately thin: the browser draws the board and forwards clicks, and every
rule decision -- what is free, what matches, whether a position is still
solvable -- is answered by the same `sigmar` code the solver uses.  Autoplay
replays a solution through the ordinary click endpoint, so a run that finishes
is a genuine end-to-end check of the solution, not a replay of the search.
"""

from __future__ import annotations

import json
import random
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .board import CELLS, N_CELLS, ROWCOL, parse_board
from .game import Game
from .generator import random_board, random_solvable_board
from .marbles import NAMES, STANDARD_COUNTS, Marble
from .solver import solve

WEB = Path(__file__).parent / "web"

# Static board geometry, sent once at page load.
GEOMETRY = [
    {"i": i, "q": q, "r": r, "row": ROWCOL[i][0], "col": ROWCOL[i][1]}
    for i, (q, r) in enumerate(CELLS)
]


class Session:
    """One game, plus whatever the solver last worked out about it."""

    def __init__(self):
        self.game = Game(random_solvable_board(random.Random()))
        self.hint: list[int] | None = None

    def new(self, mode: str, seed: int | None) -> None:
        rng = random.Random(seed)
        board = random_board(rng) if mode == "deal" else random_solvable_board(rng)
        label = "random deal" if mode == "deal" else "generated solvable"
        self.game = Game(board, label)
        self.hint = None

    def load(self, text: str, name: str) -> None:
        self.game = Game(parse_board(text), name)
        self.hint = None

    def state(self) -> dict:
        game = self.game
        counts = game.counts()
        selected = game.selected
        return {
            "name": game.name,
            "cells": [
                None if not m else NAMES[Marble(m)].lower() for m in game.board.cells
            ],
            "free": sorted(game.free()),
            "playable": sorted(game.playable()),
            "selected": selected,
            "partners": sorted(game.partners(selected)) if selected is not None else [],
            "remaining": game.board.marble_count(),
            "moveCount": len(game.moves),
            "canUndo": bool(game.moves),
            "won": game.won(),
            "hint": self.hint,
            "tally": [
                {
                    "kind": NAMES[m].lower(),
                    "left": counts[m],
                    "of": STANDARD_COUNTS[m],
                }
                for m in STANDARD_COUNTS
            ],
        }


SESSION = Session()


class Server(ThreadingHTTPServer):
    # Off, deliberately. On Windows SO_REUSEADDR lets a second process bind a
    # port that is already served, and which copy answers a given request is
    # then undefined -- a stale instance from an older run can keep serving and
    # reply "not found" to endpoints the current code has. Better to refuse.
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the console quiet
        pass

    # ---- plumbing -------------------------------------------------------

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(json.dumps(payload).encode("utf-8"), "application/json", status)

    MAX_BODY = 40 * 1024 * 1024  # a pasted 4K screenshot, base64-encoded

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > self.MAX_BODY:
            raise ValueError("That image is too large to send.")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---- routes ---------------------------------------------------------

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send((WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/geometry":
            self._json({"cells": GEOMETRY, "total": N_CELLS})
        elif self.path == "/api/state":
            self._json(SESSION.state())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            payload = self._handle(self.path, self._body())
        except ValueError as exc:  # bad board text, mostly
            self._json({"error": str(exc)}, 400)
            return
        except Exception as exc:
            # Anything unhandled would otherwise propagate out of the handler
            # and drop the connection with no reply at all, which reaches the
            # page as an opaque network failure rather than a message.
            traceback.print_exc()
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            return
        self._json(payload)

    def _handle(self, path: str, body: dict) -> dict:
        game = SESSION.game

        if path == "/api/new":
            SESSION.new(body.get("mode", "solvable"), body.get("seed"))
            return {"message": f"New board ({SESSION.game.name}).", **SESSION.state()}

        if path == "/api/load":
            SESSION.load(body["text"], body.get("name", "loaded board"))
            counts = SESSION.game.counts()
            odd = [
                f"{NAMES[m]} {counts[m]}/{n}"
                for m, n in STANDARD_COUNTS.items()
                if counts[m] != n
            ]
            note = (
                "Loaded. Warning: counts differ from a standard deal -- " + ", ".join(odd)
                if odd
                else "Loaded; marble counts match a standard deal."
            )
            return {"message": note, **SESSION.state()}

        if path == "/api/screenshot":
            from .vision import VisionError, read_data_url

            if not body.get("image"):
                return {"message": "No image in that paste.", "ok": False, **SESSION.state()}
            try:
                board, report = read_data_url(body["image"])
            except VisionError as exc:
                return {"message": str(exc), "ok": False, **SESSION.state()}

            SESSION.game = Game(board, "read from screenshot")
            SESSION.hint = None
            counts = ", ".join(
                f"{name} {got}/{want}"
                for name, (got, want) in report["countMismatches"].items()
            )
            if counts:
                note = (
                    f"Read {report['marbles']} marbles, but the counts are off: {counts}. "
                    "Check the highlighted cells and fix the text below."
                )
            elif report["uncertain"]:
                note = (
                    f"Read {report['marbles']} marbles; counts match a standard deal. "
                    f"{len(report['uncertain'])} cell(s) were a close call -- worth a look."
                )
            else:
                note = (
                    f"Read {report['marbles']} marbles; counts match a standard deal "
                    "and every cell was a clear match."
                )
            return {
                "message": note,
                "ok": True,
                "report": report,
                "uncertain": report["uncertain"],
                "boardText": report["text"],
                **SESSION.state(),
            }

        if path == "/api/click":
            SESSION.hint = None
            result = game.click(int(body["cell"]))
            return {"message": result.get("message", ""), "ok": result["ok"], **SESSION.state()}

        if path == "/api/undo":
            ok = game.undo()
            SESSION.hint = None
            return {"message": "Undone." if ok else "Nothing to undo.", **SESSION.state()}

        if path == "/api/reset":
            game.reset()
            SESSION.hint = None
            return {"message": "Board reset.", **SESSION.state()}

        if path in ("/api/hint", "/api/solve", "/api/check"):
            result = solve(game.board, max_nodes=int(body.get("maxNodes", 2_000_000)))
            if not result.solved:
                SESSION.hint = None
                message = (
                    f"No solution from here -- this position is lost. "
                    f"({result.nodes} states searched)"
                    if result.exhausted
                    else f"No solution found within {result.nodes} states; "
                    "the position may still be winnable."
                )
                return {"message": message, "solvable": False, **SESSION.state()}

            moves = [
                {
                    "a": m.a,
                    "b": m.b,
                    "kindA": NAMES[m.kind_a].lower(),
                    "kindB": None if m.kind_b is None else NAMES[m.kind_b].lower(),
                    "text": m.describe(),
                }
                for m in result.moves
            ]
            if path == "/api/hint":
                first = result.moves[0]
                SESSION.hint = [first.a] if first.b is None else [first.a, first.b]
                return {
                    "message": f"Hint: {first.describe()}",
                    "solvable": True,
                    **SESSION.state(),
                }
            SESSION.hint = None
            state = SESSION.state()
            message = (
                f"Solvable: {len(moves)} moves, {result.nodes} states searched."
                if path == "/api/check"
                else f"Solution found: {len(moves)} moves."
            )
            return {"message": message, "solvable": True, "solution": moves, **state}

        return {"error": "not found"}


def serve(port: int = 8765, open_browser: bool = True, board=None) -> None:
    if board is not None:
        SESSION.game = Game(board, "board from file")
    try:
        server = Server(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise SystemExit(
            f"Cannot serve on port {port}: {exc}\n"
            "Another copy is probably still running -- stop it, or use --port."
        ) from exc
    url = f"http://127.0.0.1:{port}/"
    print(f"Sigmar's Garden -- serving at {url}")
    print("Press Ctrl-C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
