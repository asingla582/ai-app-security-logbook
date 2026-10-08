// Week 8: the human approval gate's UI. The API decides what is pending and
// executes its own stored copy; this card only shows the action and sends back the
// hash it displayed. Everything renders as escaped text, never markup.

export type PendingAction = {
  id: string;
  tool_name: string;
  args: { title: string; body: string };
  args_sha256: string;
  flags: {
    urls: { url: string; host: string; source: string }[];
    doc_spans: { source: string; excerpt: string }[];
  };
  expires_at: string;
};

export type CardStatus = "pending" | "busy" | "executed" | "denied" | "expired" | "failed" | "error";

// Card v1 truncated the body to this many characters. Kept only so the theater
// script can reproduce the v1 comparison; v2 no longer uses it for rendering.
export const PREVIEW_CHARS = 120;

const DONE: Record<Exclude<CardStatus, "pending" | "busy">, string> = {
  executed: "Note saved.",
  denied: "Denied. Nothing was saved.",
  expired: "This request expired. Ask again to create it.",
  failed: "Approved, but saving failed. Nothing was saved.",
  error: "Could not complete this request.",
};

export function ApprovalCard({ action, status, onApprove, onDeny }: {
  action: PendingAction;
  status: CardStatus;
  onApprove: () => void;
  onDeny: () => void;
}) {
  const { urls, doc_spans } = action.flags;
  const expires = new Date(action.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="mt-2 max-w-[80%] rounded-xl border border-amber-300 bg-amber-50 p-3 text-left text-sm text-neutral-900">
      <div className="text-xs font-medium uppercase tracking-wide text-amber-800">
        The assistant wants to create a note. Read it before approving.
      </div>
      <div className="mt-1 font-medium text-neutral-900">{action.args.title}</div>
      {/* Full body, plain text: React escapes it, no markdown renderer, no truncation.
          What the human approves must be everything that will be written. */}
      <div className="mt-1 whitespace-pre-wrap rounded-lg bg-white p-2 font-mono text-xs text-neutral-800">
        {action.args.body}
      </div>
      {urls.length > 0 && (
        <div className="mt-2">
          <div className="text-xs font-medium text-red-800">Links in this note</div>
          <ul className="mt-1 space-y-1">
            {urls.map((u) => (
              <li key={u.url} className="text-xs">
                {/* Never an anchor: a card must not be a click-through to the link it warns about. */}
                <span className="font-semibold">{u.host}</span>{" "}
                <span className="text-neutral-500">
                  ({u.source === "model" ? "written by the assistant" : `from ${u.source}`})
                </span>
                <div className="break-all font-mono text-neutral-600">{u.url}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
      {doc_spans.length > 0 && (
        <div className="mt-2 text-xs text-neutral-600">
          {doc_spans.map((s, i) => (
            <div key={i}>Copied from {s.source}: “{s.excerpt}”</div>
          ))}
        </div>
      )}
      <div className="mt-2 text-xs text-neutral-500">Expires at {expires}.</div>
      {status === "pending" || status === "busy" ? (
        <div className="mt-2 flex gap-2">
          <button onClick={onApprove} disabled={status === "busy"}
            className="rounded-lg bg-neutral-900 px-3 py-1 text-white disabled:opacity-50">Approve</button>
          <button onClick={onDeny} disabled={status === "busy"}
            className="rounded-lg border border-neutral-300 bg-white px-3 py-1 text-neutral-900 disabled:opacity-50">Deny</button>
        </div>
      ) : (
        <div className="mt-2 text-xs text-neutral-500">{DONE[status]}</div>
      )}
    </div>
  );
}
