"""
Yosi Bingo - Full Server
Runs Flask (Mini App + API) + Telegram bot in one process.
Everything is automated - no manual game management needed.

Setup:
  export TELEGRAM_BOT_TOKEN="..."
  export WEBAPP_URL="https://yosi-bingo.onrender.com"
  export ADMIN_ID="your_telegram_user_id"   # your personal Telegram ID for admin notifications
  python3 server.py
"""
import os, threading, logging, asyncio
from flask import Flask, request, jsonify, send_from_directory
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import data as db
import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
ADMIN_ID = os.environ.get("ADMIN_ID", "")  # your Telegram user ID for deposit notifications

# ---------------------------------------------------------------------------
# Flask
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="webapp", static_url_path="")

@app.route("/")
def index():
    return send_from_directory("webapp", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("webapp", path)

@app.route("/api/player")
def api_player():
    uid = request.args.get("user_id")
    name = request.args.get("name", "Player")
    if not uid:
        return jsonify({"error": "Missing user_id"}), 400
    p = db.get_or_create_player(uid, name)
    return jsonify(p)

@app.route("/api/lobbies")
def api_lobbies():
    return jsonify(engine.all_lobby_states())

@app.route("/api/join", methods=["POST"])
def api_join():
    d = request.get_json(force=True)
    uid = d.get("user_id")
    name = d.get("name", "Player")
    bet = d.get("bet")
    if not uid or not bet:
        return jsonify({"error": "Missing fields"}), 400
    try:
        room, slot = engine.join_lobby(uid, name, int(bet))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
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
    return jsonify(db.get_leaderboard())

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
    # notify admin via Telegram
    _notify_admin(
        f"💰 New deposit request!\n"
        f"ID: {req['id']}\n"
        f"Player: {name} ({uid})\n"
        f"Amount: {amount} ETB via {method}\n"
        f"Ref: {ref}\n\n"
        f"Reply with: /approve {req['id']} to credit\n"
        f"Or: /reject {req['id']} to reject"
    )
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
    if not player or player["balance"] < int(amount):
        return jsonify({"error": f"Insufficient balance. Available: {player['balance'] if player else 0} ETB"}), 400
    req = db.add_withdraw_request(uid, name, int(amount), method, account)
    # debit immediately, will send manually
    db.debit_balance(uid, int(amount), f"Withdrawal ({method})")
    _notify_admin(
        f"⬆ Withdrawal request!\n"
        f"ID: {req['id']}\n"
        f"Player: {name} ({uid})\n"
        f"Amount: {amount} ETB via {method}\n"
        f"To: {account}\n\n"
        f"Send money then: /approvew {req['id']}"
    )
    return jsonify({"message": f"✅ Withdrawal of {amount} ETB requested. Processing within 24h."})

# Admin API (called from Telegram bot commands)
@app.route("/api/admin/approve", methods=["POST"])
def api_admin_approve():
    d = request.get_json(force=True)
    req = db.approve_deposit(d.get("req_id"))
    if not req:
        return jsonify({"error": "Not found"}), 404
    return jsonify(req)

@app.route("/api/admin/reject", methods=["POST"])
def api_admin_reject():
    d = request.get_json(force=True)
    req = db.reject_deposit(d.get("req_id"))
    if not req:
        return jsonify({"error": "Not found"}), 404
    return jsonify(req)

# ---------------------------------------------------------------------------
# Telegram notifications
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
    """Handle game events and notify relevant players."""
    if event == "win":
        for w in payload["winners"]:
            _notify_player(w["user_id"],
                f"🏆 BINGO! You won {payload['prize_each']} ETB in the {payload['bet']} ETB game!\n"
                f"Your wallet has been credited automatically. 🎉")
    elif event == "start":
        logger.info(f"Game started: bet={payload['bet']}, players={payload['players']}")
    elif event == "countdown":
        logger.info(f"Countdown started: bet={payload['bet']}")

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
        "Tap below to open the game, deposit ETB, and start playing!\n\n"
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
    await update.message.reply_text(
        "💰 To deposit:\n\n"
        "Send money to:\n"
        "📱 Telebirr / CBE Birr: 0936414865\n"
        "Name: Amanuel Abiy\n\n"
        "Then open the app and tap Deposit — fill in your amount and reference number.\n"
        "Your wallet will be credited after confirmation (usually within minutes)."
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
        _notify_player(req["user_id"],
            f"✅ Your deposit of {req['amount']} ETB has been approved and credited to your wallet!")
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
        _notify_player(req["user_id"],
            f"❌ Your deposit of {req['amount']} ETB was rejected. "
            f"Please contact support if you believe this is an error.")
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
        _notify_player(req["user_id"],
            f"✅ Your withdrawal of {req['amount']} ETB has been sent to your {req['method']}!")
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
    await update.message.reply_text(msg or "Nothing pending.")

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

    logger.info("Telegram bot starting...")
    _bot_app.run_polling(stop_signals=None)

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Flask starting on port {port}...")
    app.run(host="0.0.0.0", port=port)
