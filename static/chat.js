"use strict";

const byId = (id) => document.getElementById(id);
const form = byId("composer");
const input = byId("message");
const send = byId("send");
const thread = byId("thread");
const viewport = byId("viewport");
const historyList = byId("history");
const sidebar = byId("sidebar");
const menu = byId("menu");
const scrim = byId("scrim");
const main = document.querySelector("main");
const phone = matchMedia("(max-width: 720px)");
const composerArea = byId("composer-area");
const storageKey = "darglobal-property-chat-v1";
const captureLine = /^Captured \d{4}-\d{2}-\d{2}T\S+$/;
const pending = new Map();
const live = document.body.dataset.mode === "live";
const pendingMessage = live
  ? "Checking your request."
  : document.body.dataset.dataMode === "live"
    ? "Looking through the property snapshot."
    : "Looking through the sample records.";

function readState() {
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey));
    if (Array.isArray(stored?.chats)) return stored;
  } catch {
    // The app can still run if storage is unavailable.
  }
  return { activeId: null, chats: [] };
}

const state = readState();

function save() {
  try {
    localStorage.setItem(storageKey, JSON.stringify(state));
  } catch {
    // Keep this session usable in browsers that block or limit storage.
  }
}

function activeChat() {
  return state.chats.find((chat) => chat.id === state.activeId);
}

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function updateComposer() {
  input.style.height = "auto";
  input.style.height = `${Math.min(128, input.scrollHeight)}px`;
  send.disabled = !input.value.trim() || pending.has(activeChat()?.id);
}

function placeComposer() {
  const destination = byId(!phone.matches && !activeChat() ? "welcome-composer" : "thread-composer");
  if (composerArea.parentElement === destination) return;

  const focused = composerArea.contains(document.activeElement) ? document.activeElement : null;
  destination.append(composerArea);
  focused?.focus({ preventScroll: true });
}

function setDrawer(open, focusTarget) {
  const visible = open && phone.matches;
  sidebar.classList.toggle("open", visible);
  menu.setAttribute("aria-expanded", String(visible));
  scrim.hidden = !visible;
  main.inert = visible;
  if (visible) {
    sidebar.setAttribute("role", "dialog");
    sidebar.setAttribute("aria-modal", "true");
    byId("new-chat").focus();
  } else {
    sidebar.removeAttribute("role");
    sidebar.removeAttribute("aria-modal");
    focusTarget?.focus();
  }
}

function renderHistory() {
  historyList.replaceChildren();
  byId("empty-history").hidden = state.chats.length > 0;
  for (const chat of state.chats) {
    const li = element("li");
    const button = element("button", "", chat.title);
    button.type = "button";
    if (chat.id === state.activeId) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => selectChat(chat.id));
    li.append(button);
    historyList.append(li);
  }
}

function renderSources(data) {
  const list = element("ul", "sources");
  list.setAttribute("aria-label", "Sources");
  for (const source of data.sources) {
    const card = element("li");
    const sample = source.fixture ?? (data.mode === "mock");
    card.append(element("small", "", sample ? `Sample source: ${source.publisher}` : source.publisher));
    if (!sample && /^https?:\/\//i.test(source.url || "")) {
      const link = element("a", "", source.title);
      link.href = source.url;
      card.append(link);
    } else {
      card.append(element("strong", "", source.title));
    }
    list.append(card);
  }
  return list;
}

function renderListingCards(cards) {
  const list = element("ul", "listing-cards");
  list.setAttribute("aria-label", "Verified listings");
  for (const card of cards) {
    const item = element("li", "listing-card");
    item.append(element("h3", "", card.title));
    for (const line of card.lines) {
      if (!captureLine.test(line)) item.append(element("p", "", line));
    }
    item.append(renderSources({ sources: [card.source] }));
    list.append(item);
  }
  return list;
}

function renderConversation({ scroll = false, reveal = null } = {}) {
  const chat = activeChat();
  byId("welcome").hidden = Boolean(chat);
  thread.hidden = !chat;
  placeComposer();
  thread.replaceChildren();
  let revealedReply;
  for (const turn of chat?.turns || []) {
    const article = element("article", "turn");
    article.setAttribute("aria-label", "Question and answer");
    const question = element("p", "question", turn.message);
    question.prepend(element("span", "sr-only", "You: "));
    const reply = element("div", "reply");
    if (turn === reveal) revealedReply = reply;
    reply.append(element("h2", "", "Research assistant"));
    if (turn.response) {
      const cards = turn.response.listing_cards;
      const text = cards?.length ? turn.response.listing_intro
        : turn.response.answer.split("\n").filter((line) => !captureLine.test(line)).join("\n");
      for (const paragraph of text.split(/\n\s*\n/)) {
        const bullets = paragraph.trim().split("\n").map((line) => line.match(/^[-*•]\s+(.+)$/));
        if (bullets.every(Boolean)) {
          const list = element("ul", "answer-list");
          for (const bullet of bullets) list.append(element("li", "", bullet[1]));
          reply.append(list);
        } else {
          reply.append(element("p", "", paragraph));
        }
      }
      if (cards?.length) reply.append(renderListingCards(cards));
      else if (turn.response.sources.length) reply.append(renderSources(turn.response));
      if (turn.response.listing_followup) reply.append(element("p", "listing-followup", turn.response.listing_followup));
      if (turn.response.inference_provider && turn.response.inference_model) {
        const provider = turn.response.inference_provider === "nvidia" ? "NVIDIA" : "OpenRouter";
        reply.append(element("small", "inference", `${provider} · ${turn.response.inference_model}`));
      }
    } else if (pending.get(chat.id) === turn) {
      reply.append(element("p", "pending", pendingMessage));
    } else {
      reply.append(element("p", "", "The answer didn’t load. Retry to send the same question again."));
      const retry = element("button", "retry", "Retry");
      retry.type = "button";
      retry.disabled = pending.has(chat.id);
      retry.addEventListener("click", () => {
        input.focus();
        requestAnswer(chat, turn);
      });
      reply.append(retry);
    }
    article.append(question, reply);
    thread.append(article);
  }
  updateComposer();
  if (scroll) {
    if (revealedReply && revealedReply.offsetHeight > viewport.clientHeight) {
      const offset = revealedReply.getBoundingClientRect().top - viewport.getBoundingClientRect().top;
      viewport.scrollTop += offset - 12;
    } else {
      viewport.scrollTop = viewport.scrollHeight;
    }
  }
}

function selectChat(id) {
  state.activeId = id;
  save();
  input.value = "";
  renderHistory();
  renderConversation({ scroll: true });
  setDrawer(false, input);
}

async function requestAnswer(chat, turn) {
  pending.set(chat.id, turn);
  const announcement = byId("announcement");
  announcement.textContent = pendingMessage;
  renderConversation({ scroll: true });
  let status = "Answer ready.";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: turn.message, listing_ids: turn.listingIds || undefined,
        listing_context: turn.listingContext || undefined, previous_question: turn.previousQuestion,
      }),
      signal: AbortSignal.timeout(live ? 85000 : 15000),
    });
    if (!response.ok) throw new Error("Chat request failed");
    turn.response = await response.json();
  } catch {
    status = "The answer didn’t load. Retry is available.";
  } finally {
    pending.delete(chat.id);
    save();
    if (state.activeId === chat.id) {
      announcement.textContent = status;
      const nearBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 80;
      renderConversation({ scroll: nearBottom, reveal: turn.response ? turn : null });
    }
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || pending.has(activeChat()?.id)) return;

  let chat = activeChat();
  if (!chat) {
    chat = { id: crypto.randomUUID(), title: message.replace(/\s+/g, " ").slice(0, 65), turns: [] };
    state.activeId = chat.id;
  }
  state.chats = [chat, ...state.chats.filter((item) => item !== chat)];
  const previous = chat.turns.at(-1);
  const turn = {
    message, response: null, previousQuestion: previous?.message,
    listingIds: previous?.response?.listing_ids,
    listingContext: previous?.response?.listing_context,
  };
  chat.turns.push(turn);
  input.value = "";
  input.focus();
  save();
  renderHistory();
  requestAnswer(chat, turn);
});

input.addEventListener("input", updateComposer);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!send.disabled) form.requestSubmit();
  }
});
for (const example of document.querySelectorAll(".examples button")) {
  example.addEventListener("click", () => {
    input.value = example.querySelector("[data-question]").textContent;
    updateComposer();
    input.focus();
  });
}

byId("new-chat").addEventListener("click", () => selectChat(null));
menu.addEventListener("click", () => setDrawer(true));
scrim.addEventListener("click", () => setDrawer(false, menu));
phone.addEventListener("change", () => {
  const drawerWasOpen = !scrim.hidden;
  setDrawer(false, drawerWasOpen ? input : undefined);
  placeComposer();
  updateComposer();
});
sidebar.addEventListener("keydown", (event) => {
  if (scrim.hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    setDrawer(false, menu);
  }
  if (event.key === "Tab") {
    const buttons = sidebar.querySelectorAll("button");
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});

renderHistory();
renderConversation({ scroll: true });
