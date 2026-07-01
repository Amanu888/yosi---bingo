"""
Yosi Bingo - Full Server
Runs Flask (Mini App + API + Admin dashboard) + Socket.IO (real-time sync)
+ Telegram bot, all in one process.

Setup (Render environment variables):
  TELEGRAM_BOT_TOKEN   - from @BotFather
  WEBAPP_URL           - e.g. https://yosi-bingo.onrender.com
  ADMIN_ID             - your personal Telegram user ID (for deposit/withdraw alerts)
  ADMIN_PASSWORD       - password to log into the web admin dashboard at /admin.html
  PORT                 - provided automatically by Render

Render start command:
  python3 server.py
"""
import os, threading, logging, asyncio, time
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, leave_room
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import data as db
import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
ADMIN_ID = os.environ.get("ADMIN_ID", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme123")

# ---------------------------------------------------------------------------
# Flask + Socket.IO
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="webapp", static_url_path="")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "yosi-bingo-secret")
# threading async_mode keeps this compatible with the existing threading-based
# engine and the asyncio-based Telegram bot without needing eventlet/gevent
# monkey-patching. Fine for small/medium load; swap to eventlet+gunicorn if
# you outgrow it.
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


@app.route("/")
def index():
    return send_from_directory("webapp", "index.html")


@app.route("/admin.html")
def admin_page():
    return send_from_directory("webapp", "admin.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("webapp", path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr


def require_admin(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Admin-Token", "")
        if not db.check_admin_session(token):
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Public player API
# ---------------------------------------------------------------------------
@app.route("/api/player")
def api_player():
    uid = request.args.get("user_id")
    name = request.args.get("name", "Player")
    if not uid:
        return jsonify({"error": "Missing user_id"}), 400
    p = db.get_or_create_player(uid, name)
    if p.get("banned"):
        return jsonify({"error": "Your account has been suspended."}), 403
    return jsonify(p)


@app.route("/api/player/prefs", methods=["POST"])
def api_player_prefs():
    d = request.get_json(force=True)
    uid = d.get("user_id")
    if not uid:
        return jsonify({"error": "Missing user_id"}), 400
    p = db.set_player_prefs(uid, language=d.get("language"), sound=d.get("sound"),
                             voice=d.get("voice"), auto_mark=d.get("auto_mark"))
    if not p:
        return jsonify({"error": "Player not found"}), 404
    return jsonify(p)


@app.route("/api/settings/public")
def api_settings_public():
    s = db.get_settings()
    return jsonify({
        "deposit_number": s["deposit_number"],
        "deposit_name": s["deposit_name"],
        "win_patterns": s["win_patterns"],
        "maintenance_mode": s["maintenance_mode"],
        "maintenance_message": s["maintenance_message"],
        "signup_bonus": s["signup_bonus"],
        "min_players": s["min_players"],
        "countdown_seconds": s["countdown_seconds"],
    })


@app.route("/api/lobbies")
def api_lobbies():
    return jsonify(engine.all_lobby_states())


@app.route("/api/enter_lobby", methods=["POST"])
def api_enter_lobby():
    """View a lobby - live board, taken/free cards, everything - WITHOUT
    charging anything and WITHOUT requiring any balance. Anyone can look."""
    d = request.get_json(force=True)
    uid = d.get("user_id")
    name = d.get("name", "Player")
    bet = d.get("bet")
    device_id = d.get("device_id")
    if not uid or not bet:
        return jsonify({"error": "Missing fields"}), 400
    try:
        room = engine.enter_lobby(uid, name, int(bet), device_id=device_id, ip=client_ip())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"game_id": room.game_id, **room.get_state(uid)})


@app.route("/api/select_card", methods=["POST"])
def api_select_card():
    """Instantly debits the lobby stake for this card. Allowed only while
    the game is still waiting/counting down - never once it's running."""
    d = request.get_json(force=True)
    uid = d.get("user_id")
    name = d.get("name", "Player")
    bet = d.get("bet")
    card_number = d.get("card_number")
    device_id = d.get("device_id")
    if not uid or not bet or not card_number:
        return jsonify({"error": "Missing fields"}), 400
    try:
        room, slot = engine.select_card(uid, name, int(bet), int(card_number),
                                         device_id=device_id, ip=client_ip())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    socketio.emit("lobbies_changed", {}, room="home_room")
    socketio.emit("cards_changed", room.get_state(), room=room.game_id)
    return jsonify({"game_id": room.game_id, **room.get_state(uid)})


@app.route("/api/deselect_card", methods=["POST"])
def api_deselect_card():
    """Instantly refunds the stake for this card. Allowed only until the
    game actually starts (see engine.py)."""
    d = request.get_json(force=True)
    uid = d.get("user_id")
    bet = d.get("bet")
    card_number = d.get("card_number")
    if not uid or not bet or not card_number:
        return jsonify({"error": "Missing fields"}), 400
    try:
        room = engine.deselect_card(uid, int(bet), int(card_number))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    socketio.emit("lobbies_changed", {}, room="home_room")
    socketio.emit("cards_changed", room.get_state(), room=room.game_id)
    return jsonify({"game_id": room.game_id, **room.get_state(uid)})


@app.route("/api/game_state")
def api_game_state():
    gid = request.args.get("game_id")
    uid = request.args.get("user_id")
    room = engine.get_game(gid)
    if not room:
        return jsonify({"error": "Game not found"}), 404
    return jsonify(room.get_state(uid))


@app.route("/api/leaderboard")
def api_leaderboard():
    period = request.args.get("period", "all")
    if period not in ("all", "daily", "weekly", "monthly"):
        period = "all"
    return jsonify(db.get_leaderboard(period))


@app.route("/api/deposit", methods=["POST"])
def api_deposit():
    d = request.get_json(force=True)
    uid = d.get("user_id")
    name = d.get("name", "Player")
    amount = d.get("amount")
    method = d.get("method", "Telebirr")
    ref = d.get("reference", "")
    if not uid or not amount or not ref:
        return jsonify({"error": "Missing fields"}), 400
    db.get_or_create_player(uid, name)
    req = db.add_deposit_request(uid, name, int(amount), method, ref)
    _notify_admin(
        f"💰 New deposit request!\n"
        f"ID: {req['id']}\n"
        f"Player: {name} ({uid})\n"
        f"Amount: {amount} ETB via {method}\n"
        f"Ref: {ref}\n\n"
        f"Reply with: /approve {req['id']} to credit\n"
        f"Or: /reject {req['id']} to reject\n"
        f"(You can also manage this from the admin dashboard)"
    )
    socketio.emit("admin_alert", {"type": "deposit", "req": req}, room="admin_room")
    return jsonify({"message": f"✅ Deposit of {amount} ETB submitted! You'll be credited after confirmation."})


@app.route("/api/withdraw", methods=["POST"])
def api_withdraw():
    d = request.get_json(force=True)
    uid = d.get("user_id")
    name = d.get("name", "Player")
    amount = d.get("amount")
    method = d.get("method", "Telebirr")
    account = d.get("account", "")
    if not uid or not amount or not account:
        return jsonify({"error": "Missing fields"}), 400
    player = db.get_player(uid)
    amount = int(amount)
    withdrawable = db.withdrawable_amount(uid)
    if not player or withdrawable < amount:
        return jsonify({"error": f"Insufficient withdrawable balance. Available: {withdrawable} ETB "
                                  f"(signup bonus money can't be withdrawn)"}), 400
    req = db.add_withdraw_request(uid, name, amount, method, account)
    db.debit_balance(uid, amount, f"Withdrawal ({method})")
    _notify_admin(
        f"⬆ Withdrawal request!\n"
        f"ID: {req['id']}\n"
        f"Player: {name} ({uid})\n"
        f"Amount: {amount} ETB via {method}\n"
        f"To: {account}\n\n"
        f"Send money then: /approvew {req['id']}"
    )
    socketio.emit("admin_alert", {"type": "withdrawal", "req": req}, room="admin_room")
    return jsonify({"message": f"✅ Withdrawal of {amount} ETB requested. Processing within 24h."})


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------
@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    d = request.get_json(force=True)
    if d.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "Invalid password"}), 401
    token = db.create_admin_session()
    return jsonify({"token": token})


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    token = request.headers.get("X-Admin-Token", "")
    db.revoke_admin_session(token)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Admin: dashboard / reporting
# ---------------------------------------------------------------------------
@app.route("/api/admin/dashboard")
@require_admin
def api_admin_dashboard():
    return jsonify(db.dashboard_stats())


@app.route("/api/admin/commission")
@require_admin
def api_admin_commission():
    return jsonify(db.commission_summary())


@app.route("/api/admin/anticheat")
@require_admin
def api_admin_anticheat():
    return jsonify(db.get_anticheat_flags())


# ---------------------------------------------------------------------------
# Admin: players
# ---------------------------------------------------------------------------
@app.route("/api/admin/players")
@require_admin
def api_admin_players():
    search = request.args.get("search")
    return jsonify(db.list_players(search=search))


@app.route("/api/admin/player/<uid>/ban", methods=["POST"])
@require_admin
def api_admin_ban(uid):
    d = request.get_json(force=True) or {}
    db.ban_player(uid, d.get("reason", "Violation of terms"))
    _notify_player(uid, "🚫 Your account has been suspended. Contact support for details.")
    return jsonify({"ok": True})


@app.route("/api/admin/player/<uid>/unban", methods=["POST"])
@require_admin
def api_admin_unban(uid):
    db.unban_player(uid)
    _notify_player(uid, "✅ Your account has been reinstated. You can play again.")
    return jsonify({"ok": True})


@app.route("/api/admin/player/<uid>/adjust", methods=["POST"])
@require_admin
def api_admin_adjust(uid):
    d = request.get_json(force=True)
    amount = int(d.get("amount", 0))
    note = d.get("note", "Admin adjustment")
    ok = db.adjust_balance_admin(uid, amount, note)
    if not ok:
        return jsonify({"error": "Player not found"}), 404
    _notify_player(uid, f"💳 Your balance was adjusted by {amount:+d} ETB by an admin. Reason: {note}")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Admin: deposits / withdrawals
# ---------------------------------------------------------------------------
@app.route("/api/admin/deposits")
@require_admin
def api_admin_deposits():
    return jsonify({
        "pending": db.get_pending_deposits(),
        "all": db.get_all_deposits(),
    })


@app.route("/api/admin/withdrawals")
@require_admin
def api_admin_withdrawals():
    return jsonify({
        "pending": db.get_pending_withdrawals(),
        "all": db.get_all_withdrawals(),
    })


@app.route("/api/admin/approve", methods=["POST"])
@require_admin
def api_admin_approve():
    d = request.get_json(force=True)
    req = db.approve_deposit(d.get("req_id"))
    if not req:
        return jsonify({"error": "Not found"}), 404
    _notify_player(req["user_id"], f"✅ Your deposit of {req['amount']} ETB has been approved and credited!")
    return jsonify(req)


@app.route("/api/admin/reject", methods=["POST"])
@require_admin
def api_admin_reject():
    d = request.get_json(force=True)
    req = db.reject_deposit(d.get("req_id"))
    if not req:
        return jsonify({"error": "Not found"}), 404
    _notify_player(req["user_id"], f"❌ Your deposit of {req['amount']} ETB was rejected.")
    return jsonify(req)


@app.route("/api/admin/approve_withdrawal", methods=["POST"])
@require_admin
def api_admin_approve_withdrawal():
    d = request.get_json(force=True)
    req = db.approve_withdrawal(d.get("req_id"))
    if not req:
        return jsonify({"error": "Not found"}), 404
    _notify_player(req["user_id"], f"✅ Your withdrawal of {req['amount']} ETB has been sent!")
    return jsonify(req)


@app.route("/api/admin/reject_withdrawal", methods=["POST"])
@require_admin
def api_admin_reject_withdrawal():
    d = request.get_json(force=True)
    req = db.reject_withdrawal(d.get("req_id"))
    if not req:
        return jsonify({"error": "Not found"}), 404
    _notify_player(req["user_id"], f"❌ Your withdrawal of {req['amount']} ETB was rejected and refunded.")
    return jsonify(req)


# ---------------------------------------------------------------------------
# Admin: settings
# ---------------------------------------------------------------------------
@app.route("/api/admin/settings", methods=["GET", "POST"])
@require_admin
def api_admin_settings():
    if request.method == "POST":
        patch = request.get_json(force=True)
        return jsonify(db.update_settings(patch))
    return jsonify(db.get_settings())


# ---------------------------------------------------------------------------
# Admin: live game control
# ---------------------------------------------------------------------------
@app.route("/api/admin/games")
@require_admin
def api_admin_games():
    return jsonify(engine.admin_list_active_games())


@app.route("/api/admin/games/<gid>/pause", methods=["POST"])
@require_admin
def api_admin_pause(gid):
    return jsonify({"ok": engine.admin_pause_game(gid)})


@app.route("/api/admin/games/<gid>/resume", methods=["POST"])
@require_admin
def api_admin_resume(gid):
    return jsonify({"ok": engine.admin_resume_game(gid)})


@app.route("/api/admin/games/<gid>/force_draw", methods=["POST"])
@require_admin
def api_admin_force_draw(gid):
    return jsonify({"ok": engine.admin_force_draw(gid)})


@app.route("/api/admin/games/<gid>/end", methods=["POST"])
@require_admin
def api_admin_end(gid):
    return jsonify({"ok": engine.admin_end_game(gid)})


@app.route("/api/admin/games/<gid>/add_bots", methods=["POST"])
@require_admin
def api_admin_add_bots(gid):
    d = request.get_json(force=True)
    count = int(d.get("count", 1))
    return jsonify({"ok": engine.admin_add_bots(gid, count)})


# ---------------------------------------------------------------------------
# Admin: seat-filler bots (fill lobbies faster; never win the pot - see
# engine.py for why. Admin controls only WHETHER and HOW MANY, never odds.)
# ---------------------------------------------------------------------------
@app.route("/api/admin/bots", methods=["GET", "POST"])
@require_admin
def api_admin_bots():
    if request.method == "POST":
        d = request.get_json(force=True)
        return jsonify(db.update_bot_settings(enabled=d.get("enabled"), counts=d.get("counts")))
    return jsonify(db.get_bot_settings())


@app.route("/api/admin/lobby_configs")
@require_admin
def api_admin_lobby_configs():
    return jsonify(db.LOBBY_CONFIGS)


@app.route("/api/admin/bot_names", methods=["GET", "POST"])
@require_admin
def api_admin_bot_names():
    """Admin can rename bots. Deliberately NO win-probability control here -
    see README/engine.py for why that was refused."""
    if request.method == "POST":
        d = request.get_json(force=True)
        return jsonify({"names": db.update_bot_names(d.get("names", []))})
    return jsonify({"names": db.get_bot_names()})


@app.route("/api/admin/player/<uid>")
@require_admin
def api_admin_player_detail(uid):
    """Full detail for one player: profile, every transaction, every win -
    for the admin Players panel."""
    p = db.get_player(uid)
    if not p:
        return jsonify({"error": "Player not found"}), 404
    return jsonify({
        "user_id": uid,
        "name": p.get("name"),
        "balance": p.get("balance", 0),
        "bonus_balance": p.get("bonus_balance", 0),
        "total_wins": p.get("total_wins", 0),
        "total_winnings": p.get("total_winnings", 0),
        "games_played": p.get("games_played", 0),
        "banned": p.get("banned", False),
        "created_at": p.get("created_at"),
        "transactions": list(reversed(p.get("transactions", []))),
        "wins": list(reversed(p.get("wins", []))),
    })


# ---------------------------------------------------------------------------
# Admin: broadcast message to all players via Telegram
# ---------------------------------------------------------------------------
@app.route("/api/admin/broadcast", methods=["POST"])
@require_admin
def api_admin_broadcast():
    d = request.get_json(force=True)
    message = d.get("message", "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    db.log_broadcast(message)
    all_players = db.load()["players"]
    sent = 0
    for uid in all_players.keys():
        _notify_player(uid, f"📢 {message}")
        sent += 1
    return jsonify({"ok": True, "sent": sent})


# ---------------------------------------------------------------------------
# Socket.IO - real-time sync so every player sees the exact same countdown,
# draw, number board and winner at the same moment (no more relying on
# independent polling loops drifting out of sync).
# ---------------------------------------------------------------------------
@socketio.on("join_game_room")
def on_join_game_room(data):
    gid = data.get("game_id")
    if gid:
        join_room(gid)


@socketio.on("leave_game_room")
def on_leave_game_room(data):
    gid = data.get("game_id")
    if gid:
        leave_room(gid)


@socketio.on("join_home_room")
def on_join_home_room():
    join_room("home_room")


@socketio.on("join_admin_room")
def on_join_admin_room(data):
    token = data.get("token", "")
    if db.check_admin_session(token):
        join_room("admin_room")


# ---------------------------------------------------------------------------
# Telegram notifications + bridging engine events -> Socket.IO
# ---------------------------------------------------------------------------
_bot_app = None


def _notify_admin(msg):
    if not ADMIN_ID or not _bot_app:
        return
    async def _send():
        try:
            await _bot_app.bot.send_message(chat_id=ADMIN_ID, text=msg)
        except Exception as e:
            logger.warning(f"Admin notify failed: {e}")
    asyncio.run_coroutine_threadsafe(_send(), engine._get_loop())


def _notify_player(user_id, msg):
    if not _bot_app:
        return
    async def _send():
        try:
            await _bot_app.bot.send_message(chat_id=user_id, text=msg)
        except Exception as e:
            logger.warning(f"Player notify failed: {e}")
    asyncio.run_coroutine_threadsafe(_send(), engine._get_loop())


async def _game_event_handler(event, payload):
    """Handle game events: notify Telegram players AND push to Socket.IO so
    every connected browser updates instantly and identically."""
    gid = payload.get("game_id")
    bet = payload.get("bet")

    if gid:
        socketio.emit(event, payload, room=gid)
    socketio.emit("lobbies_changed", {}, room="home_room")

    if event == "win":
        for w in payload["winners"]:
            _notify_player(w["user_id"],
                f"🏆 BINGO! You won {payload['prize_each']} ETB in the {bet} ETB game "
                f"with a {w.get('pattern', 'Bingo')}!\nYour wallet has been credited automatically. 🎉")
    elif event == "start":
        logger.info(f"Game started: bet={bet}, players={payload['players']}")
    elif event == "countdown":
        logger.info(f"Countdown started: bet={bet}")


engine.register_draw_callback(_game_event_handler)


# ---------------------------------------------------------------------------
# Telegram Bot Commands
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = None
    if WEBAPP_URL:
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🎱 Play Yosi Bingo", web_app=WebAppInfo(url=WEBAPP_URL))
        ]])
    await update.message.reply_text(
        "🎱 Welcome to Yosi Bingo!\n\n"
        "Tap below to open the game, deposit ETB, and start playing!\n"
        "New players get a free signup bonus to try it out. 🎁\n\n"
        "Commands:\n"
        "/balance - check your balance\n"
        "/deposit - how to deposit\n",
        reply_markup=markup
    )


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name
    p = db.get_or_create_player(uid, name)
    await update.message.reply_text(f"💳 Your balance: {p['balance']} ETB")


async def cmd_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db.get_settings()
    await update.message.reply_text(
        f"💰 To deposit:\n\n"
        f"Send money to:\n"
        f"📱 Telebirr / CBE Birr: {s['deposit_number']}\n"
        f"Name: {s['deposit_name']}\n\n"
        f"Then open the app and tap Deposit — fill in your amount and reference number.\n"
        f"Your wallet will be credited after confirmation (usually within minutes)."
    )


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve <request_id>")
        return
    req = db.approve_deposit(context.args[0])
    if req:
        await update.message.reply_text(f"✅ Approved {req['amount']} ETB for {req['name']}")
        _notify_player(req["user_id"], f"✅ Your deposit of {req['amount']} ETB has been approved and credited!")
    else:
        await update.message.reply_text("❌ Request not found or already processed.")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /reject <request_id>")
        return
    req = db.reject_deposit(context.args[0])
    if req:
        await update.message.reply_text(f"❌ Rejected deposit from {req['name']}")
        _notify_player(req["user_id"], f"❌ Your deposit of {req['amount']} ETB was rejected.")
    else:
        await update.message.reply_text("❌ Request not found.")


async def cmd_approvew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /approvew <request_id>")
        return
    req = db.approve_withdrawal(context.args[0])
    if req:
        await update.message.reply_text(f"✅ Withdrawal confirmed for {req['name']}")
        _notify_player(req["user_id"], f"✅ Your withdrawal of {req['amount']} ETB has been sent to your {req['method']}!")
    else:
        await update.message.reply_text("❌ Not found.")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        return
    deps = db.get_pending_deposits()
    wits = db.get_pending_withdrawals()
    msg = f"📋 Pending Deposits ({len(deps)}):\n"
    for r in deps:
        msg += f"  • {r['id']}: {r['name']} - {r['amount']} ETB ({r['method']}) ref:{r['reference']}\n"
    msg += f"\n💸 Pending Withdrawals ({len(wits)}):\n"
    for r in wits:
        msg += f"  • {r['id']}: {r['name']} - {r['amount']} ETB to {r['account']}\n"
    msg += "\n🖥 Or manage everything from the admin dashboard at /admin.html"
    await update.message.reply_text(msg or "Nothing pending.")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ADMIN_ID:
        return
    s = db.dashboard_stats()
    await update.message.reply_text(
        f"📊 Yosi Bingo Stats\n\n"
        f"Players: {s['total_players']}\n"
        f"Wallet balances (total): {s['total_wallet_balance']} ETB\n"
        f"Total deposited: {s['total_deposited']} ETB\n"
        f"Commission earned (all-time): {s['total_commission']} ETB\n"
        f"Commission earned (today): {s['today_commission']} ETB\n"
        f"Games played: {s['total_games_played']}\n"
        f"Pending deposits: {s['pending_deposits']} | withdrawals: {s['pending_withdrawals']}\n"
        f"Anti-cheat flags: {s['anticheat_flags']} | Banned: {s['banned_players']}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_bot():
    global _bot_app
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    engine.set_event_loop(loop)

    _bot_app = ApplicationBuilder().token(token).build()
    _bot_app.add_handler(CommandHandler("start", cmd_start))
    _bot_app.add_handler(CommandHandler("balance", cmd_balance))
    _bot_app.add_handler(CommandHandler("deposit", cmd_deposit))
    _bot_app.add_handler(CommandHandler("approve", cmd_approve))
    _bot_app.add_handler(CommandHandler("reject", cmd_reject))
    _bot_app.add_handler(CommandHandler("approvew", cmd_approvew))
    _bot_app.add_handler(CommandHandler("pending", cmd_pending))
    _bot_app.add_handler(CommandHandler("stats", cmd_stats))

    logger.info("Telegram bot starting...")
    _bot_app.run_polling(stop_signals=None)


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Flask + Socket.IO starting on port {port}...")
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
