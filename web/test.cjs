/* Checks the JavaScript port against the same facts the Python suite asserts.
 * Run from the repository root:  node web/test.cjs
 */
const fs = require("fs");
const path = require("path");
const S = require("./sigmar.js");

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  ok   ${label}${detail ? "  " + detail : ""}`);
  } else {
    console.log(`  FAIL ${label}${detail ? "  " + detail : ""}`);
    failures++;
  }
}

const root = path.join(__dirname, "..");

console.log("the two reference boards");
for (const [name, expected] of [["screenshot", 28], ["screenshot2", 28]]) {
  const board = S.parseBoard(fs.readFileSync(path.join(root, "boards", name + ".txt"), "utf8"));
  const counts = board.counts();
  const countsOk = Object.entries(S.STANDARD_COUNTS).every(([k, v]) => counts[k] === v);
  check(`${name}: 55 marbles`, board.marbleCount() === 55);
  check(`${name}: counts match a standard deal`, countsOk);
  const started = Date.now();
  const result = S.solve(board);
  check(`${name}: solved in ${expected} moves`,
        result.solved && result.moves.length === expected,
        `${result.nodes} states, ${Date.now() - started}ms`);
}

console.log("the freedom rule");
{
  const centre = S.indexOf(5, 5);
  const alternating = S.Board.empty();
  alternating.cells[centre] = S.SALT;
  for (const j of [0, 2, 4]) alternating.cells[S.NEIGHBOURS[centre][j]] = S.SALT;
  check("three alternating neighbours leave no run of three", !alternating.isFree(centre));

  const bunched = S.Board.empty();
  bunched.cells[centre] = S.SALT;
  for (const j of [0, 1, 2]) bunched.cells[S.NEIGHBOURS[centre][j]] = S.SALT;
  check("the same three bunched together do", bunched.isFree(centre));

  const wrapping = S.Board.empty();
  wrapping.cells[centre] = S.SALT;
  for (const j of [1, 2, 3]) wrapping.cells[S.NEIGHBOURS[centre][j]] = S.SALT;
  check("the empty run may wrap around the ring", wrapping.isFree(centre));

  const corner = S.indexOf(0, 0);
  const edge = S.Board.empty();
  edge.cells[corner] = S.SALT;
  for (const n of S.NEIGHBOURS[corner]) if (n !== S.N_CELLS) edge.cells[n] = S.SALT;
  check("spaces off the board count as empty", edge.isFree(corner));
}

console.log("matching");
{
  const counts = new Array(S.GOLD + 1).fill(0);
  counts[S.LEAD] = 1; counts[S.TIN] = 1; counts[S.QUICKSILVER] = 2;
  check("lead pairs with quicksilver", S.matches(S.LEAD, S.QUICKSILVER, counts));
  check("tin cannot, while lead is still down", !S.matches(S.TIN, S.QUICKSILVER, counts));
  check("gold never pairs with quicksilver", !S.matches(S.GOLD, S.QUICKSILVER, counts));
  const goldOnly = new Array(S.GOLD + 1).fill(0);
  goldOnly[S.GOLD] = 1;
  check("gold clears alone once last", S.isSolo(S.GOLD, goldOnly));
}

console.log("unsolvable boards are proven, not merely unfound");
{
  const dead = S.Board.empty();
  dead.cells[S.indexOf(0, 0)] = S.FIRE;
  dead.cells[S.indexOf(10, 5)] = S.WATER;
  const result = S.solve(dead);
  check("fire + water: no solution exists", !result.solved && result.exhausted);
}

console.log("generated boards solve, and replay through the click path");
{
  let worstNodes = 0, slowest = 0, bad = 0;
  for (let seed = 0; seed < 40; seed++) {
    let state = (seed * 2654435761) % 4294967296;
    const random = () => {
      state = (state * 1664525 + 1013904223) % 4294967296;
      return state / 4294967296;
    };
    const board = S.randomSolvableBoard(random);
    const started = Date.now();
    const result = S.solve(board);
    slowest = Math.max(slowest, Date.now() - started);
    worstNodes = Math.max(worstNodes, result.nodes);
    if (!result.solved || result.moves.length !== 28) { bad++; continue; }
    // The UI plays by clicking, so a solution has to survive that.
    const game = new S.Game(board);
    for (const move of result.moves) {
      game.click(move.a);
      if (move.b !== null) game.click(move.b);
    }
    if (!game.won()) bad++;
  }
  check("40 boards solved in 28 moves and cleared by clicking", bad === 0,
        `worst ${worstNodes} states, slowest ${slowest}ms`);
}

console.log(failures ? `\n${failures} FAILED` : "\nall checks passed");
process.exit(failures ? 1 : 0);
