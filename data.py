"""
Persistent data layer. Stores everything in data.json.
Thread-safe via a single RLock.
"""
import json, os, time, threading, uuid, secrets, hashlib
from datetime import datetime

DATA_FILE = "data.json"
_lock = threading.RLock()

LOBBY_CONFIGS = [
    {"bet": 10,  "label": "10 ETB",  "jackpot_target": 500, "bonus": False},
    {"bet": 20,  "label": "20 ETB",  "jackpot_target": 500, "bonus": True},
    {"bet": 50,  "label": "50 ETB",  "jackpot_target": 500, "bonus": False},
    {"bet": 80,  "label": "80 ETB",  "jackpot_target": 500, "bonus": False},
    {"bet": 100, "label": "100 ETB", "jackpot_target": 500, "bonus": True},
    {"bet": 150, "label": "150 ETB", "jackpot_target": 500, "bonus": False},
    {"bet": 300, "label": "300 ETB", "jackpot_target": 500, "bonus": True},
]

DEFAULT_SETTINGS = {
    "commission_percent": 20,     # house cut of the total pot per game (e.g. 20 -> 2 ETB out of every 10 ETB bet)
    "jackpot_percent": 5,         # separate slice that feeds the progressive jackpot
    "signup_bonus": 10,           # ETB given once to every new player, non-withdrawable
    "min_players": 2,
    "countdown_seconds": 30,
    "draw_interval_seconds": 5,
    "win_patterns": ["row", "column", "diagonal", "corners"],  # which patterns count as a win
    "deposit_number": "0936414865",
    "deposit_name": "Amanuel Abiy",
    "maintenance_mode": False,
    "maintenance_message": "We're doing quick maintenance. Back soon!",
    "bots_enabled": True,   # master switch for lobby-filler bots (see bot_counts below)
}


def _default():
    return {
        "players": {},
        "deposit_requests": [],
        "withdraw_requests": [],
        "jackpots": {str(c["bet"]): 0 for c in LOBBY_CONFIGS},
        "games": {},
        "games_history": [],   # finished-game summaries for admin Game History view
        "lobby_waiting": {str(c["bet"]): None for c in LOBBY_CONFIGS},
        "settings": dict(DEFAULT_SETTINGS),
        "commission_ledger": [],   # per-game commission records for reporting
        "anticheat": {
            "device_map": {},      # device_id -> [user_ids]
            "ip_map": {},          # ip -> [user_ids]
            "flags": [],           # list of flag records
        },
        "admin_sessions": {},      # token -> expiry_ts
        "broadcasts": [],
        "banned": {},               # user_id -> reason
        # Per-lobby count of "seat filler" bots. Bots exist ONLY to help a
        # lobby reach min_players faster - they never cost real players
        # anything and are never eligible to win the pot (see engine.py).
        # They are always labeled "Bot" in the UI so nobody is misled.
        "bot_counts": {str(c["bet"]): 0 for c in LOBBY_CONFIGS},
        # Editable via Admin -> Bots. Only the display NAME is editable -
        # there is no win-probability control (see engine.py / README).
        "bot_names": list(DEFAULT_BOT_NAMES),
    }


DEFAULT_BOT_NAMES = ["Abebe", "Kebede", "Selam", "Meron", "Yared", "Liya", "Dawit", "Hana",
                     "Nardos", "Bereket", "Sara", "Mekdes", "Yonas", "Ruth", "Solomon", "Tigist",
                     "Henok", "Betelhem", "Natnael", "Eden"]


def load():
    with _lock:
        if not os.path.exists(DATA_FILE):
            d = _default()
            save(d)
            return d
        with open(DATA_FILE) as f:
            try:
                d = json.load(f)
                default = _default()
                for k, v in default.items():
                    if k not in d:
                        d[k] = v
                for k, v in DEFAULT_SETTINGS.items():
                    if k not in d.setdefault("settings", {}):
                        d["settings"][k] = v
                return d
            except Exception:
                return _default()


def save(data):
    with _lock:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, DATA_FILE)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_settings():
    return load()["settings"]


def update_settings(patch):
    with _lock:
        d = load()
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                d["settings"][k] = v
        save(d)
        return d["settings"]


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------
def get_or_create_player(user_id, name):
    uid = str(user_id)
    with _lock:
        d = load()
        if uid not in d["players"]:
            bonus = d["settings"].get("signup_bonus", 10)
            d["players"][uid] = {
                "name": name,
                "balance": bonus,
                "bonus_balance": bonus,   # portion of balance that can't be withdrawn until wagered
                "total_wins": 0,
                "total_winnings": 0,
                "games_played": 0,
                "transactions": [],
                "wins": [],
                "active_game": None,
                "language": "en",
                "sound": True,
                "voice": True,
                "auto_mark": True,
                "device_id": None,
                "last_ip": None,
                "banned": False,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            if bonus:
                d["players"][uid]["transactions"].append({
                    "type": "credit", "amount": bonus, "note": "Signup bonus (one-time, non-withdrawable)",
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M")
                })
            save(d)
        else:
            # backfill any new fields for existing players
            p = d["players"][uid]
            changed = False
            for field, default in (("language", "en"), ("sound", True), ("voice", True),
                                    ("auto_mark", True), ("bonus_balance", 0), ("banned", False),
                                    ("device_id", None), ("last_ip", None),
                                    ("last_seen", p.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M")))):
                if field not in p:
                    p[field] = default
                    changed = True
            if changed:
                save(d)
        return d["players"][uid]


def touch_active(user_id):
    """Marks a player as active right now - called on every app screen load
    so 'Active Users Today' in admin reflects real usage, not just signups."""
    uid = str(user_id)
    with _lock:
        d = load()
        if uid in d["players"]:
            d["players"][uid]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            save(d)


def get_player(user_id):
    d = load()
    return d["players"].get(str(user_id))


def set_player_prefs(user_id, **prefs):
    uid = str(user_id)
    with _lock:
        d = load()
        if uid not in d["players"]:
            return None
        for k in ("language", "sound", "voice", "auto_mark"):
            if k in prefs:
                d["players"][uid][k] = prefs[k]
        save(d)
        return d["players"][uid]


def credit_balance(user_id, amount, note="deposit"):
    uid = str(user_id)
    with _lock:
        d = load()
        if uid not in d["players"]:
            return False
        d["players"][uid]["balance"] += amount
        d["players"][uid]["transactions"].append({
            "type": "credit", "amount": amount, "note": note,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        save(d)
        return True


def debit_balance(user_id, amount, note="bet"):
    """Debits balance. Bonus balance is spent first (FIFO), so real cash stays
    protected the longest, then falls back to real balance."""
    uid = str(user_id)
    with _lock:
        d = load()
        if uid not in d["players"]:
            return False
        p = d["players"][uid]
        if p["balance"] < amount:
            return False
        p["balance"] -= amount
        bonus_used = min(p.get("bonus_balance", 0), amount)
        p["bonus_balance"] = p.get("bonus_balance", 0) - bonus_used
        p["transactions"].append({
            "type": "debit", "amount": amount, "note": note,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        save(d)
        return True


def withdrawable_amount(user_id):
    """Real-cash balance only - bonus money can never be withdrawn."""
    p = get_player(user_id)
    if not p:
        return 0
    return max(0, p["balance"] - p.get("bonus_balance", 0))


def ban_player(user_id, reason="Violation of terms"):
    uid = str(user_id)
    with _lock:
        d = load()
        if uid in d["players"]:
            d["players"][uid]["banned"] = True
        d["banned"][uid] = reason
        save(d)


def unban_player(user_id):
    uid = str(user_id)
    with _lock:
        d = load()
        if uid in d["players"]:
            d["players"][uid]["banned"] = False
        d["banned"].pop(uid, None)
        save(d)


def is_banned(user_id):
    d = load()
    return d["players"].get(str(user_id), {}).get("banned", False)


def adjust_balance_admin(user_id, amount, note="Admin adjustment"):
    """Admin can add or remove funds directly (amount can be negative)."""
    uid = str(user_id)
    with _lock:
        d = load()
        if uid not in d["players"]:
            return False
        d["players"][uid]["balance"] = max(0, d["players"][uid]["balance"] + amount)
        d["players"][uid]["transactions"].append({
            "type": "credit" if amount >= 0 else "debit", "amount": abs(amount), "note": note,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        save(d)
        return True


def list_players(search=None, limit=200):
    d = load()
    items = list(d["players"].items())
    if search:
        s = search.lower()
        items = [(uid, p) for uid, p in items if s in uid.lower() or s in p.get("name", "").lower()]
    items.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
    return [{"user_id": uid, **p} for uid, p in items[:limit]]


# ---------------------------------------------------------------------------
# Deposits / withdrawals (manual only - this is the ONLY way to add funds
# besides the one-time signup bonus)
# ---------------------------------------------------------------------------
def add_deposit_request(user_id, name, amount, method, reference):
    req = {
        "id": str(uuid.uuid4())[:8],
        "user_id": str(user_id),
        "name": name,
        "amount": amount,
        "method": method,
        "reference": reference,
        "status": "pending",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with _lock:
        d = load()
        d["deposit_requests"].append(req)
        save(d)
    return req


def add_withdraw_request(user_id, name, amount, method, account):
    req = {
        "id": str(uuid.uuid4())[:8],
        "user_id": str(user_id),
        "name": name,
        "amount": amount,
        "method": method,
        "account": account,
        "status": "pending",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    with _lock:
        d = load()
        d["withdraw_requests"].append(req)
        save(d)
    return req


def get_pending_deposits():
    d = load()
    return [r for r in d["deposit_requests"] if r["status"] == "pending"]


def get_pending_withdrawals():
    d = load()
    return [r for r in d["withdraw_requests"] if r["status"] == "pending"]


def get_all_deposits(limit=200):
    d = load()
    return list(reversed(d["deposit_requests"]))[:limit]


def get_all_withdrawals(limit=200):
    d = load()
    return list(reversed(d["withdraw_requests"]))[:limit]


def approve_deposit(req_id):
    with _lock:
        d = load()
        for r in d["deposit_requests"]:
            if r["id"] == req_id and r["status"] == "pending":
                r["status"] = "approved"
                save(d)
                credit_balance(r["user_id"], r["amount"], f"Deposit approved ({r['method']})")
                return r
    return None


def reject_deposit(req_id):
    with _lock:
        d = load()
        for r in d["deposit_requests"]:
            if r["id"] == req_id and r["status"] == "pending":
                r["status"] = "rejected"
                save(d)
                return r
    return None


def approve_withdrawal(req_id):
    with _lock:
        d = load()
        for r in d["withdraw_requests"]:
            if r["id"] == req_id and r["status"] == "pending":
                r["status"] = "approved"
                save(d)
                return r
    return None


def reject_withdrawal(req_id):
    """Rejecting refunds the already-debited amount back to the player."""
    with _lock:
        d = load()
        for r in d["withdraw_requests"]:
            if r["id"] == req_id and r["status"] == "pending":
                r["status"] = "rejected"
                save(d)
                credit_balance(r["user_id"], r["amount"], "Withdrawal rejected - refunded")
                return r
    return None


# ---------------------------------------------------------------------------
# Leaderboard / jackpot
# ---------------------------------------------------------------------------
def get_leaderboard(period="all"):
    """period: 'all' | 'daily' | 'weekly' | 'monthly'.
    For daily/weekly/monthly, wins are counted only within that rolling
    window (based on each win's own timestamp), not lifetime totals."""
    d = load()
    now = datetime.now()

    def in_period(time_str):
        if period == "all":
            return True
        try:
            wt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return False
        if period == "daily":
            return wt.date() == now.date()
        if period == "weekly":
            return (now - wt).days < 7 and (now - wt).total_seconds() >= 0
        if period == "monthly":
            return wt.year == now.year and wt.month == now.month
        return True

    players = []
    for uid, p in d["players"].items():
        if period == "all":
            total_wins = p["total_wins"]
            total_winnings = p["total_winnings"]
        else:
            wins_in_period = [w for w in p.get("wins", []) if in_period(w.get("time", ""))]
            if not wins_in_period:
                continue
            total_wins = len(wins_in_period)
            total_winnings = sum(w["amount"] for w in wins_in_period)
        if total_wins == 0 and total_winnings == 0:
            continue
        players.append({"name": p["name"], "total_wins": total_wins, "total_winnings": total_winnings})
    return sorted(players, key=lambda x: x["total_winnings"], reverse=True)[:20]


def get_jackpot(bet):
    d = load()
    return d["jackpots"].get(str(bet), 0)


def add_to_jackpot(bet, amount):
    with _lock:
        d = load()
        key = str(bet)
        d["jackpots"][key] = d["jackpots"].get(key, 0) + amount
        save(d)


def reset_jackpot(bet):
    with _lock:
        d = load()
        d["jackpots"][str(bet)] = 0
        save(d)


# ---------------------------------------------------------------------------
# Commission ledger (for admin revenue reporting)
# ---------------------------------------------------------------------------
def record_commission(game_id, bet, player_count, total_pot, commission_amount, jackpot_amount):
    with _lock:
        d = load()
        d["commission_ledger"].append({
            "game_id": game_id, "bet": bet, "players": player_count,
            "total_pot": total_pot, "commission": commission_amount,
            "jackpot_cut": jackpot_amount,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        save(d)


def commission_summary():
    d = load()
    ledger = d["commission_ledger"]
    total = sum(r["commission"] for r in ledger)
    today_str = datetime.now().strftime("%Y-%m-%d")
    today = sum(r["commission"] for r in ledger if r["time"].startswith(today_str))
    return {
        "total_commission": total,
        "today_commission": today,
        "total_games": len(ledger),
        "recent": list(reversed(ledger))[:50],
    }


def commission_by_day(days=30):
    """Day-by-day commission breakdown, most recent first - lets the admin
    browse earnings for any specific day rather than only a lifetime total."""
    d = load()
    ledger = d["commission_ledger"]
    by_day = {}
    for r in ledger:
        day = r["time"][:10]  # 'YYYY-MM-DD'
        entry = by_day.setdefault(day, {"date": day, "games": 0, "commission": 0, "jackpot_cut": 0, "total_pot": 0})
        entry["games"] += 1
        entry["commission"] += r["commission"]
        entry["jackpot_cut"] += r["jackpot_cut"]
        entry["total_pot"] += r["total_pot"]
    rows = sorted(by_day.values(), key=lambda x: x["date"], reverse=True)
    return rows[:days]


# ---------------------------------------------------------------------------
# Game history (persisted summary of every finished game, for the admin
# Players -> Game History view. Separate from the ephemeral in-memory
# GameRoom objects in engine.py, which disappear once a lobby resets.)
# ---------------------------------------------------------------------------
def record_game_history(record):
    with _lock:
        d = load()
        d["games_history"].append(record)
        save(d)


def get_games_history(limit=100, bet=None, start=None, end=None):
    d = load()
    rows = list(reversed(d.get("games_history", [])))
    if bet:
        rows = [r for r in rows if str(r.get("bet")) == str(bet)]
    if start:
        rows = [r for r in rows if r.get("date", "") >= start]
    if end:
        rows = [r for r in rows if r.get("date", "") <= end]
    return rows[:limit]


def games_history_count():
    d = load()
    return len(d.get("games_history", []))


# ---------------------------------------------------------------------------
# Player counts (admin -> Players summary stats)
# ---------------------------------------------------------------------------
def player_counts():
    d = load()
    total_users = len(d["players"])
    today_str = datetime.now().strftime("%Y-%m-%d")
    active_today = sum(1 for p in d["players"].values() if p.get("last_seen", "").startswith(today_str))
    return {
        "total_users": total_users,
        "active_today": active_today,
        "total_games": len(d.get("games_history", [])),
    }


# ---------------------------------------------------------------------------
# Financial report (admin -> Financial Report): profit = commission earned,
# shown alongside deposit/withdrawal cash flow for the same date range.
# ---------------------------------------------------------------------------
def financial_report(start=None, end=None):
    """start/end are 'YYYY-MM-DD' strings (inclusive). None = no bound."""
    d = load()

    def in_range(time_str):
        day = (time_str or "")[:10]
        if start and day < start:
            return False
        if end and day > end:
            return False
        return True

    ledger = [r for r in d["commission_ledger"] if in_range(r["time"])]
    commission_total = sum(r["commission"] for r in ledger)
    games_count = len(ledger)

    deposits = [r for r in d["deposit_requests"] if r["status"] == "approved" and in_range(r["time"])]
    deposits_total = sum(r["amount"] for r in deposits)

    withdrawals = [r for r in d["withdraw_requests"] if r["status"] == "approved" and in_range(r["time"])]
    withdrawals_total = sum(r["amount"] for r in withdrawals)

    # In-house profit = the commission the house actually kept. Deposits and
    # withdrawals are player cash flow, not house revenue - the prize pool and
    # jackpot payouts already belong to players, so they're shown separately
    # for context but are NOT subtracted here (they were never house money).
    in_house_profit = commission_total

    return {
        "start": start, "end": end,
        "commission_total": commission_total,
        "games_count": games_count,
        "in_house_profit": in_house_profit,
        "deposits_total": deposits_total,
        "deposits": list(reversed(deposits))[:200],
        "withdrawals_total": withdrawals_total,
        "withdrawals": list(reversed(withdrawals))[:200],
        "daily": commission_by_day(days=366) if not (start or end) else
                 [r for r in commission_by_day(days=366) if (not start or r["date"] >= start) and (not end or r["date"] <= end)],
    }


# ---------------------------------------------------------------------------
# Lobby-filler bots (fairness note: bots never receive prize money and never
# cost a real player anything - they only exist to help a quiet lobby reach
# min_players so real people don't wait around alone. See engine.py.)
# ---------------------------------------------------------------------------
def get_bot_settings():
    d = load()
    counts = dict(d.get("bot_counts", {}))
    for c in LOBBY_CONFIGS:
        counts.setdefault(str(c["bet"]), 0)
    return {"enabled": d["settings"].get("bots_enabled", True), "counts": counts,
            "names": get_bot_names()}


def update_bot_settings(enabled=None, counts=None):
    with _lock:
        d = load()
        if enabled is not None:
            d["settings"]["bots_enabled"] = bool(enabled)
        if counts:
            d.setdefault("bot_counts", {})
            for bet, n in counts.items():
                try:
                    d["bot_counts"][str(bet)] = max(0, int(n))
                except (TypeError, ValueError):
                    continue
        save(d)
    return get_bot_settings()


def get_bot_names():
    d = load()
    names = d.get("bot_names")
    return names if names else list(DEFAULT_BOT_NAMES)


def update_bot_names(names):
    """Admin-editable display names only - NOT win probability. See README."""
    with _lock:
        d = load()
        cleaned = [str(n).strip() for n in (names or []) if str(n).strip()]
        d["bot_names"] = cleaned if cleaned else list(DEFAULT_BOT_NAMES)
        save(d)
        return d["bot_names"]


# ---------------------------------------------------------------------------
# Anti-cheat: flag likely multi-accounting / suspicious behaviour
# ---------------------------------------------------------------------------
def register_device(user_id, device_id, ip):
    uid = str(user_id)
    with _lock:
        d = load()
        ac = d["anticheat"]
        if uid in d["players"]:
            d["players"][uid]["device_id"] = device_id
            d["players"][uid]["last_ip"] = ip

        new_flags = []
        if device_id:
            users = ac["device_map"].setdefault(device_id, [])
            if uid not in users:
                users.append(uid)
            if len(users) > 1:
                new_flags.append({
                    "type": "shared_device", "device_id": device_id, "user_ids": users[:],
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
        if ip:
            users = ac["ip_map"].setdefault(ip, [])
            if uid not in users:
                users.append(uid)
            if len(users) > 3:  # a handful of players sharing wifi is normal; many is suspicious
                new_flags.append({
                    "type": "shared_ip_many_accounts", "ip": ip, "user_ids": users[:],
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                })
        for f in new_flags:
            ac["flags"].append(f)
        save(d)
        return new_flags


def get_anticheat_flags(limit=100):
    d = load()
    return list(reversed(d["anticheat"]["flags"]))[:limit]


# ---------------------------------------------------------------------------
# Admin auth (simple token session, password comes from env ADMIN_PASSWORD)
# ---------------------------------------------------------------------------
def create_admin_session(ttl_seconds=8 * 3600):
    token = secrets.token_hex(24)
    with _lock:
        d = load()
        d["admin_sessions"][token] = time.time() + ttl_seconds
        # prune expired
        now = time.time()
        d["admin_sessions"] = {t: exp for t, exp in d["admin_sessions"].items() if exp > now}
        d["admin_sessions"][token] = time.time() + ttl_seconds
        save(d)
    return token


def check_admin_session(token):
    if not token:
        return False
    d = load()
    exp = d["admin_sessions"].get(token)
    return bool(exp and exp > time.time())


def revoke_admin_session(token):
    with _lock:
        d = load()
        d["admin_sessions"].pop(token, None)
        save(d)


# ---------------------------------------------------------------------------
# Broadcasts (admin -> all players, sent via Telegram)
# ---------------------------------------------------------------------------
def log_broadcast(message):
    with _lock:
        d = load()
        d["broadcasts"].append({"message": message, "time": datetime.now().strftime("%Y-%m-%d %H:%M")})
        save(d)


def dashboard_stats():
    d = load()
    players = d["players"]
    total_players = len(players)
    total_balance = sum(p["balance"] for p in players.values())
    total_deposited = sum(t["amount"] for p in players.values() for t in p["transactions"]
                           if t["type"] == "credit" and "Deposit approved" in t["note"])
    pending_dep = len([r for r in d["deposit_requests"] if r["status"] == "pending"])
    pending_wit = len([r for r in d["withdraw_requests"] if r["status"] == "pending"])
    comm = commission_summary()
    today_str = datetime.now().strftime("%Y-%m-%d")
    active_today = sum(1 for p in players.values() if p.get("last_seen", "").startswith(today_str))
    return {
        "total_players": total_players,
        "active_today": active_today,
        "total_wallet_balance": total_balance,
        "total_deposited": total_deposited,
        "pending_deposits": pending_dep,
        "pending_withdrawals": pending_wit,
        "total_commission": comm["total_commission"],
        "today_commission": comm["today_commission"],
        "total_games_played": len(d.get("games_history", [])) or comm["total_games"],
        "anticheat_flags": len(d["anticheat"]["flags"]),
        "banned_players": len(d["banned"]),
    }
