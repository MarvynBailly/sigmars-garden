"""Tests for the autoplay driver.

Every one of these stubs out the screen and the mouse. Nothing here moves a real
pointer or clicks anything -- the point is to check the decisions the driver
makes (where to click, when to stop, when to give up), not the OS calls.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sigmar import autoplay  # noqa: E402
from sigmar.autoplay import Aborted, Autoplayer, AutoplayError, Options  # noqa: E402
from sigmar.board import Board, index_of  # noqa: E402
from sigmar.marbles import Marble  # noqa: E402
from sigmar.solver import Move  # noqa: E402
from sigmar.vision import cell_centres  # noqa: E402

MONITOR = (0, 0, 3840, 2160)
GRID = (1354.0, 844.0, 38.0)


@pytest.fixture
def player(monkeypatch):
    """An Autoplayer whose screen and mouse are fakes."""
    monkeypatch.setattr(autoplay.desktop, "require", lambda: None)
    monkeypatch.setattr(autoplay.desktop, "monitors", lambda: [MONITOR])
    monkeypatch.setattr(autoplay.desktop, "escape_pressed", lambda: False)

    clicks = []

    def fake_click(x, y, **kw):
        clicks.append((x, y))

    # The real pointer ends up where it was last sent, and the drift check
    # compares against exactly that, so the fake has to move too.
    monkeypatch.setattr(autoplay.desktop, "click", fake_click)
    monkeypatch.setattr(
        autoplay.desktop, "cursor_position", lambda: clicks[-1] if clicks else (0, 0)
    )
    p = Autoplayer(
        Options(countdown=0, click_delay=0, click_settle=0, poll=0, clear_timeout=0.05),
        log=lambda *a: None,
    )
    p.grid = GRID
    p.clicks = clicks
    return p


def test_screen_point_offsets_by_the_monitor_origin(player):
    cell = index_of(5, 5)
    x, y = cell_centres(*GRID)[cell]
    assert player.screen_point(cell) == (round(x), round(y))

    player.rect = (5760, 100, 8640, 1720)
    assert player.screen_point(cell) == (round(x) + 5760, round(y) + 100)


def test_screen_point_needs_a_located_board(player):
    player.grid = None
    with pytest.raises(AutoplayError, match="not established"):
        player.screen_point(0)


def test_rejects_a_monitor_that_does_not_exist(monkeypatch):
    monkeypatch.setattr(autoplay.desktop, "require", lambda: None)
    monkeypatch.setattr(autoplay.desktop, "monitors", lambda: [MONITOR])
    with pytest.raises(AutoplayError, match="No monitor 3"):
        Autoplayer(Options(monitor=3))


def _pair_move():
    a, b = index_of(0, 0), index_of(10, 5)
    return Move(a, b, Marble.FIRE, Marble.FIRE)


def test_a_pair_is_two_clicks_on_the_right_cells(player, monkeypatch):
    monkeypatch.setattr(player, "_cells_are_empty", lambda cells: True)
    move = _pair_move()
    player.play_move(move, 1)
    assert player.clicks == [player.screen_point(move.a), player.screen_point(move.b)]


def test_gold_is_a_single_click(player, monkeypatch):
    monkeypatch.setattr(player, "_cells_are_empty", lambda cells: True)
    cell = index_of(5, 5)
    player.play_move(Move(cell, None, Marble.GOLD, None), 1)
    assert player.clicks == [player.screen_point(cell)]


def test_gives_up_rather_than_clicking_on_blind(player, monkeypatch):
    """If a pair does not actually clear, every later click is on stale data."""
    monkeypatch.setattr(player, "_cells_are_empty", lambda cells: False)
    with pytest.raises(AutoplayError, match="did not clear"):
        player.play_move(_pair_move(), 4)
    # One attempt plus the configured retry, and the retry's deselect click.
    assert len(player.clicks) > 2


def test_a_move_that_takes_on_the_retry_is_accepted(player, monkeypatch):
    outcomes = iter([False, True])
    monkeypatch.setattr(player, "_cells_are_empty", lambda cells: next(outcomes))
    player.play_move(_pair_move(), 1)  # must not raise


def test_each_retry_clicks_more_slowly_than_the_last(player, monkeypatch):
    """Retrying at the speed that just failed tends to fail the same way."""
    settles = []
    monkeypatch.setattr(
        autoplay.desktop, "click",
        lambda x, y, settle=0.0, **kw: settles.append(settle) or player.clicks.append((x, y)),
    )
    monkeypatch.setattr(player, "_cells_are_empty", lambda cells: False)
    player.options.click_settle = 0.01
    with pytest.raises(AutoplayError):
        player.play_move(_pair_move(), 1)
    assert len(set(settles)) >= 3, f"expected escalating settles, got {sorted(set(settles))}"
    assert max(settles) > min(settles) * 4


def test_escape_aborts_before_clicking(player, monkeypatch):
    monkeypatch.setattr(autoplay.desktop, "escape_pressed", lambda: True)
    with pytest.raises(Aborted, match="Escape"):
        player.play_move(_pair_move(), 1)
    assert player.clicks == []


def test_taking_the_mouse_back_aborts(player, monkeypatch):
    """The pointer sitting somewhere the driver did not put it means a human."""
    player._commanded = (100, 100)
    monkeypatch.setattr(autoplay.desktop, "cursor_position", lambda: (900, 900))
    with pytest.raises(Aborted, match="took over"):
        player.play_move(_pair_move(), 1)
    assert player.clicks == []


def test_small_pointer_drift_is_tolerated(player, monkeypatch):
    """A few pixels is the pointer settling, not a person reaching for it."""
    player._commanded = (100, 100)
    monkeypatch.setattr(autoplay.desktop, "cursor_position", lambda: (110, 105))
    player._check_abort()  # must not raise


def test_dry_run_clicks_nothing(player, monkeypatch):
    board = Board.empty()
    board.cells[index_of(0, 0)] = Marble.FIRE
    board.cells[index_of(10, 5)] = Marble.FIRE
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (board, _report(board)))
    player.options.dry_run = True
    player.options.focus_game = False

    progress = player.play()
    assert player.clicks == []
    assert progress.played == 0
    assert progress.planned == 1


def test_refuses_to_play_an_unsolvable_board(player, monkeypatch):
    board = Board.empty()
    board.cells[index_of(0, 0)] = Marble.FIRE
    board.cells[index_of(10, 5)] = Marble.WATER
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (board, _report(board)))
    player.options.focus_game = False
    with pytest.raises(AutoplayError, match="lost"):
        player.play()
    assert player.clicks == []


# ---- playing several games -----------------------------------------------


def _four_marble_board():
    board = Board.empty()
    for cell in (index_of(0, 0), index_of(10, 5), index_of(0, 5), index_of(10, 0)):
        board.cells[cell] = Marble.FIRE
    return board


def _report(board):
    return {
        "marbles": board.marble_count(),
        "uncertain": [],
        "countMismatches": {},
        "corrected": [],
        "rawMarbles": board.marble_count(),
        "rawMismatches": {},
    }


@pytest.fixture
def loop(player, monkeypatch):
    monkeypatch.setattr(player, "_cells_are_empty", lambda cells: True)
    player.options.focus_game = False
    made = autoplay.GameLoop(player, log=lambda *a: None)
    monkeypatch.setattr(made, "wins_snapshot", lambda: None)
    # Nothing here should touch the real screen.
    monkeypatch.setattr(player, "wait_until_still", lambda *a, **k: True)
    return made


def test_new_game_button_sits_off_the_board_centre(player):
    made = autoplay.GameLoop(player, log=lambda *a: None)
    cx, cy, size = GRID
    x, y = made.new_game_point()
    # Down and to the left of the board, by the measured panel offsets.
    assert x == round(cx + autoplay.NEW_GAME_OFFSET[0] * size)
    assert y == round(cy + autoplay.NEW_GAME_OFFSET[1] * size)
    assert x < cx and y > cy


def test_plays_until_the_target_is_reached(loop, player, monkeypatch):
    boards = iter([_four_marble_board() for _ in range(3)])
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (b := next(boards), _report(b)))
    deals = []
    monkeypatch.setattr(loop, "deal", lambda: deals.append(1) or
                        (b := _four_marble_board(), _report(b))[0:2])

    session = loop.play_session(3)
    assert session.wins == 3 and session.complete
    assert len(deals) == 2, "a new board is dealt between games, not after the last"


def test_an_unsolvable_board_is_redealt_rather_than_fatal(loop, player, monkeypatch):
    dead = Board.empty()
    dead.cells[index_of(0, 0)] = Marble.FIRE
    dead.cells[index_of(10, 5)] = Marble.WATER

    boards = [dead, _four_marble_board()]
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (boards[0], _report(boards[0])))

    def fake_deal():
        boards.pop(0)
        return boards[0], _report(boards[0])

    monkeypatch.setattr(loop, "deal", fake_deal)

    session = loop.play_session(1)
    assert session.wins == 1
    assert session.unsolvable == 1
    assert session.deals == 2


def test_an_already_empty_board_is_not_counted_as_a_win(loop, player, monkeypatch):
    """A cleared board solves in zero moves, which is not a win.

    Left over from a previous run, it would otherwise be scored -- and the
    game's own WINS counter would not move, because nothing was won.
    """
    boards = [Board.empty(), _four_marble_board()]
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (boards[0], _report(boards[0])))

    def fake_deal():
        boards.pop(0)
        return boards[0], _report(boards[0])

    monkeypatch.setattr(loop, "deal", fake_deal)

    session = loop.play_session(1)
    assert session.wins == 1
    assert session.deals == 1, "the empty board should not count as a board played"


def test_a_board_that_goes_wrong_is_abandoned_not_fatal(loop, player, monkeypatch):
    """A move that will not go through should cost one board, not the run.

    A misread board can produce a plan whose first pair does not really match,
    and no amount of clicking will clear it. That ended an 82-game run at one
    win; now it deals another and carries on.
    """
    boards = [_four_marble_board(), _four_marble_board()]
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (boards[0], _report(boards[0])))

    def fake_deal():
        boards.pop(0)
        return boards[0], _report(boards[0])

    monkeypatch.setattr(loop, "deal", fake_deal)
    monkeypatch.setattr(loop, "deal_retrying", fake_deal)

    calls = []
    real_play = player.play_board

    def flaky(board, report):
        calls.append(1)
        if len(calls) == 1:
            raise autoplay._MoveFailed("move 1 did not clear: it was misread")
        return real_play(board, report)

    monkeypatch.setattr(player, "play_board", flaky)

    session = loop.play_session(1)
    assert session.wins == 1
    assert session.abandoned == 1
    assert any("did not clear" in note for note in session.notes)


def test_max_deals_stops_a_run_that_keeps_losing(loop, player, monkeypatch):
    dead = Board.empty()
    dead.cells[index_of(0, 0)] = Marble.FIRE
    dead.cells[index_of(10, 5)] = Marble.WATER
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (dead, _report(dead)))
    monkeypatch.setattr(loop, "deal", lambda: (dead, _report(dead)))
    player.options.max_deals = 4

    session = loop.play_session(2)
    assert not session.complete
    assert session.deals == 4 and session.unsolvable == 4
    assert any("stopped after" in n for n in session.notes)


def test_deal_waits_for_a_full_board(loop, player, monkeypatch):
    """Clicking NEW GAME starts an animation; a half-dealt board is not ready."""
    import random

    from sigmar.generator import random_solvable_board

    partial = Board.empty()
    partial.cells[index_of(0, 0)] = Marble.FIRE
    full = random_solvable_board(random.Random(1))

    states = [partial, partial, full]

    def fake_read(image=None, **kw):
        board = states.pop(0) if len(states) > 1 else states[0]
        return board, _report(board)

    monkeypatch.setattr(player, "read", fake_read)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    board, _ = loop.deal()
    assert board.marble_count() == 55
    assert player.clicks == [loop.new_game_point()]


def test_a_board_that_will_not_read_is_redealt(loop, monkeypatch):
    """One unreadable deal should not end a long run."""
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise autoplay._BadDeal("new board read badly (Lead 0/1)")
        return _four_marble_board(), _report(_four_marble_board())

    monkeypatch.setattr(loop, "deal", flaky)
    board, _ = loop.deal_retrying()
    assert len(attempts) == 3 and board.marble_count() == 4


def test_gives_up_after_enough_unreadable_deals(loop, monkeypatch):
    monkeypatch.setattr(loop, "deal", lambda: (_ for _ in ()).throw(
        autoplay._BadDeal("new board read badly (Lead 0/1)")))
    with pytest.raises(AutoplayError, match="could not get a readable board"):
        loop.deal_retrying(attempts=2)


def test_deal_gives_up_if_no_board_appears(loop, player, monkeypatch):
    empty = Board.empty()
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (empty, _report(empty)))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    player.options.deal_timeout = 0.05
    with pytest.raises(autoplay._BadDeal, match="no new board appeared"):
        loop.deal()


def test_stops_after_the_requested_number_of_moves(player, monkeypatch):
    board = Board.empty()
    for cell in (index_of(0, 0), index_of(10, 5), index_of(0, 5), index_of(10, 0)):
        board.cells[cell] = Marble.FIRE
    monkeypatch.setattr(player, "read", lambda image=None, **kw: (board, {
        "marbles": 4, "uncertain": [], "countMismatches": {}}))
    monkeypatch.setattr(player, "_cells_are_empty", lambda cells: True)
    player.options.focus_game = False
    player.options.max_moves = 1

    progress = player.play()
    assert progress.played == 1 and progress.planned == 2
    assert len(player.clicks) == 2
