/**
 * Chat page — message thread with SSE streaming and DM integration.
 *
 * Layout:
 *   ┌─────────────────────────────────┬──────────┐
 *   │  message list (scrollable)      │ DMPanel  │
 *   ├─────────────────────────────────┤ (if DM   │
 *   │  composer                       │ active)  │
 *   └─────────────────────────────────┴──────────┘
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { chatsApi, sendMessage, type Message } from "../api/chats";
import { charactersApi, type Character } from "../api/characters";
import { ApiError } from "../api/client";
import { DMPanel } from "../components/DMPanel";
import { DMMessage, type DMMessageType } from "../components/chat/DMMessage";
import { useDMStore, handleDMEvent } from "../store/dmStore";
import { dmApi, type StatDefinition } from "../api/dm";

// ── Message bubble ────────────────────────────────────────────────────────────

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
          isUser
            ? "bg-indigo-600 text-white rounded-br-sm"
            : "bg-gray-800 text-gray-100 rounded-bl-sm"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

function StreamingBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-start">
      <div className="max-w-[75%] rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm leading-relaxed bg-gray-800 text-gray-100">
        {text || <span className="inline-flex gap-1 items-center text-gray-500"><span className="animate-pulse">●</span><span className="animate-pulse delay-100">●</span><span className="animate-pulse delay-200">●</span></span>}
      </div>
    </div>
  );
}

// ── DM event list item ────────────────────────────────────────────────────────

interface DMEvent {
  id: string;
  message: DMMessageType;
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function ChatPage() {
  const { chatId } = useParams<{ chatId: string }>();
  const navigate = useNavigate();

  const [messages, setMessages] = useState<Message[]>([]);
  const [dmEvents, setDmEvents] = useState<DMEvent[]>([]);
  const [character, setCharacter] = useState<Character | null>(null);
  const [statSchema, setStatSchema] = useState<StatDefinition[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const { isActive: dmActive } = useDMStore();

  // Load chat + messages + character
  useEffect(() => {
    if (!chatId) return;
    (async () => {
      try {
        const [chat, msgs] = await Promise.all([
          chatsApi.get(chatId),
          chatsApi.listMessages(chatId),
        ]);
        setMessages(msgs.messages);
        const char = await charactersApi.get(chat.character_id);
        setCharacter(char);

        // Check for DM session
        try {
          const session = await dmApi.getSession(chatId);
          if (session.is_active) {
            useDMStore.getState().setSession(session.id, session.ruleset_id);
            const ruleset = await dmApi.getRuleset(session.ruleset_id);
            setStatSchema(ruleset.stat_schema);
            const sheets = await dmApi.listSheets(session.id);
            if (sheets[0]) {
              useDMStore.getState().updateStats(sheets[0].stats as Record<string, number | string>);
              useDMStore.getState().updateInventory(sheets[0].inventory);
              useDMStore.getState().updateSkills(sheets[0].skills as Record<string, number | string>);
            }
          }
        } catch {
          // no DM session — that's fine
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) navigate("/characters");
      } finally {
        setLoading(false);
      }
    })();

    return () => {
      abortRef.current?.abort();
      useDMStore.getState().clearSession();
    };
  }, [chatId, navigate]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, dmEvents, streamingText]);

  const pushDMEvent = useCallback((msg: DMMessageType) => {
    setDmEvents((prev) => [...prev, { id: `${Date.now()}-${Math.random()}`, message: msg }]);
  }, []);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    const content = input.trim();
    if (!content || streaming || !chatId) return;

    setInput("");
    setError(null);
    setStreaming(true);
    setStreamingText("");
    setDmEvents([]);

    // Optimistically add user message
    const tempMsg: Message = {
      id: `temp-${Date.now()}`,
      chat_id: chatId,
      role: "user",
      content,
      metadata_: null,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempMsg]);

    const abort = new AbortController();
    abortRef.current = abort;

    try {
      await sendMessage(
        chatId,
        content,
        (type, data) => {
          // DM events
          if (type.startsWith("dm_")) {
            handleDMEvent(type, data);

            if (type === "dm_roll") {
              pushDMEvent({
                kind: "roll",
                roll: {
                  expression: data.expression as string,
                  allRolls: data.all_rolls as number[],
                  keptRolls: data.kept_rolls as number[],
                  modifier: (data.modifier as number) ?? 0,
                  total: data.total as number,
                  context: data.context as string | undefined,
                },
              });
            } else if (type === "dm_stat_update") {
              const changes = (data.changes as Array<{name: string; display_name: string; old_value: number | string; new_value: number | string; delta: number | null}>) ?? [];
              if (changes.length > 0) {
                pushDMEvent({
                  kind: "stat_update",
                  changes,
                  deathState: data.death_state as boolean | undefined,
                });
              }
            } else if (type === "dm_reject") {
              pushDMEvent({ kind: "rejection", reason: data.reason as string });
            } else if (type === "dm_ask") {
              pushDMEvent({ kind: "ask", question: data.question as string });
            } else if (type === "dm_done") {
              const summary = (data as { summary?: string }).summary;
              if (summary) pushDMEvent({ kind: "ruling", text: summary });
            }
            return;
          }

          // Story AI events
          if (type === "token") {
            setStreamingText((prev) => prev + (data.text as string));
          } else if (type === "done") {
            const finalMsg: Message = {
              id: data.message_id as string,
              chat_id: chatId,
              role: "character",
              content: data.content as string,
              metadata_: null,
              created_at: new Date().toISOString(),
            };
            setMessages((prev) => [...prev.filter((m) => m.id !== tempMsg.id), finalMsg]);
            setStreamingText("");
          } else if (type === "error") {
            setError((data.message as string) ?? "Something went wrong");
          }
        },
        abort.signal,
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setError(err instanceof Error ? err.message : "Failed to send");
      }
    } finally {
      setStreaming(false);
    }
  }

  if (loading) {
    return <div className="flex h-full items-center justify-center text-gray-500">Loading…</div>;
  }

  // Build interleaved message list
  // We render all stored messages + DM events + streaming bubble
  const allEvents = [...dmEvents];

  return (
    <div className="flex h-full overflow-hidden">
      {/* Main chat area */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Character header */}
        {character && (
          <div className="flex-shrink-0 h-12 border-b border-gray-800 flex items-center px-4 gap-3">
            <div className="w-7 h-7 rounded-full bg-indigo-900 border border-indigo-700 flex items-center justify-center text-sm">
              {character.name[0].toUpperCase()}
            </div>
            <span className="font-medium text-sm text-gray-200">{character.name}</span>
            {dmActive && (
              <span className="ml-auto text-xs text-indigo-400 flex items-center gap-1">
                <span>⚔</span> DM Active
              </span>
            )}
          </div>
        )}

        {/* Message list */}
        <div className="flex-1 overflow-y-auto scrollbar-thin px-4 py-4 space-y-3">
          {messages.map((msg) =>
            msg.role === "dm" ? null : <MessageBubble key={msg.id} message={msg} />
          )}

          {/* DM events for current exchange */}
          {allEvents.map((ev) => (
            <DMMessage key={ev.id} message={ev.message} />
          ))}

          {/* Streaming character response */}
          {streaming && streamingText !== undefined && (
            <StreamingBubble text={streamingText} />
          )}

          {error && (
            <div className="text-center">
              <span className="text-xs text-red-400 bg-red-950/30 px-3 py-1 rounded-full">{error}</span>
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Composer */}
        <form
          onSubmit={handleSend}
          className="flex-shrink-0 border-t border-gray-800 p-3 flex gap-2"
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend(e as unknown as React.FormEvent);
              }
            }}
            disabled={streaming}
            placeholder={streaming ? "Waiting for response…" : "Type a message… (Enter to send, Shift+Enter for newline)"}
            rows={2}
            className="flex-1 px-3 py-2 rounded-xl bg-gray-800 border border-gray-700 text-gray-100 text-sm placeholder-gray-500 focus:outline-none focus:border-indigo-500 resize-none disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={streaming || !input.trim()}
            className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors disabled:opacity-40 self-end"
          >
            {streaming ? "…" : "Send"}
          </button>
        </form>
      </div>

      {/* DM Panel */}
      {dmActive && <DMPanel statSchema={statSchema} />}
    </div>
  );
}
