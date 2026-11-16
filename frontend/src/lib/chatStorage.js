/** One chat thread per station, kept in the browser so a page switch or reload keeps it. */
const KEY = (stationId) => `aivy.chat.${stationId}`;
const MAX_MESSAGES = 50; // ponytail: a demo thread, not a mailbox

const EMPTY = { conversationId: null, messages: [], proposal: null };

/** The saved thread for one station; anything unreadable reads as an empty thread. */
export function loadChat(storage, stationId) {
  try {
    const saved = JSON.parse(storage.getItem(KEY(stationId)) ?? "null");
    if (!Array.isArray(saved?.messages)) return { ...EMPTY };
    return {
      conversationId: saved.conversationId ?? null,
      messages: saved.messages.slice(-MAX_MESSAGES),
      proposal: saved.proposal ?? null,
    };
  } catch {
    return { ...EMPTY };
  }
}

/** Writes the thread back; a full or blocked storage only costs the history. */
export function saveChat(storage, stationId, { conversationId, messages, proposal }) {
  try {
    storage.setItem(
      KEY(stationId),
      JSON.stringify({
        conversationId,
        messages: messages.slice(-MAX_MESSAGES),
        proposal,
      }),
    );
  } catch {
    // private mode / quota: the thread still works for this visit
  }
}