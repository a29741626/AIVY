import { request } from "./client";

/** One grounded assistant turn; the envelope's data is a ChatResponseData. */
export const sendChatMessage = (
  stationId,
  message,
  { conversationId, signal } = {},
) =>
  request("/api/assistant/chat", {
    method: "POST",
    signal,
    body: {
      station_id: stationId,
      message,
      conversation_id: conversationId,
    },
  });
