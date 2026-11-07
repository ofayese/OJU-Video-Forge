import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "./client";
import { ReviewController, type ReviewState } from "./reviewController";
import type { JobReview, VideoJob } from "./types";
import { ReviewEvidence, ReviewPanel } from "../ui/ReviewPanel";

const job: VideoJob = {
  id: "job", operation: "render_test", status: "awaiting_review", artifact: null, error: null,
  review_contract_version: 1, review_version: 0, review_enabled: true, can_review: true,
  review_artifact: { id: "opaque", sha256: "a".repeat(64) },
};
const evidence: JobReview = {
  contract_version: 1, job_id: "job", actor: "owner", at: "2026-11-06T00:00:00Z",
  artifact_id: "opaque", artifact_sha256: "a".repeat(64), expected_version: 0,
  version: 1, decision: "approved",
};
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}
function loadedClient(nextJob = job) {
  const fetcher = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(response(nextJob))
    .mockResolvedValueOnce(response([]));
  return { fetcher, controller: new ReviewController(new ApiClient("", fetcher)) };
}

describe("review controller", () => {
  it.each(["approved", "rejected"] as const)("sends the v1 %s payload and refreshes history", async (decision) => {
    const { controller, fetcher } = loadedClient();
    await controller.load("synthetic-token", "job");
    fetcher.mockResolvedValueOnce(response({ ...job, status: decision, can_review: false, review_version: 1 }))
      .mockResolvedValueOnce(response({ ...job, status: decision, can_review: false, review_version: 1 }))
      .mockResolvedValueOnce(response([{ ...evidence, decision }]));
    await controller.decide(decision);
    const [url, options] = fetcher.mock.calls[2]!;
    expect(url).toBe("/v1/jobs/job/review");
    expect(options?.method).toBe("POST");
    expect(new Headers(options?.headers).get("Authorization")).toBe("Bearer " + "synthetic-token");
    expect(JSON.parse(String(options?.body))).toEqual({
      decision, expected_version: 0, expected_artifact: job.review_artifact,
    });
    expect(controller.getState().history[0]?.decision).toBe(decision);
    expect(controller.getState().busy).toBe(false);
  });

  it.each([
    { ...job, review_enabled: false }, { ...job, can_review: false },
    { ...job, review_artifact: null },
  ])("does not send disabled/read-only/ineligible decisions", async (nextJob) => {
    const { controller, fetcher } = loadedClient(nextJob);
    await controller.load("token", "job");
    await controller.decide("approved");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("blocks repeat clicks while busy", async () => {
    const { controller, fetcher } = loadedClient();
    await controller.load("token", "job");
    let finish!: (response: Response) => void;
    fetcher.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const pending = controller.decide("approved");
    expect(controller.getState().busy).toBe(true);
    await controller.decide("rejected");
    expect(fetcher).toHaveBeenCalledTimes(3);
    fetcher.mockResolvedValueOnce(response(job)).mockResolvedValueOnce(response([]));
    finish(response(job));
    await pending;
  });

  it.each([409, 503])("refreshes on %i without automatically resubmitting", async (status) => {
    const { controller, fetcher } = loadedClient();
    await controller.load("token", "job");
    fetcher.mockResolvedValueOnce(response({ detail: "State changed" }, status))
      .mockResolvedValueOnce(response({ ...job, can_review: false, review_version: 1 }))
      .mockResolvedValueOnce(response([evidence]));
    await controller.decide("approved");
    expect(fetcher).toHaveBeenCalledTimes(5);
    expect(controller.getState().job?.review_version).toBe(1);
    expect(controller.getState().history).toEqual([evidence]);
    expect(controller.getState().error).toContain("Refreshed");
    expect(controller.getState().needsConfirmation).toBe(true);
    await controller.decide("approved");
    expect(fetcher).toHaveBeenCalledTimes(5);
    controller.acknowledge();
    expect(controller.getState().needsConfirmation).toBeUndefined();
  });

  it("requires renewed review even when conflict refresh is still eligible", async () => {
    const { controller, fetcher } = loadedClient();
    await controller.load("token", "job");
    fetcher.mockResolvedValueOnce(response({ detail: "Stale version" }, 409))
      .mockResolvedValueOnce(response({ ...job, review_version: 1 }))
      .mockResolvedValueOnce(response([]));
    await controller.decide("approved");
    await controller.decide("approved");
    expect(fetcher).toHaveBeenCalledTimes(5);
    controller.acknowledge();
    fetcher.mockResolvedValueOnce(response({ ...job, can_review: false }))
      .mockResolvedValueOnce(response({ ...job, can_review: false }))
      .mockResolvedValueOnce(response([evidence]));
    await controller.decide("approved");
    expect(JSON.parse(String(fetcher.mock.calls[5]?.[1]?.body)).expected_version).toBe(1);
  });

  it.each([401, 403, 500])("clears confidential data on %i", async (status) => {
    const { controller, fetcher } = loadedClient();
    await controller.load("token", "job");
    fetcher.mockResolvedValueOnce(response({ detail: "Denied" }, status));
    await controller.decide("approved");
    expect(controller.getState()).toEqual({ job: null, history: [], busy: false, error: "Denied" });
    await controller.decide("approved");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("clears confidential data and credentials on network disconnect", async () => {
    const { controller, fetcher } = loadedClient();
    await controller.load("old-token", "old-job");
    fetcher.mockRejectedValueOnce(new TypeError("offline"));
    await controller.decide("approved");
    expect(controller.getState().job).toBeNull();
    expect(controller.getState().history).toEqual([]);
    expect(controller.getState().error).toContain("Unable to reach");
    await controller.decide("approved");
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("clears data on 401 while refreshing a conflict", async () => {
    const { controller, fetcher } = loadedClient();
    await controller.load("token", "job");
    fetcher.mockResolvedValueOnce(response({ detail: "Conflict" }, 409))
      .mockResolvedValueOnce(response({ detail: "Expired" }, 401))
      .mockResolvedValueOnce(response([evidence]));
    await controller.decide("approved");
    expect(controller.getState()).toEqual({ job: null, history: [], busy: false, error: "Expired" });
  });

  it("does not restore private results from a cleared in-flight session", async () => {
    let resolve!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(() => new Promise((done) => { resolve = done; }))
      .mockResolvedValueOnce(response([evidence]));
    const controller = new ReviewController(new ApiClient("", fetcher));
    const loading = controller.load("token", "job");
    controller.clear();
    resolve(response(job));
    await loading;
    expect(controller.getState()).toEqual({ job: null, history: [], busy: false, error: "" });
  });
  it("does not refresh using another session after an in-flight decision is cleared", async () => {
    const { controller, fetcher } = loadedClient();
    await controller.load("token", "job");
    let resolve!: (response: Response) => void;
    fetcher.mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
    const pending = controller.decide("approved");
    controller.clear();
    resolve(response(job));
    await pending;
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(controller.getState().job).toBeNull();
  });
  it("never restores old actor results or sends old credentials after an actor change", async () => {
    let finishOld!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }))
      .mockResolvedValueOnce(response([evidence]))
      .mockResolvedValueOnce(response({ ...job, id: "new-job" }))
      .mockResolvedValueOnce(response([]));
    const controller = new ReviewController(new ApiClient("", fetcher));
    const oldLoad = controller.load("old-token", "old-job");
    await controller.load("new-token", "new-job");
    finishOld(response(job));
    await oldLoad;
    expect(controller.getState().job?.id).toBe("new-job");
    expect(controller.getState().history).toEqual([]);
    fetcher.mockResolvedValueOnce(response({ ...job, can_review: false }))
      .mockResolvedValueOnce(response({ ...job, can_review: false }))
      .mockResolvedValueOnce(response([]));
    await controller.decide("approved");
    expect(fetcher.mock.calls[4]?.[0]).toBe("/v1/jobs/new-job/review");
    expect(new Headers(fetcher.mock.calls[4]?.[1]?.headers).get("Authorization")).toBe("Bearer " + "new-token");
  });
});

describe("review presentation (server-rendered without DOM dependencies)", () => {
  function render(state: ReviewState) {
    return renderToStaticMarkup(createElement(ReviewEvidence, { state, onDecision: vi.fn() }));
  }
  it("presents immutable identity, actor, timestamp, decision and version without media paths", () => {
    const html = render({ job, history: [evidence], busy: false, error: "" });
    expect(html).toContain("approved by owner");
    expect(html).toContain(evidence.at);
    expect(html).toContain("Version 0 → 1");
    expect(html).toContain("opaque");
    expect(html).toContain(evidence.artifact_sha256);
    expect(html).not.toContain("test.mp4");
    expect(html).not.toContain("disabled=");
  });
  it.each([
    { job: { ...job, review_enabled: false }, busy: false, message: "Review disabled" },
    { job: { ...job, can_review: false }, busy: false, message: "Read-only" },
    { job, busy: true, message: "Loading review" },
  ])("shows disabled/busy/read-only controls", ({ job: nextJob, busy, message }) => {
    const html = render({ job: nextJob, busy, history: [], error: "API error" });
    expect(html.match(/disabled=""/g)).toHaveLength(2);
    expect(html).toContain(message);
    expect(html).toContain('role="alert"');
    expect(html).toContain("No decisions recorded");
  });
  it("exposes a token path outside account login and explains the bridge", () => {
    const html = renderToStaticMarkup(createElement(ReviewPanel));
    expect(html).toContain('type="password"');
    expect(html).toContain("never stored");
    expect(html).toContain("#8/#9");
    expect(html).toContain("Load job and history");
    expect(html).toContain("not independent human signoff");
  });

  it("requires acknowledgement of conflict-refreshed evidence before decisions", () => {
    const html = render({ job, history: [], busy: false, error: "Conflict", needsConfirmation: true });
    expect(html.match(/disabled=""/g)).toHaveLength(2);
    expect(html).toContain("I reviewed the refreshed artifact details");
  });
});
