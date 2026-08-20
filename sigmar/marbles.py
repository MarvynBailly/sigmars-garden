"""Marble types and the matching rules of Sigmar's Garden."""

from __future__ import annotations

from enum import IntEnum


class Marble(IntEnum):
    """A marble kind. Value 0 is reserved for "empty space"."""

    FIRE = 1
    WATER = 2
    EARTH = 3
    AIR = 4
    SALT = 5
    VITAE = 6
    MORS = 7
    QUICKSILVER = 8
    # Metals, in transmutation order. The numeric order matters: the solver
    # relies on LEAD < TIN < ... < GOLD to find the lowest remaining metal.
    LEAD = 9
    TIN = 10
    IRON = 11
    COPPER = 12
    SILVER = 13
    GOLD = 14


EMPTY = 0

CARDINALS = (Marble.FIRE, Marble.WATER, Marble.EARTH, Marble.AIR)
METALS = (
    Marble.LEAD,
    Marble.TIN,
    Marble.IRON,
    Marble.COPPER,
    Marble.SILVER,
    Marble.GOLD,
)

# One character per marble, used by the board file format.
CHARS = {
    Marble.FIRE: "f",
    Marble.WATER: "w",
    Marble.EARTH: "e",
    Marble.AIR: "a",
    Marble.SALT: "s",
    Marble.VITAE: "v",
    Marble.MORS: "m",
    Marble.QUICKSILVER: "q",
    Marble.LEAD: "1",
    Marble.TIN: "2",
    Marble.IRON: "3",
    Marble.COPPER: "4",
    Marble.SILVER: "5",
    Marble.GOLD: "6",
}
EMPTY_CHARS = ".-_"
EMPTY_CHAR = "."

FROM_CHAR = {c: m for m, c in CHARS.items()}
FROM_CHAR.update({c.upper(): m for c, m in FROM_CHAR.copy().items() if c.isalpha()})
FROM_CHAR.update({c: EMPTY for c in EMPTY_CHARS})

NAMES = {
    Marble.FIRE: "Fire",
    Marble.WATER: "Water",
    Marble.EARTH: "Earth",
    Marble.AIR: "Air",
    Marble.SALT: "Salt",
    Marble.VITAE: "Vitae",
    Marble.MORS: "Mors",
    Marble.QUICKSILVER: "Quicksilver",
    Marble.LEAD: "Lead",
    Marble.TIN: "Tin",
    Marble.IRON: "Iron",
    Marble.COPPER: "Copper",
    Marble.SILVER: "Silver",
    Marble.GOLD: "Gold",
}

# The marble multiset a standard Sigmar's Garden board is dealt from: 55 marbles.
STANDARD_COUNTS = {
    Marble.FIRE: 8,
    Marble.WATER: 8,
    Marble.EARTH: 8,
    Marble.AIR: 8,
    Marble.SALT: 4,
    Marble.VITAE: 4,
    Marble.MORS: 4,
    Marble.QUICKSILVER: 5,
    Marble.LEAD: 1,
    Marble.TIN: 1,
    Marble.IRON: 1,
    Marble.COPPER: 1,
    Marble.SILVER: 1,
    Marble.GOLD: 1,
}


def lowest_metal(counts) -> Marble | None:
    """The only metal that may currently be removed, or None if none are left.

    Metals must go in transmutation order, so at any moment exactly one metal
    is eligible: the lowest one still on the board.
    """
    for metal in METALS:
        if counts[metal]:
            return metal
    return None


def matches(a: Marble, b: Marble, counts) -> bool:
    """Whether marbles `a` and `b` may be cleared together.

    `counts` is the tally of marbles still on the board, needed only to decide
    whether a metal is currently at the head of the transmutation chain.
    """
    if a > b:
        a, b = b, a
    if a in CARDINALS:
        # Same cardinal element, or a cardinal plus salt.
        return a == b or b == Marble.SALT
    if a == Marble.SALT:
        return b == Marble.SALT
    if a == Marble.VITAE:
        return b == Marble.MORS
    if a == Marble.QUICKSILVER:
        return b in METALS and b is not Marble.GOLD and b == lowest_metal(counts)
    return False


def is_solo(marble: Marble, counts) -> bool:
    """Whether `marble` may be cleared on its own (only gold, once last)."""
    return marble == Marble.GOLD and lowest_metal(counts) == Marble.GOLD
