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
  const body = action.args.body;
  const preview = body.length > PREVIEW_CHARS ? `${body.slice(0, PREVIEW_CHARS)}…` : body;
  return (
    <div className="mt-2 max-w-[80%] rounded-xl border border-amber-300 bg-amber-50 p-3 text-left text-sm">
      <div className="font-medium text-neutral-900">Create note: {action.args.title}</div>
      <div className="mt-1 whitespace-pre-wrap text-neutral-600">{preview}</div>
      {status === "pending" || status === "busy" ? (
        <div className="mt-2 flex gap-2">
          <button onClick={onApprove} disabled={status === "busy"}
            className="rounded-lg bg-neutral-900 px-3 py-1 text-white disabled:opacity-50">Approve</button>
          <button onClick={onDeny} disabled={status === "busy"}
            className="rounded-lg border border-neutral-300 px-3 py-1 disabled:opacity-50">Deny</button>
        </div>
      ) : (
        <div className="mt-2 text-xs text-neutral-500">{DONE[status]}</div>
      )}
    </div>
  );
}
