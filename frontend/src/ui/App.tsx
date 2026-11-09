import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import { artifactReady, buildRenderInput, moveClip } from "../api/timeline";
import { MemoryArtifact } from "../api/memoryArtifact";
import type {
  CreateWanGenerationInput,
  MediaAsset,
  Project,
  TimelineClip,
  TimelineOverlay,
  User,
  VideoJob,
  VideoModel,
} from "../api/types";
import { AuthenticatedReview, ReviewPanel } from "./ReviewPanel";
import { NarrationWorkspace } from "./NarrationWorkspace";

const ACTIVE = new Set(["queued", "dispatch_pending", "running", "verifying"]);
const errorText = (error: unknown) => error instanceof Error ? error.message : "Request failed.";

// A project/session change unmounts the workspace. Never accept its late results.
function useLifetime() {
  const live = useRef(false);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  return live;
}

export function App() {
  const [session, setSession] = useState<{ token: string; user: User } | null>(null);
  const [error, setError] = useState("");
  const currentToken = useRef("");
  const generation = useRef(0);
  function clear(message = "") {
    generation.current++;
    currentToken.current = "";
    setSession(null);
    setError(message);
  }
  useEffect(() => {
    api.setUnauthorizedHandler((token) => {
      if (token && token === currentToken.current) clear("Your session expired. Sign in again.");
    });
    const offline = () => clear("Connection lost. Sign in again when the API is available.");
    window.addEventListener("offline", offline);
    return () => {
      generation.current++;
      api.setUnauthorizedHandler(() => undefined);
      window.removeEventListener("offline", offline);
    };
  }, []);
  async function authenticate(username: string, password: string, register: boolean) {
    const attempt = ++generation.current;
    if (register) await api.register({ username, password });
    const result = await api.login({ username, password });
    if (!result.access_token) throw new Error("No access token returned.");
    const user = result.user ?? await api.currentUser(result.access_token);
    if (attempt !== generation.current) return;
    currentToken.current = result.access_token;
    setSession({ token: result.access_token, user });
    setError("");
  }
  async function logout() {
    const token = currentToken.current;
    clear();
    try { if (token) await api.logout(token); } catch { /* Local sign-out is unconditional. */ }
  }
  return session
    ? <Workspace key={session.token} {...session} onLogout={() => void logout()} />
    : <>
      <LoginScreen error={error} onAuthenticate={authenticate} />
      <details><summary>Local service-token review bridge</summary><ReviewPanel /></details>
    </>;
}

export function LoginScreen({ error, onAuthenticate }: {
  error: string;
  onAuthenticate: (username: string, password: string, register: boolean) => Promise<void>;
}) {
  const [register, setRegister] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const live = useLifetime();
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const password = String(data.get("password") ?? "");
    form.reset(); // Do not retain credentials after submission.
    setBusy(true);
    setFormError("");
    try {
      await onAuthenticate(String(data.get("username") ?? ""), password, register);
    } catch (reason) {
      if (live.current) setFormError(errorText(reason));
    } finally {
      if (live.current) setBusy(false);
    }
  }
  return <main className="login-shell"><section className="login-card" aria-labelledby="login-heading">
    <h1 id="login-heading">OJU Video Forge</h1>
    <p>{register ? "Create a local account and sign in." : "Sign in to your private projects."}</p>
    {(formError || error) && <p role="alert" className="notice error">{formError || error}</p>}
    <form onSubmit={submit}>
      <label htmlFor="username">Username</label>
      <input id="username" name="username" autoComplete="off" minLength={3} maxLength={64} required disabled={busy} />
      <label htmlFor="password">Password (12–128 characters)</label>
      <input id="password" name="password" type="password" autoComplete="off" minLength={12} maxLength={128} required disabled={busy} />
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Please wait…" : register ? "Register and sign in" : "Sign in"}
      </button>
      <button type="button" disabled={busy} onClick={() => { setRegister(!register); setFormError(""); }}>
        {register ? "Use existing account" : "Create account"}
      </button>
    </form>
    <p className="fine-print">Bearer session in memory only. Signing out or reloading clears it.</p>
  </section></main>;
}

function Workspace({ token, user, onLogout }: { token: string; user: User; onLogout: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const live = useLifetime();
  async function refresh() {
    setBusy(true);
    setError("");
    try {
      const result = await api.listProjects(token);
      if (live.current) {
        setProjects(result);
        setSelected((id) => result.some((project) => project.id === id) ? id : result[0]?.id ?? "");
      }
    } catch (reason) {
      if (live.current) { setProjects([]); setSelected(""); setError(errorText(reason)); }
    } finally { if (live.current) setBusy(false); }
  }
  useEffect(() => { void refresh(); }, [token]);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const project = await api.createProject({ name: name.trim() }, token);
      if (live.current) {
        setProjects((items) => [project, ...items]);
        setSelected(project.id);
        setName("");
      }
    } catch (reason) { if (live.current) setError(errorText(reason)); }
    finally { if (live.current) setBusy(false); }
  }
  return <div className="app-shell">
    <header className="topbar"><strong>OJU Video Forge</strong><span>{user.username}</span>
      <button onClick={onLogout}>Sign out</button></header>
    <main className="workspace">
      <section className="panel" aria-labelledby="projects-heading">
        <h1 id="projects-heading">Projects</h1>
        <button disabled={busy} onClick={() => void refresh()}>Refresh projects</button>
        {busy && <p role="status">Loading projects…</p>}
        {error && <p role="alert" className="notice error">{error}</p>}
        <form className="inline-form" onSubmit={create}>
          <label htmlFor="project-name">New project name</label>
          <input id="project-name" required maxLength={128} value={name} onChange={(e) => setName(e.target.value)} />
          <button disabled={busy || !name.trim()} type="submit">Create project</button>
        </form>
        {!busy && !error && !projects.length && <p>No projects yet. Create one to begin.</p>}
        {!!projects.length && <>
          <label htmlFor="active-project">Active project</label>
          <select id="active-project" value={selected} disabled={busy} onChange={(e) => setSelected(e.target.value)}>
            {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
          <ul>{projects.map((project) => <li key={project.id}>{project.name}</li>)}</ul>
        </>}
      </section>
      {selected && <ProjectWorkspace key={selected} projectId={selected} token={token} />}
    </main>
  </div>;
}

export function OrderedTimeline({ clips, media, disabled, onChange }: {
  clips: TimelineClip[]; media: MediaAsset[]; disabled: boolean; onChange: (clips: TimelineClip[]) => void;
}) {
  return <section aria-labelledby="timeline-heading">
    <h2 id="timeline-heading">Ordered clip timeline</h2>
    <p>Preview MP4 · 640 × 360 at 24 fps · maximum 16 clips / 120 seconds.
      Use multiples of 125 ms for clip times. Source audio is omitted unless an audio track is selected.</p>
    {!clips.length && <p>Add uploaded videos to the timeline.</p>}
    <ol>{clips.map((clip, index) => <li key={index}>
      <strong>{media.find((item) => item.id === clip.media_id)?.filename ?? clip.media_id}</strong>
      <label>Clip {index + 1} in (ms)<input type="number" min={0} max={120000} step={125}
        required disabled={disabled} value={clip.in_ms} onChange={(e) => onChange(clips.map((item, i) =>
          i === index ? { ...item, in_ms: Number(e.target.value) } : item))} /></label>
      <label>Clip {index + 1} out (ms)<input type="number" min={clip.in_ms + 125} max={120000} step={125}
        required disabled={disabled} value={clip.out_ms} onChange={(e) => onChange(clips.map((item, i) =>
          i === index ? { ...item, out_ms: Number(e.target.value) } : item))} /></label>
      <button type="button" aria-label={`Move clip ${index + 1} up`} disabled={disabled || index === 0}
        onClick={() => onChange(moveClip(clips, index, -1))}>Up</button>
      <button type="button" aria-label={`Move clip ${index + 1} down`} disabled={disabled || index === clips.length - 1}
        onClick={() => onChange(moveClip(clips, index, 1))}>Down</button>
      <button type="button" disabled={disabled} onClick={() => onChange(clips.filter((_, i) => i !== index))}>Remove clip {index + 1}</button>
    </li>)}</ol>
  </section>;
}

function OverlayFields({ value, onChange, label }: {
  value: TimelineOverlay; onChange: (value: TimelineOverlay) => void; label: string;
}) {
  return <fieldset><legend>{label}</legend>
    <label>Text<input required maxLength={200} value={value.text} onChange={(e) => onChange({ ...value, text: e.target.value })} /></label>
    <label>Start (ms)<input required type="number" min={0} step={1} value={value.start_ms}
      onChange={(e) => onChange({ ...value, start_ms: Number(e.target.value) })} /></label>
    <label>End (ms)<input required type="number" min={value.start_ms + 1} step={1} value={value.end_ms}
      onChange={(e) => onChange({ ...value, end_ms: Number(e.target.value) })} /></label>
  </fieldset>;
}

export function ProjectWorkspace({ projectId, token }: { projectId: string; token: string }) {
  const [media, setMedia] = useState<MediaAsset[]>([]);
  const [clips, setClips] = useState<TimelineClip[]>([]);
  const [title, setTitle] = useState<TimelineOverlay | undefined>();
  const [captions, setCaptions] = useState<TimelineOverlay[]>([]);
  const [audioId, setAudioId] = useState("");
  const [audioIn, setAudioIn] = useState(0);
  const [volume, setVolume] = useState(100);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<VideoJob | null>(null);
  const [jobError, setJobError] = useState("");
  const [narrationSession, setNarrationSession] = useState(0);
  const [artifactUrl, setArtifactUrl] = useState("");
  const [models, setModels] = useState<VideoModel[]>([]);
  const [modelError, setModelError] = useState("");
  const [generationPrompt, setGenerationPrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [width, setWidth] = useState(512);
  const [height, setHeight] = useState(288);
  const [frames, setFrames] = useState(17);
  const [fps, setFps] = useState(8);
  const [seed, setSeed] = useState(42);
  const live = useLifetime();
  const [artifact] = useState(() => new MemoryArtifact(api));
  const pending = useRef<{ payload: string; key: string } | null>(null);
  const locked = useRef(false);
  const jobGeneration = useRef(0);
  function releaseArtifact() {
    artifact.clear();
    setArtifactUrl("");
  }
  useEffect(() => () => artifact.clear(), [artifact]);
  async function loadMedia() {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await api.listMedia(projectId, token);
      if (live.current) setMedia(result);
    } catch (reason) {
      if (live.current) {
        setMedia([]); setClips([]); setTitle(undefined); setCaptions([]); setAudioId("");
        pending.current = null; releaseArtifact(); setError(errorText(reason));
      }
    } finally { locked.current = false; if (live.current) setBusy(false); }
  }
  useEffect(() => { void loadMedia(); }, [projectId, token]);
  useEffect(() => {
    let active = true;
    void api.listModels(token).then((result) => {
      if (active && live.current) {
        setModels(result.models);
        setModelError("");
      }
    }).catch((reason: unknown) => {
      if (active && live.current) {
        setModels([]);
        setModelError(errorText(reason));
      }
    });
    return () => { active = false; };
  }, [token]);
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked.current) return;
    const form = event.currentTarget;
    const input = form.elements.namedItem("upload") as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    locked.current = true; setBusy(true); setError("");
    try {
      const asset = await api.uploadMedia(projectId, file, token);
      if (live.current) {
        setMedia((items) => [...items.filter((item) => item.id !== asset.id), asset]);
        form.reset();
      }
    } catch (reason) { if (live.current) setError(errorText(reason)); }
    finally { locked.current = false; if (live.current) setBusy(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked.current) return;
    setError(""); setJobError("");
    let input;
    try {
      input = buildRenderInput(clips, title, captions,
        audioId ? { media_id: audioId, in_ms: audioIn, volume_percent: volume } : undefined);
    } catch (reason) { setError(errorText(reason)); return; }
    const payload = JSON.stringify(input);
    // Reuse only for an identical retry after an ambiguous response.
    if (pending.current?.payload !== payload) pending.current = { payload, key: crypto.randomUUID() };
    const key = pending.current.key;
    locked.current = true; setBusy(true); releaseArtifact();
    jobGeneration.current++; setJob(null); setNarrationSession((value) => value + 1);
    try {
      const result = await api.createRenderJob(projectId, input, key, token);
      if (live.current) {
        if (result.id !== job?.id) jobGeneration.current++;
        setJob(result);
        pending.current = null;
      }
    } catch (reason) {
      if (live.current) {
        setError(errorText(reason));
        if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) pending.current = null;
      }
    } finally { locked.current = false; if (live.current) setBusy(false); }
  }
  async function submitGeneration(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (locked.current) return;
    const input: CreateWanGenerationInput = {
      operation: "wan22_t2v",
      prompt: generationPrompt.trim(),
      negative_prompt: negativePrompt.trim(),
      width,
      height,
      frames,
      fps,
      seed,
    };
    const payload = JSON.stringify({ ...input, project_id: projectId });
    if (pending.current?.payload !== payload) pending.current = { payload, key: crypto.randomUUID() };
    setError(""); setJobError("");
    locked.current = true; setBusy(true); releaseArtifact();
    jobGeneration.current++; setJob(null); setNarrationSession((value) => value + 1);
    try {
      const result = await api.createWanGenerationJob(
        projectId, input, pending.current.key, token,
      );
      if (live.current) {
        if (result.id !== job?.id) jobGeneration.current++;
        setJob(result);
        pending.current = null;
      }
    } catch (reason) {
      if (live.current) {
        setError(errorText(reason));
        if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) {
          pending.current = null;
        }
      }
    } finally { locked.current = false; if (live.current) setBusy(false); }
  }
  useEffect(() => {
    if (!job || !ACTIVE.has(job.status)) return;
    let active = true;
    let timeout: ReturnType<typeof setTimeout>;
    const id = job.id;
    const generation = jobGeneration.current;
    async function poll() {
      try {
        const result = await api.getJob(id, token);
        if (!active || !live.current || generation !== jobGeneration.current) return;
        setJob(result); setJobError("");
        if (!artifactReady(result.status)) releaseArtifact();
        if (ACTIVE.has(result.status)) timeout = setTimeout(() => void poll(), 3000);
      } catch (reason) {
        if (!active || !live.current || generation !== jobGeneration.current) return;
        setJobError(errorText(reason));
        if (reason instanceof ApiError && reason.status === 403) { setJob(null); releaseArtifact(); }
        if (reason instanceof ApiError && (reason.status === 0 || reason.status === 429 || reason.status >= 500)) {
          timeout = setTimeout(() => void poll(), 3000);
        }
      }
    }
    timeout = setTimeout(() => void poll(), 3000);
    return () => { active = false; clearTimeout(timeout); };
  }, [job?.id, job?.status, token]);
  const generationModel = models.find((model) => model.operation === "wan22_t2v");
  const generationReady = generationModel?.status === "configured";
  async function check() {
    if (!job || locked.current) return;
    locked.current = true; setBusy(true); setJobError("");
    try {
      const result = await api.getJob(job.id, token);
      if (live.current) { setJob(result); if (!artifactReady(result.status)) releaseArtifact(); }
    } catch (reason) {
      if (live.current) { setJob(null); releaseArtifact(); setJobError(errorText(reason)); }
    } finally { locked.current = false; if (live.current) setBusy(false); }
  }
  async function preview() {
    if (!job || !artifactReady(job.status) || locked.current) return;
    locked.current = true; setBusy(true); setJobError(""); releaseArtifact();
    try {
      const projectArtifact = job.operation === "wan22_t2v" && job.project_artifact_id
        ? { projectId, artifactId: job.project_artifact_id }
        : undefined;
      const result = await artifact.load(job.id, token, projectArtifact);
      if (live.current && result) setArtifactUrl(result);
    } catch (reason) { if (live.current) setJobError(errorText(reason)); }
    finally { locked.current = false; if (live.current) setBusy(false); }
  }
  const emptyOverlay = (): TimelineOverlay => ({ text: "", start_ms: 0, end_ms: 1000 });
  return <>
    <section className="panel" aria-labelledby="media-heading">
      <h2 id="media-heading">Project media</h2>
      {busy && <p role="status">Working…</p>}
      {error && <p role="alert" className="notice error">{error}</p>}
      <button disabled={busy} onClick={() => void loadMedia()}>Refresh media</button>
      <form onSubmit={upload}>
        <label htmlFor="upload">Video or audio file</label>
        <input id="upload" name="upload" required disabled={busy} type="file"
          accept=".mp4,.mov,.mkv,.webm,.wav,.mp3" />
        <button type="submit" disabled={busy}>Upload media</button>
      </form>
      {!busy && !media.length && <p>No media uploaded to this project.</p>}
      <ul>{media.map((asset) => <li key={asset.id}>
        {asset.filename} · {asset.content_type} · {asset.size_bytes.toLocaleString()} bytes
        {asset.content_type.startsWith("video/") && <button disabled={busy || clips.length >= 16} onClick={() =>
          setClips((items) => [...items, { media_id: asset.id, in_ms: 0, out_ms: 5000 }])}>Add {asset.filename} to timeline</button>}
      </li>)}</ul>
    </section>
    <NarrationWorkspace key={narrationSession} projectId={projectId} token={token} media={media} onActivate={() => {
      jobGeneration.current++; setJob(null); releaseArtifact();
    }} />
    <section className="panel" aria-labelledby="ai-generation-heading">
      <h2 id="ai-generation-heading">AI video generation</h2>
      <p>Operation: wan22_t2v. Creates a new video from text, not narration of an upload.
        Uploaded visuals, audio, aspect ratio, and duration are not preserved.</p>
      <p>Model: {generationModel?.id ?? "Wan 2.2 TI2V-5B"} · {generationModel?.status ?? "checking"}</p>
      {modelError && <p role="alert" className="notice error">{modelError}</p>}
      <form onSubmit={submitGeneration}>
        <fieldset disabled={busy || !generationReady}>
          <label htmlFor="generation-prompt">Prompt</label>
          <textarea id="generation-prompt" required maxLength={1000} value={generationPrompt}
            onChange={(event) => setGenerationPrompt(event.target.value)} />
          <label htmlFor="generation-negative-prompt">Negative prompt (optional)</label>
          <textarea id="generation-negative-prompt" maxLength={1000} value={negativePrompt}
            onChange={(event) => setNegativePrompt(event.target.value)} />
          <div className="inline-form">
            <label htmlFor="generation-width">Width</label>
            <input id="generation-width" type="number" min={256} max={1280} step={32}
              value={width} onChange={(event) => setWidth(Number(event.target.value))} />
            <label htmlFor="generation-height">Height</label>
            <input id="generation-height" type="number" min={160} max={720} step={32}
              value={height} onChange={(event) => setHeight(Number(event.target.value))} />
            <label htmlFor="generation-frames">Frames</label>
            <input id="generation-frames" type="number" min={17} max={81} step={4}
              value={frames} onChange={(event) => setFrames(Number(event.target.value))} />
            <label htmlFor="generation-fps">FPS</label>
            <select id="generation-fps" value={fps} onChange={(event) => setFps(Number(event.target.value))}>
              <option value={8}>8</option><option value={16}>16</option><option value={24}>24</option>
            </select>
            <label htmlFor="generation-seed">Seed</label>
            <input id="generation-seed" type="number" min={0} max={4294967295}
              value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
          </div>
          <button className="primary" type="submit" disabled={!generationReady || !generationPrompt.trim()}>
            Generate AI video
          </button>
        </fieldset>
      </form>
    </section>
    <section className="panel">
      <h2>Timeline rendering</h2>
      <p>Operation: render_timeline_v1. Renders only the selected clips and audio track; it does not generate speech.</p>
      <form onSubmit={submit}>
        <fieldset disabled={busy}>
          <OrderedTimeline clips={clips} media={media} disabled={busy} onChange={setClips} />
          <label><input type="checkbox" checked={!!title} onChange={(e) => setTitle(e.target.checked ? emptyOverlay() : undefined)} /> Include title</label>
          {title && <OverlayFields label="Title" value={title} onChange={setTitle} />}
          <h3>Optional captions</h3>
          {captions.map((cue, index) => <div key={index}>
            <OverlayFields label={`Caption ${index + 1}`} value={cue} onChange={(value) =>
              setCaptions((items) => items.map((item, i) => i === index ? value : item))} />
            <button type="button" onClick={() => setCaptions((items) => items.filter((_, i) => i !== index))}>Remove caption {index + 1}</button>
          </div>)}
          <button type="button" disabled={captions.length >= 100} onClick={() => setCaptions((items) => [...items, emptyOverlay()])}>Add caption</button>
          <label>Optional audio track<select value={audioId} onChange={(e) => setAudioId(e.target.value)}>
            <option value="">None</option>
            {media.filter((asset) => asset.content_type.startsWith("audio/")).map((asset) =>
              <option key={asset.id} value={asset.id}>{asset.filename}</option>)}
          </select></label>
          {audioId && <>
            <label>Audio in (ms)<input required type="number" min={0} max={120000} step={1} value={audioIn} onChange={(e) => setAudioIn(Number(e.target.value))} /></label>
            <label>Volume (%)<input required type="number" min={0} max={100} step={1} value={volume} onChange={(e) => setVolume(Number(e.target.value))} /></label>
          </>}
          <button className="primary" type="submit" disabled={!clips.length}>Submit preview render</button>
        </fieldset>
      </form>
    </section>
    <section className="panel" aria-labelledby="job-heading">
      <h2 id="job-heading">Job status</h2>
      {jobError && <p role="alert" className="notice error">{jobError}</p>}
      {!job && <p>No render submitted in this project session.</p>}
      {job && <>
        <p aria-live="polite">Job {job.id}: {job.status.replaceAll("_", " ")}</p>
        <JobOutputDescription job={job} media={media} />
        {job.error && <p role="alert">{job.error}</p>}
        {ACTIVE.has(job.status) && <p role="status">Checking status every 3 seconds…</p>}
        <button disabled={busy} onClick={() => void check()}>Check status</button>
        {job.artifact && artifactReady(job.status) &&
          ["wan22_t2v", "render_timeline_v1", "render_test"].includes(job.operation ?? "") &&
          <button disabled={busy} onClick={() => void preview()}>Load authorized preview/download</button>}
        {artifactUrl && <>
          <video controls preload="metadata" src={artifactUrl} aria-label="Rendered video preview" style={{ maxWidth: "100%" }} />
          <a className="download-link" href={artifactUrl} download="oju-render.mp4">Download MP4</a>
          <button onClick={releaseArtifact}>Clear preview</button>
        </>}
        <AuthenticatedReview key={job.id} token={token} jobId={job.id} />
      </>}
    </section>
  </>;
}

export function JobOutputDescription({ job, media }: { job: VideoJob; media: MediaAsset[] }) {
  const source = media.find((asset) => asset.id === job.media_id);
  const output = job.operation === "wan22_t2v" ? "AI-generated video (not narration)"
    : job.operation === "render_timeline_v1" ? "Timeline-rendered video"
    : job.operation === "render_test" ? "Synthetic test video"
    : "No verified narrated video";
  const videoOperation = ["wan22_t2v", "render_timeline_v1", "render_test"].includes(job.operation ?? "");
  return <>
    <p>Operation: {job.operation ?? "unknown"} · Output: {output}</p>
    <p>Source video: {source?.filename ?? (job.media_id ? job.media_id
      : job.operation === "render_timeline_v1" ? "Selected timeline clips" : "None")}</p>
    {job.status === "awaiting_review" && videoOperation &&
      <p role="status">{output} complete; awaiting review. This does not verify narration.</p>}
  </>;
}
