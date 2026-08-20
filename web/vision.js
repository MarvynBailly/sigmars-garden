/* Reading a Sigmar's Garden board out of a screenshot, in the browser.
 *
 * Follows sigmar/vision.py, with one deliberate simplification. The Python
 * version scrubs the board's frame rails off with a morphological opening,
 * because leaving them on drags the fit several percent out. That is expensive
 * to do in JavaScript, so this skips it and lets the template-matching
 * refinement clean up instead -- measured on the reference screenshots, the
 * un-scrubbed fit lands ~6.5% large and refinement pulls it back to within a
 * pixel. The support constants below are calibrated for the un-scrubbed mask.
 */

const MIN_BRIGHT = 0.30;
const SUPPORT_W = 9.918;      // hexagon half-width, in units of the hex size
const SUPPORT_H = 8.924;      // hexagon half-height
const HEX_AREA = 269.3;       // blob area / s^2 for the cell field
const FILL_RANGE = [0.70, 1.35];
const MAX_SKEW = 0.06;
const MAX_SKEW_LOOSE = 0.16;
const MIN_AREA_FRACTION = 0.05;
const DETECT_MAX = 900;       // detection resolution; refinement uses full pixels
const MIN_SIZE = 20;
const UNCERTAIN = 0.15;

const REFINE_LEVELS = 4;
const REFINE_CELL_STRIDE = 2;
// Templates come in fives -- centre plus four offsets -- so scoring candidate
// grids against every fifth one uses each source cell once and is 5x quicker.
const REFINE_TEMPLATE_STRIDE = 5;
const REFINE_MIN_GAIN = 0.15;

class VisionError extends Error {}

/* ---- templates --------------------------------------------------------- */

let TEMPLATES = null;

function loadTemplates(data) {
  const decode = (text) => {
    const binary = atob(text);
    const out = new Int8Array(binary.length);
    for (let i = 0; i < binary.length; i++) out[i] = (binary.charCodeAt(i) << 24) >> 24;
    return out;
  };
  const tile = data.tile;
  const strokesRaw = decode(data.strokes);
  const coloursRaw = decode(data.colours);
  const count = data.count;
  const strokes = new Float32Array(count * tile * tile);
  for (let i = 0; i < strokes.length; i++) strokes[i] = strokesRaw[i] / data.quant;
  const colours = new Float32Array(count * 3);
  for (let i = 0; i < colours.length; i++) colours[i] = coloursRaw[i] / data.colourQuant;

  TEMPLATES = {
    tile, sigma: data.sigma, radius: data.radius, count,
    kinds: [...data.kinds], strokes, colours,
    area: tile * tile,
  };
  return TEMPLATES;
}

/* ---- pixels ------------------------------------------------------------ */

function toCanvas(image, width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(image, 0, 0, width, height);
  return { canvas, ctx, data: ctx.getImageData(0, 0, width, height) };
}

function otsu(values, buckets = 64) {
  const histogram = new Float64Array(buckets);
  for (const v of values) histogram[Math.min(buckets - 1, Math.floor(v * buckets))]++;
  const total = values.length;
  let sum = 0;
  for (let i = 0; i < buckets; i++) sum += (i + 0.5) / buckets * histogram[i];

  let weightLow = 0, sumLow = 0, best = 0, bestVariance = -1;
  for (let i = 0; i < buckets; i++) {
    weightLow += histogram[i];
    if (weightLow === 0) continue;
    const weightHigh = total - weightLow;
    if (weightHigh === 0) break;
    sumLow += (i + 0.5) / buckets * histogram[i];
    const meanLow = sumLow / weightLow;
    const meanHigh = (sum - sumLow) / weightHigh;
    const variance = weightLow * weightHigh * (meanLow - meanHigh) ** 2;
    if (variance > bestVariance) { bestVariance = variance; best = (i + 0.5) / buckets; }
  }
  return best;
}

/* Label the bright regions, and describe how board-like each one is. On a
 * screenshot of just the game the board is the biggest bright thing; on a whole
 * desktop it is not, so they are ranked by shape rather than size. */
function candidates(mask, width, height, limit = 8) {
  const labels = new Int32Array(width * height).fill(-1);
  const found = [];
  const stack = new Int32Array(width * height);

  for (let start = 0; start < mask.length; start++) {
    if (!mask[start] || labels[start] !== -1) continue;
    const id = found.length;
    let top = 0, area = 0;
    stack[top++] = start;
    labels[start] = id;
    let minX = width, maxX = 0, minY = height, maxY = 0;
    const xs = [], ys = [];

    while (top > 0) {
      const at = stack[--top];
      const x = at % width, y = (at / width) | 0;
      area++;
      xs.push(x); ys.push(y);
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
      const neighbours = [
        x > 0 ? at - 1 : -1, x < width - 1 ? at + 1 : -1,
        y > 0 ? at - width : -1, y < height - 1 ? at + width : -1,
      ];
      for (const n of neighbours) {
        if (n >= 0 && mask[n] && labels[n] === -1) { labels[n] = id; stack[top++] = n; }
      }
    }
    found.push({ area, xs, ys });
  }

  found.sort((a, b) => b.area - a.area);
  return found.slice(0, limit).filter((blob) => blob.area >= 200).map((blob) => {
    const fit = fitHexagon(blob.xs, blob.ys);
    return { ...fit, area: blob.area, fill: fit.size > 1 ? blob.area / (HEX_AREA * fit.size * fit.size) : 0 };
  });
}

function percentile(values, fraction) {
  const sorted = Float64Array.from(values).sort();
  const at = Math.min(sorted.length - 1, Math.max(0, Math.round(fraction * (sorted.length - 1))));
  return sorted[at];
}

/* The 91-cell field is a hexagon; its extent in four directions pins down
 * centre and scale, and the two scale estimates disagreeing means part of it is
 * missing. High percentiles rather than extremes, to shrug off stray specks. */
function fitHexagon(xs, ys) {
  const right = percentile(xs, 0.999);
  const left = -percentile(xs.map((x) => -x), 0.999);
  const down = percentile(ys, 0.999);
  const up = -percentile(ys.map((y) => -y), 0.999);
  const sizeW = (right - left) / 2 / SUPPORT_W;
  const sizeH = (down - up) / 2 / SUPPORT_H;
  return {
    cx: (right + left) / 2, cy: (down + up) / 2,
    size: (sizeW + sizeH) / 2,
    skew: Math.abs(sizeW - sizeH) / Math.max(sizeW, sizeH, 1e-6),
  };
}

function pickBoard(list) {
  if (!list.length) throw new VisionError("Could not find a board in this image.");
  const biggest = Math.max(...list.map((c) => c.area));
  const serious = list.filter((c) => c.area >= MIN_AREA_FRACTION * biggest);
  const shaped = (limit) => serious.filter(
    (c) => c.skew <= limit && c.fill >= FILL_RANGE[0] && c.fill <= FILL_RANGE[1]
  );
  const strict = shaped(MAX_SKEW);
  if (strict.length) return strict.reduce((a, b) => (b.area > a.area ? b : a));
  const loose = shaped(MAX_SKEW_LOOSE);
  if (loose.length) return loose.reduce((a, b) => (b.area > a.area ? b : a));

  const closest = serious.reduce((a, b) => (Math.abs(b.fill - 1) < Math.abs(a.fill - 1) ? b : a));
  if (closest.skew > MAX_SKEW) {
    throw new VisionError(
      `That does not look like a whole board — its width and height disagree by ` +
      `${Math.round(closest.skew * 100)}%. Is part of it cut off?`
    );
  }
  throw new VisionError("Found a bright region, but it is not board-shaped.");
}

function findGridCoarse(image) {
  const scale = Math.min(1, DETECT_MAX / Math.max(image.width, image.height));
  const width = Math.max(1, Math.round(image.width * scale));
  const height = Math.max(1, Math.round(image.height * scale));
  const { data } = toCanvas(image, width, height);
  const pixels = data.data;

  const brightness = new Float32Array(width * height);
  for (let i = 0, p = 0; i < brightness.length; i++, p += 4) {
    brightness[i] = Math.max(pixels[p], pixels[p + 1], pixels[p + 2]) / 255;
  }
  const threshold = Math.max(MIN_BRIGHT, otsu(brightness));
  const mask = new Uint8Array(width * height);
  let bright = 0;
  for (let i = 0; i < brightness.length; i++) {
    if (brightness[i] > threshold) { mask[i] = 1; bright++; }
  }
  if (bright / mask.length < 0.02) throw new VisionError("No board-coloured region in this image.");

  const board = pickBoard(candidates(mask, width, height));
  return { cx: board.cx / scale, cy: board.cy / scale, size: board.size / scale };
}

/* ---- per-cell features ------------------------------------------------- */

function gaussianBlur(values, size, sigma) {
  const radius = Math.max(1, Math.ceil(sigma * 3));
  const kernel = new Float32Array(radius * 2 + 1);
  let sum = 0;
  for (let i = -radius; i <= radius; i++) {
    const weight = Math.exp(-(i * i) / (2 * sigma * sigma));
    kernel[i + radius] = weight;
    sum += weight;
  }
  for (let i = 0; i < kernel.length; i++) kernel[i] /= sum;

  const pass = new Float32Array(values.length);
  const out = new Float32Array(values.length);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let total = 0;
      for (let k = -radius; k <= radius; k++) {
        const sx = Math.min(size - 1, Math.max(0, x + k));
        total += values[y * size + sx] * kernel[k + radius];
      }
      pass[y * size + x] = total;
    }
  }
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let total = 0;
      for (let k = -radius; k <= radius; k++) {
        const sy = Math.min(size - 1, Math.max(0, y + k));
        total += pass[sy * size + x] * kernel[k + radius];
      }
      out[y * size + x] = total;
    }
  }
  return out;
}

/* Two things have to survive the game drawing locked marbles washed out and
 * free ones vivid: the stroke map (tile minus a blurred copy, normalised, which
 * throws away the marble's shading and keeps the engraved glyph) and the colour
 * direction (the disc's colour minus the cell background, scaled to unit length,
 * because fading changes that vector's length but not its heading). */
function cellFeatures(source, x, y, size, tile, sigma) {
  const half = Math.max(4, TEMPLATES.radius * size);
  const scratch = cellFeatures.scratch || (cellFeatures.scratch = (() => {
    const canvas = document.createElement("canvas");
    canvas.width = tile; canvas.height = tile;
    return { canvas, ctx: canvas.getContext("2d", { willReadFrequently: true }) };
  })());

  scratch.ctx.imageSmoothingEnabled = true;
  scratch.ctx.imageSmoothingQuality = "high";
  scratch.ctx.clearRect(0, 0, tile, tile);
  scratch.ctx.drawImage(source, x - half, y - half, half * 2, half * 2, 0, 0, tile, tile);
  const pixels = scratch.ctx.getImageData(0, 0, tile, tile).data;

  const grey = new Float32Array(tile * tile);
  for (let i = 0, p = 0; i < grey.length; i++, p += 4) {
    grey[i] = (pixels[p] + pixels[p + 1] + pixels[p + 2]) / (3 * 255);
  }
  const blurred = gaussianBlur(grey, tile, sigma);
  const strokes = new Float32Array(tile * tile);
  let mean = 0;
  for (let i = 0; i < strokes.length; i++) { strokes[i] = grey[i] - blurred[i]; mean += strokes[i]; }
  mean /= strokes.length;
  let variance = 0;
  for (let i = 0; i < strokes.length; i++) variance += (strokes[i] - mean) ** 2;
  const deviation = Math.sqrt(variance / strokes.length);
  if (deviation > 1e-4) for (let i = 0; i < strokes.length; i++) strokes[i] /= deviation;

  const centre = (tile - 1) / 2;
  let discR = 0, discG = 0, discB = 0, discN = 0;
  let bgR = 0, bgG = 0, bgB = 0, bgN = 0;
  for (let yy = 0; yy < tile; yy++) {
    for (let xx = 0; xx < tile; xx++) {
      const radius = Math.hypot(yy - centre, xx - centre) / centre;
      const p = (yy * tile + xx) * 4;
      if (radius < 0.55) { discR += pixels[p]; discG += pixels[p + 1]; discB += pixels[p + 2]; discN++; }
      else if (radius > 0.88) { bgR += pixels[p]; bgG += pixels[p + 1]; bgB += pixels[p + 2]; bgN++; }
    }
  }
  let dr = discN ? discR / discN / 255 : 0, dg = discN ? discG / discN / 255 : 0, db = discN ? discB / discN / 255 : 0;
  if (bgN) { dr -= bgR / bgN / 255; dg -= bgG / bgN / 255; db -= bgB / bgN / 255; }
  const magnitude = Math.hypot(dr, dg, db);
  const colour = magnitude > 1e-6 ? [dr / magnitude, dg / magnitude, db / magnitude] : [0, 0, 0];
  return { strokes, colour };
}

const COLOUR_WEIGHT = 0.25;

function distancesToTemplates(features, stride = 1) {
  const { strokes, colours, count, area } = TEMPLATES;
  const out = new Float32Array(count);
  const cell = features.strokes;
  const [cr, cg, cb] = features.colour;
  if (stride > 1) out.fill(Infinity);
  for (let t = 0; t < count; t += stride) {
    const base = t * area;
    let total = 0;
    for (let i = 0; i < area; i++) total += Math.abs(cell[i] - strokes[base + i]);
    const c = t * 3;
    const dr = colours[c] - cr, dg = colours[c + 1] - cg, db = colours[c + 2] - cb;
    out[t] = total / area + COLOUR_WEIGHT * Math.hypot(dr, dg, db);
  }
  return out;
}

/* ---- geometry helpers -------------------------------------------------- */

function cellCentres(cx, cy, size, cells) {
  const width = Math.sqrt(3) * size;
  return cells.map(([q, r]) => [cx + width * (q + r / 2), cy + 1.5 * size * r]);
}

/* ---- refinement -------------------------------------------------------- */

/* Fitting the bright region gets close but not exact: the game shades the board
 * unevenly and its frame rails are still in the mask here, so the hexagon comes
 * out a few percent large. Finish by optimising what actually matters -- how
 * well every cell matches some template -- coarse to fine over offset and scale.
 * Only accepted on a clear improvement, so a fit that was already right is not
 * nudged off it. */
function refineGrid(source, cx, cy, size, cells) {
  const score = (ox, oy, s) => {
    const centres = cellCentres(ox, oy, s, cells);
    const best = [];
    for (let i = 0; i < centres.length; i += REFINE_CELL_STRIDE) {
      const features = cellFeatures(source, centres[i][0], centres[i][1], s, TEMPLATES.tile, TEMPLATES.sigma);
      const distances = distancesToTemplates(features, REFINE_TEMPLATE_STRIDE);
      let smallest = Infinity;
      for (const d of distances) if (d < smallest) smallest = d;
      best.push(smallest);
    }
    best.sort((a, b) => a - b);
    return best[best.length >> 1];
  };

  const start = [cx, cy, size];
  let best = [score(cx, cy, size), cx, cy, size];
  let span = 0.34, scaleSpan = 0.05;

  for (let level = 0; level < REFINE_LEVELS; level++) {
    const [, bx, by, bs] = best;
    for (const dx of [-span * bs, 0, span * bs]) {
      for (const dy of [-span * bs, 0, span * bs]) {
        for (const factor of [1 - scaleSpan, 1, 1 + scaleSpan]) {
          if (dx === 0 && dy === 0 && factor === 1) continue;
          const trial = [bx + dx, by + dy, bs * factor];
          const value = score(trial[0], trial[1], trial[2]);
          if (value < best[0]) best = [value, ...trial];
        }
      }
    }
    span *= 0.5;
    scaleSpan *= 0.5;
  }

  const found = [best[1], best[2], best[3]];
  const before = score(...start), after = score(...found);
  return before > 0 && (before - after) / before >= REFINE_MIN_GAIN ? found : start;
}

/* ---- reading ----------------------------------------------------------- */

function readBoardFromImage(image, options = {}) {
  if (!TEMPLATES) throw new VisionError("Templates are not loaded.");
  const { cells, fromChar, standardCounts, chars } = options;

  const coarse = findGridCoarse(image);
  if (coarse.size < MIN_SIZE) {
    throw new VisionError(
      `The board is too small in this image (cells about ${Math.round(coarse.size * 2)}px ` +
      `across). Use a larger screenshot.`
    );
  }
  const source = toCanvas(image, image.width, image.height).canvas;
  const [cx, cy, size] = options.grid || refineGrid(source, coarse.cx, coarse.cy, coarse.size, cells);

  const centres = cellCentres(cx, cy, size, cells);
  const margin = TEMPLATES.radius * size;
  const outside = centres.filter(([x, y]) =>
    x < margin || y < margin || x > image.width - margin || y > image.height - margin).length;
  if (outside) {
    throw new VisionError(`The board looks cut off — ${outside} of its 91 cells fall outside the image.`);
  }

  const kinds = [...new Set(TEMPLATES.kinds)].sort();
  const table = [];
  const picked = [];
  const margins = [];

  for (const [x, y] of centres) {
    const features = cellFeatures(source, x, y, size, TEMPLATES.tile, TEMPLATES.sigma);
    const distances = distancesToTemplates(features);
    const perKind = new Map();
    for (let t = 0; t < distances.length; t++) {
      const kind = TEMPLATES.kinds[t];
      if (!perKind.has(kind) || distances[t] < perKind.get(kind)) perKind.set(kind, distances[t]);
    }
    const ranked = [...perKind.entries()].sort((a, b) => a[1] - b[1]);
    table.push(perKind);
    picked.push(ranked[0][0]);
    margins.push(ranked.length > 1 ? (ranked[1][1] - ranked[0][1]) / Math.max(ranked[1][1], 1e-6) : 1);
  }

  let corrected = [];
  if (options.expectFresh) corrected = repairToCounts(picked, table, kinds, standardCounts, chars);

  const cellsOut = picked.map((c) => fromChar[c]);
  return {
    cells: cellsOut,
    grid: { cx, cy, size },
    uncertain: margins.map((m, i) => (m < UNCERTAIN ? i : -1)).filter((i) => i >= 0),
    corrected,
    confidence: margins,
  };
}

/* A just-dealt board holds a known multiset, which is a much stronger
 * constraint than judging each cell alone -- lead in particular has very few
 * templates. Greedily move the least-confident cells of an over-represented
 * kind to whichever under-represented kind they fit best. */
function repairToCounts(picked, table, kinds, standardCounts, chars) {
  const want = { ".": picked.length };
  for (const [code, count] of Object.entries(standardCounts)) {
    want[chars[code]] = count;
    want["."] -= count;
  }
  const corrected = [];
  for (let round = 0; round < 8; round++) {
    const have = {};
    for (const kind of picked) have[kind] = (have[kind] || 0) + 1;
    const over = Object.keys(want).filter((k) => (have[k] || 0) > want[k]);
    const under = Object.keys(want).filter((k) => (have[k] || 0) < want[k]);
    if (!over.length || !under.length) break;

    let move = null;
    for (let i = 0; i < picked.length; i++) {
      if (!over.includes(picked[i])) continue;
      for (const target of under) {
        const cost = (table[i].get(target) ?? Infinity) - (table[i].get(picked[i]) ?? 0);
        if (!move || cost < move.cost) move = { index: i, target, cost };
      }
    }
    if (!move) break;
    picked[move.index] = move.target;
    corrected.push(move.index);
  }
  return corrected;
}

if (typeof module !== "undefined") {
  module.exports = { loadTemplates, readBoardFromImage, findGridCoarse, VisionError };
}
