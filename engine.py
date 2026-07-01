"""
Fully automated Bingo game engine.
- Lobbies auto-start when MIN_PLAYERS join
- Numbers auto-draw every DRAW_INTERVAL seconds
- Winners auto-detected, prizes auto-paid
- Lobby auto-resets for next round
"""
import random, time, threading, uuid, json
from datetime import datetime
from cards import COLUMNS, load_cards, card_all_numbers
import data as db

MIN_PLAYERS = 2
DRAW_INTERVAL = 5       # seconds between each number draw
COUNTDOWN = 30          # seconds before game starts after min players reached

_games = {}             # game_id -> GameRoom
_lobby_lock = threading.Lock()
_on_draw_callbacks = []  # list of async funcs to notify Telegram


def register_draw_callback(fn):
    _on_draw_callbacks.append(fn)


def _fire_callbacks(event, payload):
    import asyncio
    for fn in _on_draw_callbacks:
        try:
            asyncio.run_coroutine_threadsafe(fn(event, payload), _get_loop())
        except Exception:
            pass

_loop = None
def set_event_loop(loop):
    global _loop
    _loop = loop

def _get_loop():
    return _loop


def col_letter(n):
    if n <= 15: return "B"
    if n <= 30: return "I"
    if n <= 45: return "N"
    if n <= 60: return "G"
    return "O"


class GameRoom:
    def __init__(self, bet, game_id=None):
        self.game_id = game_id or str(uuid.uuid4())[:8]
        self.bet = bet
        self.players = {}        # user_id -> PlayerSlot
        self.drawn = []
        self.remaining = list(range(1, 76))
        random.shuffle(self.remaining)
        self.status = "waiting"  # waiting | countdown | running | finished
        self.winners = []
        self.prize_per_winner = 0
        self.start_time = None
        self.countdown_end = None
        self._draw_timer = None
        self._countdown_timer = None
        self._card_pool = load_cards()
        self._used_card_indices = set()

    def _pick_card(self):
        available = [i for i in range(len(self._card_pool)) if i not in self._used_card_indices]
        if not available:
            raise RuntimeError("No more cards available")
        idx = random.choice(available)
        self._used_card_indices.add(idx)
        return self._card_pool[idx]

    def add_player(self, user_id, name):
        uid = str(user_id)
        if uid in self.players:
            return self.players[uid]
        card = self._pick_card()
        slot = {"user_id": uid, "name": name, "card": card, "marked": [], "won": False}
        self.players[uid] = slot
        if self.status == "waiting" and len(self.players) >= MIN_PLAYERS:
            self._begin_countdown()
        return slot

    def _begin_countdown(self):
        self.status = "countdown"
        self.countdown_end = time.time() + COUNTDOWN
        _fire_callbacks("countdown", {"game_id": self.game_id, "bet": self.bet, "seconds": COUNTDOWN})
        self._countdown_timer = threading.Timer(COUNTDOWN, self._start_game)
        self._countdown_timer.daemon = True
        self._countdown_timer.start()

    def _start_game(self):
        self.status = "running"
        self.start_time = time.time()
        total_pot = self.bet * len(self.players)
        db.add_to_jackpot(self.bet, total_pot * 0.05)  # 5% to jackpot
        self.prize_per_winner = int(total_pot * 0.90)  # 90% to winner(s), 5% house
        _fire_callbacks("start", {"game_id": self.game_id, "bet": self.bet,
                                   "players": len(self.players), "prize": self.prize_per_winner})
        self._schedule_draw()

    def _schedule_draw(self):
        self._draw_timer = threading.Timer(DRAW_INTERVAL, self._auto_draw)
        self._draw_timer.daemon = True
        self._draw_timer.start()

    def _auto_draw(self):
        if self.status != "running" or not self.remaining:
            self._finish()
            return
        num = self.remaining.pop()
        self.drawn.append(num)
        # mark for all players
        for slot in self.players.values():
            card_nums = card_all_numbers(slot["card"])
            if num in card_nums:
                slot["marked"].append(num)
        _fire_callbacks("draw", {"game_id": self.game_id, "bet": self.bet, "number": num,
                                  "col": col_letter(num), "drawn_count": len(self.drawn)})
        new_winners = self._check_winners()
        if new_winners:
            self._finish(new_winners)
        elif not self.remaining:
            self._finish()
        else:
            self._schedule_draw()

    def _check_winners(self):
        new_winners = []
        for slot in self.players.values():
            if slot["won"]:
                continue
            if self._has_bingo(slot):
                slot["won"] = True
                new_winners.append(slot)
        return new_winners

    def _has_bingo(self, slot):
        marked = set(slot["marked"])
        card = slot["card"]
        def is_marked(col, row):
            v = card[col][row]
            return v == "FREE" or v in marked
        # rows
        for row in range(5):
            if all(is_marked(c, row) for c in COLUMNS):
                return True
        # cols
        for c in COLUMNS:
            if all(is_marked(c, r) for r in range(5)):
                return True
        # diagonals
        if all(is_marked(COLUMNS[i], i) for i in range(5)):
            return True
        if all(is_marked(COLUMNS[i], 4-i) for i in range(5)):
            return True
        return False

    def _finish(self, winners=None):
        self.status = "finished"
        if self._draw_timer:
            self._draw_timer.cancel()
        if winners:
            self.winners = winners
            prize_each = self.prize_per_winner // len(winners)
            for w in winners:
                db.credit_balance(w["user_id"], prize_each, f"Bingo win ({self.bet} ETB game)")
                p = db.get_player(w["user_id"])
                if p:
                    d = db.load()
                    uid = w["user_id"]
                    d["players"][uid]["total_wins"] += 1
                    d["players"][uid]["total_winnings"] += prize_each
                    d["players"][uid]["games_played"] += 1
                    d["players"][uid]["wins"].append({
                        "amount": prize_each, "bet": self.bet,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M")
                    })
                    db.save(d)
            _fire_callbacks("win", {
                "game_id": self.game_id, "bet": self.bet,
                "winners": [{"name": w["name"], "user_id": w["user_id"]} for w in winners],
                "prize_each": prize_each,
            })
        else:
            _fire_callbacks("no_winner", {"game_id": self.game_id, "bet": self.bet})
        # record games_played for non-winners too
        for slot in self.players.values():
            if not slot["won"]:
                d = db.load()
                uid = slot["user_id"]
                if uid in d["players"]:
                    d["players"][uid]["games_played"] += 1
                db.save(d)
        # auto-reset lobby after 10 seconds
        threading.Timer(10, lambda: _reset_lobby(self.bet)).start()

    def get_state(self, user_id=None):
        uid = str(user_id) if user_id else None
        slot = self.players.get(uid) if uid else None
        return {
            "game_id": self.game_id,
            "bet": self.bet,
            "status": self.status,
            "player_count": len(self.players),
            "drawn": self.drawn,
            "countdown_left": max(0, int(self.countdown_end - time.time())) if self.countdown_end else 0,
            "prize": self.prize_per_winner,
            "winners": [w["name"] for w in self.winners],
            "card": slot["card"] if slot else None,
            "marked": slot["marked"] if slot else [],
            "won": slot["won"] if slot else False,
        }


def _reset_lobby(bet):
    with _lobby_lock:
        key = str(bet)
        _games.pop(_get_lobby_game_id(bet), None)
        d = db.load()
        d["lobby_waiting"][key] = None
        db.save(d)
        _fire_callbacks("lobby_reset", {"bet": bet})


def _get_lobby_game_id(bet):
    d = db.load()
    return d["lobby_waiting"].get(str(bet))


def get_or_create_lobby(bet):
    """Get the current waiting/running game for this bet, or create one."""
    with _lobby_lock:
        gid = _get_lobby_game_id(bet)
        if gid and gid in _games:
            return _games[gid]
        room = GameRoom(bet)
        _games[room.game_id] = room
        d = db.load()
        d["lobby_waiting"][str(bet)] = room.game_id
        db.save(d)
        return room


def get_game(game_id):
    return _games.get(game_id)


def join_lobby(user_id, name, bet):
    """Debit bet from wallet and add player to lobby. Returns (room, slot) or raises."""
    player = db.get_or_create_player(user_id, name)
    if player["balance"] < bet:
        raise ValueError(f"Insufficient balance. You need {bet} ETB but have {player['balance']} ETB.")
    room = get_or_create_lobby(bet)
    if room.status in ("running", "finished"):
        # game already started, put them in next lobby
        with _lobby_lock:
            d = db.load()
            d["lobby_waiting"][str(bet)] = None
            db.save(d)
        room = get_or_create_lobby(bet)
    uid = str(user_id)
    if uid in room.players:
        return room, room.players[uid]
    if not db.debit_balance(user_id, bet, f"Bet placed ({bet} ETB game)"):
        raise ValueError("Failed to debit balance.")
    slot = room.add_player(user_id, name)
    return room, slot


def all_lobby_states():
    """Returns status of all lobbies for home screen."""
    states = []
    d = db.load()
    for cfg in db.LOBBY_CONFIGS:
        bet = cfg["bet"]
        gid = d["lobby_waiting"].get(str(bet))
        room = _games.get(gid) if gid else None
        jackpot = db.get_jackpot(bet)
        states.append({
            "bet": bet,
            "label": cfg["label"],
            "bonus": cfg["bonus"],
            "jackpot": jackpot,
            "jackpot_target": cfg["jackpot_target"],
            "player_count": len(room.players) if room else 0,
            "status": room.status if room else "waiting",
            "prize": room.prize_per_winner if room else 0,
            "game_id": gid,
        })
    return states
