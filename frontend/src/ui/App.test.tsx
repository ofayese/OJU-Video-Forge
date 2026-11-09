import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { JobOutputDescription, ProjectWorkspace } from "./App";
import type { VideoJob } from "../api/types";

describe("operation and output display", () => {
  it("requires a source video for narration and distinguishes generation from uploads", () => {
    const html = renderToStaticMarkup(<ProjectWorkspace projectId="project" token="synthetic" />);
    expect(html).toContain("Narrate existing video");
    expect(html).toContain("Start narration analysis");
    expect(html).toContain("Select an uploaded video");
    expect(html).toContain("not narration of an upload");
    expect(html).toContain("render_timeline_v1");
    expect(html).not.toContain("Verified video complete");
  });

  it.each([
    ["wan22_t2v", "AI-generated video (not narration)"],
    ["narrate_existing_video", "No verified narrated video"],
  ])("identifies %s outputs without claiming narration success", (operation, expected) => {
    const job: VideoJob = {
      id: "job", operation, media_id: operation === "wan22_t2v" ? null : "source",
      status: "awaiting_review", artifact: null, error: null,
      review_contract_version: 1, review_version: 0, review_enabled: false,
      can_review: false, review_artifact: null,
    };
    const html = renderToStaticMarkup(<JobOutputDescription job={job} media={[{
      id: "source", project_id: "project", filename: "synthetic.mp4",
      size_bytes: 12, content_type: "video/mp4", sha256: "a".repeat(64),
    }]} />);
    expect(html).toContain(expected);
    expect(html).not.toContain("Verified video complete");
    if (operation === "narrate_existing_video") {
      expect(html).toContain("synthetic.mp4");
      expect(html).not.toContain("complete; awaiting review");
    } else {
      expect(html).toContain("Source video: None");
    }
  });
});
