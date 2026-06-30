# Telegram Bingo Bot

A full 75-ball Bingo game (B 1-15, I 16-30, N 31-45, G 46-60, O 61-75) playable
inside a Telegram chat, with 200 unique pre-generated cards, automatic number
calling, and automatic win detection (any full row, column, or diagonal — center
N square is a free space).

## Files
- `cards.py` — generates the pool of 200 unique cards (run once to create `cards_pool.json`)
- `cards_pool.json` — the generated pool (already included; regenerate anytime)
- `game.py` — core game engine (rooms, drawing, marking, win checking) — no Telegram code, easy to test/reuse
- `bot.py` — the Telegram bot wiring commands to the game engine
- `requirements.txt` — Python dependencies

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

2. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram and copy the token it gives you.

3. Set the token as an environment variable:
   ```
   export TELEGRAM_BOT_TOKEN="123456:ABC-your-token-here"
   ```

4. (Optional) Regenerate the 200-card pool — it's already generated and included, but you can re-roll it:
   ```
   python3 cards.py
   ```

5. Run the bot:
   ```
   python3 bot.py
   ```

## How to play (in any chat with the bot)

- `/newgame` — opens a lobby
- `/join` — grabs you a random card (sent to you privately, so you'll need to have started
  a DM with the bot at least once — Telegram requires that before a bot can message you directly)
- `/mycard` — re-shows your card with current marks
- `/startgame` — locks the lobby and begins
- `/draw` — calls the next number manually, OR
- `/auto` — toggles automatic calling every 8 seconds (change `DRAW_INTERVAL_SECONDS` in `bot.py`)
- `/status` — shows numbers called so far
- `/endgame` — stops the game

Win condition defaults to a line (row, column, or diagonal). To require a full
blackout card instead, change `pattern="line"` to `pattern="full"` where
`BingoGame(chat_id)` is constructed in `bot.py`.

## Notes on the 200-card limit
Each chat's game draws cards from the same 200-card pool without repeats within
a single game (so no two players in one game ever get an identical card). Once
all 200 are handed out, further `/join` attempts will get a friendly error —
plenty for any normal-sized group, but increase `n=200` in `cards.py` and
regenerate if you need more.

## Deploying with the rest of your bot
If you already have a larger Telegram bot, the cleanest way to fold this in is
to import `BingoGame` from `game.py` and copy the command handlers from `bot.py`
into your existing `Application` instance, rather than running this as a second
separate bot process.
