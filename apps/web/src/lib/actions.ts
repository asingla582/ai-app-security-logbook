import { apiFetch } from "./api";
import type { CardStatus, PendingAction } from "@/app/(app)/chat/ApprovalCard";

// Week 8: the approve request carries only the hash the card displayed. The server
// executes its own stored copy; there is no way to send args from here.
export async function approveAction(action: PendingAction, token: string): Promise<CardStatus> {
  const res = await apiFetch(`/actions/${action.id}/approve`, token, {
    method: "POST",
    body: JSON.stringify({ args_sha256: action.args_sha256 }),
  });
  if (res.status === 410) return "expired";
  if (!res.ok) return "error";
  const { status } = await res.json();
  return status === "executed" ? "executed" : "failed";
}

export async function denyAction(id: string, token: string): Promise<CardStatus> {
  const res = await apiFetch(`/actions/${id}/deny`, token, { method: "POST" });
  return res.ok ? "denied" : "error";
}

export async function listPending(conversationId: string, token: string): Promise<PendingAction[]> {
  const res = await apiFetch(`/actions?status=pending&conversation_id=${conversationId}`, token);
  return res.ok ? res.json() : [];
}
