import type { CardStatus, PendingAction } from "./ApprovalCard";

export type PendingMap = Record<string, { action: PendingAction; status: CardStatus }>;

// A decision can resolve after the map was reset (conversation switch). Updating a
// missing id must be a no-op, never re-insert a half-formed entry.
export function withStatus(p: PendingMap, id: string, status: CardStatus): PendingMap {
  return p[id] ? { ...p, [id]: { ...p[id], status } } : p;
}

// A decision that throws (network failure, token refresh failure) must still settle
// the card; otherwise it stays on "busy" with both buttons disabled.
export async function settleDecision(fn: () => Promise<CardStatus>): Promise<CardStatus> {
  try {
    return await fn();
  } catch {
    return "error";
  }
}
