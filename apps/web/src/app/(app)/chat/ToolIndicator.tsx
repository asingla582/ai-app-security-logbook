// A one-line note under an assistant reply when the turn took a tool action.
// Purely presentational: the API decides what ran and returns tool_used; this
// only labels it. Single-step means at most one action per turn.

export type ToolUsed = { name: string; summary: string } | null;

const LABELS: Record<string, string> = {
  search_documents: "searched documents",
  create_note: "created a note",
};

export function ToolIndicator({ toolUsed }: { toolUsed: ToolUsed }) {
  if (!toolUsed) return null;
  const label = LABELS[toolUsed.name] ?? toolUsed.name;
  return (
    <span className="mt-1 block text-xs text-neutral-400" title={toolUsed.summary}>
      {label}
    </span>
  );
}
