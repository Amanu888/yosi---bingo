"""
Telegram Bingo Bot
==================

Commands:
  /newgame        - start a new game lobby in this chat
  /join           - join the current lobby and get a random card
  /mycard         - show your card with current marks
  /startgame      - (host or any player) lock the lobby and begin calling numbers
  /draw           - draw the next number (manual mode)
  /auto           - toggle automatic drawing every few seconds
  /status         - show numbers drawn so far
  /endgame        - end the current game

Setup:
  1. pip install -r requirements.txt
  2. export TELEGRAM_BOT_TOKEN="your-token-from-BotFather"
  3. Make sure cards_pool.json exists (run `python3 cards.py` once to generate it)
  4. python3 bot.py
"""
import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

from game import BingoGame

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# chat_id -> BingoGame
GAMES = {}
# chat_id -> bool, whether auto-draw is on
AUTO_DRAW = {}

DRAW_INTERVAL_SECONDS = 8  # how often numbers call themselves in auto mode


def col_letter(num: int) -> str:
    if num <= 15:
        return "B"
    if num <= 30:
        return "I"
    if num <= 45:
        return "N"
    if num <= 60:
        return "G"
    return "O"


async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in GAMES and not GAMES[chat_id].finished:
        await update.message.reply_text(
            "A game is already running here. Use /endgame to stop it first."
        )
        return
    GAMES[chat_id] = BingoGame(chat_id)
    AUTO_DRAW[chat_id] = False
    await update.message.reply_text(
        "🎱 New Bingo lobby created!\n"
        "Players: send /join to grab a card.\n"
        "When everyone's in, send /startgame to begin."
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if game is None:
        await update.message.reply_text("No game running. Start one with /newgame.")
        return
    if game.started:
        await update.message.reply_text("Game already started — too late to join this round.")
        return
    user = update.effective_user
    try:
        player = game.add_player(user.id, user.first_name)
    except RuntimeError as e:
        await update.message.reply_text(str(e))
        return
    await update.message.reply_text(
        f"{user.first_name} joined! ({len(game.players)} players)\n"
        f"Use /mycard anytime to see your card."
    )
    await context.bot.send_message(
        chat_id=user.id,
        text=f"Here is your Bingo card for chat '{update.effective_chat.title or chat_id}':\n\n"
        f"```\n{game.render_card_text(player)}\n```",
        parse_mode="Markdown",
    )


async def mycard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if game is None or update.effective_user.id not in game.players:
        await update.message.reply_text("You haven't joined a game here. Send /join first.")
        return
    player = game.players[update.effective_user.id]
    await update.message.reply_text(
        f"```\n{game.render_card_text(player)}\n```", parse_mode="Markdown"
    )


async def startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if game is None:
        await update.message.reply_text("No game running. Start one with /newgame.")
        return
    if not game.players:
        await update.message.reply_text("No players joined yet. Send /join first.")
        return
    game.start()
    await update.message.reply_text(
        f"🟢 Game started with {len(game.players)} players!\n"
        f"Pattern to win: any full row, column, or diagonal.\n"
        f"Use /draw to call the next number, or /auto to let the bot call automatically."
    )


async def _announce_draw(context: ContextTypes.DEFAULT_TYPE, chat_id: int, game: BingoGame):
    num = game.draw_number()
    if num is None:
        await context.bot.send_message(chat_id, "All 75 numbers have been called! Game over.")
        game.finished = True
        AUTO_DRAW[chat_id] = False
        return
    await context.bot.send_message(chat_id, f"📣 {col_letter(num)}-{num}")

    winners = game.check_winners()
    if winners:
        names = ", ".join(w.name for w in winners)
        await context.bot.send_message(chat_id, f"🏆 BINGO! Winner(s): {names}")
        game.finished = True
        AUTO_DRAW[chat_id] = False
        for w in winners:
            await context.bot.send_message(
                w.user_id,
                f"You won! 🎉 Final card:\n```\n{game.render_card_text(w)}\n```",
                parse_mode="Markdown",
            )


async def draw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if game is None or not game.started:
        await update.message.reply_text("Game hasn't started. Use /newgame then /startgame.")
        return
    if game.finished:
        await update.message.reply_text("Game already finished. Start a new one with /newgame.")
        return
    await _announce_draw(context, chat_id, game)


async def auto_draw_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    game = GAMES.get(chat_id)
    if game is None or game.finished or not AUTO_DRAW.get(chat_id):
        return
    await _announce_draw(context, chat_id, game)


async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if game is None or not game.started:
        await update.message.reply_text("Game hasn't started. Use /newgame then /startgame.")
        return
    if AUTO_DRAW.get(chat_id):
        AUTO_DRAW[chat_id] = False
        for j in context.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
            j.schedule_removal()
        await update.message.reply_text("Auto-draw stopped. Use /draw to call manually.")
    else:
        AUTO_DRAW[chat_id] = True
        context.job_queue.run_repeating(
            auto_draw_job,
            interval=DRAW_INTERVAL_SECONDS,
            first=1,
            chat_id=chat_id,
            name=f"auto_{chat_id}",
        )
        await update.message.reply_text(
            f"Auto-draw on — calling a number every {DRAW_INTERVAL_SECONDS}s."
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = GAMES.get(chat_id)
    if game is None:
        await update.message.reply_text("No game running.")
        return
    called = ", ".join(f"{col_letter(n)}{n}" for n in game.drawn_numbers) or "none yet"
    await update.message.reply_text(
        f"Players: {len(game.players)}\nNumbers called ({len(game.drawn_numbers)}): {called}"
    )


async def endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in GAMES:
        del GAMES[chat_id]
    AUTO_DRAW[chat_id] = False
    for j in context.job_queue.get_jobs_by_name(f"auto_{chat_id}"):
        j.schedule_removal()
    await update.message.reply_text("Game ended. Send /newgame to start another.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Bingo Bot! 🎱\n"
        "/newgame - create a lobby\n"
        "/join - join and get a card\n"
        "/mycard - view your card\n"
        "/startgame - begin calling numbers\n"
        "/draw - call next number manually\n"
        "/auto - toggle automatic calling\n"
        "/status - see numbers called so far\n"
        "/endgame - stop the game"
    )


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable before running.")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("mycard", mycard))
    app.add_handler(CommandHandler("startgame", startgame))
    app.add_handler(CommandHandler("draw", draw))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("endgame", endgame))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
