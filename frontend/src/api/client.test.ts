import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApiClient", () => {
  it("uses the versioned session and project endpoints", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ access_token: "session-token", user: { name: "editor" } }))
      .mockResolvedValueOnce(jsonResponse([{ id: "project-one", name: "My project" }]))
      .mockResolvedValueOnce(jsonResponse({ id: "project-two", name: "New project" }, 201));
    const client = new ApiClient("http://localhost:8000", fetcher);

    await client.login({ username: "editor", password: "example-password" });
    expect(await client.listProjects("session-token")).toEqual([
      { id: "project-one", name: "My project" },
    ]);
    await client.createProject({ name: "New project" }, "session-token");

    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      "http://localhost:8000/v1/auth/session",
      "http://localhost:8000/v1/projects",
      "http://localhost:8000/v1/projects",
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      username: "editor",
      password: "example-password",
    });
  });

  it("uses the versioned session route and disables browser caching", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ name: "editor", roles: ["producer"], provider: "local" }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    await client.currentUser("session-token");

    const [url, options] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("http://localhost:8000/v1/auth/session");
    expect(new Headers(options?.headers).get("Authorization")).toBe(["Bearer", "session-token"].join(" "));
    expect(options?.cache).toBe("no-store");
  });

  it("submits a project-scoped upload without overriding the multipart boundary", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ id: "media-one", size_bytes: 12, media_type: "video/mp4", sha256: "abc123" }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    await client.uploadMedia("project/one", new File(["synthetic"], "clip.mp4", { type: "video/mp4" }), "token");

    const [url, options] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("http://localhost:8000/v1/projects/project%2Fone/media");
    expect(new Headers(options?.headers).get("Content-Type")).toBeNull();
    expect(options?.body).toBeInstanceOf(FormData);
    expect((options?.body as FormData).get("file")).toBeInstanceOf(File);
  });

  it("submits a versioned render with the idempotency key and polls a scoped job", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ id: "job-one", status: "queued" }, 202))
      .mockResolvedValueOnce(jsonResponse({ id: "job-one", status: "running" }));
    const client = new ApiClient("http://localhost:8000", fetcher);
    const input = {
      timeline: { version: 1 as const, clips: [{ media_id: "media-one", in_ms: 0, out_ms: 5000 }] },
      preset: "preview" as const,
    };

    await client.createRenderJob("project-one", input, "request-one", "token");
    await client.getProjectJob("project-one", "job-one", "token");

    const [submitUrl, submitOptions] = fetcher.mock.calls[0] ?? [];
    expect(submitUrl).toBe("http://localhost:8000/v1/projects/project-one/jobs");
    expect(new Headers(submitOptions?.headers).get("Idempotency-Key")).toBe("request-one");
    expect(JSON.parse(String(submitOptions?.body))).toEqual(input);
    expect(fetcher.mock.calls[1]?.[0]).toBe(
      "http://localhost:8000/v1/projects/project-one/jobs/job-one",
    );
  });

  it("retrieves a project-scoped artifact as a blob", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { "Content-Type": "video/mp4" } }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    const result = await client.getProjectArtifact("project-one", "artifact/one", "token");

    expect(fetcher.mock.calls[0]?.[0]).toBe(
      "http://localhost:8000/v1/projects/project-one/artifacts/artifact%2Fone",
    );
    expect(result).toBeInstanceOf(Blob);
    expect([...new Uint8Array(await result.arrayBuffer())]).toEqual([1, 2, 3]);
  });

  it("uses the documented model route and bearer authorization", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ models: [{ id: "wan22-ti2v-5b", operation: "wan22_t2v", status: "requires_local_gpu_profile_and_approved_workflow" }] }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    const result = await client.listModels("session-token");

    expect(result.models[0]?.id).toBe("wan22-ti2v-5b");
    const request = fetcher.mock.calls[0];
    expect(request?.[0]).toBe("http://localhost:8000/v1/models");
    expect(new Headers(request?.[1]?.headers).get("Authorization")).toBe(["Bearer", "session-token"].join(" "));
  });

  it("downloads an artifact through the owner-authorized API route", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("video"));
    const client = new ApiClient("http://localhost:8000", fetcher);

    const artifact = await client.getArtifact("job/one", "session-token");

    expect(await artifact.text()).toBe("video");
    const request = fetcher.mock.calls[0];
    expect(request?.[0]).toBe("http://localhost:8000/v1/jobs/job%2Fone/artifact");
    expect(new Headers(request?.[1]?.headers).get("Authorization")).toBe(
      ["Bearer", "session-token"].join(" "),
    );
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

  it("does not clear the session when the API denies a role-protected request", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ detail: "Insufficient permissions" }, 403),
    );
    const expired = vi.fn();
    const client = new ApiClient("", fetcher);
    client.setUnauthorizedHandler(expired);

    await expect(client.listProjects("viewer-token")).rejects.toMatchObject({
      name: "ApiError",
      status: 403,
    } satisfies Partial<ApiError>);
    expect(expired).not.toHaveBeenCalled();
  });
});
