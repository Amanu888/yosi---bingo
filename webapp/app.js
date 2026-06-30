const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}

// chat_id is passed as a URL query param by the bot when it builds the web_app button.
// user_id comes from Telegram's own initData (trusted, not user-editable).
const params = new URLSearchParams(window.location.search);
const chatId = params.get("chat_id");
const userId = tg?.initDataUnsafe?.user?.id;
const userName = tg?.initDataUnsafe?.user?.first_name || "Player";

const COLUMNS = ["B", "I", "N", "G", "O"];

const statusText = document.getElementById("status-text");
const lastCall = document.getElementById("last-call");
const cardContainer = document.getElementById("card-container");
const calledGrid = document.getElementById("called-grid");
const drawBtn = document.getElementById("draw-btn");
const refreshBtn = document.getElementById("refresh-btn");
const winnerBanner = document.getElementById("winner-banner");

function colLetter(n) {
  if (n <= 15) return "B";
  if (n <= 30) return "I";
  if (n <= 45) return "N";
  if (n <= 60) return "G";
  return "O";
}

function renderCard(card, markedSet) {
  cardContainer.innerHTML = "";
  COLUMNS.forEach((c) => {
    const h = document.createElement("div");
    h.className = "col-header";
    h.textContent = c;
    cardContainer.appendChild(h);
  });
  for (let row = 0; row < 5; row++) {
    COLUMNS.forEach((c) => {
      const val = card[c][row];
      const cell = document.createElement("div");
      cell.className = "cell";
      if (val === "FREE") {
        cell.classList.add("free", "marked");
        cell.textContent = "FREE";
      } else {
        cell.textContent = val;
        if (markedSet.includes(val)) cell.classList.add("marked");
      }
      cardContainer.appendChild(cell);
    });
  }
}

function renderCalled(drawn) {
  calledGrid.innerHTML = "";
  drawn.forEach((n) => {
    const chip = document.createElement("div");
    chip.className = "called-chip";
    chip.textContent = `${colLetter(n)}${n}`;
    calledGrid.appendChild(chip);
  });
}

async function fetchState() {
  if (!chatId || !userId) {
    statusText.textContent = "Missing chat or user info. Open this from the bot in Telegram.";
    return;
  }
  try {
    const res = await fetch(`/api/state?chat_id=${chatId}&user_id=${userId}`);
    const data = await res.json();
    if (data.error) {
      statusText.textContent = data.error;
      return;
    }
    statusText.textContent = `${data.player_count} players · ${data.drawn.length} numbers called`;
    if (data.drawn.length) {
      const last = data.drawn[data.drawn.length - 1];
      lastCall.textContent = `${colLetter(last)}-${last}`;
    }
    renderCard(data.card, data.marked);
    renderCalled(data.drawn);

    if (data.won) {
      winnerBanner.classList.remove("hidden");
      winnerBanner.textContent = data.winners.includes(userName)
        ? "🏆 You got BINGO!"
        : `🏆 BINGO! Winner(s): ${data.winners.join(", ")}`;
      drawBtn.disabled = true;
    }
  } catch (e) {
    statusText.textContent = "Connection error. Pull to refresh.";
  }
}

async function draw() {
  drawBtn.disabled = true;
  try {
    await fetch(`/api/draw`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId }),
    });
  } catch (e) {
    // ignore, polling below will catch up
  }
  await fetchState();
  drawBtn.disabled = false;
}

drawBtn.addEventListener("click", draw);
refreshBtn.addEventListener("click", fetchState);

fetchState();
setInterval(fetchState, 4000); // keep all players' screens in sync
