import type { CreateRenderInput, TimelineClip, TimelineOverlay } from "./types";

// UI preflight only. The API remains authoritative for media and rendering limits.
export function buildRenderInput(
  clips: TimelineClip[], title?: TimelineOverlay, captions: TimelineOverlay[] = [],
  audio?: { media_id: string; in_ms: number; volume_percent: number },
): CreateRenderInput {
  const integer = (value: number, min: number, max: number) =>
    Number.isInteger(value) && value >= min && value <= max;
  if (clips.length < 1 || clips.length > 16) throw new Error("Add 1–16 video clips.");
  let total = 0;
  for (const clip of clips) {
    if (!clip.media_id || !integer(clip.in_ms, 0, 120000) ||
        !integer(clip.out_ms, clip.in_ms + 1, 120000) ||
        clip.in_ms * 24 % 1000 || clip.out_ms * 24 % 1000) {
      throw new Error("Clip times must be ordered integer milliseconds on the preview frame grid (use multiples of 125 ms).");
    }
    total += clip.out_ms - clip.in_ms;
  }
  if (total > 120000) throw new Error("Timeline must not exceed 120 seconds.");
  if (captions.length > 100) throw new Error("Use at most 100 captions.");
  for (const overlay of [...(title ? [title] : []), ...captions]) {
    if (!overlay.text.trim() || [...overlay.text].length > 200 ||
        /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(overlay.text) ||
        !integer(overlay.start_ms, 0, total - 1) ||
        !integer(overlay.end_ms, overlay.start_ms + 1, total)) {
      throw new Error("Title/captions need 1–200 printable characters and valid times within the timeline.");
    }
  }
  if (captions.some((cue, index) => index > 0 && cue.start_ms < captions[index - 1].end_ms)) {
    throw new Error("Captions must be ordered and non-overlapping.");
  }
  if (audio && (!audio.media_id || !integer(audio.in_ms, 0, 120000) ||
      !integer(audio.volume_percent, 0, 100))) {
    throw new Error("Audio requires a media ID, integer start time and volume from 0–100.");
  }
  return {
    operation: "render_timeline_v1",
    timeline: {
      version: 1, clips: clips.map((clip) => ({ ...clip })),
      output: { profile: "preview", format: "mp4" },
      ...(title ? { title: { ...title } } : {}),
      ...(captions.length ? { captions: captions.map((cue) => ({ ...cue })) } : {}),
      ...(audio ? { audio: { ...audio } } : {}),
    },
  };
}

export function moveClip(clips: TimelineClip[], index: number, offset: -1 | 1): TimelineClip[] {
  const result = [...clips];
  const target = index + offset;
  if (index >= 0 && index < result.length && target >= 0 && target < result.length) {
    [result[index], result[target]] = [result[target], result[index]];
  }
  return result;
}

export function artifactReady(status: string): boolean {
  return ["awaiting_review", "approved", "rejected", "completed"].includes(status);
}
