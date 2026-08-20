# Sigmar's Garden

A solver for the marble puzzle inside [Opus Magnum](https://www.zachtronics.com/opus-magnum/).
It takes a board and returns the moves that clear it — or proves that none exist.
It can also read the board off a screenshot, and play the real game by driving
your mouse.

**▶ [Play it at marvyn.com/sigmars-garden](https://marvyn.com/sigmars-garden/)** —
the rules, the search and the screenshot reader all ported to JavaScript, so you
can play a board, have it solved, or drop in a screenshot of the real game and
watch it read all 55 marbles back. Nothing to install.

```
python -m sigmar autoplay --games 64        # play the real game until 64 wins
python -m sigmar play                       # play in a browser, backed by the solver
python -m sigmar read shot.png --solve      # read a board off an image
python -m sigmar solve boards/screenshot.txt
```

---

## How the game works

Marbles sit on a hexagonal board of 91 cells. You clear them in matching pairs and
win by clearing the lot. Two rules do all the work.

**A marble is free only if three of its six neighbours are empty and touching.**
Not any three — three *in a row* around it. Two gaps on opposite sides do not
count. Space off the edge of the board counts as empty, which is why the rim
clears first and the middle is the fight.

**What matches what:**

- The four elements — fire, water, earth, air — pair with their own kind.
- Salt pairs with any element, or with another salt. It is the flexible one, and
  only four exist.
- Vitae and mors pair only with each other.
- The five metals go in order of transmutation — lead, tin, iron, copper, silver —
  each with a quicksilver. Tin cannot go while lead is still down.
- Gold clears alone, once it is the last metal standing.

A full deal is always the same multiset: eight of each element, four salt, four
vitae, four mors, five quicksilver, one of each metal. 55 marbles, and exactly 28
moves — 27 pairs and the gold. That count matters enormously later.

---

## The solver

A marble never moves, so its position implies its type. The whole state of a game
is therefore just *which cells are still occupied* — 91 bits, one integer. The
solver is a depth-first search over those integers with a memo of every state
proven unwinnable, so a dead end reached by a different move order is recognised
rather than re-explored.

Freedom is a lookup, not a calculation. Each cell's six neighbours are stored in
cyclic order, so "three contiguous empty spaces" is a window over a 64-entry
table. Directions running off the board point at a bit that is never set, so edge
cells count their missing sides as empty for free.

Two properties of the game drive the move ordering:

- **Removing marbles never blocks anything.** Freedom only ever increases, so the
  difficulty is not the order of moves but the *pairing* — spend a salt on an
  element that still had a partner of its own kind and you can orphan a marble
  twenty moves later. Salt is played last, and the search carries a parity check:
  each element left in an odd number must consume a salt, and the leftover salt
  has to pair with itself. Positions failing that are dead before the search
  notices.
- **Metals are forced.** Only the lowest metal can go, and only against
  quicksilver, so those moves have no alternative pairing and cost nothing to take
  early.

### Restarts, and why the jitter has to be big

Runtimes are heavy-tailed: the median board falls in 29 states, but occasionally
one choice near the root buries the search. So it runs in rounds of doubling node
budgets, re-shuffling tie-breaks each round, and *keeps* the set of dead states
across rounds — deadness does not depend on how a state was reached, so a restart
never repeats prior work, it only re-picks its gambles. The search stays complete,
and "no solution exists" still means it.

The size of the shuffle was the whole trick. With a small jitter, restarts only
reshuffled marbles *within* a class of move, kept re-walking the same doomed
opening, and the two worst boards in 300 needed over 1.5M states. Making it large
enough to reconsider which *kind* of move to open with brought both under 140k.

### Two good ideas that measurement threw out

- Scoring moves by how many marbles they actually unlock searched fewer states but
  cost more wall-clock than it saved.
- A fixpoint "could this marble ever be freed?" prune never fired once across 600
  boards — these boards die on pairing, not geometry — and cost 2–3× as much.

| boards | result | median | p99 | worst |
|---|---|---|---|---|
| 300 generated solvable | 300 solved | 0.6 ms | 984 ms | 11.9 s |
| 300 dense random deals | 68 solved, 232 *proven* unsolvable | 23 ms | 1.8 s | 2.8 s |

Test boards are built **backwards** — pairs of a solution placed onto an empty
board in reverse, each required to land free, which is exactly the condition the
forward removal would have needed. Every generated board therefore comes with a
guaranteed solution, so the solver is checked against a real oracle rather than
its own output.

---

## Reading a board off a screenshot

```
python -m sigmar read shot.png --solve
```

Transcribing 55 marbles by hand is miserable. Two problems: find the board, then
name every cell.

**Finding it.** The playing surface is far brighter than the metal around it, so a
brightness split isolates it — chosen per image, because a fixed threshold read
the reference perfectly and then lost 60 cells on a copy 20% brighter. A
morphological opening scrubs off the frame rails, which matters because the game
lights the board from the upper left: the left-hand rails pass the threshold and
the right-hand ones do not, and that asymmetry alone drags the centre 12px off. On
a whole desktop the board is *not* the biggest bright thing — a bright wallpaper
beats it — so candidates are ranked by shape: the wallpaper scored 37% skew and
0.55 fill against the board's 3% and 1.06.

**Naming the cells.** The game draws blocked marbles washed out and free ones
vivid, so the features must ignore that. Each cell is high-pass filtered — the
tile minus a blurred copy of itself — which throws away the marble's shading and
the cell's bevel and leaves the engraved glyph, then normalised so faint and bold
match. Colour enters only as a *direction*: the disc's colour minus the cell
background, scaled to unit length, because fading a marble toward the beige
changes that vector's length but not its heading.

Three things were found by measuring rather than assuming:

- Plain contrast-stretched brightness gets 96.5% of cells, and its mistakes are
  exactly fire↔earth and vitae↔salt — pairs the eye separates instantly. The
  radial shading dominates the tile and the glyph is a small part of the signal.
  High-pass filtering takes it to 100%.
- "Empty" has to be a template class, not a brightness threshold. Six blocked
  marbles — faint vitae, salt, quicksilver — sit *below* the brightest empty cell.
- Templates are stored at nine small offsets each. Detection lands within a pixel
  or two but not exactly, and matching a glyph against a template shifted 2px
  loses the outer ring of cells.

**The counts as a constraint.** A long run once died after one win: lead read as
mors — both dark, and lead has the fewest examples because it appears once per
board. A fresh deal holds a known multiset, so it is now read as an assignment
against those counts: the cell that *has* to be the lead is the one that fits it
least badly. Testing that caught something worse — imposing the counts always
succeeds, and on a board still animating in it invented 55 marbles from an empty
table, destroying the very check meant to validate the deal. So the constrained
result is only accepted when the plain read already saw a full board and needed at
most a couple of cells overruled.

The reader refuses rather than guessing when the board is too small to resolve,
cut off by the crop, or not a board at all.

---

## Playing the real game

```
python -m sigmar autoplay --games 10                  # until ten boards are won
python -m sigmar autoplay --dry-run --shot plan.png   # show the plan, click nothing
```

Screenshots the monitor, finds the board, reads it, solves it, and clicks the
pairs. Windows only — screen capture and mouse control go through `ctypes` rather
than an automation library, because the usual one scales its coordinates by the
display's DPI setting and so misses every click on a 4K screen.

Start with `--dry-run --shot plan.png`: nothing moves, and the annotated
screenshot puts a numbered ring on every marble it intends to click.

**It checks its own work.** The loop is deliberately not "solve once, then fire 56
clicks and hope". After every pair it re-reads those two cells and confirms they
went empty. A click that misses leaves the plan and the board disagreeing, and
every later click then lands on the wrong marble — so it retries, more slowly,
then abandons that board rather than clicking on blind. Failures say *which* thing
went wrong, because the fixes differ: "they read as Fire and Water, which do not
match — misread" versus "the pair still matches, so the click did not register".

It stops on any of three things: **Escape**, you **moving the mouse** (the pointer
somewhere it was not put means a person reached for it), or a failed check.

With `--games N` it plays until N boards are won, clicking NEW GAME between them.
The button and the WINS box are located from the board itself — the panel scales
as one piece, so they sit at fixed multiples of the hex size from the board
centre. After each board it confirms the game's own WINS counter changed; it never
reads the number, only sees the box change. That caught a real bug immediately,
when a board left already-cleared from an earlier run "solved in 0 moves" and was
scored as a win while the counter sat still.

### Making it quick

The first version took 1.37s a move, nearly 40s a board. Almost none of it was the
clicking:

- **Screen capture cost 110ms a go, whatever the size.** Pillow's `ImageGrab`
  rebuilds its device contexts on every call. Holding them open across calls takes
  the same grab to 6ms, pixel for pixel identical.
- **Verifying a move re-read all 91 cells** to look at two. Reading just those two
  costs 27ms against 370.
- **A fixed 450ms sleep** waited for the clearing animation. Polling finishes as
  soon as the pair actually goes.

That is **227ms a move, about six seconds a board**. What is left is the game's own
animations: dealing takes 4.3s and nothing can hurry it, so the loop waits by
watching the board stop changing — an idle board measures a frame-to-frame
difference of exactly zero. It waits for the animation to *start* first, because
the deal does not begin until 400ms after the click.

### One unattended run

```
64/64 wins over 65 boards, 1 abandoned
1,792 moves played, 1 click missed
7 of 65 deals needed the known counts to settle a cell
```

Every win confirmed by the game's own counter. The seven corrected deals are each
a board that, before that fix, would have ended the run.

---

## Layout

```
sigmar/
  marbles.py     marble kinds and the matching rules
  board.py       hex geometry, the freedom rule, the board file format
  solver.py      the search, and solution verification
  generator.py   boards built backwards, for testing
  game.py        interactive state: selection, clicking, undo
  vision.py      finding and reading a board in an image
  desktop.py     Windows screen capture and mouse control
  autoplay.py    the play-and-verify loop
  server.py      local web front end
  web/           its page
  templates.npz  marble templates cut from two real screenshots
web/             the browser port: same rules, search and reader in JavaScript
boards/          board files, and the two reference screenshots
tools/           template building and page assembly
tests/           113 tests
```

### Board file format

Eleven rows of single characters, one per cell, top to bottom, left to right. Row
lengths are 6 7 8 9 10 11 10 9 8 7 6 — 91 cells, 55 of them marbles. Indentation
is cosmetic and `#` starts a comment.

```
     . . . . . .
    f m v . . . .
   s a s e q w v e
  m q . a 2 w . f e
 . w . f a a v . e 4
. . e a m 6 a v f 1 .
 . . a w q w m f w .
  . . e f q e w . .
   . w a . . 3 . .
    . s q f f . .
     . e 5 s . .
```

`f`/`w`/`e`/`a` are the elements, `s` salt, `v` vitae, `m` mors, `q` quicksilver,
`1`–`6` the metals in transmutation order.

## Running it

Solving needs nothing but Python 3.10+. Reading screenshots needs `numpy`, `scipy`
and `pillow`; autoplay additionally needs Windows.

```
python -m pytest tests -q     # 113 tests
node web/test.cjs             # the JavaScript port, against the same facts
python tools/build_artifact.py    # rebuild the page from web/
```

`tools/build_artifact.py` writes `web/site/index.html`, which is what is
published at [marvyn.com/sigmars-garden](https://marvyn.com/sigmars-garden/) —
copy it to `sigmars-garden/index.html` in the `MarvynBailly.github.io` repo. It
is one self-contained file: the rules, the search, the reader and the templates
are all inside it, so it needs no server and no network.

The vision tests check that reading survives the ways *another* screenshot would
differ — 0.7×–3× rescaling, croppings, JPEG down to q=35, brightness, contrast and
saturation shifts — and that undersized, clipped and non-board images raise rather
than guess. The real evidence is cross-validation between the two reference
boards: both ship in `templates.npz`, so reading either back proves little, and the
test instead rebuilds the templates from one board alone and reads the *other*.
Both directions recover 91/91.

The autoplay tests stub out the screen and the mouse entirely — nothing in the
suite moves a real pointer. They check the decisions: which cells a move clicks,
that gold is a single click, that Escape and a hijacked pointer abort *before*
clicking, that a failed verification retries and then gives up rather than
carrying on, and that a dry run clicks nothing.

---

The puzzle, its artwork and its alchemical glyphs belong to
Zachtronics; this is a solver for it.
