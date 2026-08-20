"""Tests: geometry, rules, and end-to-end solving."""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sigmar.board import (
    N_CELLS,
    NEIGHBOURS,
    OFF_BOARD,
    ROW_LENGTHS,
    ROWCOL,
    Board,
    index_of,
    load_board,
    parse_board,
)
from sigmar.game import Game
from sigmar.generator import random_board, random_solvable_board
from sigmar.marbles import STANDARD_COUNTS, Marble, matches
from sigmar.solver import Solver, solve, verify

EMPTY_TEXT = "\n".join(" ".join("." for _ in range(n)) for n in ROW_LENGTHS)


# ---- geometry ------------------------------------------------------------


def test_board_has_91_cells_in_hexagonal_rows():
    assert N_CELLS == 91
    assert ROW_LENGTHS == (6, 7, 8, 9, 10, 11, 10, 9, 8, 7, 6)
    assert sum(ROW_LENGTHS) == N_CELLS


def test_neighbours_are_symmetric_and_cyclic():
    for i, nbrs in enumerate(NEIGHBOURS):
        assert len(nbrs) == 6
        for j, n in enumerate(nbrs):
            if n == OFF_BOARD:
                continue
            # Adjacency is mutual, and the reverse direction sits opposite.
            assert NEIGHBOURS[n][(j + 3) % 6] == i


def test_centre_cell_has_six_neighbours_corners_have_three():
    centre = index_of(5, 5)
    assert all(n != OFF_BOARD for n in NEIGHBOURS[centre])
    assert sum(n != OFF_BOARD for n in NEIGHBOURS[index_of(0, 0)]) == 3


# ---- the freedom rule ----------------------------------------------------


def test_lone_marble_is_free():
    board = Board.empty()
    board.cells[index_of(5, 5)] = Marble.SALT
    assert board.is_free(index_of(5, 5))


def test_marble_needs_three_contiguous_empty_neighbours():
    centre = index_of(5, 5)
    board = Board.empty()
    board.cells[centre] = Marble.SALT
    # Three occupied neighbours placed alternately leave only single gaps.
    for j in (0, 2, 4):
        board.cells[NEIGHBOURS[centre][j]] = Marble.SALT
    assert not board.is_free(centre)

    board = Board.empty()
    board.cells[centre] = Marble.SALT
    # The same three, now bunched together, leave a run of three empties.
    for j in (0, 1, 2):
        board.cells[NEIGHBOURS[centre][j]] = Marble.SALT
    assert board.is_free(centre)


def test_empty_run_wraps_around_the_ring():
    centre = index_of(5, 5)
    board = Board.empty()
    board.cells[centre] = Marble.SALT
    for j in (1, 2, 3):
        board.cells[NEIGHBOURS[centre][j]] = Marble.SALT
    # Free directions are 4, 5, 0 -- contiguous only if the ring wraps.
    assert board.is_free(centre)


def test_off_board_spaces_count_as_empty():
    corner = index_of(0, 0)
    board = Board.empty()
    board.cells[corner] = Marble.SALT
    for n in NEIGHBOURS[corner]:
        if n != OFF_BOARD:
            board.cells[n] = Marble.SALT
    # Every on-board neighbour is occupied, but the three off-board sides are
    # contiguous, so the corner marble is still free.
    assert board.is_free(corner)


# ---- matching rules ------------------------------------------------------


def _counts(**kw):
    tally = [0] * (int(Marble.GOLD) + 1)
    for name, n in kw.items():
        tally[Marble[name.upper()]] = n
    return tally


def test_cardinals_match_their_own_kind_and_salt():
    c = _counts(fire=2, water=1, salt=1)
    assert matches(Marble.FIRE, Marble.FIRE, c)
    assert matches(Marble.FIRE, Marble.SALT, c)
    assert not matches(Marble.FIRE, Marble.WATER, c)


def test_salt_matches_itself():
    assert matches(Marble.SALT, Marble.SALT, _counts(salt=2))


def test_vitae_matches_only_mors():
    c = _counts(vitae=1, mors=1, salt=1)
    assert matches(Marble.VITAE, Marble.MORS, c)
    assert not matches(Marble.VITAE, Marble.VITAE, c)
    assert not matches(Marble.VITAE, Marble.SALT, c)


def test_metals_need_quicksilver_in_transmutation_order():
    c = _counts(lead=1, tin=1, quicksilver=2)
    assert matches(Marble.LEAD, Marble.QUICKSILVER, c)
    assert not matches(Marble.TIN, Marble.QUICKSILVER, c)  # lead is still there
    c = _counts(tin=1, quicksilver=1)
    assert matches(Marble.TIN, Marble.QUICKSILVER, c)


def test_gold_never_pairs_with_quicksilver():
    assert not matches(Marble.GOLD, Marble.QUICKSILVER, _counts(gold=1, quicksilver=1))


def test_gold_clears_alone_once_it_is_the_last_metal():
    from sigmar.marbles import is_solo

    assert is_solo(Marble.GOLD, _counts(gold=1))
    assert not is_solo(Marble.GOLD, _counts(gold=1, silver=1))
    assert not is_solo(Marble.SILVER, _counts(silver=1))


# ---- parsing -------------------------------------------------------------


def test_parse_round_trips():
    board = random_solvable_board(random.Random(7))
    assert parse_board(board.render()).cells == board.cells


def test_parse_ignores_comments_and_blank_lines():
    text = "# a comment\n\n" + EMPTY_TEXT + "\n\n# trailing\n"
    assert parse_board(text).marble_count() == 0


def test_parse_rejects_wrong_row_length():
    bad = EMPTY_TEXT.replace(". . . . . .", ". . . . .", 1)
    with pytest.raises(ValueError, match="needs 6 cells"):
        parse_board(bad)


def test_parse_rejects_unknown_marble():
    with pytest.raises(ValueError, match="unknown marble"):
        parse_board(EMPTY_TEXT.replace(".", "z", 1))


# ---- solving -------------------------------------------------------------


def test_empty_board_is_already_solved():
    assert solve(Board.empty()).moves == []


def test_solves_a_trivial_pair():
    board = Board.empty()
    board.cells[index_of(0, 0)] = Marble.FIRE
    board.cells[index_of(10, 5)] = Marble.FIRE
    result = solve(board)
    assert result.solved and len(result.moves) == 1


def test_unmatchable_leftover_is_reported_unsolvable():
    board = Board.empty()
    board.cells[index_of(0, 0)] = Marble.FIRE
    board.cells[index_of(10, 5)] = Marble.WATER
    result = solve(board)
    assert not result.solved and result.exhausted


def test_metal_order_is_respected():
    # Tin sits free next to quicksilver, but lead must go first.
    board = Board.empty()
    board.cells[index_of(0, 0)] = Marble.TIN
    board.cells[index_of(0, 1)] = Marble.QUICKSILVER
    board.cells[index_of(10, 0)] = Marble.LEAD
    board.cells[index_of(10, 5)] = Marble.QUICKSILVER
    result = solve(board)
    assert result.solved
    assert result.moves[0].kind_a == Marble.LEAD


def test_locked_marbles_make_a_board_unsolvable():
    # A ring of six salts around a centre marble that can never be freed:
    # the ring itself is stuck because each ring marble is hemmed in.
    board = Board.empty()
    centre = index_of(5, 5)
    board.cells[centre] = Marble.FIRE
    for n in NEIGHBOURS[centre]:
        board.cells[n] = Marble.FIRE
    for n in NEIGHBOURS[centre]:
        for nn in NEIGHBOURS[n]:
            if nn != OFF_BOARD and not board.cells[nn]:
                board.cells[nn] = Marble.WATER
    result = solve(board, max_nodes=200_000)
    # The centre fire has six occupied neighbours and nothing can reach it.
    assert not result.solved


def test_greedy_trap_needs_search():
    # Two fires and two salts free. Pairing fire+salt twice leaves salt-less
    # fires; the solver has to see that and pair like with like.
    board = Board.empty()
    spots = [index_of(0, 0), index_of(0, 5), index_of(10, 0), index_of(10, 5),
             index_of(5, 0), index_of(5, 10)]
    for spot, marble in zip(spots, [Marble.FIRE, Marble.FIRE, Marble.FIRE,
                                    Marble.FIRE, Marble.SALT, Marble.SALT]):
        board.cells[spot] = marble
    result = solve(board)
    assert result.solved
    verify(board, result.moves)


@pytest.mark.parametrize("seed", range(25))
def test_generated_boards_are_solvable_and_solutions_verify(seed):
    board = random_solvable_board(random.Random(seed))
    assert board.counts() == STANDARD_COUNTS
    assert board.marble_count() == 55

    result = solve(board, max_nodes=500_000)
    assert result.solved, f"seed {seed} unsolved after {result.nodes} states"
    verify(board, result.moves)
    assert len(result.moves) == 28  # 27 pairs + gold on its own


def test_verify_rejects_a_locked_move():
    board = random_solvable_board(random.Random(3))
    moves = solve(board).moves
    with pytest.raises(ValueError):
        verify(board, moves[1:] + moves[:1])


def test_real_board_from_screenshot():
    """The board transcribed from the game, as an end-to-end regression.

    Its 9 free marbles are exactly the ones the game draws highlighted, which
    checks the freedom rule against the real thing.  (The copper is free too
    but drawn faded, because three metals below it are still on the board.)
    """
    board = load_board(Path(__file__).parents[1] / "boards" / "screenshot.txt")
    assert board.counts() == STANDARD_COUNTS

    free = {ROWCOL[i] for i in board.free_cells()}
    assert free == {
        (1, 0),   # fire, top left
        (1, 2),   # vitae
        (2, 7),   # earth, right edge
        (3, 0),   # mors, left edge
        (4, 9),   # copper -- free, but not yet playable
        (6, 8),   # water
        (8, 1),   # water
        (10, 1),  # earth
        (10, 3),  # salt
    }

    result = solve(board)
    assert result.solved
    verify(board, result.moves)
    assert len(result.moves) == 28


def test_random_deal_has_a_standard_multiset():
    board = random_board(random.Random(11))
    assert board.counts() == STANDARD_COUNTS


# ---- interactive game layer ---------------------------------------------


def _screenshot_board():
    return load_board(Path(__file__).parents[1] / "boards" / "screenshot.txt")


def test_click_selects_then_clears_a_matching_pair():
    game = Game(_screenshot_board())
    water_a, water_b = index_of(6, 8), index_of(8, 1)

    assert game.click(water_a)["ok"]
    assert game.selected == water_a
    assert water_b in game.partners(water_a)

    result = game.click(water_b)
    assert result["cleared"] == [water_a, water_b]
    assert game.board.cells[water_a] == 0 and game.board.cells[water_b] == 0
    assert game.selected is None


def test_click_refuses_a_blocked_marble_and_says_why():
    game = Game(_screenshot_board())
    result = game.click(index_of(5, 5))  # the gold, walled in at the centre
    assert not result["ok"]
    assert "3 contiguous empty neighbours" in result["message"]


def test_click_refuses_a_metal_that_is_out_of_order():
    game = Game(_screenshot_board())
    result = game.click(index_of(4, 9))  # copper: free, but lead is still down
    assert not result["ok"]
    assert "Lead is still on the board" in result["message"]


def test_free_but_unplayable_marbles_are_not_highlighted():
    game = Game(_screenshot_board())
    copper = index_of(4, 9)
    assert copper in game.free()
    assert copper not in game.playable()


def test_clicking_a_non_matching_marble_switches_selection():
    game = Game(_screenshot_board())
    fire, earth = index_of(1, 0), index_of(10, 1)
    game.click(fire)
    game.click(earth)
    assert game.selected == earth
    assert game.board.cells[fire] and game.board.cells[earth]  # nothing cleared


def test_undo_puts_both_marbles_back():
    game = Game(_screenshot_board())
    a, b = index_of(6, 8), index_of(8, 1)
    game.click(a)
    game.click(b)
    assert game.undo()
    assert game.board.cells[a] == Marble.WATER and game.board.cells[b] == Marble.WATER
    assert game.moves == []
    assert not game.undo()


def test_gold_clears_on_a_single_click_once_it_is_last():
    board = Board.empty()
    board.cells[index_of(0, 0)] = Marble.GOLD
    game = Game(board)
    result = game.click(index_of(0, 0))
    assert result["cleared"] == [index_of(0, 0)]
    assert game.won()


@pytest.mark.parametrize("seed", range(6))
def test_solutions_replay_through_the_click_path(seed):
    """The UI plays a solution by clicking, so a solution must survive that.

    This is a real cross-check: `Game` decides legality with `marbles.matches`,
    while the solver generates moves from its own inlined rules.  A disagreement
    between the two would surface here as a refused click.
    """
    board = random_solvable_board(random.Random(seed))
    result = solve(board)
    assert result.solved

    game = Game(board)
    for n, move in enumerate(result.moves, 1):
        first = game.click(move.a)
        assert first["ok"], f"move {n}: {first['message']}"
        if move.b is None:
            assert first["cleared"], f"move {n}: gold did not clear"
        else:
            second = game.click(move.b)
            assert second["ok"] and second["cleared"], f"move {n}: {second['message']}"
    assert game.won()
    assert len(game.moves) == 28


def test_screenshot_board_replays_through_the_click_path():
    board = _screenshot_board()
    game = Game(board)
    for move in solve(board).moves:
        game.click(move.a)
        if move.b is not None:
            game.click(move.b)
    assert game.won()


def test_solver_reports_budget_exhaustion_separately():
    board = random_solvable_board(random.Random(5))
    result = Solver(board).solve(max_nodes=5)
    assert not result.solved and not result.exhausted
