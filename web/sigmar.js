/* Sigmar's Garden: board, rules and solver.
 *
 * A direct port of the Python package, so the web page can play and solve
 * without a server. Kept deliberately close to sigmar/board.py, marbles.py and
 * solver.py -- same freedom rule, same move ordering, same restart scheme.
 */

const RADIUS = 5;
const N_CELLS = 91;

// Marble codes. 0 is empty; metals are ordered so LEAD < ... < GOLD, which the
// solver relies on to find the lowest metal still on the board.
const EMPTY = 0, FIRE = 1, WATER = 2, EARTH = 3, AIR = 4, SALT = 5,
      VITAE = 6, MORS = 7, QUICKSILVER = 8,
      LEAD = 9, TIN = 10, IRON = 11, COPPER = 12, SILVER = 13, GOLD = 14;

const CARDINALS = [FIRE, WATER, EARTH, AIR];
const METALS = [LEAD, TIN, IRON, COPPER, SILVER, GOLD];

const CHARS = {
  [FIRE]: "f", [WATER]: "w", [EARTH]: "e", [AIR]: "a", [SALT]: "s",
  [VITAE]: "v", [MORS]: "m", [QUICKSILVER]: "q",
  [LEAD]: "1", [TIN]: "2", [IRON]: "3", [COPPER]: "4", [SILVER]: "5", [GOLD]: "6",
};
const FROM_CHAR = { ".": EMPTY, "-": EMPTY, "_": EMPTY };
for (const [code, ch] of Object.entries(CHARS)) FROM_CHAR[ch] = Number(code);

const NAMES = {
  [FIRE]: "Fire", [WATER]: "Water", [EARTH]: "Earth", [AIR]: "Air",
  [SALT]: "Salt", [VITAE]: "Vitae", [MORS]: "Mors", [QUICKSILVER]: "Quicksilver",
  [LEAD]: "Lead", [TIN]: "Tin", [IRON]: "Iron", [COPPER]: "Copper",
  [SILVER]: "Silver", [GOLD]: "Gold",
};

const STANDARD_COUNTS = {
  [FIRE]: 8, [WATER]: 8, [EARTH]: 8, [AIR]: 8, [SALT]: 4, [VITAE]: 4,
  [MORS]: 4, [QUICKSILVER]: 5, [LEAD]: 1, [TIN]: 1, [IRON]: 1, [COPPER]: 1,
  [SILVER]: 1, [GOLD]: 1,
};

/* ---- geometry ---------------------------------------------------------- */

const ROW_LENGTHS = [];
for (let r = -RADIUS; r <= RADIUS; r++) ROW_LENGTHS.push(2 * RADIUS + 1 - Math.abs(r));

function qStart(r) { return Math.max(-RADIUS, -RADIUS - r); }

const CELLS = [];      // index -> [q, r]
const ROWCOL = [];     // index -> [row, col]
const INDEX = new Map();
for (let r = -RADIUS; r <= RADIUS; r++) {
  const start = qStart(r);
  for (let q = start; q <= Math.min(RADIUS, RADIUS - r); q++) {
    INDEX.set(`${q},${r}`, CELLS.length);
    ROWCOL.push([r + RADIUS, q - start]);
    CELLS.push([q, r]);
  }
}

// The six neighbours in cyclic order: adjacent entries are themselves adjacent,
// which is what makes "three contiguous empty neighbours" a window over a ring.
const DIRS = [[1, 0], [1, -1], [0, -1], [-1, 0], [-1, 1], [0, 1]];
const OFF_BOARD = N_CELLS;
const NEIGHBOURS = CELLS.map(([q, r]) =>
  DIRS.map(([dq, dr]) => {
    const found = INDEX.get(`${q + dq},${r + dr}`);
    return found === undefined ? OFF_BOARD : found;
  })
);

// occupancy pattern of the six neighbours -> is that marble free?
const FREE_TABLE = new Uint8Array(64);
for (let pattern = 0; pattern < 64; pattern++) {
  const ring = pattern | (pattern << 6);
  for (let i = 0; i < 6; i++) {
    if (((ring >> i) & 0b111) === 0) { FREE_TABLE[pattern] = 1; break; }
  }
}

function indexOf(row, col) {
  const r = row - RADIUS;
  return INDEX.get(`${qStart(r) + col},${r}`);
}

/* ---- rules ------------------------------------------------------------- */

function lowestMetal(counts) {
  for (const metal of METALS) if (counts[metal]) return metal;
  return null;
}

function matches(a, b, counts) {
  if (a > b) [a, b] = [b, a];
  if (CARDINALS.includes(a)) return a === b || b === SALT;
  if (a === SALT) return b === SALT;
  if (a === VITAE) return b === MORS;
  if (a === QUICKSILVER) return METALS.includes(b) && b !== GOLD && b === lowestMetal(counts);
  return false;
}

function isSolo(marble, counts) {
  return marble === GOLD && lowestMetal(counts) === GOLD;
}

/* ---- board ------------------------------------------------------------- */

class Board {
  constructor(cells) { this.cells = Int8Array.from(cells); }
  static empty() { return new Board(new Array(N_CELLS).fill(0)); }
  clone() { return new Board(this.cells); }
  marbleCount() { let n = 0; for (const m of this.cells) if (m) n++; return n; }

  counts() {
    const tally = {};
    for (const m of this.cells) if (m) tally[m] = (tally[m] || 0) + 1;
    return tally;
  }

  isFree(i) {
    let pattern = 0;
    const nbrs = NEIGHBOURS[i];
    for (let j = 0; j < 6; j++) {
      const n = nbrs[j];
      if (n !== OFF_BOARD && this.cells[n]) pattern |= 1 << j;
    }
    return FREE_TABLE[pattern] === 1;
  }

  freeCells() {
    const out = [];
    for (let i = 0; i < N_CELLS; i++) if (this.cells[i] && this.isFree(i)) out.push(i);
    return out;
  }

  render() {
    const lines = [];
    let i = 0;
    ROW_LENGTHS.forEach((length) => {
      const glyphs = [];
      for (let k = 0; k < length; k++) glyphs.push(CHARS[this.cells[i++]] || ".");
      lines.push(" ".repeat(ROW_LENGTHS.length - length) + glyphs.join(" "));
    });
    return lines.join("\n");
  }
}

function parseBoard(text) {
  const rows = text.split("\n")
    .map((line) => line.split("#")[0].trim())
    .filter((line) => line.length);
  if (rows.length !== ROW_LENGTHS.length) {
    throw new Error(`expected ${ROW_LENGTHS.length} rows, got ${rows.length}`);
  }
  const cells = [];
  rows.forEach((line, row) => {
    const tokens = [...line].filter((c) => !/\s/.test(c));
    if (tokens.length !== ROW_LENGTHS[row]) {
      throw new Error(`row ${row + 1} needs ${ROW_LENGTHS[row]} cells, got ${tokens.length}`);
    }
    for (const token of tokens) {
      if (!(token in FROM_CHAR)) throw new Error(`unknown marble "${token}"`);
      cells.push(FROM_CHAR[token]);
    }
  });
  return new Board(cells);
}

/* ---- solver ------------------------------------------------------------ */

// Move ordering. Metals are forced moves with no alternative pairing, so they
// cost nothing to take early; salt is the scarce flexible resource, played last.
const PRI_GOLD = 6, PRI_METAL = 5, PRI_CARDINAL = 4, PRI_VITAE = 3,
      PRI_SALT_CARDINAL = 1, PRI_SALT_SALT = 0;
// Priority classes sit 16 apart, so a restart's jitter must exceed that to
// reconsider which *kind* of move to open with, not merely which marbles.
const RESTART_JITTER = 120;

const NEIGHBOUR_MASK = CELLS.map((_, i) => {
  let mask = 0n;
  for (const n of NEIGHBOURS[i]) if (n !== OFF_BOARD) mask |= 1n << BigInt(n);
  return mask;
});

function popcount(value) {
  let n = 0;
  while (value) { value &= value - 1n; n++; }
  return n;
}

class Solver {
  constructor(board) {
    this.types = Array.from(board.cells);
    this.start = 0n;
    this.counts = new Array(GOLD + 1).fill(0);
    for (let i = 0; i < N_CELLS; i++) {
      if (this.types[i]) { this.start |= 1n << BigInt(i); this.counts[this.types[i]]++; }
    }
  }

  freeCells(occ) {
    const free = [];
    for (let i = 0; i < N_CELLS; i++) {
      if (!((occ >> BigInt(i)) & 1n)) continue;
      let pattern = 0;
      const nbrs = NEIGHBOURS[i];
      for (let j = 0; j < 6; j++) {
        const n = nbrs[j];
        if (n !== OFF_BOARD && ((occ >> BigInt(n)) & 1n)) pattern |= 1 << j;
      }
      if (FREE_TABLE[pattern]) free.push(i);
    }
    return free;
  }

  // Every cardinal must pair with its own kind or with salt, so each element
  // left in an odd number consumes one salt, and the rest of the salt has to
  // pair with itself. States failing that are dead before the search notices.
  feasible(counts) {
    const salt = counts[SALT];
    const odd = (counts[FIRE] & 1) + (counts[WATER] & 1) + (counts[EARTH] & 1) + (counts[AIR] & 1);
    return odd <= salt && !((salt - odd) & 1);
  }

  moves(occ, counts) {
    const byKind = new Map();
    for (const i of this.freeCells(occ)) {
      const kind = this.types[i];
      if (!byKind.has(kind)) byKind.set(kind, []);
      byKind.get(kind).push(i);
    }
    const scored = [];
    const salts = byKind.get(SALT) || [];
    const add = (pri, a, b) => {
      // Clearing a marble hemmed in by neighbours opens the densest part of the
      // board, so break ties by how blocked the pair is.
      let tie = popcount(occ & NEIGHBOUR_MASK[a]);
      if (b !== null) tie += popcount(occ & NEIGHBOUR_MASK[b]);
      scored.push([pri * 16 + tie, a, b]);
    };

    for (const cardinal of CARDINALS) {
      const same = byKind.get(cardinal) || [];
      for (let x = 0; x < same.length; x++)
        for (let y = x + 1; y < same.length; y++) add(PRI_CARDINAL, same[x], same[y]);
      for (const a of same) for (const s of salts) add(PRI_SALT_CARDINAL, a, s);
    }
    for (let x = 0; x < salts.length; x++)
      for (let y = x + 1; y < salts.length; y++) add(PRI_SALT_SALT, salts[x], salts[y]);
    for (const v of byKind.get(VITAE) || [])
      for (const m of byKind.get(MORS) || []) add(PRI_VITAE, v, m);

    for (const metal of METALS) {
      if (!counts[metal]) continue;
      if (metal === GOLD) {
        for (const g of byKind.get(GOLD) || []) add(PRI_GOLD, g, null);
      } else {
        for (const m of byKind.get(metal) || [])
          for (const q of byKind.get(QUICKSILVER) || []) add(PRI_METAL, m, q);
      }
      break;   // only the lowest remaining metal is eligible
    }
    scored.sort((p, q) => q[0] - p[0]);
    return scored;
  }

  /* Runtimes are heavy-tailed: nearly every board falls in a few dozen states,
   * but occasionally one choice near the root buries the search. So rounds of
   * doubling budgets, re-shuffling tie-breaks each round -- and the set of dead
   * states is kept across rounds, since deadness does not depend on the order a
   * state was reached in. That keeps the search complete. */
  solve(maxNodes = 400000, firstPass = 20000, seed = 1) {
    const dead = new Set();
    let total = 0, budget = Math.min(firstPass, maxNodes), attempt = 0;

    for (;;) {
      const counts = this.counts.slice();
      const path = [];
      this.nodes = 0;
      this.budget = budget;
      this.outOfBudget = false;
      this.random = attempt ? mulberry32(seed + attempt) : null;

      const found = this.search(this.start, counts, dead, path);
      total += this.nodes;
      if (found) {
        return {
          moves: path.map(([a, b]) => ({
            a, b, kindA: this.types[a], kindB: b === null ? null : this.types[b],
          })),
          nodes: total, exhausted: false, solved: true,
        };
      }
      if (!this.outOfBudget) return { moves: null, nodes: total, exhausted: true, solved: false };
      if (total >= maxNodes) return { moves: null, nodes: total, exhausted: false, solved: false };
      attempt++;
      budget = Math.min(budget * 2, maxNodes - total);
    }
  }

  search(occ, counts, dead, path) {
    if (occ === 0n) return true;
    if (dead.has(occ)) return false;
    if (this.nodes >= this.budget) { this.outOfBudget = true; return false; }
    this.nodes++;
    if (!this.feasible(counts)) { dead.add(occ); return false; }

    let moves = this.moves(occ, counts);
    if (this.random) {
      moves = moves.map(([score, a, b]) => [score + this.random() * RESTART_JITTER, a, b]);
      moves.sort((p, q) => q[0] - p[0]);
    }

    for (const [, a, b] of moves) {
      counts[this.types[a]]--;
      let next = occ & ~(1n << BigInt(a));
      if (b !== null) { counts[this.types[b]]--; next &= ~(1n << BigInt(b)); }
      path.push([a, b]);

      if (this.search(next, counts, dead, path)) return true;

      path.pop();
      counts[this.types[a]]++;
      if (b !== null) counts[this.types[b]]++;
      if (this.outOfBudget) return false;
    }
    dead.add(occ);
    return false;
  }
}

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function solve(board, maxNodes = 400000) { return new Solver(board).solve(maxNodes); }

/* ---- interactive game -------------------------------------------------- */

class Game {
  constructor(board) {
    this.start = board.clone();
    this.board = board.clone();
    this.moves = [];
    this.selected = null;
  }

  tally() {
    const counts = new Array(GOLD + 1).fill(0);
    for (const m of this.board.cells) if (m) counts[m]++;
    return counts;
  }

  partners(i) {
    if (!this.board.cells[i] || !this.board.isFree(i)) return [];
    const counts = this.tally();
    const a = this.board.cells[i];
    return this.board.freeCells().filter(
      (j) => j !== i && matches(a, this.board.cells[j], counts)
    );
  }

  playable() {
    const counts = this.tally();
    return this.board.freeCells().filter(
      (i) => isSolo(this.board.cells[i], counts) || this.partners(i).length
    );
  }

  won() { return this.board.marbleCount() === 0; }

  // Why a marble cannot be picked up. The reasons differ, and saying which is
  // the point: it is where a rule bug would show.
  refusal(i) {
    const marble = this.board.cells[i];
    if (!this.board.isFree(i))
      return `${NAMES[marble]} is blocked — it needs 3 contiguous empty neighbours.`;
    const counts = this.tally();
    if (METALS.includes(marble)) {
      const lowest = lowestMetal(counts);
      if (marble !== lowest)
        return `${NAMES[marble]} cannot go yet — metals transmute in order, and ${NAMES[lowest]} is still on the board.`;
      if (marble !== GOLD && !this.partners(i).length)
        return `No free quicksilver to pair with ${NAMES[marble]}.`;
    } else if (!this.partners(i).length) {
      return `Nothing on the board currently matches this ${NAMES[marble]}.`;
    }
    return null;
  }

  clear(a, b) {
    this.moves.push({ a, b, kindA: this.board.cells[a], kindB: b === null ? null : this.board.cells[b] });
    this.board.cells[a] = 0;
    if (b !== null) this.board.cells[b] = 0;
    this.selected = null;
  }

  click(i) {
    if (!this.board.cells[i]) { this.selected = null; return { ok: true, message: "" }; }
    const marble = this.board.cells[i];
    if (this.selected === i) { this.selected = null; return { ok: true, message: "" }; }

    if (this.selected !== null && this.partners(this.selected).includes(i)) {
      const other = this.board.cells[this.selected];
      this.clear(this.selected, i);
      return { ok: true, cleared: true, message: `Cleared ${NAMES[other]} + ${NAMES[marble]}.` };
    }

    const refusal = this.refusal(i);
    if (refusal) { this.selected = null; return { ok: false, message: refusal }; }
    if (isSolo(marble, this.tally())) {
      this.clear(i, null);
      return { ok: true, cleared: true, message: "Gold cleared on its own." };
    }
    this.selected = i;
    const [row, col] = ROWCOL[i];
    return { ok: true, message: `${NAMES[marble]} (r${row + 1},c${col + 1}) selected.` };
  }

  undo() {
    const move = this.moves.pop();
    if (!move) return false;
    this.board.cells[move.a] = move.kindA;
    if (move.b !== null) this.board.cells[move.b] = move.kindB;
    this.selected = null;
    return true;
  }

  reset() { this.board = this.start.clone(); this.moves = []; this.selected = null; }
}

/* ---- generating a solvable board --------------------------------------- */

/* Built backwards: pairs of a solution are placed onto an empty board in
 * reverse order, each required to land free, which is exactly the condition the
 * forward removal would have needed. Every board therefore has a solution. */
function randomSolvableBoard(random = Math.random) {
  for (let attempt = 0; attempt < 80; attempt++) {
    const board = buildBoard(random);
    if (board) return board;
  }
  throw new Error("board generation failed");
}

function shuffled(items, random) {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function pairPlan(random) {
  // Metals are forced: lead goes first and gold last, so in reverse the chain
  // is placed gold-first. Everything else may interleave freely.
  const chain = [[GOLD, null]];
  for (const metal of [SILVER, COPPER, IRON, TIN, LEAD]) chain.push([metal, QUICKSILVER]);

  // Each element must give up an even number of its marbles to salt, or one is
  // left with no partner.
  let saltsLeft = 4;
  const others = [];
  const remaining = { [FIRE]: 8, [WATER]: 8, [EARTH]: 8, [AIR]: 8 };
  for (const cardinal of shuffled(CARDINALS, random)) {
    if (saltsLeft >= 2 && random() < 0.35) {
      const take = 2 * (1 + Math.floor(random() * (saltsLeft / 2)));
      saltsLeft -= take;
      remaining[cardinal] -= take;
      for (let k = 0; k < take; k++) others.push([cardinal, SALT]);
    }
  }
  for (let k = 0; k < Math.floor(saltsLeft / 2); k++) others.push([SALT, SALT]);
  for (const [cardinal, count] of Object.entries(remaining))
    for (let k = 0; k < count / 2; k++) others.push([Number(cardinal), Number(cardinal)]);
  for (let k = 0; k < 4; k++) others.push([VITAE, MORS]);

  const mixed = shuffled(others, random);
  const slots = new Set(shuffled([...Array(chain.length + mixed.length).keys()], random)
    .slice(0, chain.length));
  const merged = [];
  let ci = 0, oi = 0;
  for (let position = 0; position < chain.length + mixed.length; position++) {
    merged.push(slots.has(position) ? chain[ci++] : mixed[oi++]);
  }
  return merged;
}

function buildBoard(random) {
  const board = Board.empty();
  let empties = [...Array(N_CELLS).keys()];
  const depth = (i) => {
    const [q, r] = CELLS[i];
    return Math.max(Math.abs(q), Math.abs(r), Math.abs(q + r));
  };

  for (const [kindA, kindB] of pairPlan(random)) {
    // isFree only inspects a cell's neighbours, so it answers the question for
    // an empty cell too. The central bias keeps boards clustered like a real deal.
    const candidates = (exclude) => empties
      .filter((i) => i !== exclude && board.isFree(i))
      .map((i) => [depth(i) + random(), i])
      .sort((p, q) => p[0] - q[0])
      .map(([, i]) => i)
      .slice(0, 30);

    let placed = false;
    for (const a of candidates(null)) {
      board.cells[a] = kindA;
      if (kindB === null) { empties = empties.filter((i) => i !== a); placed = true; break; }
      for (const b of candidates(a)) {
        board.cells[b] = kindB;
        // Placing b can block a if they end up adjacent, so re-check both.
        if (board.isFree(a) && board.isFree(b)) {
          empties = empties.filter((i) => i !== a && i !== b);
          placed = true;
          break;
        }
        board.cells[b] = 0;
      }
      if (placed) break;
      board.cells[a] = 0;
    }
    if (!placed) return null;
  }
  return board;
}

if (typeof module !== "undefined") {
  module.exports = {
    Board, Game, Solver, solve, parseBoard, randomSolvableBoard, matches, isSolo,
    CELLS, ROWCOL, ROW_LENGTHS, NEIGHBOURS, N_CELLS, CHARS, NAMES, FROM_CHAR,
    STANDARD_COUNTS, CARDINALS, METALS, indexOf,
    FIRE, WATER, EARTH, AIR, SALT, VITAE, MORS, QUICKSILVER,
    LEAD, TIN, IRON, COPPER, SILVER, GOLD, EMPTY,
  };
}
