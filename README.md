# Yosi Bingo — Setup

## What changed in this refinement
- **Admin dashboard** at `/admin.html` — the control center (see below)
- **Color palette** — navy (#02167f) + gold (#ffd014) + white, applied across both the player app and admin panel
- **Commission**: configurable %, defaults to 20% (2 ETB per 10 ETB bet) as you specified
- **Signup bonus**: 10 ETB, one-time, tracked separately so it can never be withdrawn (only deposited money can be)
- **Win patterns**: horizontal, vertical, diagonal, four corners — all toggleable per-lobby from admin settings
- **Real-time sync**: Socket.IO pushes draws/countdown/winners to every connected player instantly (falls back to 3s polling if a socket drops)
- **Number caller**: on-screen colored ball (B/I/N/G/O each has its own color) + optional voice-over using the browser's built-in text-to-speech
- **Auto/manual mark toggle**: cosmetic only — win detection always runs server-side off the true call history, so this setting can never cost anyone a win
- **Anti-cheat**: flags when the same device or the same IP is linked to multiple accounts (view under Admin → Anti-cheat)
- **Language setting**: English / Amharic toggle in Settings
- **Seat-filler bots**: admin-controlled count per lobby, house-funded, disclosed with a 🤖 label, and *never eligible to win the pot* — see note below

## Environment variables (set these in Render)
| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `WEBAPP_URL` | Your Render URL, e.g. `https://yosi-bingo.onrender.com` |
| `ADMIN_ID` | Your personal Telegram numeric user ID (for deposit/withdrawal alerts) |
| `ADMIN_PASSWORD` | Password to log into `/admin.html` — **change this from the default** |
| `SECRET_KEY` | Any random string, used for session signing |

## Render start command
```
python3 server.py
```

## Accessing the admin dashboard
Go to `https://YOUR-RENDER-URL/admin.html`, enter `ADMIN_PASSWORD`. From there you can:
- Approve/reject deposits & withdrawals (also still works via Telegram `/approve`, `/reject`, `/approvew`, `/pending`)
- Pause, resume, force-draw, or end any live game
- Ban/unban players, adjust balances directly
- Change commission %, bonus amount, countdown/draw timing, and which win patterns count
- Review anti-cheat flags
- Broadcast a message to every player via Telegram
- Turn seat-filler bots on/off and set how many join each lobby

## On the bots
You asked for bots you could control, including their win probability. I built the seat-filling part (they help
quiet lobbies reach the minimum player count) but left out win-probability control — letting the house dial in
which players' bots "win" real money games is rigging outcomes against paying players, which I won't build no
matter how it's framed. Bots here are purely cosmetic competition: they never pay in, and they can never be
declared the winner, so they have zero effect on real players' odds or payouts.

## Suggestions for "professional" polish beyond this pass
- **Provably-fair draw seed**: publish a hashed seed before each game and reveal it after, so players can verify the draw wasn't tampered with
- **KYC threshold**: require ID verification above a withdrawal amount (common in ETB gambling apps to reduce fraud/chargebacks)
- **Self-exclusion / spending limits**: let players cap their own daily deposit or set a cool-off period — reduces problem gambling and is often a legal requirement
- **Rate limiting on deposit/withdraw endpoints** to stop automated abuse
- **Automated deposit verification** (e.g. Telebirr/CBE API or SMS parsing) instead of manual reference-number review, once you have volume
- **Multi-admin roles** (e.g. a support-only admin who can't change commission settings)
- **Structured logging + error alerting** (Sentry or similar) so you hear about crashes before players do
