/**
 * Chats page — list all existing chat sessions.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { chatsApi, type Chat } from "../api/chats";
import { charactersApi, type Character } from "../api/characters";

function ChatRow({ chat, character }: { chat: Chat; character?: Character }) {
  const navigate = useNavigate();
  const date = new Date(chat.updated_at).toLocaleDateString();

  return (
    <button
      onClick={() => navigate(`/chat/${chat.id}`)}
      className="w-full text-left flex items-center gap-3 px-4 py-3 rounded-xl border border-gray-800 bg-gray-900 hover:border-gray-700 hover:bg-gray-850 transition-colors"
    >
      <div className="w-10 h-10 rounded-full bg-indigo-900 border border-indigo-800 flex items-center justify-center text-base flex-shrink-0">
        {character?.name[0].toUpperCase() ?? "?"}
      </div>
      <div className="flex-1 min-w-0">
        <p className="font-medium text-sm text-gray-200 truncate">
          {chat.title || character?.name || "Untitled chat"}
        </p>
        {character && (
          <p className="text-xs text-gray-500 mt-0.5">{character.name}</p>
        )}
      </div>
      <span className="text-xs text-gray-600 flex-shrink-0">{date}</span>
    </button>
  );
}

export function ChatsPage() {
  const [chats, setChats] = useState<Chat[]>([]);
  const [characters, setCharacters] = useState<Record<string, Character>>({});
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    (async () => {
      const [chatList, charList] = await Promise.all([
        chatsApi.list(),
        charactersApi.list(),
      ]);
      setChats(chatList);
      setCharacters(Object.fromEntries(charList.map((c) => [c.id, c])));
    })().finally(() => setLoading(false));
  }, []);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-semibold">Chats</h1>
          <button
            onClick={() => navigate("/characters")}
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm text-white font-medium transition-colors"
          >
            + New Chat
          </button>
        </div>

        {loading ? (
          <div className="text-center text-gray-500 py-20">Loading…</div>
        ) : chats.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-gray-500 mb-4">No chats yet.</p>
            <button onClick={() => navigate("/characters")} className="text-indigo-400 hover:text-indigo-300 text-sm">
              Pick a character to start →
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {chats.map((chat) => (
              <ChatRow key={chat.id} chat={chat} character={characters[chat.character_id]} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
