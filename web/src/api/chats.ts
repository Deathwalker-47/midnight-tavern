import { apiFetch } from "./client";

export interface Chat {
  id: string;
  user_id: string;
  character_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  chat_id: string;
  role: "user" | "character" | "dm";
  content: string;
  metadata_: Record<string, unknown> | null;
  created_at: string;
}

export interface MessageListResponse {
  messages: Message[];
  total: number;
  has_more: boolean;
}

export const chatsApi = {
  list: () => apiFetch<Chat[]>("/api/v1/chats"),

  create: (character_id: string, title?: string) =>
    apiFetch<Chat>("/api/v1/chats", {
      method: "POST",
      body: JSON.stringify({ character_id, title }),
    }),

  get: (id: string) => apiFetch<Chat>(`/api/v1/chats/${id}`),

  delete: (id: string) => apiFetch<void>(`/api/v1/chats/${id}`, { method: "DELETE" }),

  listMessages: (chatId: string, limit = 50) =>
    apiFetch<MessageListResponse>(`/api/v1/chats/${chatId}/messages?limit=${limit}`),
};

/**
 * Send a message and process the SSE stream.
 * Calls onEvent for each event received.
 */
export async function sendMessage(
  chatId: string,
  content: string,
  onEvent: (type: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/v1/chats/${chatId}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`Send message failed: ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventType = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith("data: ") && eventType) {
        try {
          const data = JSON.parse(line.slice(6)) as Record<string, unknown>;
          onEvent(eventType, data);
        } catch {
          // ignore malformed
        }
        eventType = "";
      }
    }
  }
}
