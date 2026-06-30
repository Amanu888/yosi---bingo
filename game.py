"""
Bingo game engine: manages game rooms, the number caller, players' cards,
marking, and win detection. Pure logic, no Telegram dependencies, so it's
easy to test independently of the bot.
"""
import random
from cards import COLUMNS, load_cards, card_all_numbers


class Player:
    def __init__(self, user_id, name, card):
        self.user_id = user_id
        self.name = name
        self.card = card  # dict col -> list of 5 (numbers / "FREE")
        self.marked = set()  # numbers marked so far (subset of card numbers)
        self.won = False

    def mark(self, number):
        if number in card_all_numbers(self.card):
            self.marked.add(number)
            return True
        return False

    def is_marked(self, col, row):
        val = self.card[col][row]
        if val == "FREE":
            return True
        return val in self.marked

    def grid_marks(self):
        """5x5 boolean grid of marked state, rows x cols, indexed [row][col_index]."""
        grid = []
        for row in range(5):
            row_marks = []
            for col in COLUMNS:
                row_marks.append(self.is_marked(col, row))
            grid.append(row_marks)
        return grid

    def check_win(self, pattern="line"):
        """pattern: 'line' (any row/col/diagonal), 'full' (blackout)."""
        grid = self.grid_marks()  # grid[row][col_index]
        if pattern == "full":
            return all(all(r) for r in grid)

        # line pattern: any full row, full column, or either diagonal
        for row in grid:
            if all(row):
                return True
        for c in range(5):
            if all(grid[r][c] for r in range(5)):
                return True
        if all(grid[i][i] for i in range(5)):
            return True
        if all(grid[i][4 - i] for i in range(5)):
            return True
        return False


class BingoGame:
    """One game room. chat_id ties it to a Telegram chat."""

    def __init__(self, chat_id, pattern="line"):
        self.chat_id = chat_id
        self.pattern = pattern
        self.pool = load_cards("cards_pool.json")
        self.available_card_indices = list(range(len(self.pool)))
        random.shuffle(self.available_card_indices)
        self.players = {}  # user_id -> Player
        self.drawn_numbers = []  # order of calls
        self.remaining_numbers = list(range(1, 76))
        random.shuffle(self.remaining_numbers)
        self.started = False
        self.finished = False
        self.winners = []

    def add_player(self, user_id, name):
        if user_id in self.players:
            return self.players[user_id]
        if not self.available_card_indices:
            raise RuntimeError("No more cards available (200 card limit reached)")
        idx = self.available_card_indices.pop()
        card = self.pool[idx]
        player = Player(user_id, name, card)
        self.players[user_id] = player
        return player

    def remove_player(self, user_id):
        self.players.pop(user_id, None)

    def start(self):
        self.started = True

    def draw_number(self):
        if not self.remaining_numbers:
            return None
        num = self.remaining_numbers.pop()
        self.drawn_numbers.append(num)
        # auto-mark for everyone
        for p in self.players.values():
            p.mark(num)
        return num

    def check_winners(self):
        """Return list of players who have won and weren't already recorded."""
        new_winners = []
        for p in self.players.values():
            if not p.won and p.check_win(self.pattern):
                p.won = True
                new_winners.append(p)
                self.winners.append(p)
        return new_winners

    def render_card_text(self, player):
        """Plain-text rendering of a player's card with marks shown as brackets."""
        lines = ["  ".join(COLUMNS)]
        for row in range(5):
            cells = []
            for col in COLUMNS:
                val = player.card[col][row]
                marked = player.is_marked(col, row)
                label = "FR" if val == "FREE" else f"{val:>2}"
                cells.append(f"[{label}]" if marked else f" {label} ")
            lines.append(" ".join(cells))
        return "\n".join(lines)
