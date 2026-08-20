"""Tests for reading a board off a screenshot.

The reference screenshot is the only real board image available, so these
mostly check that recognition survives the ways a *different* screenshot would
differ from it -- resolution, cropping, compression, brightness -- rather than
just re-reading the image the templates were cut from.
"""

import base64
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("numpy")
pytest.importorskip("scipy")
Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")

from PIL import Image, ImageEnhance  # noqa: E402

from sigmar.board import load_board  # noqa: E402
from sigmar.marbles import STANDARD_COUNTS  # noqa: E402
from sigmar.solver import solve  # noqa: E402
from sigmar.vision import (  # noqa: E402
    MIN_SIZE,
    VisionError,
    find_grid,
    read_board,
    read_data_url,
)

ROOT = Path(__file__).resolve().parents[1]
SHOT = ROOT / "boards" / "screenshot.png"
TRUTH = ROOT / "boards" / "screenshot.txt"

pytestmark = pytest.mark.skipif(not SHOT.exists(), reason="reference screenshot missing")


@pytest.fixture(scope="module")
def shot():
    return Image.open(SHOT).convert("RGB")


@pytest.fixture(scope="module")
def truth():
    return load_board(TRUTH)


def _wrong(board, truth):
    return [i for i in range(91) if board.cells[i] != truth.cells[i]]


def test_finds_the_grid(shot):
    cx, cy, size = find_grid(shot)
    # Measured by hand off the reference screenshot.
    assert cx == pytest.approx(452, abs=3)
    assert cy == pytest.approx(391, abs=3)
    assert size == pytest.approx(38.05, abs=0.5)


def test_reads_every_cell(shot, truth):
    board, report = read_board(shot)
    assert _wrong(board, truth) == []
    assert board.counts() == STANDARD_COUNTS
    assert report["uncertain"] == []


@pytest.mark.parametrize("factor", [0.7, 0.9, 1.25, 2.0])
def test_survives_rescaling(shot, truth, factor):
    """A screenshot at another resolution still reads, on the same templates."""
    resized = shot.resize(
        (int(shot.width * factor), int(shot.height * factor)), Image.LANCZOS
    )
    board, _ = read_board(resized)
    assert _wrong(board, truth) == []


@pytest.mark.parametrize(
    "box", [(60, 50, 830, 730), (0, 0, 904, 740), (30, 20, 904, 808)]
)
def test_survives_cropping(shot, truth, box):
    board, _ = read_board(shot.crop(box))
    assert _wrong(board, truth) == []


@pytest.mark.parametrize("quality", [85, 50])
def test_survives_jpeg(shot, truth, quality):
    buf = io.BytesIO()
    shot.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    board, _ = read_board(Image.open(buf))
    assert _wrong(board, truth) == []


@pytest.mark.parametrize("factor", [1.2, 0.8])
def test_survives_brightness_shifts(shot, truth, factor):
    """The board/chrome split is chosen per image, not fixed.

    A fixed threshold read this correctly and then lost 60 cells on a copy 20%
    brighter, because the whole grid estimate moved.
    """
    board, _ = read_board(ImageEnhance.Brightness(shot).enhance(factor))
    assert _wrong(board, truth) == []


def test_rejects_an_image_with_no_board():
    blank = Image.new("RGB", (800, 600), (20, 20, 25))
    with pytest.raises(VisionError, match="No board-coloured region"):
        read_board(blank)


def test_rejects_a_board_too_small_to_read(shot):
    """Better to refuse than to return a confidently wrong board."""
    tiny = shot.resize((int(shot.width * 0.5), int(shot.height * 0.5)), Image.LANCZOS)
    with pytest.raises(VisionError, match="too small"):
        read_board(tiny)


@pytest.mark.parametrize(
    "box,side",
    [((150, 0, 904, 808), "left"), ((0, 0, 750, 808), "right"), ((0, 120, 904, 808), "top")],
)
def test_rejects_a_board_cut_off_by_the_crop(shot, box, side):
    """A truncated hexagon fits happily and then reads the wrong cells.

    Scale measured from the width and from the height agree to ~2% on a whole
    board, so a big disagreement means part of it is missing.
    """
    with pytest.raises(VisionError, match="cut off"):
        read_board(shot.crop(box))


def test_a_clip_that_slips_past_geometry_still_fails_the_count_check(shot, truth):
    """The last line of defence: a misread never passes silently.

    Not every crop skews the fit enough to catch, but a board read from a
    truncated image gets the marble counts wildly wrong, and that is reported.
    """
    # The board's lowest cells end around y=699, so this cuts through them.
    clipped = shot.crop((0, 0, 904, 640))
    try:
        board, report = read_board(clipped)
    except VisionError:
        return  # caught by geometry, which is fine too
    assert report["countMismatches"], "a clipped board was accepted silently"


def test_reads_from_a_data_url(truth):
    encoded = base64.b64encode(SHOT.read_bytes()).decode()
    board, _ = read_data_url("data:image/png;base64," + encoded)
    assert _wrong(board, truth) == []


def test_the_board_it_reads_is_solvable(shot):
    board, _ = read_board(shot)
    result = solve(board)
    assert result.solved
    assert len(result.moves) == 28


# ---- the second reference board -----------------------------------------

SHOT2 = ROOT / "boards" / "screenshot2.png"
TRUTH2 = ROOT / "boards" / "screenshot2.txt"

needs_second = pytest.mark.skipif(
    not SHOT2.exists(), reason="second reference screenshot missing"
)


@needs_second
def test_reads_the_second_board():
    """A different layout and a different image size, read end to end."""
    board, report = read_board(Image.open(SHOT2).convert("RGB"))
    assert _wrong(board, load_board(TRUTH2)) == []
    assert board.counts() == STANDARD_COUNTS
    assert solve(board).solved


@needs_second
def test_cross_validation_between_the_two_boards(tmp_path):
    """Read each board using templates cut only from the *other* one.

    Both boards ship in templates.npz, so reading either one back proves
    little on its own. This rebuilds the templates from one board and reads
    the other, which is the only genuinely held-out evidence available: a
    second real screenshot, different layout, different resolution, never seen
    by the templates doing the reading.
    """
    from sigmar.vision import build_templates
    import sigmar.vision as vision

    boards = [
        (Image.open(SHOT).convert("RGB"), load_board(TRUTH)),
        (Image.open(SHOT2).convert("RGB"), load_board(TRUTH2)),
    ]

    original_file, original_cache = vision.TEMPLATE_FILE, vision._CACHE
    try:
        for held_out in (0, 1):
            trained_on = boards[1 - held_out]
            image, truth_board = boards[held_out]

            path = tmp_path / f"templates{held_out}.npz"
            build_templates([trained_on], out_path=path)
            vision.TEMPLATE_FILE, vision._CACHE = path, None

            board, _ = read_board(image)
            wrong = _wrong(board, truth_board)
            assert wrong == [], (
                f"board {held_out} misread {len(wrong)} cells using templates "
                f"from the other board alone"
            )
    finally:
        vision.TEMPLATE_FILE, vision._CACHE = original_file, original_cache
