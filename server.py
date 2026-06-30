"""
Combined process: runs the Telegram bot (polling) AND a Flask web server
that serves the Mini App (webapp/) and a small JSON API, both sharing the
same in-memory game state. This is what you deploy (e.g. on Render.com)
instead of running bot.py alone, once you want the visual Mini App.

Run:
  export TELEGRAM_BOT_TOKEN="..."
  export WEBAPP_URL="https://your-deployed-url.onrender.com"   # set after first deploy
  python3 server.py
"""
import os
import threading
import logging

from flask import Flask, request, jsonify, send_from_directory
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from game import BingoGame, COLUMNS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GAMES = {}          # chat_id -> BingoGame
GAMES_LOCK = threading.Lock()

WEBAPP_URL = os.environ.get("WEBAPP_URL", "")  # e.g. https://yosibingo.onrender.com

# ---------------------------------------------------------------------------
# Flask app: serves the Mini App static files + API
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="webapp", static_url_path="")


@app.route("/")
def index():
    return send_from_directory("webapp", "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("webapp", path)


@app.route("/api/state")
def api_state():
    chat_id = request.args.get("chat_id")
    user_id = request.args.get("user_id")
    if not chat_id or not user_id:
        return jsonify({"error": "Missing chat_id or user_id"}), 400

    with GAMES_LOCK:
        game = GAMES.get(int(chat_id))
        if game is None:
            return jsonify({"error": "No game running in this chat. Send /newgame in the bot chat first."}), 404
        player = game.players.get(int(user_id))
        if player is None:
            return jsonify({"error": "You haven't joined this game. Send /join in the bot chat first."}), 404

        return jsonify({
            "card": player.card,
            "marked": list(player.marked),
            "drawn": game.drawn_numbers,
            "player_count": len(game.players),
            "won": len(game.winners) > 0,
            "winners": [w.name for w in game.winners],
        })


@app.route("/api/draw", methods=["POST"])
def api_draw():
    data = request.get_json(force=True)
    chat_id = data.get("chat_id")
    if not chat_id:
        return jsonify({"error": "Missing chat_id"}), 400

    with GAMES_LOCK:
        game = GAMES.get(int(chat_id))
        if game is None or not game.started or game.finished:
            return jsonify({"error": "Game not active"}), 400
        num = game.draw_number()
        game.check_winners()
        return jsonify({"drawn": num})


# ---------------------------------------------------------------------------
# Telegram bot: same commands as bot.py, plus a button that opens the Mini App
# ---------------------------------------------------------------------------
def col_letter(num: int) -> str:
    if num <= 15: return "B"
    if num <= 30: return "I"
    if num <= 45: return "N"
    if num <= 60: return "G"
    return "O"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Yosi Bingo! 🎱\n"
        "/newgame - create a lobby\n"
        "/join - join and get a card + open the visual board\n"
        "/startgame - begin the game"
    )


async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with GAMES_LOCK:
        if chat_id in GAMES and not GAMES[chat_id].finished:
            await update.message.reply_text("A game is already running. Use /endgame first.")
            return
        GAMES[chat_id] = BingoGame(chat_id)
    await update.message.reply_text(
        "🎱 New lobby created! Send /join to grab a card and open the board."
    )


def webapp_button(chat_id):
    if not WEBAPP_URL:
        return None
    url = f"{WEBAPP_URL}/?chat_id={chat_id}"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎟️ Open My Bingo Card", web_app=WebAppInfo(url=url))]]
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    with GAMES_LOCK:
        game = GAMES.get(chat_id)
        if game is None:
            await update.message.reply_text("No game running. Send /newgame first.")
            return
        if game.started:
            await update.message.reply_text("Game already started — too late to join this round.")
            return
        try:
            game.add_player(user.id, user.first_name)
        except RuntimeError as e:
            await update.message.reply_text(str(e))
            return
        count = len(game.players)

    markup = webapp_button(chat_id)
    if markup:
        await update.message.reply_text(
            f"{user.first_name} joined! ({count} players) Tap below to open your card:",
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            f"{user.first_name} joined! ({count} players). "
            f"Set WEBAPP_URL on the server to enable the visual board button."
        )


async def startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with GAMES_LOCK:
        game = GAMES.get(chat_id)
        if game is None or not game.players:
            await update.message.reply_text("No players yet. Send /join first.")
            return
        game.start()
    await update.message.reply_text(
        "🟢 Game started! Open your card (button from /join) and tap 'Draw Next Number' "
        "to call numbers — everyone's card updates live."
    )


async def endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    with GAMES_LOCK:
        GAMES.pop(chat_id, None)
    await update.message.reply_text("Game ended. Send /newgame to start another.")


def run_bot():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable.")
    bot_app = ApplicationBuilder().token(token).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("newgame", newgame))
    bot_app.add_handler(CommandHandler("join", join))
    bot_app.add_handler(CommandHandler("startgame", startgame))
    bot_app.add_handler(CommandHandler("endgame", endgame))
    logger.info("Telegram bot starting (polling)...")
    bot_app.run_polling(stop_signals=None)


if __name__ == "__main__":
    # Run the Telegram bot polling loop in a background thread,
    # and Flask (the Mini App + API) on the main thread.
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Flask web server starting on port {port}...")
    app.run(host="0.0.0.0", port=port)
