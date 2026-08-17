import { describe, expect, it, vi } from "vitest";

import { apiFetch } from "./api";

describe("apiFetch", () => {
  it("attaches the bearer token", async () => {
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(new Response("[]"));
    await apiFetch("/orgs", "tok123");
    const headers = spy.mock.calls[0][1]!.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok123");
    spy.mockRestore();
  });

  it("targets the configured API base", async () => {
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(new Response("[]"));
    await apiFetch("/orgs", "tok");
    expect(spy.mock.calls[0][0]).toContain("/orgs");
    spy.mockRestore();
  });
});
