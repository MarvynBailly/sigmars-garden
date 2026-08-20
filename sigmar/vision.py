"""Read a board off a screenshot of the game.

Two steps.  First find the grid: the playing surface is far brighter than the
metal chrome around it, so a brightness threshold isolates it, and a
morphological opening scrubs off the thin frame rails -- which matter because
the game lights the board from the upper left, so the left-hand rails are bright
enough to pass the threshold and the right-hand ones are not.  Left in, that
asymmetry drags the estimated centre ~12px off.  What survives is the 91-cell
hexagon, whose extent in six directions pins down centre and scale.

Then classify each cell against templates cut from reference screenshots.
Locked marbles are drawn washed out and free ones vivid, so the features have to
ignore that: each tile is high-pass filtered down to its engraved glyph and
normalised, and colour enters only as a direction away from the cell's beige.
Empty is just another template class -- no brightness threshold separates the
faintest locked marbles from an empty cell.
"""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from .board import CELLS, N_CELLS, Board
from .marbles import CHARS, EMPTY_CHAR, FROM_CHAR, NAMES, STANDARD_COUNTS, Marble

TEMPLATE_FILE = Path(__file__).parent / "templates.npz"

# Stroke maps are stored as int8 at this scale. As JSON floats the template set
# ran to 17MB; quantised and compressed it is ~500KB, and the rounding error is
# ~0.02 against values whose deviation is 1, which changes no classification.
QUANT = 16.0

# Tile normalisation.
TILE = 32                    # templates are TILE x TILE
TILE_RADIUS = 0.62           # crop half-width, as a fraction of the hex size
HP_SIGMA = 3.5               # high-pass radius, in tile pixels
COLOUR_WEIGHT = 0.25         # weight of the colour term against the stroke term

# Support distances of the 91-cell hexagon from its centre, in units of the hex
# size s, measured after an opening of radius 0.315*s. The opening rounds off
# the corners slightly, so these are calibrated rather than exact geometry.
SUPPORT_W = 9.346            # +/- x
SUPPORT_H = 8.292            # +/- y
OPEN_FRAC = 0.315

MIN_BRIGHT = 0.30            # floor for the adaptive board/chrome split
MIN_SIZE = 25                # below this the engraved glyphs stop resolving
MAX_SKEW = 0.05              # width- vs height-derived scale may differ this much
MAX_SKEW_LOOSE = 0.15        # second pass, when nothing passes cleanly
MIN_AREA_FRACTION = 0.05     # a candidate must be this much of the largest one
HEX_AREA = 225.7             # blob area / s^2 for the cell field after the opening
FILL_RANGE = (0.75, 1.30)    # how solidly a candidate must fill its own hexagon
CANDIDATES = 8               # bright regions to measure before choosing
DETECT_MAX = 1200            # downscale before morphology; it is O(image area)
UNCERTAIN = 0.15             # margin below which a cell is flagged for review

# Grid refinement: subsampling keeps it to a couple of seconds.
REFINE_LEVELS = 4
REFINE_CELL_STRIDE = 2
REFINE_TEMPLATE_STRIDE = 9   # one per source cell; the rest are jitter copies
# A real misalignment improves the match by 28-76%; noise wanders in at ~7%.
# Demanding a clear margin stops a coarse fit that was already right from being
# nudged off it.
REFINE_MIN_GAIN = 0.15

# Template jitter, as a fraction of the hex size (~+/-1.7px at s=38).
JITTER = [(dx, dy) for dx in (-0.045, 0.0, 0.045) for dy in (-0.045, 0.0, 0.045)]


class VisionError(RuntimeError):
    pass


def _require_numpy():
    try:
        import numpy as np
        from scipy import ndimage
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise VisionError(
            "Reading screenshots needs numpy, scipy and Pillow: "
            "pip install numpy scipy pillow"
        ) from exc
    return np, ndimage


def _disk(np, r: int):
    r = max(1, int(r))
    y, x = np.ogrid[-r : r + 1, -r : r + 1]
    return x * x + y * y <= r * r


def _candidates(np, ndimage, mask, limit=CANDIDATES):
    """Measure the largest bright regions and describe how board-like each is.

    On a screenshot of just the game the board is the biggest bright thing, but
    on a whole desktop it is not -- a bright wallpaper easily beats it. So rank
    by shape instead of size: a board is a hexagon whose width and height imply
    the same cell size, and which is solidly filled at the area that size
    predicts. The wallpaper scores 37% skew and 0.55 fill against the board's
    3% and 1.06.
    """
    labels, count = ndimage.label(mask)
    if count == 0:
        return []
    sizes = ndimage.sum(mask, labels, range(1, count + 1))
    found = []
    for index in np.argsort(sizes)[::-1][:limit]:
        area = float(sizes[index])
        if area < 200:
            continue
        blob = labels == (int(index) + 1)
        cx, cy, s_w, s_h = _fit(np, blob)
        size = (s_w + s_h) / 2
        found.append(
            {
                "cx": cx,
                "cy": cy,
                "size": size,
                "area": area,
                "skew": abs(s_w - s_h) / max(s_w, s_h, 1e-6),
                "fill": area / (HEX_AREA * size * size) if size > 1 else 0.0,
            }
        )
    return found


def _pick(candidates):
    """Choose the board, or explain what the closest thing to one was wrong about."""
    if not candidates:
        raise VisionError(
            "Could not find the board. Is this a screenshot of the game window?"
        )

    # A speck a couple of thousand pixels across can be perfectly hexagonal by
    # accident. Only regions of a serious size are in the running at all --
    # without this, one such speck beat the real board the moment the board
    # itself failed the shape test.
    biggest = max(c["area"] for c in candidates)
    serious = [c for c in candidates if c["area"] >= MIN_AREA_FRACTION * biggest]

    def shaped(limit):
        return [
            c
            for c in serious
            if c["skew"] <= limit and FILL_RANGE[0] <= c["fill"] <= FILL_RANGE[1]
        ]

    # Size is judged later, against the original pixels: this may be running on
    # a downscaled copy, where every board looks too small.
    strict = shaped(MAX_SKEW)
    if strict:
        return max(strict, key=lambda c: c["area"])

    # The board's own shading can push a perfectly good board past the strict
    # limit -- one live capture measured 7.8% -- so take a second, looser pass.
    # Anything it lets through still has to survive the cells-inside-the-image
    # check, grid refinement, and the marble counts.
    loose = shaped(MAX_SKEW_LOOSE)
    if loose:
        return max(loose, key=lambda c: c["area"])

    closest = min(serious, key=lambda c: abs(c["fill"] - 1))
    if closest["skew"] > MAX_SKEW:
        raise VisionError(
            f"That does not look like a whole board -- its width and height "
            f"disagree by {closest['skew'] * 100:.0f}%. Is part of it cut off?"
        )
    raise VisionError("Found a bright region, but it is not board-shaped.")


def _fit(np, blob) -> tuple[float, float, float, float]:
    """Fit the cell hexagon: (centre_x, centre_y, scale from width, from height)."""
    ys, xs = np.nonzero(blob)
    pts = np.stack([xs, ys], 1).astype(np.float32)

    def support(ux, uy):
        return float(np.percentile(pts @ np.array([ux, uy], dtype=np.float32), 99.9))

    right, left = support(1, 0), support(-1, 0)
    down, up = support(0, 1), support(0, -1)
    cx = (right - left) / 2
    cy = (down - up) / 2
    # Scale measured two independent ways. They agree to about 2% on a whole
    # board, and diverge sharply when the board runs off the edge of the image.
    s_w = (right + left) / 2 / SUPPORT_W
    s_h = (down + up) / 2 / SUPPORT_H
    return cx, cy, s_w, s_h


def _otsu(np, pixels) -> float:
    """Split the brightness histogram into "board" and "everything else".

    A fixed threshold looked fine on the reference shot and then fell apart on
    a copy 20% brighter or darker -- the board is defined by being much lighter
    than the chrome around it, not by an absolute value, so the threshold has to
    come from the image.
    """
    counts, edges = np.histogram(pixels, bins=64, range=(0.0, 1.0))
    counts = counts.astype(np.float64)
    weight_low = np.cumsum(counts)
    weight_high = weight_low[-1] - weight_low
    centres = (edges[:-1] + edges[1:]) / 2
    sum_low = np.cumsum(counts * centres)
    sum_high = sum_low[-1] - sum_low
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_low = sum_low / weight_low
        mean_high = sum_high / weight_high
        between = weight_low * weight_high * (mean_low - mean_high) ** 2
    between[~np.isfinite(between)] = 0
    return float(centres[int(np.argmax(between))])


def _measure(np, ndimage, image):
    """Threshold, open twice, and return the best board-shaped region."""
    pixels = np.asarray(image.convert("RGB")).astype(np.float32).max(2) / 255
    bright = pixels > max(MIN_BRIGHT, _otsu(np, pixels))
    if bright.mean() < 0.02:
        raise VisionError("No board-coloured region found in this image.")

    # The first opening only sizes the board, so the second can use a radius
    # proportional to the cell size. With the frame rails still attached nothing
    # is shaped well yet, so go by the lowest skew rather than by area -- on a
    # desktop the biggest bright thing is rarely the board.
    coarse = _candidates(np, ndimage, ndimage.binary_opening(bright, _disk(np, 4)))
    if not coarse:
        raise VisionError(
            "Could not find the board. Is this a screenshot of the game window?"
        )
    # Only substantial regions: a blob of a few hundred pixels can have near-zero
    # skew by chance, and taking its size sets the opening radius to nothing,
    # which leaves the rails on and puts the fit ~12px out.
    biggest = max(c["area"] for c in coarse)
    substantial = [c for c in coarse if c["area"] >= 0.1 * biggest]
    rough = min(substantial, key=lambda c: c["skew"])["size"]
    radius = max(2, round(OPEN_FRAC * rough))
    return _pick(_candidates(np, ndimage, ndimage.binary_opening(bright, _disk(np, radius))))


def _locate(np, ndimage, image):
    """Find the board, cheaply then precisely.

    Morphology costs scale with area, so a big capture is searched on a
    downscaled copy. But the answer has to be accurate to a pixel or two at full
    resolution, and a 4K desktop shrinks the board to a cell size of ~12, where
    scaling back up multiplies every error by three. So the small pass only says
    *where*; the measurement is redone at full resolution on a crop around it.

    Returns (board, scale, origin) where the board's coordinates are in the
    measured image and (x/scale + origin) maps them back to the original.
    """
    from PIL import Image

    shrink = min(1.0, DETECT_MAX / max(image.width, image.height))
    if shrink == 1.0:
        return _measure(np, ndimage, image), 1.0, (0, 0)

    small = image.resize(
        (max(1, round(image.width * shrink)), max(1, round(image.height * shrink))),
        Image.LANCZOS,
    )
    rough = _measure(np, ndimage, small)

    cx, cy = rough["cx"] / shrink, rough["cy"] / shrink
    half_w = SUPPORT_W * rough["size"] / shrink * 1.15
    half_h = SUPPORT_H * rough["size"] / shrink * 1.15
    box = (
        max(0, int(cx - half_w)),
        max(0, int(cy - half_h)),
        min(image.width, int(cx + half_w)),
        min(image.height, int(cy + half_h)),
    )
    if box[2] - box[0] < 40 or box[3] - box[1] < 40:
        return rough, shrink, (0, 0)
    return _measure(np, ndimage, image.crop(box)), 1.0, (box[0], box[1])


def _refine(np, ndimage, image, cx, cy, s):
    """Nudge the grid until the cells actually look like marbles.

    Fitting the bright region gets close but not always close enough. The game
    shades the board unevenly, so on some windows the darkest corner falls below
    the threshold while a sliver of frame rail stays above it; the fitted
    hexagon then comes out a couple of percent small and offset, which is
    invisible in the middle of the board and fatal at its edges. One captured
    desktop read 2 of 55 marbles for exactly that reason.

    So finish by optimising what actually matters: how well every cell matches
    some template. Coarse to fine over offset and scale, on a subsample of cells
    and templates to keep it a few seconds rather than a minute.
    """
    kinds, strokes, colours = _templates(np)
    ref_s = strokes[::REFINE_TEMPLATE_STRIDE]
    ref_c = colours[::REFINE_TEMPLATE_STRIDE]

    def score(ox, oy, size):
        centres = cell_centres(ox, oy, size)[::REFINE_CELL_STRIDE]
        worst = []
        for x, y in centres:
            stroke, colour = _cell_features(np, ndimage, image, x, y, size)
            distance = np.abs(ref_s - stroke).mean((1, 2)) + COLOUR_WEIGHT * np.linalg.norm(
                ref_c - colour, axis=1
            )
            worst.append(distance.min())
        return float(np.median(worst))

    def full_score(ox, oy, size):
        """The same measure over every cell and template, for the final call."""
        worst = []
        for x, y in cell_centres(ox, oy, size):
            stroke, colour = _cell_features(np, ndimage, image, x, y, size)
            distance = np.abs(strokes - stroke).mean((1, 2)) + COLOUR_WEIGHT * np.linalg.norm(
                colours - colour, axis=1
            )
            worst.append(distance.min())
        return float(np.median(worst))

    start = (cx, cy, s)
    best = (score(cx, cy, s), cx, cy, s)
    span, scale_span = 0.34, 0.035
    for _ in range(REFINE_LEVELS):
        _, cx, cy, s = best
        for dx in (-span * s, 0.0, span * s):
            for dy in (-span * s, 0.0, span * s):
                for factor in (1 - scale_span, 1.0, 1 + scale_span):
                    if dx == dy == 0.0 and factor == 1.0:
                        continue
                    trial = (cx + dx, cy + dy, s * factor)
                    value = score(*trial)
                    if value < best[0]:
                        best = (value, *trial)
        span *= 0.5
        scale_span *= 0.5

    # The search optimises a subsample; confirm against the whole board before
    # accepting, so a marginal gain on the sample cannot drift the grid off a
    # coarse fit that was already right.
    found = (best[1], best[2], best[3])
    if found == start:
        return start
    before = full_score(*start)
    after = full_score(*found)
    improved = before > 0 and (before - after) / before >= REFINE_MIN_GAIN
    return found if improved else start


def find_grid(image, refine: bool = True) -> tuple[float, float, float]:
    """Locate the board: returns (centre_x, centre_y, hex_size) in pixels.

    Runs twice, because the opening radius has to be proportional to the hex
    size for the calibration to hold at any screenshot resolution -- and the hex
    size is what we are trying to measure.  The first pass only needs to be
    close enough to set the radius for the second.
    """
    np, ndimage = _require_numpy()

    board, scale, origin = _locate(np, ndimage, image)
    cx = board["cx"] / scale + origin[0]
    cy = board["cy"] / scale + origin[1]
    s = board["size"] / scale

    width, height = image.width, image.height
    if not (0 < cx < width and 0 < cy < height) or s < 6:
        raise VisionError("Found a bright region, but it is not board-shaped.")
    # Judged here rather than while ranking candidates, because ranking may have
    # run on a downscaled copy where every board looks too small.
    if s < MIN_SIZE:
        raise VisionError(
            f"The board is too small in this image (cells are {2 * s:.0f}px across). "
            "Grab a bigger screenshot -- the marble glyphs cannot be told apart below "
            f"about {2 * MIN_SIZE}px."
        )

    # Every cell has to be inside the picture. A screenshot cropped through the
    # board fits the truncated hexagon happily and then reads the wrong cells,
    # so check rather than trust the fit.
    margin = TILE_RADIUS * s
    outside = sum(
        1
        for x, y in cell_centres(cx, cy, s)
        if x < margin or y < margin or x > width - margin or y > height - margin
    )
    if outside:
        raise VisionError(
            f"The board looks cut off -- {outside} of its 91 cells fall outside "
            "the image. Capture the whole board."
        )

    if refine:
        cx, cy, s = _refine(np, ndimage, image, cx, cy, s)
    return cx, cy, s


def cell_centres(cx: float, cy: float, s: float) -> list[tuple[float, float]]:
    """Pixel centre of every one of the 91 cells, in board index order."""
    width = math.sqrt(3) * s
    return [(cx + width * (q + r / 2), cy + 1.5 * s * r) for q, r in CELLS]


# ---- cell appearance -----------------------------------------------------


def _radial(np):
    axis = np.arange(TILE, dtype=np.float32)
    yy, xx = np.meshgrid(axis, axis, indexing="ij")
    centre = (TILE - 1) / 2
    return np.hypot(yy - centre, xx - centre) / centre


def _cell_features(np, ndimage, image, x: float, y: float, s: float):
    """Describe one cell as (stroke map, colour direction).

    Two things have to survive the fact that the game draws locked marbles
    washed out and free ones vivid:

    * The **stroke map** is the tile minus a blurred copy of itself, divided by
      its own deviation.  Subtracting the blur throws away the marble's radial
      shading and the cell's bevel, leaving just the engraved glyph; the
      division normalises faint against bold.  Matching on plain contrast-
      stretched intensity instead confuses vitae with salt, because the radial
      gradient dominates and the glyph is a small part of the signal.
    * The **colour direction** is the disc's mean colour minus the surrounding
      cell background, scaled to unit length.  A locked marble is effectively
      its own colour blended toward the beige cell, so fading changes that
      vector's length but not its direction.
    """
    from PIL import Image

    half = max(4.0, TILE_RADIUS * s)
    box = (int(round(x - half)), int(round(y - half)),
           int(round(x + half)), int(round(y + half)))
    crop = image.crop(box).convert("RGB").resize((TILE, TILE), Image.LANCZOS)
    rgb = np.asarray(crop).astype(np.float32) / 255

    grey = rgb.mean(2)
    strokes = grey - ndimage.gaussian_filter(grey, HP_SIGMA)
    deviation = float(strokes.std())
    if deviation > 1e-4:
        strokes = strokes / deviation

    radius = _radial(np)
    offset = rgb[radius < 0.55].mean(0) - rgb[radius > 0.88].mean(0)
    magnitude = float(np.linalg.norm(offset))
    colour = offset / magnitude if magnitude > 1e-6 else offset * 0
    return strokes.astype(np.float32), colour.astype(np.float32)


def _distance(np, strokes_a, colour_a, strokes_b, colour_b):
    return float(
        np.abs(strokes_a - strokes_b).mean()
        + COLOUR_WEIGHT * np.linalg.norm(colour_a - colour_b)
    )


# ---- templates -----------------------------------------------------------


def build_templates(pairs, out_path=TEMPLATE_FILE) -> dict:
    """Cut labelled templates from screenshots whose boards are known.

    `pairs` is a sequence of (PIL image, Board). More reference boards is
    strictly better: each one adds a second rendering of every marble, and the
    metals in particular appear only once per board.

    Empty cells become a template class of their own rather than being split
    off by a brightness threshold: the faintest locked marbles (vitae, salt,
    quicksilver) are lower contrast than the brightest empty cell, so no
    threshold separates them, but their glyphs are unmistakable.
    """
    np, ndimage = _require_numpy()

    chars: list[str] = []
    strokes: list = []
    colours: list = []
    for image, board in pairs:
        # Coarse fit only: refinement scores candidate grids against templates,
        # and there are none yet. The reference screenshots are framed tightly
        # enough that the coarse fit is exact on them anyway.
        cx, cy, s = find_grid(image, refine=False)
        centres = cell_centres(cx, cy, s)
        for i, marble in enumerate(board.cells):
            char = CHARS[Marble(marble)] if marble else EMPTY_CHAR
            x, y = centres[i]
            # Store each cell at several small offsets. Grid detection lands
            # within a pixel or two, but not exactly, and matching a glyph
            # against a template shifted 2px is enough to lose outer-ring
            # cells. Jittered copies make the match tolerant without having to
            # nail the alignment.
            for dx, dy in JITTER:
                stroke, colour = _cell_features(
                    np, ndimage, image, x + dx * s, y + dy * s, s
                )
                chars.append(char)
                strokes.append(
                    np.clip(np.round(stroke * QUANT), -127, 127).astype(np.int8)
                )
                colours.append(colour.astype(np.float16))

    np.savez_compressed(
        out_path,
        kinds=np.array(chars),
        strokes=np.stack(strokes),
        colours=np.stack(colours),
        meta=np.array([TILE, HP_SIGMA, TILE_RADIUS, QUANT], np.float32),
    )
    counts: dict[str, int] = {}
    for char in chars:
        counts[char] = counts.get(char, 0) + 1
    return {"tile": TILE, "sigma": HP_SIGMA, "radius": TILE_RADIUS, "templates": counts}


_CACHE = None


def _templates(np):
    global _CACHE
    if _CACHE is None:
        if not TEMPLATE_FILE.exists():
            raise VisionError(
                "No templates.npz -- run tools/build_templates.py to create it."
            )
        raw = np.load(TEMPLATE_FILE)
        tile, sigma, radius, quant = raw["meta"]
        # Stored as float32, so compare with tolerance rather than exactly.
        if int(tile) != TILE or abs(sigma - HP_SIGMA) > 1e-4 or abs(radius - TILE_RADIUS) > 1e-4:
            raise VisionError(
                "templates.npz was built with different settings -- rebuild it "
                "with tools/build_templates.py."
            )
        _CACHE = (
            raw["kinds"],
            raw["strokes"].astype(np.float32) / quant,
            raw["colours"].astype(np.float32),
        )
    return _CACHE


# ---- reading -------------------------------------------------------------


def read_board(image, grid=None, refine: bool = True, expect_fresh: bool = False):
    """Read a board from a PIL image. Returns (board, report).

    Pass `grid` to reuse a previously located board -- finding and refining it
    costs a couple of seconds, and while a game is being played the window does
    not move.

    Set `expect_fresh` when the board is known to be a full, just-dealt one. The
    cells are then chosen together, against the multiset a deal always holds,
    rather than one at a time. This can only ever succeed, so the report also
    carries `rawMarbles` and `rawMismatches` from the unconstrained read -- check
    those before trusting the result.
    """
    np, ndimage = _require_numpy()
    templates = _templates(np)
    if grid is None:
        grid = find_grid(image, refine=refine)
    cx, cy, s = grid

    kinds, table = _class_distances(np, ndimage, image, grid, templates)
    best = table.argmin(1)
    order = np.sort(table, axis=1)
    # Confidence is the margin over the best *other* kind: a tile that looks
    # nearly as much like something else is the one worth flagging.
    margins = np.where(order[:, 1] > 0, (order[:, 1] - order[:, 0]) / order[:, 1], 0.0)

    chosen = [kinds[j] for j in best]
    raw = Board([FROM_CHAR[c] for c in chosen])
    raw_counts = raw.counts()
    raw_mismatches = {
        NAMES[m]: [raw_counts.get(m, 0), n]
        for m, n in STANDARD_COUNTS.items()
        if raw_counts.get(m, 0) != n
    }

    corrected = []
    if expect_fresh:
        wanted = {EMPTY_CHAR: N_CELLS - sum(STANDARD_COUNTS.values())}
        for marble, count in STANDARD_COUNTS.items():
            wanted[CHARS[marble]] = count
        constrained = _assign_with_counts(np, kinds, table, wanted)
        corrected = [i for i in range(len(chosen)) if constrained[i] != chosen[i]]
        chosen = constrained

    cells = [FROM_CHAR[c] for c in chosen]
    confidences = [round(float(m), 3) for m in margins]
    low = [i for i, m in enumerate(margins) if m < UNCERTAIN]

    board = Board(cells)
    counts = board.counts()
    mismatches = {
        NAMES[m]: [counts.get(m, 0), n]
        for m, n in STANDARD_COUNTS.items()
        if counts.get(m, 0) != n
    }
    report = {
        "grid": {"cx": round(cx, 1), "cy": round(cy, 1), "size": round(s, 2)},
        "marbles": board.marble_count(),
        "confidence": confidences,
        "uncertain": low,
        "corrected": corrected,
        "countMismatches": mismatches,
        # What the cells said before the counts were imposed. Forcing a read to
        # match a standard deal always succeeds -- it would happily invent 55
        # marbles on an empty board -- so the unconstrained result is what any
        # caller must sanity-check against.
        "rawMarbles": raw.marble_count(),
        "rawMismatches": raw_mismatches,
        "text": board.render(),
    }
    return board, report


def _class_distances(np, ndimage, image, grid, templates):
    """Distance from every cell to every marble kind (best template of each)."""
    ref_chars, ref_strokes, ref_colours = templates
    kinds = sorted(set(str(c) for c in ref_chars))
    masks = {kind: (ref_chars == kind) for kind in kinds}

    table = np.empty((len(cell_centres(*grid)), len(kinds)), np.float32)
    for i, (x, y) in enumerate(cell_centres(*grid)):
        strokes, colour = _cell_features(np, ndimage, image, x, y, grid[2])
        distances = np.abs(ref_strokes - strokes).mean((1, 2)) + COLOUR_WEIGHT * np.linalg.norm(
            ref_colours - colour, axis=1
        )
        for j, kind in enumerate(kinds):
            table[i, j] = distances[masks[kind]].min()
    return kinds, table


def _assign_with_counts(np, kinds, table, wanted):
    """Choose kinds for all 91 cells subject to how many of each there must be.

    A freshly dealt board holds a known multiset -- eight of each element, one
    of each metal, and so on -- and that is a strong constraint to throw away by
    classifying every cell on its own. Lead appears once per board and so has
    very few templates; read independently it can lose to mors, which is also
    dark, giving five mors and no lead. Solved as an assignment against the
    known counts instead, the one cell that has to be the lead is the one it
    fits least badly, and the rest fall into place.
    """
    from scipy.optimize import linear_sum_assignment

    columns = []
    for kind, count in wanted.items():
        columns.extend([kinds.index(kind)] * count)
    if len(columns) != table.shape[0]:
        raise VisionError(
            f"expected counts covering {table.shape[0]} cells, got {len(columns)}"
        )

    cost = table[:, columns]
    rows, chosen = linear_sum_assignment(cost)
    out = [None] * table.shape[0]
    for row, column in zip(rows, chosen):
        out[row] = kinds[columns[column]]
    return out


def read_cells(image, grid, cells) -> dict:
    """Classify just a few cells of a board whose grid is already known.

    Autoplay checks two cells after every click; running the whole 91-cell read
    for that costs 370ms against about ten.
    """
    np, ndimage = _require_numpy()
    ref_chars, ref_strokes, ref_colours = _templates(np)
    centres = cell_centres(*grid)
    out = {}
    for index in cells:
        x, y = centres[index]
        strokes, colour = _cell_features(np, ndimage, image, x, y, grid[2])
        distances = np.abs(ref_strokes - strokes).mean((1, 2)) + COLOUR_WEIGHT * np.linalg.norm(
            ref_colours - colour, axis=1
        )
        out[index] = FROM_CHAR[str(ref_chars[int(np.argmin(distances))])]
    return out


def read_image_bytes(data: bytes) -> tuple[Board, dict]:
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise VisionError("That does not look like an image file.") from exc
    return read_board(image)


def read_data_url(url: str) -> tuple[Board, dict]:
    if "," in url:
        url = url.split(",", 1)[1]
    try:
        data = base64.b64decode(url, validate=False)
    except (ValueError, TypeError) as exc:
        raise VisionError("Could not decode that image.") from exc
    return read_image_bytes(data)
