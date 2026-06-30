"""
Bingo card generation (75-ball, 5x5, B-I-N-G-O columns, free center).
Generates a pool of unique cards.
"""
import random
import json

COLUMN_RANGES = {
    "B": (1, 15),
    "I": (16, 30),
    "N": (31, 45),
    "G": (46, 60),
    "O": (61, 75),
}
COLUMNS = ["B", "I", "N", "G", "O"]


def generate_card():
    """Generate one 5x5 card as a dict of column -> list of 5 numbers (row order).
    Center of N column is 'FREE'."""
    card = {}
    for col in COLUMNS:
        lo, hi = COLUMN_RANGES[col]
        nums = random.sample(range(lo, hi + 1), 5)
        card[col] = nums
    card["N"][2] = "FREE"
    return card


def card_signature(card):
    """A hashable signature to detect duplicate cards regardless of generation order."""
    return tuple(tuple(card[c]) for c in COLUMNS)


def generate_unique_cards(n=200, max_attempts=100000):
    """Generate n unique bingo cards."""
    seen = set()
    cards = []
    attempts = 0
    while len(cards) < n and attempts < max_attempts:
        attempts += 1
        c = generate_card()
        sig = card_signature(c)
        if sig in seen:
            continue
        seen.add(sig)
        cards.append(c)
    if len(cards) < n:
        raise RuntimeError(f"Could only generate {len(cards)} unique cards out of {n} requested")
    return cards


def card_all_numbers(card):
    """Flat set of all numbers on a card (excluding FREE)."""
    nums = set()
    for col in COLUMNS:
        for v in card[col]:
            if v != "FREE":
                nums.add(v)
    return nums


def save_cards(cards, path="cards_pool.json"):
    with open(path, "w") as f:
        json.dump(cards, f)


def load_cards(path="cards_pool.json"):
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    random.seed()
    pool = generate_unique_cards(200)
    save_cards(pool, "cards_pool.json")
    print(f"Generated {len(pool)} unique cards -> cards_pool.json")
