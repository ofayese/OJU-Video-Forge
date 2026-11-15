import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { MediaAsset, NarrationResult, NarrationSegment, NarrationSettings, TrainingAgentStatus, TrainingProcedureStep } from "../api/types";

const PROCESSING = new Set(["queued", "transcribing", "analyzing_scenes", "drafting_script",
  "synthesizing_audio", "mixing_audio", "verifying_artifact"]);

export function safeHref(url: string | undefined, trustedOrigin: string | undefined): string | undefined {
  if (!url || !trustedOrigin) return undefined;
  try {
    const originUrl = new URL(trustedOrigin);
    if (!["http:", "https:"].includes(originUrl.protocol) || originUrl.username || originUrl.password) {
      return undefined;
    }
    const parsed = new URL(url, originUrl.origin);
    const credentialUrl = parsed.protocol === "blob:" ? new URL(parsed.pathname) : parsed;
    if (!["blob:", "http:", "https:"].includes(parsed.protocol) ||
      parsed.origin !== originUrl.origin || credentialUrl.username || credentialUrl.password) {
      return undefined;
    }
    return parsed.href;
  } catch {
    return undefined;
  }
}

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
  const [workflow, setWorkflow] = useState("narration");
  const [ocr, setOcr] = useState(true);
  const [vision, setVision] = useState(false);
  const [style, setStyle] = useState("instructional");
  const [instructions, setInstructions] = useState("");
  const [objectives, setObjectives] = useState("");
  const [learnerLevel, setLearnerLevel] = useState("beginner");
  const [feedback, setFeedback] = useState("");
  const [row, setRow] = useState<NarrationResult | null>(null);
  const [script, setScript] = useState<NarrationSegment[]>([]);
  const [procedure, setProcedure] = useState<TrainingProcedureStep[]>([]);
  const [procedureComment, setProcedureComment] = useState("");
  const [dirty, setDirty] = useState(false);
  const [moduleText, setModuleText] = useState("");
  const [moduleDirty, setModuleDirty] = useState(false);
  const [agentStatus, setAgentStatus] = useState<TrainingAgentStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [audioUrl, setAudioUrl] = useState("");
  const [downloads, setDownloads] = useState<Record<string, string>>({});
  const [settings, setSettings] = useState<NarrationSettings>({
    voice: "en-us", rate: 160, audio_mode: "replace", original_volume: 20, narration_gain: 100,
    render_annotations: false,
  });
  const epoch = useRef(0);
  const locked = useRef(false);
  const pendingStart = useRef<{ payload: string; key: string } | null>(null);
  const urls = useRef<string[]>([]);
  function clearUrls() {
    epoch.current++;
    for (const url of urls.current) URL.revokeObjectURL(url);
    urls.current = [];
    setVideoUrl(""); setAudioUrl(""); setSourceUrl(""); setDownloads({});
  }
  function safeBlobUrl(blob: Blob) {
    const url = URL.createObjectURL(blob);
    if (!url.startsWith("blob:")) throw new Error("Unexpected object URL.");
    return url;
  }
  useEffect(() => () => {
    epoch.current++;
    for (const url of urls.current) URL.revokeObjectURL(url);
  }, []);
  function accept(result: NarrationResult) {
    if (result.project_id !== projectId || result.media_id !== sourceId) {
      throw new Error("Narration response does not belong to the selected source/project.");
    }
    setRow(result); setScript(result.script); setProcedure(result.analysis.procedure?.steps ?? []); setDirty(false);
    setModuleText(result.analysis.training_module ? JSON.stringify(result.analysis.training_module.draft, null, 2) : "");
    setModuleDirty(false);
    setAgentStatus(null);
    setProcedureComment("");
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
          setRow(result); setScript(result.script); setProcedure(result.analysis.procedure?.steps ?? []); setDirty(false);
          setModuleText(result.analysis.training_module ? JSON.stringify(result.analysis.training_module.draft, null, 2) : "");
          setModuleDirty(false);
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
  const trustedOrigin = typeof globalThis.location === "undefined"
    ? undefined
    : globalThis.location.origin;
  const videoDownloadHref = safeHref(videoUrl, trustedOrigin);
  async function download(id: string, kind: "source" | "video" | "audio" | "download") {
    const ticket = epoch.current;
    setError("");
    try {
      const blob = await api.getProjectArtifact(projectId, id, token);
      if (ticket !== epoch.current) return;
      const url = safeBlobUrl(blob);
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
        pendingStart.current = null; setScript([]); setProcedure([]); setProcedureComment("");
        setDirty(false); setAgentStatus(null); setError(""); onActivate();
      }}>
        <option value="">Select an uploaded video</option>
        {media.filter((item) => item.content_type.startsWith("video/")).map((item) =>
          <option value={item.id} key={item.id}>{item.filename}</option>)}
      </select></label>
      <label>Workflow<select value={workflow} onChange={(event) => {
        setWorkflow(event.target.value);
        if (event.target.value !== "silent_training") setSettings((old) => ({ ...old, render_annotations: false }));
      }}>
        <option value="narration">Narrate existing video</option>
        <option value="silent_training">Silent recording to training module</option>
      </select></label>
      <label>Analysis mode<select value={workflow === "silent_training" ? "manual" : mode}
        disabled={workflow === "silent_training"} onChange={(event) => setMode(event.target.value)}>
        <option value="transcript">Speech transcription (local model required)</option>
        <option value="manual">Manual script + sampled scenes (no ASR)</option>
      </select></label>
      <label><input type="checkbox" checked={ocr} onChange={(event) => setOcr(event.target.checked)} />
        Extract on-screen text with private Docling RapidOCR</label>
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
        const options = { workflow, analysis_mode: workflow === "silent_training" ? "manual" : mode,
          ocr, vision, style, instructions, language: "en" };
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
      <p>OCR: {row.analysis.ocr ?? "not run"}</p>
      <p>Vision: {row.analysis.vision ?? "not run"}</p>
      {row.options.workflow === "silent_training" && <>
        <button disabled={busy} onClick={() => {
          const ticket = epoch.current;
          setError("");
          void api.getTrainingAgentStatus(projectId, row.id, token).then((result) => {
            if (ticket === epoch.current) setAgentStatus(result);
          }).catch((reason: unknown) => {
            if (ticket === epoch.current) setError(reason instanceof Error ? reason.message : "Agent status failed.");
          });
        }}>Check configured local agent from API</button>
        {agentStatus && <p role="status">API-side agent check: {agentStatus.status} · {agentStatus.reason}
          {" · Worker network reachability is checked during generation."}
          {agentStatus.provider && ` · ${agentStatus.provider.model}`}</p>}
      </>}
      <button disabled={busy} onClick={() => void action(() =>
        api.getNarration(projectId, row.id, token))}>Refresh narration</button>
      {processing && <button disabled={busy} onClick={() => void action(async () => {
        await api.cancelJob(row.job_id, token);
        return api.getNarration(projectId, row.id, token);
      })}>Cancel narration job</button>}
      {row.stage === "failed" && <button disabled={busy} onClick={() =>
        void action(() => api.retryNarration(projectId, row.id, token))}>Retry failed eligible operation</button>}
      <details><summary>Timestamped scene evidence (not confirmed actions)</summary>
        {row.analysis.scenes?.map((scene) => {
          const artifactId = scene.artifact_id;
          const frameUrl = artifactId ? safeHref(downloads[artifactId], trustedOrigin) : undefined;
          return <div key={scene.id}><p>{scene.id} · {scene.timestamp_ms} ms
          {scene.major_change ? " · visual change detected" : ""} · OCR ({scene.ocr_status ?? "unknown"}): {scene.ocr ?? "unavailable"}
          {scene.ocr_error && ` · ${scene.ocr_error}`}
          · OCR engine: {scene.ocr_provenance
            ? `${scene.ocr_provenance.engine} ${scene.ocr_provenance.engine_version} (${scene.ocr_provenance.provider} ${scene.ocr_provenance.provider_version})`
            : "unavailable"}
          · Vision ({scene.vision_status ?? "unknown"}): {scene.vision?.text ?? "not analyzed"}
          {scene.vision_error && ` · ${scene.vision_error}`}</p>
          {artifactId && <button onClick={() => void download(artifactId, "download")}>
            Load source frame</button>}
          {frameUrl &&
            <img src={frameUrl}
              alt={`Source frame at ${scene.timestamp_ms} milliseconds`} style={{ maxWidth: "100%" }} />}
        </div>;})}
      </details>
      {row.stage === "awaiting_procedure_review" && <fieldset disabled={busy}>
        <legend>Review procedure evidence</legend>
        <p>Sampled frames do not prove clicks, hidden keyboard input, or intent.
          Review each interval against the source; accept only supported steps, or reject it.
          Classifications and confidence are your judgments, not automated measurements.
          No teaching draft is generated until the procedure is approved.</p>
        {procedure.map((step, index) => <fieldset key={step.id}>
          <legend>{step.id} · {step.start_ms}–{step.end_ms} ms</legend>
          <p>Evidence: {step.evidence.join(", ")} · Review: {step.uncertainty.join(", ")}</p>
          <label>Evidence-backed step<textarea maxLength={1000} value={step.description}
            onChange={(event) => setProcedure((items) => items.map((item, i) =>
              i === index ? { ...item, description: event.target.value } : item))} /></label>
          <label>Evidence classification<select value={step.evidence_category} onChange={(event) =>
            setProcedure((items) => items.map((item, i) => i === index
              ? { ...item, evidence_category: event.target.value as TrainingProcedureStep["evidence_category"] } : item))}>
            <option value="unverified">Unverified interpretation</option>
            <option value="direct_observation">Directly observed visual state</option>
            <option value="supported_inference">Supported inference</option>
            <option value="missing">Missing evidence</option>
          </select></label>
          <label>Reviewer confidence<select value={step.confidence} onChange={(event) =>
            setProcedure((items) => items.map((item, i) => i === index
              ? { ...item, confidence: event.target.value as TrainingProcedureStep["confidence"] } : item))}>
            <option value="unassessed">Not assessed</option><option value="low">Low</option>
            <option value="medium">Medium</option><option value="high">High</option>
          </select></label>
          <label>Decision<select value={step.decision} onChange={(event) =>
            setProcedure((items) => items.map((item, i) => i === index
              ? { ...item, decision: event.target.value as TrainingProcedureStep["decision"] } : item))}>
            <option value="pending">Needs review</option><option value="accepted">Accept</option>
            <option value="rejected">Reject</option>
          </select></label>
        </fieldset>)}
        <label>Review comment<textarea maxLength={1000} value={procedureComment}
          onChange={(event) => setProcedureComment(event.target.value)} /></label>
        <button disabled={!procedure.length || procedure.some((step) => step.decision === "pending") ||
          !procedure.some((step) => step.decision === "accepted" && step.description.trim()) ||
          procedure.some((step) => step.decision === "accepted" &&
            (step.confidence === "unassessed" || step.evidence_category === "missing"))}
          onClick={() => void action(() =>
            api.reviewTrainingProcedure(projectId, row.id, row.revision, procedure, procedureComment, token))}>
          Approve reviewed procedure</button>
      </fieldset>}
      {row.analysis.procedure?.status === "approved" && <details>
        <summary>Approved evidence-backed procedure</summary>
        {row.analysis.procedure.steps.filter((step) => step.decision === "accepted").map((step) =>
          <p key={step.id}>{step.start_ms}–{step.end_ms} ms: {step.description}
            {" · Evidence: "}{step.evidence.join(", ")}</p>)}
      </details>}
      {row.analysis.procedure?.status === "approved" &&
        ["evidence_approved", "script_rejected"].includes(row.stage) && <fieldset disabled={busy}>
          <legend>Generate training draft</legend>
          <p>Uses the configured local model; generated material requires review.</p>
          <label>Learning objectives<textarea maxLength={1000} value={objectives}
            onChange={(event) => setObjectives(event.target.value)} /></label>
          <label>Learner level<select value={learnerLevel} onChange={(event) => setLearnerLevel(event.target.value)}>
            <option value="beginner">Beginner</option><option value="intermediate">Intermediate</option>
            <option value="advanced">Advanced</option>
          </select></label>
          <label>Reviewer feedback<textarea maxLength={1000} value={feedback}
            onChange={(event) => setFeedback(event.target.value)} /></label>
          <button onClick={() => void action(() => api.generateTrainingDraft(
            projectId, row.id, row.revision, objectives, learnerLevel, feedback, token,
          ))}>Generate local training draft</button>
        </fieldset>}
      {row.analysis.procedure?.status === "approved" &&
        ["evidence_approved", "awaiting_script_approval", "script_approved", "script_rejected", "awaiting_review"]
          .includes(row.stage) && <button disabled={busy} onClick={() => void action(() =>
            api.reopenTrainingProcedure(projectId, row.id, row.revision, token))}>
            Reopen evidence review and invalidate script approval</button>}
      {row.analysis.training_module && <details>
        <summary>Training draft and model provenance</summary>
        <p>Review every section against the source before approval. Saving changes creates a new
          revision and replaces the editable narration segments with the draft script.</p>
        <p>{row.analysis.training_module.draft.title} · {row.analysis.training_module.draft.audience}</p>
        <p>Model: {row.analysis.training_module.provider.model}
          {" · Digest: "}{row.analysis.training_module.provider.model_digest}</p>
        {row.analysis.training_module.provenance && <>
          <p>Review status: {row.analysis.training_module.provenance.review_status}
            {" · Source SHA-256: "}{row.analysis.training_module.provenance.source_video_sha256}
            {" · Approved procedure revision: "}
            {row.analysis.training_module.provenance.approved_procedure_revision}</p>
          <ul aria-label="Training generation stages">
            {row.analysis.training_module.provenance.stages.map((stage, index) =>
              <li key={`${stage.stage}-${index}`}>
                <strong>{stage.stage}</strong>
                {" · Model: "}{stage.model_identifier ?? "unknown"}
                {" · Digest: "}{stage.model_digest ?? "unknown"}
                {" · Ollama: "}{stage.ollama_version ?? "unknown"}
                {" · Route: "}{stage.api_route ?? "unknown"}
                {" · Endpoint SHA-256: "}{stage.endpoint_sha256 ?? "unknown"}
                {" · Schema: "}{stage.schema_validation_result ?? "unknown"}
                {" · evidence: "}{stage.evidence_validation_result ?? "not reported"}
                {" · tokens: "}{stage.prompt_tokens ?? "?"}/{stage.generated_tokens ?? "?"}
                {" · response chars: "}{stage.response_length_chars ?? "?"}
                {" · duration ms: "}{stage.inference_duration_ms ?? "?"}
                {" · completion: "}{stage.completion_reason ?? "unknown"}
                {" · context/output limit: "}{stage.context_tokens ?? "?"}/{stage.output_token_limit ?? "?"}
                {" · thinking requested/present: "}{String(stage.thinking_requested ?? "unknown")}
                /{String(stage.thinking_present ?? "unknown")}
                {" · thinking control reported/behavior/disable support/default: "}
                {String(stage.thinking_control_reported ?? "unknown")}
                /{stage.thinking_behavior ?? "unknown"}
                /{String(stage.thinking_disable_supported ?? "unknown")}
                /{String(stage.thinking_default ?? "unknown")}
                {" · worker memory bytes: "}{stage.worker_container_memory_bytes ?? "?"}
                {" · model memory status: "}{stage.ollama_model_memory?.status ?? "unknown"}
                {" · model/GPU bytes: "}{stage.ollama_model_memory?.model_bytes ?? "?"}
                /{stage.ollama_model_memory?.gpu_bytes ?? "?"}
              </li>)}
          </ul>
        </>}
        {(["learning_objectives", "prerequisites", "chapters", "annotations", "technical_explanations",
          "lab_steps", "validation_checkpoints", "troubleshooting", "assessment", "answer_explanations"] as const)
          .map((section) => <div key={section}><h3>{section.replaceAll("_", " ")}</h3>
            {row.analysis.training_module?.draft[section].map((claim, index) =>
              <p key={index}>{claim.text} · {claim.kind} · {claim.evidence.join(", ")}</p>)}</div>)}
        <fieldset disabled={busy || !editable || dirty}>
          <label>Complete training draft (JSON)
            <textarea rows={20} maxLength={500000} value={moduleText}
              onChange={(event) => { setModuleText(event.target.value); setModuleDirty(true); }} />
          </label>
          <button disabled={!moduleDirty} onClick={() => {
            let draft: unknown;
            try { draft = JSON.parse(moduleText); }
            catch { setError("Training draft is not valid JSON."); return; }
            void action(() => api.editTrainingDraft(projectId, row.id, row.revision, draft, token));
          }}>Save complete training draft revision</button>
        </fieldset>
      </details>}
      {row.analysis.training_generation_diagnostics && <details>
        <summary>Incomplete training generation diagnostics</summary>
        <ul>
          {row.analysis.training_generation_diagnostics.stages.map((stage, index) =>
            <li key={`${stage.stage}-${index}`}>
              <strong>{stage.stage}</strong>
              {" · Model: "}{stage.model_identifier ?? "unknown"}
              {" · Digest: "}{stage.model_digest ?? "unknown"}
              {" · Ollama: "}{stage.ollama_version ?? "unknown"}
              {" · Route: "}{stage.api_route ?? "unknown"}
              {" · Endpoint SHA-256: "}{stage.endpoint_sha256 ?? "unknown"}
              {" · Schema: "}{stage.schema_validation_result ?? "unknown"}
              {" · tokens: "}{stage.prompt_tokens ?? "?"}/{stage.generated_tokens ?? "?"}
              {" · response chars: "}{stage.response_length_chars ?? "?"}
              {" · duration ms: "}{stage.inference_duration_ms ?? "?"}
              {" · completion: "}{stage.completion_reason ?? "unknown"}
              {" · thinking requested/present: "}{String(stage.thinking_requested ?? "unknown")}
              /{String(stage.thinking_present ?? "unknown")}
              {" · thinking control reported/behavior/disable support/default: "}
              {String(stage.thinking_control_reported ?? "unknown")}
              /{stage.thinking_behavior ?? "unknown"}
              /{String(stage.thinking_disable_supported ?? "unknown")}
              /{String(stage.thinking_default ?? "unknown")}
              {" · worker memory bytes: "}{stage.worker_container_memory_bytes ?? "?"}
              {" · model memory status/GPU bytes: "}
              {stage.ollama_model_memory?.status ?? "unknown"}
              /{stage.ollama_model_memory?.gpu_bytes ?? "?"}
            </li>)}
        </ul>
      </details>}
      {script.map((segment, index) => <fieldset disabled={busy || !editable || moduleDirty} key={segment.id}>
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
      {editable && <fieldset disabled={busy || moduleDirty}>
        <button disabled={script.length >= 200 || (script.at(-1)?.end_ms ?? 0) >=
          Math.round(Number(row.analysis.media?.format.duration ?? 0) * 1000)}
          onClick={() => {
            const start = script.at(-1)?.end_ms ?? 0;
            const end = Math.min(start + 5000, Math.round(Number(row.analysis.media?.format.duration ?? 0) * 1000));
            setScript((items) => [...items, { id: crypto.randomUUID(), start_ms: start, end_ms: end,
              text: "", evidence: row.options.workflow === "silent_training" ? [] : ["manual"],
              uncertainty: row.options.workflow === "silent_training"
                ? "Supplemental guidance; not established by recording"
                : "Manually authored; verify against source", estimated_ms: 0 }]);
            clearUrls(); setDirty(true);
          }}>Add timestamped segment</button>
        <button disabled={!dirty || !script.length} onClick={() =>
          void action(() => api.editNarration(projectId, row.id, row.revision, script, token))}>Save new script revision</button>
        <button disabled={dirty || row.stage !== "awaiting_script_approval"} onClick={() =>
          void action(() => api.approveNarration(projectId, row.id, row.revision, "approved", token))}>
          {row.options.workflow === "silent_training" ? "Approve training draft and script" : "Approve complete script"}</button>
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
        {row.options.workflow === "silent_training" && <label>
          <input type="checkbox" checked={settings.render_annotations}
            onChange={(event) => setSettings({ ...settings, render_annotations: event.target.checked })} />
          Burn reviewed annotations into the derived video (re-encodes visuals; original is preserved)
        </label>}
        <button disabled={dirty || row.stage !== "script_approved" || row.approved_revision !== row.revision}
          onClick={() => void action(() => api.renderNarration(projectId, row.id, row.revision, settings, token))}>
          Generate approved narrated MP4</button>
      </fieldset>}
      {audio && <button onClick={() => void download(audio.id, "audio")}>Preview narration audio</button>}
      {audioUrl && <audio controls src={audioUrl} aria-label="Approved narration audio" />}
      {output && <button onClick={() => void download(output.id, "video")}>Preview completed narration MP4</button>}
      {videoUrl && <><video controls src={videoUrl} aria-label="Narrated source video" style={{ maxWidth: "100%" }} />
        {videoDownloadHref && <a href={videoDownloadHref} download="narrated-video.mp4" rel="noopener">
          Download narrated MP4</a>}</>}
      {row.artifacts.filter((item) => item.filename !== "video.mp4" &&
        (item.script_revision === row.revision || item.filename.startsWith("transcript.") ||
         item.filename === "analysis.json")).map((item) => {
        const href = safeHref(downloads[item.id], trustedOrigin);
        return <p key={item.id}><button onClick={() => void download(item.id, "download")}>Load {item.filename}</button>
          {href && <a href={href} download={String(item.filename).replace(/[^\w.\-]+/g,"_")} rel="noopener">
            Download {item.filename}</a>}</p>;
      })}
    </>}
  </section>;
}
