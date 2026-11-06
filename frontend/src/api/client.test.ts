import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApiClient", () => {
  it("uses the documented model route and bearer authorization", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ models: [{ id: "wan22-ti2v-5b", operation: "wan22_t2v", status: "requires_workflow_registration" }] }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    const result = await client.listModels("session-token");

    expect(result.models[0]?.id).toBe("wan22-ti2v-5b");
    const request = fetcher.mock.calls[0];
    expect(request?.[0]).toBe("http://localhost:8000/v1/models");
    expect(new Headers(request?.[1]?.headers).get("Authorization")).toBe(
      "Bearer session-token",
    );
  });

  it("uses only the existing per-ID job status route", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ id: "job/one", operation: "render_test", status: "queued", artifact: null, error: null }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    await client.getJob("job/one", "token");

    expect(fetcher.mock.calls[0]?.[0]).toBe("http://localhost:8000/v1/jobs/job%2Fone");
  });

  it("notifies the app and rejects when a session has expired", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ detail: "Not authenticated" }, 401),
    );
    const expired = vi.fn();
    const client = new ApiClient("", fetcher);
    client.setUnauthorizedHandler(expired);

    await expect(client.currentUser("expired-token")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
    } satisfies Partial<ApiError>);
    expect(expired).toHaveBeenCalledOnce();
  });
});
