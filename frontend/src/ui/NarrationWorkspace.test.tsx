import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiClient } from "../api/client";
import type { NarrationResult } from "../api/types";
import { NarrationWorkspace, narrationVideo, safeHref } from "./NarrationWorkspace";

function result(stage = "awaiting_review"): NarrationResult {
  return {
    id: "narration", project_id: "project", media_id: "source", job_id: "current",
    stage, error: null, revision: 2, approved_revision: 2, script: [], analysis: {},
    options: {}, artifacts: [
      { id: "old-video", job_id: "old", filename: "video.mp4", media_type: "video/mp4", sha256: "a", size_bytes: 10, script_revision: 1 },
      { id: "new-video", job_id: "current", filename: "video.mp4", media_type: "video/mp4", sha256: "b", size_bytes: 12, script_revision: 2 },
    ],
  };
}

describe("source narration", () => {
  it("allows same-origin downloads and rejects unsafe or cross-origin destinations", () => {
    const origin = "https://forge.example";
    expect(safeHref("blob:https://forge.example/123", origin)).toBe("blob:https://forge.example/123");
    expect(safeHref("/v1/artifacts/123", origin)).toBe("https://forge.example/v1/artifacts/123");
    expect(safeHref("https://forge.example/v1/artifacts/123", origin))
      .toBe("https://forge.example/v1/artifacts/123");
    expect(safeHref("https://other.example/file", origin)).toBeUndefined();
    expect(safeHref("blob:https://other.example/123", origin)).toBeUndefined();
    expect(safeHref("javascript:alert(1)", origin)).toBeUndefined();
    expect(safeHref("data:text/html,unsafe", origin)).toBeUndefined();
    expect(safeHref("blob:https://forge.example/123", undefined)).toBeUndefined();
  });
  it("only selects a completed artifact from the current narration job", () => {
    expect(narrationVideo(result())?.id).toBe("new-video");
    expect(narrationVideo(result("awaiting_script_approval"))).toBeUndefined();
    expect(narrationVideo(result("failed"))).toBeUndefined();
    expect(narrationVideo({ ...result(), revision: 3 })).toBeUndefined();
  });
  it("rejects foreign narration identity on status reads", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ ...result(), project_id: "foreign" }), { status: 200 }),
    );
    await expect(new ApiClient("", fetcher).getNarration("project", "narration", "synthetic"))
      .rejects.toThrow("identity mismatch");
  });
  it("routes source analysis separately from Wan with a bounded source ID", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(result("queued")), { status: 202 }),
    );
    await new ApiClient("", fetcher).startNarration("project", "source",
      { analysis_mode: "manual" }, "request", "synthetic");
    expect(fetcher.mock.calls[0]?.[0]).toBe("/v1/projects/project/narrations");
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      media_id: "source", options: { analysis_mode: "manual" },
    });
  });
  it("shows source, evidence and approval controls without a generated preview", () => {
    const html = renderToStaticMarkup(<NarrationWorkspace projectId="project" token="synthetic"
      media={[]} onActivate={() => undefined} />);
    expect(html).toContain("Start narration analysis");
    expect(html).toContain("Manual script");
    expect(html).toContain("Vision and OCR are optional enhancements");
    expect(html).not.toContain("Narrated source video");
    expect(html).not.toContain("Generate AI video");
  });
});
