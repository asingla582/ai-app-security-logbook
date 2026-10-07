"use client";

import { useEffect, useState } from "react";

import { approveAction, denyAction, listPending } from "@/lib/actions";
import { apiFetch } from "@/lib/api";
import { supabase } from "@/lib/supabase";

import { AssistantMarkdown } from "./AssistantMarkdown";
import { ApprovalCard, type CardStatus, type PendingAction } from "./ApprovalCard";
import { ToolIndicator, type ToolUsed } from "./ToolIndicator";

type Conversation = { id: string; title: string };
type Message = { role: string; content: string; toolUsed?: ToolUsed; pendingId?: string };

async function token(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? "";
}

export default function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Record<string, { action: PendingAction; status: CardStatus }>>({});

  async function loadConversations() {
    const res = await apiFetch("/conversations", await token());
    if (res.ok) setConversations(await res.json());
  }

  async function openConversation(id: string) {
    setActiveId(id);
    const res = await apiFetch(`/conversations/${id}`, await token());
    if (res.ok) setMessages((await res.json()).messages);
    const restored = await listPending(id, await token());
    setPending(Object.fromEntries(restored.map((a) => [a.id, { action: a, status: "pending" as CardStatus }])));
  }

  // Disabling while busy is what stops a double click from sending two approvals.
  async function decide(id: string, fn: (tok: string) => Promise<CardStatus>) {
    setPending((p) => ({ ...p, [id]: { ...p[id], status: "busy" } }));
    const status = await fn(await token());
    setPending((p) => ({ ...p, [id]: { ...p[id], status } }));
  }

  useEffect(() => {
    loadConversations();
  }, []);

  async function newConversation() {
    const res = await apiFetch("/conversations", await token(), { method: "POST" });
    if (!res.ok) return;
    const conv = await res.json();
    await loadConversations();
    setActiveId(conv.id);
    setMessages([]);
    setPending({});
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!activeId || !input.trim() || busy) return;
    const text = input;
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", content: text }]);
    const res = await apiFetch(`/conversations/${activeId}/messages`, await token(), {
      method: "POST",
      body: JSON.stringify({ content: text }),
    });
    if (res.ok) {
      const { reply, tool_used, pending_action: pa } = await res.json();
      if (pa) setPending((p) => ({ ...p, [pa.id]: { action: pa, status: "pending" } }));
      setMessages((m) => [
        ...m,
        { role: "assistant", content: reply, toolUsed: tool_used ?? null, pendingId: pa?.id },
      ]);
      loadConversations();
    } else {
      setMessages((m) => [...m, { role: "assistant", content: "(the assistant is unavailable)" }]);
    }
    setBusy(false);
  }

  function renderCard(id: string) {
    return (
      <ApprovalCard
        action={pending[id].action}
        status={pending[id].status}
        onApprove={() => decide(id, (tok) => approveAction(pending[id].action, tok))}
        onDeny={() => decide(id, (tok) => denyAction(id, tok))}
      />
    );
  }

  const referenced = new Set(messages.map((m) => m.pendingId).filter(Boolean));
  const orphans = Object.keys(pending).filter((id) => !referenced.has(id));

  return (
    <div className="flex gap-6">
      <aside className="w-56 shrink-0">
        <button
          onClick={newConversation}
          className="mb-3 w-full rounded-lg bg-neutral-900 px-3 py-2 text-sm font-medium text-white hover:bg-neutral-800"
        >
          New chat
        </button>
        <ul className="space-y-1">
          {conversations.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => openConversation(c.id)}
                className={`w-full truncate rounded-lg px-3 py-2 text-left text-sm ${
                  c.id === activeId
                    ? "bg-neutral-200 text-neutral-900"
                    : "text-neutral-600 hover:bg-neutral-100"
                }`}
              >
                {c.title}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-h-[70vh] flex-1 flex-col rounded-xl border border-neutral-200 bg-white">
        {!activeId ? (
          <div className="flex flex-1 items-center justify-center text-sm text-neutral-400">
            Start a new chat, or pick one on the left.
          </div>
        ) : (
          <>
            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {messages.map((m, i) => (
                <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
                  {/* Assistant output is markdown, sanitized twice: the API strips
                      images and de-fangs unproven links before storing, and
                      AssistantMarkdown independently refuses images and raw HTML.
                      User text stays escaped plain text. */}
                  <span
                    className={`inline-block max-w-[80%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm ${
                      m.role === "user"
                        ? "bg-neutral-900 text-white"
                        : "bg-neutral-100 text-neutral-900"
                    }`}
                  >
                    {m.role === "user" ? m.content : <AssistantMarkdown content={m.content} />}
                  </span>
                  {m.role === "assistant" && <ToolIndicator toolUsed={m.toolUsed ?? null} />}
                  {m.pendingId && pending[m.pendingId] && (
                    renderCard(m.pendingId)
                  )}
                </div>
              ))}
              {orphans.length > 0 && (
                <div>
                  <div className="text-xs font-medium text-neutral-500">Waiting for your approval</div>
                  {orphans.map((id) => (
                    <div key={id}>
                      {renderCard(id)}
                    </div>
                  ))}
                </div>
              )}
              {busy && <div className="text-sm text-neutral-400">…</div>}
            </div>
            <form onSubmit={send} className="flex gap-2 border-t border-neutral-200 p-3">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Message the assistant"
                className="flex-1 rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900"
              />
              <button
                type="submit"
                disabled={busy}
                className="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
              >
                Send
              </button>
            </form>
          </>
        )}
      </section>
    </div>
  );
}
