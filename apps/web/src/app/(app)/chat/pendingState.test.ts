import { describe, expect, it } from "vitest";

import type { PendingAction } from "./ApprovalCard";
import { settleDecision, withStatus, type PendingMap } from "./pendingState";

const action = {
  id: "a1", tool_name: "create_note", args: { title: "T", body: "B" },
  args_sha256: "h", flags: { urls: [], doc_spans: [] }, expires_at: "x",
} as PendingAction;

describe("withStatus", () => {
  it("leaves state unchanged for a missing id", () => {
    const p: PendingMap = {};
    expect(withStatus(p, "gone", "executed")).toBe(p);
  });
  it("updates only the status of a known id", () => {
    const out = withStatus({ a1: { action, status: "pending" } }, "a1", "busy");
    expect(out.a1).toEqual({ action, status: "busy" });
  });
});

describe("settleDecision", () => {
  it("passes a resolved status through", async () => {
    await expect(settleDecision(async () => "executed")).resolves.toBe("executed");
  });
  it("maps a rejection (network failure) to error, so the card never stays busy", async () => {
    await expect(settleDecision(async () => { throw new TypeError("Failed to fetch"); }))
      .resolves.toBe("error");
  });
});
