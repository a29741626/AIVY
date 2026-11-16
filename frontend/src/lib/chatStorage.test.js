import test from "node:test";
import assert from "node:assert/strict";
import { loadChat, saveChat } from "./chatStorage.js";

function fakeStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (key) => map.get(key) ?? null,
    setItem: (key, value) => map.set(key, String(value)),
  };
}

const thread = {
  conversationId: "conv_abc12345",
  messages: [
    {
      id: "q-1",
      role: "user",
      text: "How can I save more today?",
      time: "4:05 PM",
    },
    {
      id: "msg_abc12345",
      role: "ai",
      text: "Charge at 1 PM.",
      time: "4:05 PM",
      evidence: [{ label: "Import price", value: 0.21, unit: "AUD/kWh" }],
    },
  ],
  proposal: { strategy_id: "prop_strat_abc12345", type: "charge", mode_name: "save_money" },
};

test("a saved thread comes back for the same station", () => {
  const storage = fakeStorage();
  saveChat(storage, "SBX-001", thread);
  assert.deepEqual(loadChat(storage, "SBX-001"), thread);
});

test("each station keeps its own thread", () => {
  const storage = fakeStorage();
  saveChat(storage, "SBX-001", thread);
  assert.deepEqual(loadChat(storage, "SBX-002"), {
    conversationId: null,
    messages: [],
    proposal: null,
  });
});

test("junk or a wrong shape reads as an empty thread", () => {
  assert.deepEqual(loadChat(fakeStorage({ "aivy.chat.SBX-001": "{not json" }), "SBX-001").messages, []);
  assert.deepEqual(loadChat(fakeStorage({ "aivy.chat.SBX-001": '{"messages":"nope"}' }), "SBX-001").messages, []);
  assert.equal(loadChat(fakeStorage(), "SBX-001").conversationId, null);
});

test("a full storage only loses the write, not the thread", () => {
  const storage = {
    getItem: () => null,
    setItem: () => {
      throw new Error("QuotaExceededError");
    },
  };
  assert.doesNotThrow(() => saveChat(storage, "SBX-001", thread));
});