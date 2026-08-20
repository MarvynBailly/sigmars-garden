/* The page: draws the board, handles clicks, and wires up the solver and the
 * screenshot reader. Everything it needs is already in the page -- there is no
 * server behind this. */

const SVGNS = "http://www.w3.org/2000/svg";
const HEX = 30, MARBLE = HEX * 0.8;

/* Marble colours: [rim, body, glyph]. Data colours, deliberately separate from
 * the page accent -- they have to read against the dark plate in both themes. */
const PAINT = {
  [FIRE]:        ["#B02418", "#FF7A45", "#FFE0B8"],
  [WATER]:       ["#0C5C74", "#45D2E2", "#E2FBFF"],
  [EARTH]:       ["#2A751B", "#84E250", "#EAFFD4"],
  [AIR]:         ["#3A74B8", "#A6DCFF", "#F0FAFF"],
  [SALT]:        ["#8A8069", "#F4ECD8", "#FFFDF4"],
  [VITAE]:       ["#AF636B", "#FFC9C2", "#FFF2EE"],
  [MORS]:        ["#0C0C0C", "#4F4F4F", "#EFDCA8"],
  [QUICKSILVER]: ["#76828B", "#E9EFF3", "#FFFFFF"],
  [LEAD]:        ["#243039", "#6D8294", "#E0EAF3"],
  [TIN]:         ["#545427", "#BAB659", "#F6F2C4"],
  [IRON]:        ["#552017", "#A9553D", "#FFDCCB"],
  [COPPER]:      ["#743718", "#D38C4A", "#FFE4C2"],
  [SILVER]:      ["#424D5C", "#BCC6D2", "#F4FAFF"],
  [GOLD]:        ["#87560E", "#F2B94A", "#FFF5D2"],
};

// Metals and quicksilver wear their planetary glyphs, which every system font
// has. The elements are drawn, because the alchemical code points are not.
const GLYPH = {
  [QUICKSILVER]: "☿", [LEAD]: "♄", [TIN]: "♃", [IRON]: "♂",
  [COPPER]: "♀", [SILVER]: "☽", [GOLD]: "☉",
};
const UP = "M50,20 L78,68 L22,68 Z", DOWN = "M50,80 L22,32 L78,32 Z";
const STROKES = {
  [FIRE]: [UP],
  [WATER]: [DOWN],
  [AIR]: [UP, "M31,53 L69,53"],
  [EARTH]: [DOWN, "M31,47 L69,47"],
  [SALT]: ["M50,22 A28,28 0 1,1 49.9,22 Z", "M22,50 L78,50"],
  // Vitae is a triangle over a crossed stem; mors is the same, inverted.
  [VITAE]: ["M50,16 L74,54 L26,54 Z", "M50,54 L50,86", "M34,72 L66,72"],
  [MORS]: ["M50,84 L26,46 L74,46 Z", "M50,46 L50,14", "M34,28 L66,28"],
};

const board = document.getElementById("board");

/* One hidden, permanent home for the marble gradients. Putting them inside the
 * board's own <defs> meant every redraw destroyed them while copies made for
 * the legend and the tally survived, leaving fills pointing at nothing. */
const defs = (() => {
  const holder = document.createElementNS(SVGNS, "svg");
  holder.setAttribute("width", 0);
  holder.setAttribute("height", 0);
  holder.setAttribute("aria-hidden", "true");
  holder.style.position = "absolute";
  const node = document.createElementNS(SVGNS, "defs");
  holder.appendChild(node);
  document.body.appendChild(holder);
  return node;
})();

function marbleNode(kind, radius) {
  const group = document.createElementNS(SVGNS, "g");
  const [rim, body, ink] = PAINT[kind];
  const id = "grad" + kind;
  if (!document.getElementById(id)) {
    const gradient = document.createElementNS(SVGNS, "radialGradient");
    gradient.id = id;
    gradient.setAttribute("cx", "35%");
    gradient.setAttribute("cy", "28%");
    [[0, body, "1"], [0.55, body, ".55"], [1, rim, "1"]].forEach(([offset, colour, alpha]) => {
      const stop = document.createElementNS(SVGNS, "stop");
      stop.setAttribute("offset", offset);
      stop.setAttribute("stop-color", colour);
      stop.setAttribute("stop-opacity", alpha);
      gradient.appendChild(stop);
    });
    defs.appendChild(gradient);
  }

  const disc = document.createElementNS(SVGNS, "circle");
  disc.setAttribute("r", radius);
  disc.setAttribute("fill", `url(#${id})`);
  disc.setAttribute("stroke", rim);
  disc.setAttribute("stroke-width", 1.5);
  group.appendChild(disc);

  if (STROKES[kind]) {
    const marks = document.createElementNS(SVGNS, "g");
    marks.setAttribute("transform", `scale(${radius / 50}) translate(-50,-50)`);
    for (const d of STROKES[kind]) {
      const path = document.createElementNS(SVGNS, "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", ink);
      path.setAttribute("stroke-width", 6);
      path.setAttribute("stroke-linejoin", "round");
      path.setAttribute("stroke-linecap", "round");
      marks.appendChild(path);
    }
    group.appendChild(marks);
  } else {
    const text = document.createElementNS(SVGNS, "text");
    text.textContent = GLYPH[kind];
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("y", radius * 0.42);
    text.setAttribute("font-size", radius * 1.25);
    text.setAttribute("fill", ink);
    group.appendChild(text);
  }
  return group;
}

function hexPoints(size) {
  const points = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i - 30);
    points.push(`${(size * Math.cos(angle)).toFixed(1)},${(size * Math.sin(angle)).toFixed(1)}`);
  }
  return points.join(" ");
}

/* ---- state ---- */

let game = null;
let hinted = new Set();
let doubted = new Set();
let plan = null;
let busy = false;
let stopped = false;

const layout = CELLS.map(([q, r]) => [
  Math.sqrt(3) * HEX * (q + r / 2),
  1.5 * HEX * r,
]);
{
  const xs = layout.map((p) => p[0]), ys = layout.map((p) => p[1]);
  const pad = HEX + 6;
  const minX = Math.min(...xs) - pad, minY = Math.min(...ys) - pad;
  const w = Math.max(...xs) - minX + pad, h = Math.max(...ys) - minY + pad;
  board.setAttribute("viewBox", `${minX} ${minY} ${w} ${h}`);
}

function say(text, tone = "") {
  const status = document.getElementById("status");
  status.textContent = text;
  status.className = tone;
}

function render() {
  board.textContent = "";

  const live = new Set(game.playable());
  const mates = new Set(game.selected === null ? [] : game.partners(game.selected));

  CELLS.forEach((_, i) => {
    const kind = game.board.cells[i];
    const cell = document.createElementNS(SVGNS, "g");
    cell.setAttribute("class", "cell" + (live.has(i) ? " live" : ""));
    cell.setAttribute("transform", `translate(${layout[i][0]},${layout[i][1]})`);

    const hex = document.createElementNS(SVGNS, "polygon");
    hex.setAttribute("points", hexPoints(HEX - 1.5));
    hex.setAttribute("class", "hex");
    cell.appendChild(hex);

    if (kind) {
      const marble = marbleNode(kind, MARBLE);
      // Blocked marbles are drawn faded, the way the game draws them.
      marble.setAttribute("class", "marble" + (live.has(i) ? "" : " locked"));
      cell.appendChild(marble);

      let ring = null;
      if (game.selected === i) ring = "ring pick";
      else if (mates.has(i)) ring = "ring mate";
      else if (hinted.has(i)) ring = "ring hint";
      else if (doubted.has(i)) ring = "ring doubt";
      if (ring) {
        const circle = document.createElementNS(SVGNS, "circle");
        circle.setAttribute("r", MARBLE + 3);
        circle.setAttribute("class", ring);
        cell.appendChild(circle);
      }
      cell.addEventListener("click", () => onClick(i));
    }
    board.appendChild(cell);
  });

  document.getElementById("left").textContent = game.board.marbleCount();
  document.getElementById("played").textContent = game.moves.length;
  document.getElementById("undo").disabled = busy || !game.moves.length;

  const tally = document.getElementById("tally");
  tally.textContent = "";
  for (const [code, want] of Object.entries(STANDARD_COUNTS)) {
    const kind = Number(code);
    const have = game.board.cells.reduce((n, m) => n + (m === kind ? 1 : 0), 0);
    const chip = document.createElement("div");
    chip.className = "chip" + (have === 0 ? " gone" : "");
    chip.title = NAMES[kind];
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("width", 24);
    svg.setAttribute("height", 24);
    svg.setAttribute("viewBox", "-12 -12 24 24");
    svg.appendChild(marbleNode(kind, 10));
    chip.appendChild(svg);
    chip.appendChild(document.createTextNode(`${have}/${want}`));
    tally.appendChild(chip);
  }
}

function onClick(i) {
  if (busy) return;
  hinted.clear();
  doubted.clear();
  const result = game.click(i);
  render();
  if (game.won()) say(`Cleared, in ${game.moves.length} moves.`, "good");
  else say(result.message, result.ok ? "" : "bad");
}

/* ---- solver-backed controls ---- */

function showPlan(moves, upto) {
  const box = document.getElementById("plan");
  box.textContent = "";
  moves.forEach((move, n) => {
    const line = document.createElement("div");
    const at = (cell) => {
      const [row, col] = ROWCOL[cell];
      return `r${row + 1}c${col + 1}`;
    };
    line.textContent = `${String(n + 1).padStart(2)}. ${NAMES[move.kindA]} ${at(move.a)}`
      + (move.b === null ? "  (alone)" : ` + ${NAMES[move.kindB]} ${at(move.b)}`);
    if (n < upto) line.className = "done";
    else if (n === upto) line.className = "now";
    box.appendChild(line);
  });
  const now = box.querySelector(".now");
  if (now) now.scrollIntoView({ block: "nearest" });
}

function setBusy(state) {
  busy = state;
  for (const id of ["new", "reset", "hint", "check", "pick"]) {
    document.getElementById(id).disabled = state;
  }
  document.getElementById("undo").disabled = state || !game.moves.length;
  document.getElementById("autoplay").textContent = state ? "Stop" : "Solve it for me";
}

/* Yield to the browser so the status text paints before a search starts. */
const breathe = () => new Promise((resolve) => setTimeout(resolve, 16));

async function solveNow() {
  await breathe();
  return solve(game.board);
}

async function newBoard() {
  hinted.clear(); doubted.clear();
  document.getElementById("plan").textContent = "";
  say("Dealing…");
  await breathe();
  game = new Game(randomSolvableBoard());
  render();
  say("A fresh board. Click a lit marble, then a matching one.");
}

document.getElementById("new").addEventListener("click", newBoard);

document.getElementById("reset").addEventListener("click", () => {
  hinted.clear(); doubted.clear();
  game.reset();
  render();
  say("Back to the start of this board.");
});

document.getElementById("undo").addEventListener("click", () => {
  hinted.clear(); doubted.clear();
  game.undo();
  render();
  say("Took that one back.");
});

document.getElementById("hint").addEventListener("click", async () => {
  setBusy(true);
  say("Looking…");
  const result = await solveNow();
  setBusy(false);
  if (!result.solved) {
    say(result.exhausted
      ? "There is no way to finish from here — this position is lost."
      : "Could not find a way through in the time allowed.", "bad");
    return;
  }
  const first = result.moves[0];
  hinted = new Set(first.b === null ? [first.a] : [first.a, first.b]);
  doubted.clear();
  render();
  say(first.b === null
    ? `Take the ${NAMES[first.kindA]} on its own.`
    : `Pair the ${NAMES[first.kindA]} with the ${NAMES[first.kindB]}.`);
});

document.getElementById("check").addEventListener("click", async () => {
  setBusy(true);
  say("Searching…");
  const result = await solveNow();
  setBusy(false);
  if (result.solved) {
    showPlan(result.moves, -1);
    say(`Still winnable — ${result.moves.length} moves, found in ${result.nodes} states.`, "good");
  } else {
    say(result.exhausted
      ? `No. This position is lost, and that is proven, not guessed (${result.nodes} states searched).`
      : "Could not find a way through in the time allowed.", "bad");
  }
});

document.getElementById("autoplay").addEventListener("click", async () => {
  if (busy) { stopped = true; return; }
  setBusy(true);
  stopped = false;
  say("Searching…");
  const result = await solveNow();
  if (!result.solved) {
    setBusy(false);
    say(result.exhausted ? "There is no way to finish from here." : "No solution found in time.", "bad");
    return;
  }
  plan = result.moves;
  hinted.clear(); doubted.clear();
  say(`${plan.length} moves. Playing it out…`);

  for (let n = 0; n < plan.length; n++) {
    if (stopped) { say("Stopped."); break; }
    showPlan(plan, n);
    const move = plan[n];
    // Played through the same click path a person uses, so a clean run is a
    // check on the solution move by move, not a replay of the search.
    game.click(move.a);
    if (move.b !== null) game.click(move.b);
    render();
    await new Promise((resolve) => setTimeout(resolve, 170));
  }
  setBusy(false);
  if (game.won()) {
    showPlan(plan, plan.length);
    say(`Cleared, in ${game.moves.length} moves.`, "good");
  }
});

/* ---- reading a screenshot ---- */

function readImage(file) {
  if (!file || !file.type.startsWith("image/")) { say("That is not an image.", "bad"); return; }
  setBusy(true);
  say("Reading the board…");
  const reader = new FileReader();
  reader.onload = () => {
    const image = new Image();
    image.onload = async () => {
      await breathe();
      try {
        const result = readBoardFromImage(image, {
          cells: CELLS, fromChar: FROM_CHAR, standardCounts: STANDARD_COUNTS,
          chars: CHARS, expectFresh: true,
        });
        const found = new Board(result.cells);
        game = new Game(found);
        hinted.clear();
        doubted = new Set(result.uncertain);
        document.getElementById("plan").textContent = "";
        render();

        const counts = found.counts();
        const off = Object.entries(STANDARD_COUNTS)
          .filter(([k, v]) => (counts[k] || 0) !== v)
          .map(([k, v]) => `${NAMES[k]} ${counts[k] || 0}/${v}`);
        if (off.length) {
          say(`Read ${found.marbleCount()} marbles, but the counts are off: ${off.join(", ")}. `
            + `Ringed cells were close calls.`, "bad");
        } else if (result.uncertain.length) {
          say(`Read ${found.marbleCount()} marbles and the counts match a full deal. `
            + `${result.uncertain.length} cell(s) were a close call — ringed in red.`);
        } else {
          say(`Read all ${found.marbleCount()} marbles cleanly, and the counts match a full deal.`, "good");
        }
      } catch (err) {
        say(err.message, "bad");
      }
      setBusy(false);
    };
    image.onerror = () => { say("Could not decode that image.", "bad"); setBusy(false); };
    image.src = reader.result;
  };
  reader.onerror = () => { say("Could not read that file.", "bad"); setBusy(false); };
  reader.readAsDataURL(file);
}

document.getElementById("pick").addEventListener("click", () => document.getElementById("file").click());
document.getElementById("file").addEventListener("change", (event) => readImage(event.target.files[0]));

document.addEventListener("paste", (event) => {
  const item = [...(event.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
  if (!item) return;
  event.preventDefault();
  readImage(item.getAsFile());
});

const drop = document.getElementById("drop");
for (const type of ["dragenter", "dragover"]) {
  drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("hot"); });
}
for (const type of ["dragleave", "drop"]) {
  drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.remove("hot"); });
}
drop.addEventListener("drop", (e) => readImage(e.dataTransfer.files[0]));
document.body.addEventListener("dragover", (e) => e.preventDefault());
document.body.addEventListener("drop", (e) => { e.preventDefault(); readImage(e.dataTransfer.files[0]); });

/* ---- the rules legend ---- */

{
  const legend = document.getElementById("legend");
  for (const kind of [FIRE, WATER, EARTH, AIR, SALT, VITAE, MORS, QUICKSILVER, GOLD]) {
    const row = document.createElement("div");
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("width", 26);
    svg.setAttribute("height", 26);
    svg.setAttribute("viewBox", "-13 -13 26 26");
    svg.appendChild(marbleNode(kind, 11));
    row.appendChild(svg);
    const label = document.createElement("span");
    label.textContent = NAMES[kind];
    row.appendChild(label);
    legend.appendChild(row);
  }
}

/* ---- start ---- */

loadTemplates(TEMPLATE_DATA);
game = new Game(randomSolvableBoard());
render();
say("Click a lit marble, then a matching one. Faded marbles are blocked.");
