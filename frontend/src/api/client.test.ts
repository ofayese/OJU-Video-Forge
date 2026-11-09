import { describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./client";
import { artifactReady, buildRenderInput, moveClip } from "./timeline";
import { MemoryArtifact } from "./memoryArtifact";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ApiClient", () => {
  it("invokes default browser fetch with its global receiver, not the client instance", async () => {
    const fetcher = vi.fn(function (this: unknown) {
      if (this !== globalThis) throw new TypeError("Illegal invocation");
      return Promise.resolve(jsonResponse({ id: "user-one", username: "editor" }, 201));
    });
    vi.stubGlobal("fetch", fetcher);
    try {
      const client = new ApiClient();
      expect(await client.register({ username: "editor", password: "synthetic-password" })).toEqual({
        id: "user-one", username: "editor",
      });
      expect(fetcher.mock.calls).toHaveLength(1);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it.each([true, false])("uses the default %s production mode API base through the expected proxy", async (production) => {
    vi.stubEnv("PROD", production);
    vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8000/");
    vi.resetModules();
    try {
      const { ApiClient: ConfiguredClient } = await import("./client");
      const fetcher = vi.fn<typeof fetch>().mockImplementation(async () =>
        jsonResponse({ access_token: "synthetic-token", user: { id: "user-one", username: "editor" } }),
      );
      const client = new ConfiguredClient(undefined, fetcher);
      await client.register({ username: "editor", password: "synthetic-password" });
      await client.login({ username: "editor", password: "synthetic-password" });
      await client.listProjects("synthetic-token");
      await client.getArtifact("job-one", "synthetic-token");
      const prefix = production ? "" : "http://localhost:8000";
      expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
        `${prefix}/v1/auth/register`, `${prefix}/v1/auth/session`,
        `${prefix}/v1/projects`, `${prefix}/v1/jobs/job-one/artifact`,
      ]);
      for (const [, options] of fetcher.mock.calls) {
        expect(options?.credentials).toBe("omit");
        expect(options?.cache).toBe("no-store");
        expect(options?.redirect).toBe("error");
      }
    } finally {
      vi.unstubAllEnvs();
      vi.resetModules();
    }
  });

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
      jsonResponse({ id: "media-one", project_id: "project/one", filename: "clip.mp4", size_bytes: 12, content_type: "video/mp4", sha256: "abc123" }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    await client.uploadMedia("project/one", new File(["synthetic"], "clip.mp4", { type: "video/mp4" }), "token");

    const [url, options] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("http://localhost:8000/v1/projects/project%2Fone/media");
    expect(new Headers(options?.headers).get("Content-Type")).toBeNull();
    expect(options?.body).toBeInstanceOf(FormData);
    expect((options?.body as FormData).get("file")).toBeInstanceOf(File);
    expect((options?.body as FormData).get("upload")).toBeNull();
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer token");
    expect(options?.cache).toBe("no-store");
  });

  it("submits a versioned render with the idempotency key and polls the global job", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ id: "job-one", status: "queued" }, 202))
      .mockResolvedValueOnce(jsonResponse({ id: "job-one", status: "running" }));
    const client = new ApiClient("http://localhost:8000", fetcher);
    const input = {
      operation: "render_timeline_v1" as const,
      timeline: {
        version: 1 as const, clips: [{ media_id: "media-one", in_ms: 0, out_ms: 5000 }],
        output: { profile: "preview" as const, format: "mp4" as const },
        title: { text: "Synthetic title", start_ms: 0, end_ms: 1000 },
        captions: [{ text: "Synthetic caption", start_ms: 1000, end_ms: 2000 }],
        audio: { media_id: "audio-one", in_ms: 0, volume_percent: 75 },
      },
    };

    await client.createRenderJob("project-one", input, "request-one", "token");
    await client.getJob("job-one", "token");

    const [submitUrl, submitOptions] = fetcher.mock.calls[0] ?? [];
    expect(submitUrl).toBe("http://localhost:8000/v1/projects/project-one/jobs");
    expect(new Headers(submitOptions?.headers).get("Idempotency-Key")).toBe("request-one");
    expect(new Headers(submitOptions?.headers).get("Authorization")).toBe("Bearer token");
    expect(JSON.parse(String(submitOptions?.body))).toEqual(input);
    expect(fetcher.mock.calls[1]?.[0]).toBe(
      "http://localhost:8000/v1/jobs/job-one",
    );
  });

  it("retrieves a job artifact as a blob without following server-supplied URLs", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3]), { status: 200, headers: { "Content-Type": "video/mp4" } }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    const result = await client.getArtifact("job/one", "token");

    expect(fetcher.mock.calls[0]?.[0]).toBe(
      "http://localhost:8000/v1/jobs/job%2Fone/artifact",
    );
    const options = fetcher.mock.calls[0]?.[1];
    expect(options?.cache).toBe("no-store");
    expect(options?.credentials).toBe("omit");
    expect(options?.redirect).toBe("error");
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer token");
    expect(result).toBeInstanceOf(Blob);
    expect([...new Uint8Array(await result.arrayBuffer())]).toEqual([1, 2, 3]);
  });

  it("uses the documented model route and bearer authorization", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ models: [{ id: "wan22-ti2v-5b", operation: "wan22_t2v", status: "configured" }] }),
    );
    const client = new ApiClient("http://localhost:8000", fetcher);

    const result = await client.listModels("session-token");

    expect(result.models[0]?.id).toBe("wan22-ti2v-5b");
    const request = fetcher.mock.calls[0];
    expect(request?.[0]).toBe("http://localhost:8000/v1/models");
    expect(new Headers(request?.[1]?.headers).get("Authorization")).toBe(["Bearer", "session-token"].join(" "));
  });

  it("submits a project-scoped Wan generation job with idempotency", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      id: "job-1",
      operation: "wan22_t2v",
      status: "queued",
      artifact: null,
      error: null,
      review_contract_version: 1,
      review_version: 0,
      review_enabled: false,
      can_review: false,
      review_artifact: null,
    }));
    const client = new ApiClient("http://localhost:8000", fetcher);

    await client.createWanGenerationJob("project/one", {
      operation: "wan22_t2v",
      prompt: "synthetic coastline",
      negative_prompt: "",
      width: 512,
      height: 288,
      frames: 17,
      fps: 8,
      seed: 42,
    }, "request-key", "session-token");

    const [url, options] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("http://localhost:8000/v1/jobs");
    expect(options?.method).toBe("POST");
    expect(new Headers(options?.headers).get("Idempotency-Key")).toBe("request-key");
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer session-token");
    expect(JSON.parse(String(options?.body))).toEqual({
      operation: "wan22_t2v",
      project_id: "project/one",
      prompt: "synthetic coastline",
      negative_prompt: "",
      width: 512,
      height: 288,
      frames: 17,
      fps: 8,
      seed: 42,
    });
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
    expect(request?.[1]?.cache).toBe("no-store");
  });

  it("downloads a project artifact through the membership-authorized route", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response("video"));
    const client = new ApiClient("http://localhost:8000", fetcher);

    const artifact = await client.getProjectArtifact("project/one", "artifact/one", "session-token");

    expect(await artifact.text()).toBe("video");
    const request = fetcher.mock.calls[0];
    expect(request?.[0]).toBe(
      "http://localhost:8000/v1/projects/project%2Fone/artifacts/artifact%2Fone",
    );
    expect(new Headers(request?.[1]?.headers).get("Authorization")).toBe("Bearer session-token");
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
    expect(expired).toHaveBeenCalledExactlyOnceWith("expired-token");
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

  it("registers against the real account contract then creates a bearer session", async () => {
    const user = { id: "user-one", username: "editor" };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(user, 201))
      .mockResolvedValueOnce(jsonResponse({ user, access_token: "synthetic-token" }));
    const client = new ApiClient("", fetcher);
    const credentials = { username: "editor", password: "synthetic-password" };
    expect(await client.register(credentials)).toEqual(user);
    expect((await client.login(credentials)).user).toEqual(user);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(["/v1/auth/register", "/v1/auth/session"]);
    for (const [, options] of fetcher.mock.calls) {
      expect(JSON.parse(String(options?.body))).toEqual(credentials);
      expect(options?.credentials).toBe("omit");
      expect(options?.cache).toBe("no-store");
      expect(new Headers(options?.headers).has("Authorization")).toBe(false);
    }
  });

  it("lists media manifests with project-scoped bearer authorization", async () => {
    const manifests = [{ id: "media", project_id: "project", filename: "synthetic.mp4",
      content_type: "video/mp4", size_bytes: 9, sha256: "a".repeat(64) }];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(manifests));
    const client = new ApiClient("", fetcher);
    expect(await client.listMedia("project/one", "token")).toEqual(manifests);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/v1/projects/project%2Fone/media");
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer token");
  });

  it.each([401, 403])("rejects protected artifact responses (%i) without returning bytes", async (status) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "Denied" }, status));
    const client = new ApiClient("", fetcher);
    const expired = vi.fn();
    client.setUnauthorizedHandler(expired);
    await expect(client.getArtifact("job", "token")).rejects.toMatchObject({ status });
    expect(expired).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
  });
});

describe("timeline v1 preflight", () => {
  const clips = [
    { media_id: "one", in_ms: 0, out_ms: 5000 },
    { media_id: "two", in_ms: 125, out_ms: 2125 },
  ];
  it("preserves clip order and omits disabled options from the versioned payload", () => {
    expect(buildRenderInput(clips)).toEqual({
      operation: "render_timeline_v1",
      timeline: { version: 1, clips, output: { profile: "preview", format: "mp4" } },
    });
    const reordered = moveClip(clips, 1, -1);
    expect(buildRenderInput(reordered).timeline.clips.map((clip) => clip.media_id)).toEqual(["two", "one"]);
    expect(clips[0].media_id).toBe("one");
    expect(moveClip(clips, 0, -1)).toEqual(clips);
  });
  it.each([
    [], Array.from({ length: 17 }, () => clips[0]),
    [{ ...clips[0], in_ms: -125 }],
    [{ ...clips[0], out_ms: 0 }],
    [{ ...clips[0], out_ms: 1001 }],
    [{ ...clips[0], out_ms: 125.5 }],
    [{ ...clips[0], out_ms: NaN }],
    [{ ...clips[0], out_ms: 120000 }, clips[1]],
  ].map((invalid) => ({ invalid })))("rejects invalid/over-limit clips before submission", ({ invalid }) => {
    expect(() => buildRenderInput(invalid)).toThrow();
  });

  describe("memory-only artifacts", () => {
    it("revokes preview/download URLs on replacement and clear", async () => {
      const createObjectURL = vi.fn(() => "blob:synthetic");
      const revokeObjectURL = vi.fn();
      const client = new ApiClient("", vi.fn<typeof fetch>().mockImplementation(async () => new Response("synthetic-video")));
      const artifact = new MemoryArtifact(client, { createObjectURL, revokeObjectURL });
      expect(await artifact.load("job", "token")).toBe("blob:synthetic");
      await artifact.load("other-job", "token");
      expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith("blob:synthetic");
      artifact.clear();
      expect(revokeObjectURL).toHaveBeenCalledTimes(2);
    });
    it("does not create a URL for a late response after session/project cleanup", async () => {
      let finish!: (value: Response) => void;
      const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
      const createObjectURL = vi.fn(() => "blob:synthetic");
      const artifact = new MemoryArtifact(new ApiClient("", fetcher), { createObjectURL, revokeObjectURL: vi.fn() });
      const pending = artifact.load("job", "old-token");
      artifact.clear();
      finish(new Response("synthetic-video"));
      expect(await pending).toBeNull();
      expect(createObjectURL).not.toHaveBeenCalled();
    });
  });
  it("validates optional overlays/audio without accepting overlapping captions", () => {
    const title = { text: "Synthetic title", start_ms: 0, end_ms: 1000 };
    const captions = [{ text: "Caption", start_ms: 1000, end_ms: 2000 }];
    const audio = { media_id: "audio", in_ms: 10, volume_percent: 75 };
    expect(buildRenderInput(clips, title, captions, audio).timeline).toMatchObject({ title, captions, audio });
    expect(() => buildRenderInput(clips, { ...title, text: "\n" })).toThrow();
    expect(() => buildRenderInput(clips, { ...title, end_ms: 8000 })).toThrow();
    expect(() => buildRenderInput(clips, title, [...captions, captions[0]])).toThrow("non-overlapping");
    expect(() => buildRenderInput(clips, title, captions, { ...audio, volume_percent: 101 })).toThrow();
  });
  it("recognizes awaiting_review as verified completion, not a polling state", () => {
    expect(artifactReady("awaiting_review")).toBe(true);
    expect(artifactReady("approved")).toBe(true);
    expect(artifactReady("running")).toBe(false);
    expect(artifactReady("failed")).toBe(false);
  });
});
