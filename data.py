"""
Persistent data layer. Stores everything in data.json.
Thread-safe via a single RLock.
"""
import json, os, time, threading, uuid
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

def _default():
    return {
        "players": {},
        "deposit_requests": [],
        "withdraw_requests": [],
        "jackpots": {str(c["bet"]): 0 for c in LOBBY_CONFIGS},
        "games": {},          # game_id -> game dict
        "lobby_waiting": {str(c["bet"]): None for c in LOBBY_CONFIGS},  # bet -> waiting game_id
    }

def load():
    with _lock:
        if not os.path.exists(DATA_FILE):
            return _default()
        with open(DATA_FILE) as f:
            try:
                d = json.load(f)
                # ensure keys exist
                for k, v in _default().items():
                    if k not in d:
                        d[k] = v
                return d
            except Exception:
                return _default()

def save(data):
    with _lock:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)

def get_or_create_player(user_id, name):
    uid = str(user_id)
    with _lock:
        d = load()
        if uid not in d["players"]:
            d["players"][uid] = {
                "name": name,
                "balance": 0,
                "total_wins": 0,
                "total_winnings": 0,
                "games_played": 0,
                "transactions": [],
                "wins": [],
                "active_game": None,
            }
            save(d)
        return d["players"][uid]

def get_player(user_id):
    d = load()
    return d["players"].get(str(user_id))

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
    uid = str(user_id)
    with _lock:
        d = load()
        if uid not in d["players"]:
            return False
        if d["players"][uid]["balance"] < amount:
            return False
        d["players"][uid]["balance"] -= amount
        d["players"][uid]["transactions"].append({
            "type": "debit", "amount": amount, "note": note,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        save(d)
        return True

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

def get_leaderboard():
    d = load()
    players = []
    for uid, p in d["players"].items():
        players.append({
            "name": p["name"],
            "total_wins": p["total_wins"],
            "total_winnings": p["total_winnings"],
        })
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
