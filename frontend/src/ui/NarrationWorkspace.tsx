import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { MediaAsset, NarrationResult, NarrationSegment, NarrationSettings } from "../api/types";

const PROCESSING = new Set(["queued", "transcribing", "analyzing_scenes", "drafting_script",
  "synthesizing_audio", "mixing_audio", "verifying_artifact"]);

export function narrationVideo(row: NarrationResult) {
  return row.stage === "awaiting_review"
    ? row.artifacts.find((item) => item.job_id === row.job_id &&
        item.script_revision === row.revision && item.filename === "video.mp4")
    : undefined;
}

export function NarrationWorkspace({ projectId, token, media, onActivate }: {
  projectId: string; token: string; media: MediaAsset[]; onActivate: () => void;
}) {
  const [sourceId, setSourceId] = useState("");
  const [mode, setMode] = useState("transcript");
  const [vision, setVision] = useState(false);
  const [style, setStyle] = useState("instructional");
  const [instructions, setInstructions] = useState("");
  const [row, setRow] = useState<NarrationResult | null>(null);
  const [script, setScript] = useState<NarrationSegment[]>([]);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [downloads, setDownloads] = useState<Record<string, string>>({});
  const [settings, setSettings] = useState<NarrationSettings>({
    voice: "en-us", rate: 160, audio_mode: "replace", original_volume: 20, narration_gain: 100,
  });
  const epoch = useRef(0);
  const locked = useRef(false);
  const pendingStart = useRef<{ payload: string; key: string } | null>(null);
  const urls = useRef<string[]>([]);
  function clearUrls() {
    for (const url of urls.current) URL.revokeObjectURL(url);
    urls.current = [];
    setVideoUrl(""); setAudioUrl(""); setSourceUrl(""); setDownloads({});
  }
  useEffect(() => () => {
    epoch.current++;
    for (const url of urls.current) URL.revokeObjectURL(url);
  }, []);
  function accept(result: NarrationResult) {
    if (result.project_id !== projectId || result.media_id !== sourceId) {
      throw new Error("Narration response does not belong to the selected source/project.");
    }
    setRow(result); setScript(result.script); setDirty(false);
  }
  async function action(work: () => Promise<NarrationResult>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(""); clearUrls(); onActivate();
    const ticket = ++epoch.current;
    try {
      const result = await work();
      if (ticket === epoch.current) accept(result);
    } catch (reason) {
      if (ticket === epoch.current) setError(reason instanceof Error ? reason.message : "Narration request failed.");
    } finally { locked.current = false; if (ticket === epoch.current) setBusy(false); }
  }
  useEffect(() => {
    if (!row || !PROCESSING.has(row.stage)) return;
    let active = true;
    const timer = setInterval(() => {
      if (locked.current) return;
      const ticket = epoch.current;
      void api.getNarration(projectId, row.id, token).then((result) => {
        if (active && ticket === epoch.current) {
          setRow(result); setScript(result.script); setDirty(false);
        }
      }).catch((reason: unknown) => {
        if (active && ticket === epoch.current) {
          setError(reason instanceof Error ? reason.message : "Status polling failed.");
          clearInterval(timer);
        }
      });
    }, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [row?.id, row?.stage, projectId, token]);
  async function download(id: string, kind: "source" | "video" | "audio" | "download") {
    const ticket = epoch.current;
    setError("");
    try {
      const blob = await api.getProjectArtifact(projectId, id, token);
      if (ticket !== epoch.current) return;
      const url = URL.createObjectURL(blob);
      urls.current.push(url);
      if (kind === "source") setSourceUrl(url);
      else if (kind === "video") setVideoUrl(url);
      else if (kind === "audio") setAudioUrl(url);
      else setDownloads((old) => ({ ...old, [id]: url }));
    } catch (reason) {
      if (ticket === epoch.current) setError(reason instanceof Error ? reason.message : "Authorized retrieval failed.");
    }
  }
  const processing = row && PROCESSING.has(row.stage);
  const editable = row && ["awaiting_script_approval", "script_approved", "script_rejected", "failed", "awaiting_review"].includes(row.stage);
  const output = row ? narrationVideo(row) : undefined;
  const audio = row?.artifacts.find((item) => item.job_id === row.job_id && item.script_revision === row.revision &&
    (item.filename === "narration.wav" || item.filename === "voice-preview.wav"));
  function edit(index: number, patch: Partial<NarrationSegment>) {
    clearUrls(); setDirty(true);
    setScript((items) => items.map((item, i) => i === index ? { ...item, ...patch } : item));
  }
  return <section className="panel" aria-labelledby="narration-heading">
    <h2 id="narration-heading">Narrate existing video</h2>
    <p>Preserves the uploaded visuals. CPU speech/OCR require the optional narration worker.
      ASR needs an approved local Whisper model. Vision and OCR are optional enhancements;
      transcript-based and manually authored scripts do not require them.</p>
    {error && <p role="alert">{error}</p>}
    <fieldset disabled={busy || !!processing}>
      <label>Source video<select value={sourceId} onChange={(event) => {
        epoch.current++; clearUrls(); setSourceId(event.target.value); setRow(null);
        pendingStart.current = null; setScript([]); setDirty(false); setError(""); onActivate();
      }}>
        <option value="">Select an uploaded video</option>
        {media.filter((item) => item.content_type.startsWith("video/")).map((item) =>
          <option value={item.id} key={item.id}>{item.filename}</option>)}
      </select></label>
      <label>Analysis mode<select value={mode} onChange={(event) => setMode(event.target.value)}>
        <option value="transcript">Speech transcription (local model required)</option>
        <option value="manual">Manual script + sampled scenes (no ASR)</option>
      </select></label>
      <label><input type="checkbox" checked={vision} onChange={(event) => setVision(event.target.checked)} />
        Analyze up to six frames using a configured local vision model</label>
      <label>Narration style<select value={style} onChange={(event) => setStyle(event.target.value)}>
        <option value="instructional">Instructional</option><option value="documentary">Documentary</option>
        <option value="concise_technical">Concise technical explanation</option>
      </select></label>
      <label>Editorial instructions<textarea maxLength={1000} value={instructions}
        onChange={(event) => setInstructions(event.target.value)} /></label>
      <p>Target language: English. Drafts copy speech hypotheses or uncertain visual observations;
        review the entire script against the source before approval.</p>
      <button disabled={!sourceId} onClick={() => {
        setRow(null); setScript([]); setDirty(false);
        const options = { analysis_mode: mode, vision, style, instructions, language: "en" };
        const payload = JSON.stringify({ sourceId, options });
        if (pendingStart.current?.payload !== payload) {
          pendingStart.current = { payload, key: crypto.randomUUID() };
        }
        const key = pendingStart.current.key;
        void action(async () => {
          const result = await api.startNarration(projectId, sourceId, options, key, token);
          pendingStart.current = null;
          return result;
        });
      }}>Start narration analysis</button>
      <button disabled={!sourceId} onClick={() => void download(sourceId, "source")}>Preview authorized source</button>
    </fieldset>
    {sourceUrl && <video controls src={sourceUrl} aria-label="Original source video" style={{ maxWidth: "100%" }} />}
    {row && <>
      <p aria-live="polite">Operation: narrate_existing_video · Source: {media.find((item) => item.id === row.media_id)?.filename}
        · Stage: {row.stage} · Job: {row.job_id} · Revision: {row.revision}</p>
      {row.error && <p role="alert">{row.error}</p>}
      {row.analysis.transcript && <p>Speech analysis: {row.analysis.transcript.status}
        · Language: {row.analysis.transcript.language ?? "not detected"}</p>}
      {row.analysis.scene_analysis && <p role="status">Optional scene analysis: {row.analysis.scene_analysis.status}
        {row.analysis.scene_analysis.error && ` · ${row.analysis.scene_analysis.error}. Continue with transcript or manual script.`}</p>}
      <p>Vision: {row.analysis.vision ?? "not run"}</p>
      <button disabled={busy} onClick={() => void action(() =>
        api.getNarration(projectId, row.id, token))}>Refresh narration</button>
      {processing && <button disabled={busy} onClick={() => void action(async () => {
        await api.cancelJob(row.job_id, token);
        return api.getNarration(projectId, row.id, token);
      })}>Cancel narration job</button>}
      {row.stage === "failed" && <button disabled={busy} onClick={() =>
        void action(() => api.retryNarration(projectId, row.id, token))}>Retry failed eligible operation</button>}
      <details><summary>Timestamped scene evidence (not confirmed actions)</summary>
        {row.analysis.scenes?.map((scene) => <p key={scene.id}>{scene.id} · {scene.timestamp_ms} ms
          {scene.major_change ? " · visual change detected" : ""} · OCR: {scene.ocr ?? "unavailable"}
          · Vision: {scene.vision?.text ?? "not analyzed"}</p>)}
      </details>
      {script.map((segment, index) => <fieldset disabled={busy || !editable} key={segment.id}>
        <legend>Segment {segment.id}</legend>
        <label>Start (ms)<input type="number" min={0} value={segment.start_ms}
          onChange={(event) => edit(index, { start_ms: Number(event.target.value) })} /></label>
        <label>End (ms)<input type="number" min={1} value={segment.end_ms}
          onChange={(event) => edit(index, { end_ms: Number(event.target.value) })} /></label>
        <label>Narration text<textarea maxLength={1000} value={segment.text}
          onChange={(event) => edit(index, { text: event.target.value })} /></label>
        <p>Evidence: {segment.evidence.join(", ")} · {segment.uncertainty}
          · Estimated speech: {segment.estimated_ms} ms</p>
        <button onClick={() => { clearUrls(); setDirty(true); setScript((items) => items.filter((_, i) => i !== index)); }}>
          Remove segment</button>
      </fieldset>)}
      {editable && <fieldset disabled={busy}>
        <button disabled={script.length >= 200 || (script.at(-1)?.end_ms ?? 0) >=
          Number(row.analysis.media?.format.duration ?? 0) * 1000}
          onClick={() => {
            const start = script.at(-1)?.end_ms ?? 0;
            const end = Math.min(start + 5000, Number(row.analysis.media?.format.duration ?? 0) * 1000);
            setScript((items) => [...items, { id: crypto.randomUUID(), start_ms: start, end_ms: end,
              text: "", evidence: ["manual"], uncertainty: "Manually authored; verify against source", estimated_ms: 0 }]);
            clearUrls(); setDirty(true);
          }}>Add timestamped segment</button>
        <button disabled={!dirty || !script.length} onClick={() =>
          void action(() => api.editNarration(projectId, row.id, row.revision, script, token))}>Save new script revision</button>
        <button disabled={dirty || row.stage !== "awaiting_script_approval"} onClick={() =>
          void action(() => api.approveNarration(projectId, row.id, row.revision, "approved", token))}>Approve complete script</button>
        <button disabled={dirty || row.stage !== "awaiting_script_approval"} onClick={() =>
          void action(() => api.approveNarration(projectId, row.id, row.revision, "rejected", token))}>Reject script</button>
        <label>Local voice<select value={settings.voice} onChange={(event) =>
          setSettings({ ...settings, voice: event.target.value as NarrationSettings["voice"] })}>
          <option value="en-us">English US · eSpeak NG</option><option value="en-gb">English UK · eSpeak NG</option>
        </select></label>
        <label>Words/minute<input type="number" min={100} max={220} value={settings.rate}
          onChange={(event) => setSettings({ ...settings, rate: Number(event.target.value) })} /></label>
        <button disabled={dirty || !["awaiting_script_approval", "script_approved"].includes(row.stage)}
          onClick={() => void action(() => api.previewNarrationVoice(projectId, row.id, row.revision, settings, token))}>
          Generate fixed voice sample (not final narration)</button>
        <label>Audio mode<select value={settings.audio_mode} onChange={(event) =>
          setSettings({ ...settings, audio_mode: event.target.value as NarrationSettings["audio_mode"] })}>
          <option value="replace">Replace original with narration</option><option value="mix">Mix original + narration</option>
          <option value="preserve">Preserve original audio (no speech synthesis)</option>
        </select></label>
        <label>Original volume (%)<input type="number" min={0} max={100} value={settings.original_volume}
          onChange={(event) => setSettings({ ...settings, original_volume: Number(event.target.value) })} /></label>
        <label>Narration gain (%)<input type="number" min={0} max={150} value={settings.narration_gain}
          onChange={(event) => setSettings({ ...settings, narration_gain: Number(event.target.value) })} /></label>
        <button disabled={dirty || row.stage !== "script_approved" || row.approved_revision !== row.revision}
          onClick={() => void action(() => api.renderNarration(projectId, row.id, row.revision, settings, token))}>
          Generate approved narrated MP4</button>
      </fieldset>}
      {audio && <button onClick={() => void download(audio.id, "audio")}>Preview narration audio</button>}
      {audioUrl && <audio controls src={audioUrl} aria-label="Approved narration audio" />}
      {output && <button onClick={() => void download(output.id, "video")}>Preview completed narration MP4</button>}
      {videoUrl && <><video controls src={videoUrl} aria-label="Narrated source video" style={{ maxWidth: "100%" }} />
        <a href={videoUrl} download="narrated-video.mp4">Download narrated MP4</a></>}
      {row.artifacts.filter((item) => item.filename !== "video.mp4" &&
        (item.script_revision === row.revision || item.filename.startsWith("transcript.") ||
         item.filename === "analysis.json")).map((item) =>
        <p key={item.id}><button onClick={() => void download(item.id, "download")}>Load {item.filename}</button>
          {downloads[item.id] && <a href={downloads[item.id]} download={item.filename}>Download {item.filename}</a>}</p>)}
    </>}
  </section>;
}
