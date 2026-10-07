import type { CardStatus, PendingAction } from "./ApprovalCard";

export type PendingMap = Record<string, { action: PendingAction; status: CardStatus }>;

// A decision can resolve after the map was reset (conversation switch). Updating a
// missing id must be a no-op, never re-insert a half-formed entry.
export function withStatus(p: PendingMap, id: string, status: CardStatus): PendingMap {
  return p[id] ? { ...p, [id]: { ...p[id], status } } : p;
}
