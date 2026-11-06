import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type {
  MediaAsset,
  Project,
  RenderPreset,
  User,
  VideoJob,
  VideoModel,
} from "../api/types";

function errorText(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "Something went wrong while contacting the API.";
}

const ACTIVE_JOB_STATUSES = new Set(["queued", "dispatch_pending", "running", "verifying"]);

export function App() {
  const [token, setToken] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [sessionError, setSessionError] = useState("");

  useEffect(() => {
    api.setUnauthorizedHandler(() => {
      setToken("");
      setUser(null);
      setSessionError("Your session expired. Sign in again to continue.");
    });
    return () => api.setUnauthorizedHandler(() => undefined);
  }, []);

  async function handleLogin(username: string, password: string): Promise<void> {
    const session = await api.login({ username, password });
    if (!session.access_token) throw new Error("The API login response did not include an access token.");
    const currentUser = session.user ?? await api.currentUser(session.access_token);
    setUser(currentUser);
    setSessionError("");
    setToken(session.access_token);
  }

  async function handleLogout(): Promise<void> {
    const activeToken = token;
    setToken("");
    setUser(null);
    setSessionError("");
    if (activeToken) {
      try {
        await api.logout(activeToken);
      } catch {
        // Local sign-out still happens if the API logout endpoint is unavailable.
      }
    }
  }

  if (!token || !user) {
    return <LoginScreen error={sessionError} onLogin={handleLogin} />;
  }

  return <Workspace key={token} user={user} token={token} onLogout={handleLogout} />;
}

function LoginScreen({
  error,
  onLogin,
}: {
  error: string;
  onLogin: (username: string, password: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFormError("");
    const form = new FormData(event.currentTarget);
    try {
      await onLogin(String(form.get("username") ?? ""), String(form.get("password") ?? ""));
    } catch (reason) {
      setFormError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-card" aria-labelledby="login-heading">
        <p className="eyebrow">LOCAL PRODUCTION WORKSPACE</p>
        <h1 id="login-heading">OJU Video Forge</h1>
        <p className="muted">Sign in with an account provided by your API administrator.</p>
        {(error || formError) && <p className="notice error" role="alert">{formError || error}</p>}
        <form onSubmit={submit}>
          <label htmlFor="username">Username</label>
          <input id="username" name="username" autoComplete="username" required />
          <label htmlFor="password">Password</label>
          <input id="password" name="password" type="password" autoComplete="current-password" required />
          <button className="primary full-width" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="fine-print">API: {import.meta.env.VITE_API_BASE_URL || "localhost development proxy"}</p>
      </section>
    </main>
  );
}

function Workspace({
  user,
  token,
  onLogout,
}: {
  user: User;
  token: string;
  onLogout: () => Promise<void>;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [models, setModels] = useState<VideoModel[]>([]);
  const [projectError, setProjectError] = useState("");
  const [modelError, setModelError] = useState("");
  const [loading, setLoading] = useState(true);
  const [projectName, setProjectName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [media, setMedia] = useState<MediaAsset | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [durationMs, setDurationMs] = useState(5000);
  const [preset, setPreset] = useState<RenderPreset>("preview");
  const [submitting, setSubmitting] = useState(false);
  const [renderError, setRenderError] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [jobs, setJobs] = useState<Record<string, VideoJob>>({});
  const [jobError, setJobError] = useState("");
  const [artifactError, setArtifactError] = useState("");
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [checkingJob, setCheckingJob] = useState(false);
  const [artifactBlob, setArtifactBlob] = useState<Blob | null>(null);
  const [artifactUrl, setArtifactUrl] = useState("");
  const [retrievingArtifact, setRetrievingArtifact] = useState(false);
  const [legacyJobId, setLegacyJobId] = useState("");
  const [legacyJob, setLegacyJob] = useState<VideoJob | null>(null);
  const [legacyJobError, setLegacyJobError] = useState("");
  const [legacyJobLoading, setLegacyJobLoading] = useState(false);
  const job = jobs[selectedProjectId] ?? null;
  const projectActionsBusy = loading || creating || uploading || submitting || checkingJob || retrievingArtifact;

  function setProjectJob(projectId: string, projectJob: VideoJob | null) {
    setJobs((current) => {
      const updated = { ...current };
      if (projectJob) updated[projectId] = projectJob;
      else delete updated[projectId];
      return updated;
    });
  }

  async function refreshWorkspace() {
    setLoading(true);
    setProjectError("");
    setModelError("");
    const [projectResult, modelResult] = await Promise.allSettled([
      api.listProjects(token),
      api.listModels(token),
    ]);
    if (projectResult.status === "fulfilled") setProjects(projectResult.value);
    else setProjectError(errorText(projectResult.reason));
    if (modelResult.status === "fulfilled") setModels(modelResult.value.models);
    else setModelError(errorText(modelResult.reason));
    setLoading(false);
  }

  useEffect(() => {
    void refreshWorkspace();
    // Load once per authenticated session; refresh button explicitly reloads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(() => {
    if (!projects.some((project) => project.id === selectedProjectId)) {
      setSelectedProjectId(projects[0]?.id ?? "");
    }
  }, [projects, selectedProjectId]);

  useEffect(() => {
    setMedia(null);
    setJobError("");
    setArtifactBlob(null);
    setIdempotencyKey("");
  }, [selectedProjectId]);

  useEffect(() => {
    if (!artifactBlob) {
      setArtifactUrl("");
      return;
    }
    const url = URL.createObjectURL(artifactBlob);
    setArtifactUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [artifactBlob]);

  useEffect(() => {
    if (!job || !selectedProjectId || !ACTIVE_JOB_STATUSES.has(job.status)) return;
    const jobId = job.id;
    let active = true;
    let timeout: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const latest = await api.getProjectJob(selectedProjectId, jobId, token);
        if (!active) return;
        setJobError("");
        setProjectJob(selectedProjectId, latest);
        if (ACTIVE_JOB_STATUSES.has(latest.status)) {
          timeout = setTimeout(() => void poll(), 3000);
        }
      } catch (error) {
        if (!active) return;
        setJobError(errorText(error));
        if (error instanceof ApiError && (error.status === 0 || error.status === 429 || error.status >= 500)) {
          timeout = setTimeout(() => void poll(), 3000);
        }
      }
    }

    timeout = setTimeout(() => void poll(), 3000);
    return () => {
      active = false;
      clearTimeout(timeout);
    };
  }, [job?.id, job?.status, selectedProjectId, token]);

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreating(true);
    setCreateError("");
    try {
      const created = await api.createProject({ name: projectName.trim() }, token);
      setProjects((current) => [created, ...current.filter((project) => project.id !== created.id)]);
      setProjectName("");
      setSelectedProjectId(created.id);
    } catch (error) {
      setCreateError(errorText(error));
    } finally {
      setCreating(false);
    }
  }

  async function uploadMedia(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("media-file");
    const file = input instanceof HTMLInputElement ? input.files?.[0] : undefined;
    if (!file || !selectedProjectId) return;
    setUploading(true);
    setUploadError("");
    setRenderError("");
    try {
      const uploaded = await api.uploadMedia(selectedProjectId, file, token);
      setProjectJob(selectedProjectId, null);
      setArtifactBlob(null);
      setMedia(uploaded);
      setIdempotencyKey("");
      form.reset();
    } catch (error) {
      setUploadError(errorText(error));
    } finally {
      setUploading(false);
    }
  }

  async function submitRender(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!media || !selectedProjectId) return;
    setSubmitting(true);
    setRenderError("");
    setJobError("");
    setArtifactBlob(null);
    const key = idempotencyKey || crypto.randomUUID();
    setIdempotencyKey(key);
    try {
      const created = await api.createRenderJob(
        selectedProjectId,
        {
          timeline: {
            version: 1,
            clips: [{ media_id: media.id, in_ms: 0, out_ms: durationMs }],
          },
          preset,
        },
        key,
        token,
      );
      setProjectJob(selectedProjectId, created);
      setIdempotencyKey("");
    } catch (error) {
      setRenderError(errorText(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function checkJobStatus() {
    if (!job || !selectedProjectId) return;
    setCheckingJob(true);
    setJobError("");
    try {
      setProjectJob(selectedProjectId, await api.getProjectJob(selectedProjectId, job.id, token));
    } catch (error) {
      setJobError(errorText(error));
    } finally {
      setCheckingJob(false);
    }
  }

  async function prepareArtifact() {
    if (!job?.artifact_id || !selectedProjectId) return;
    setArtifactError("");
    setArtifactBlob(null);
    setRetrievingArtifact(true);
    try {
      setArtifactBlob(await api.getProjectArtifact(selectedProjectId, job.artifact_id, token));
    } catch (error) {
      setArtifactError(errorText(error));
    } finally {
      setRetrievingArtifact(false);
    }
  }

  async function downloadArtifact(job: VideoJob) {
    setArtifactLoading(true);
    setArtifactError("");
    try {
      const blob = await api.getArtifact(job.id, token);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "video.mp4";
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      setArtifactError(errorText(error));
    } finally {
      setArtifactLoading(false);
    }
  }

  async function lookupLegacyJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLegacyJobLoading(true);
    setLegacyJobError("");
    setLegacyJob(null);
    try {
      setLegacyJob(await api.getJob(legacyJobId.trim(), token));
    } catch (error) {
      setLegacyJobError(errorText(error));
    } finally {
      setLegacyJobLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="OJU Video Forge home">OVF<span>.</span></a>
        <div className="account">
          <span className="user-name">{user.name}</span>
          <button className="quiet-button" type="button" onClick={() => void onLogout()}>Sign out</button>
        </div>
      </header>

      <main className="workspace">
        <section className="welcome">
          <div>
            <p className="eyebrow">PRODUCTION WORKSPACE</p>
            <h1>Good to see you, {user.name}</h1>
            <p className="muted">Projects and service data are loaded from your configured API.</p>
          </div>
          <button className="secondary" type="button" onClick={() => void refreshWorkspace()} disabled={projectActionsBusy}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        </section>

        <section className="content-grid" aria-label="Production workspace">
          <section className="panel projects-panel" aria-labelledby="projects-heading">
            <div className="panel-heading">
              <div><p className="eyebrow">WORK</p><h2 id="projects-heading">Projects</h2></div>
              <span className="count">{loading ? "…" : projects.length}</span>
            </div>
            <form className="inline-form" onSubmit={createProject}>
              <label className="sr-only" htmlFor="project-name">New project name</label>
              <input
                id="project-name"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="Name a project"
                maxLength={120}
                required
              />
              <button className="primary" type="submit" disabled={projectActionsBusy || !projectName.trim()}>
                {creating ? "Creating…" : "Create"}
              </button>
            </form>
            {createError && <p className="notice error" role="alert">{createError}</p>}
            {projectError && <p className="notice error" role="alert">{projectError}</p>}
            {loading && <p className="muted" role="status">Loading projects…</p>}
            {!loading && !projectError && projects.length === 0 && (
              <p className="empty">No projects returned by the API. Create one to get started.</p>
            )}
            {projects.length > 0 && (
              <>
                <label className="field-label" htmlFor="active-project">Active project</label>
                <select
                  id="active-project"
                  value={selectedProjectId}
                  onChange={(event) => setSelectedProjectId(event.target.value)}
                  disabled={projectActionsBusy}
                >
                  {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                </select>
                <ul className="project-list">
                  {projects.map((project) => (
                    <li key={project.id}>
                      <span className="project-icon" aria-hidden="true">{project.name.slice(0, 1).toUpperCase()}</span>
                      <span className="project-detail"><strong>{project.name}</strong><small>{project.id}</small></span>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <section className="panel models-panel" aria-labelledby="models-heading">
            <div className="panel-heading">
              <div><p className="eyebrow">CAPABILITIES</p><h2 id="models-heading">Models</h2></div>
            </div>
            {modelError && <p className="notice error" role="alert">{modelError}</p>}
            {loading && <p className="muted" role="status">Loading models…</p>}
            {!loading && !modelError && models.length === 0 && <p className="empty">No models returned by the API.</p>}
            {!modelError && models.length > 0 && (
              <ul className="model-list">
                {models.map((model) => (
                  <li key={model.id}>
                    <span className="status-dot" aria-hidden="true" />
                    <span className="model-detail"><strong>{model.id}</strong><small>{model.operation}</small></span>
                    <span className="tag">{model.status.replaceAll("_", " ")}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel workflow-panel" aria-labelledby="workflow-heading">
            <div className="panel-heading">
              <div><p className="eyebrow">CREATE</p><h2 id="workflow-heading">Upload and render</h2></div>
            </div>
            {!selectedProjectId && <p className="empty">Create or select a project before uploading media.</p>}
            <form onSubmit={uploadMedia}>
              <label className="field-label" htmlFor="media-file">Video file</label>
              <div className="inline-form">
                <input id="media-file" name="media-file" type="file" accept="video/mp4,video/quicktime,video/x-matroska,video/webm" required disabled={!selectedProjectId || projectActionsBusy} />
                <button className="secondary" type="submit" disabled={!selectedProjectId || projectActionsBusy}>
                  {uploading ? "Uploading…" : "Upload"}
                </button>
              </div>
            </form>
            {uploadError && <p className="notice error" role="alert">{uploadError}</p>}
            {media && (
              <p className="notice success" role="status">
                Uploaded media {media.id} · {media.media_type} · {media.size_bytes.toLocaleString()} bytes
              </p>
            )}
            <form className="render-form" onSubmit={submitRender}>
              <label className="field-label" htmlFor="duration-ms">Clip duration (milliseconds)</label>
              <input
                id="duration-ms"
                type="number"
                min={1000}
                max={120000}
                step={1000}
                value={durationMs}
                disabled={projectActionsBusy}
                onChange={(event) => {
                  setDurationMs(Number(event.target.value));
                  setIdempotencyKey("");
                }}
                required
              />
              <label className="field-label" htmlFor="render-preset">Output preset</label>
              <select
                id="render-preset"
                value={preset}
                disabled={projectActionsBusy}
                onChange={(event) => {
                  setPreset(event.target.value as RenderPreset);
                  setIdempotencyKey("");
                }}
              >
                <option value="preview">Preview · 640 × 360</option>
                <option value="hd">HD · 1280 × 720</option>
              </select>
              {renderError && <p className="notice error" role="alert">{renderError}</p>}
              <button className="primary" type="submit" disabled={!media || projectActionsBusy}>
                {submitting ? "Submitting…" : "Submit render"}
              </button>
            </form>
          </section>

          <section className="panel jobs-panel" aria-labelledby="jobs-heading">
            <div className="panel-heading">
              <div><p className="eyebrow">ACTIVITY</p><h2 id="jobs-heading">Render status</h2></div>
              {job && (
                <button className="secondary" type="button" onClick={() => void checkJobStatus()} disabled={projectActionsBusy}>
                  {checkingJob ? "Checking…" : "Check status"}
                </button>
              )}
            </div>
            {!job && <p className="empty">Submit a render to see its live job status here.</p>}
            {job && (
              <>
                <dl className="job-result" aria-live="polite">
                  <div><dt>Job</dt><dd>{job.id}</dd></div>
                  {job.operation && <div><dt>Operation</dt><dd>{job.operation}</dd></div>}
                  <div><dt>Status</dt><dd><span className="tag">{job.status.replaceAll("_", " ")}</span></dd></div>
                  {job.artifact_id && <div><dt>Artifact</dt><dd>{job.artifact_id}</dd></div>}
                  {job.error && <div><dt>Reported error</dt><dd>{job.error}</dd></div>}
                </dl>
                {ACTIVE_JOB_STATUSES.has(job.status) && <p className="muted" role="status">Checking for updates every 3 seconds…</p>}
                {jobError && <p className="notice error" role="alert">{jobError}</p>}
                {artifactError && <p className="notice error" role="alert">{artifactError}</p>}
                {job.artifact_id && (
                  <div className="artifact-actions">
                    <button className="secondary" type="button" onClick={() => void prepareArtifact()} disabled={projectActionsBusy}>
                      {retrievingArtifact ? "Retrieving…" : "Retrieve authorized artifact"}
                    </button>
                    {artifactUrl && <a className="download-link" href={artifactUrl} download="oju-render.mp4">Download MP4</a>}
                  </div>
                )}
              </>
            )}
            {artifactError && <p className="notice error" role="alert">{artifactError}</p>}
            <form className="inline-form" onSubmit={lookupLegacyJob}>
              <label className="sr-only" htmlFor="legacy-job-id">Generated job ID</label>
              <input
                id="legacy-job-id"
                value={legacyJobId}
                onChange={(event) => setLegacyJobId(event.target.value)}
                placeholder="Look up a generated job ID"
                required
              />
              <button className="secondary" type="submit" disabled={legacyJobLoading || !legacyJobId.trim()}>
                {legacyJobLoading ? "Looking…" : "Look up generated job"}
              </button>
            </form>
            {legacyJobError && <p className="notice error" role="alert">{legacyJobError}</p>}
            {legacyJob && (
              <dl className="job-result" aria-live="polite">
                <div><dt>Generated job</dt><dd>{legacyJob.id}</dd></div>
                {legacyJob.operation && <div><dt>Operation</dt><dd>{legacyJob.operation}</dd></div>}
                <div><dt>Status</dt><dd><span className="tag">{legacyJob.status.replaceAll("_", " ")}</span></dd></div>
                {legacyJob.error && <div><dt>Reported error</dt><dd>{legacyJob.error}</dd></div>}
                {legacyJob.artifact && (
                  <div>
                    <dt>Artifact</dt>
                    <dd>
                      <button
                        className="secondary"
                        type="button"
                        onClick={() => void downloadArtifact(legacyJob)}
                        disabled={artifactLoading}
                      >
                        {artifactLoading ? "Downloading…" : "Download video"}
                      </button>
                    </dd>
                  </div>
                )}
              </dl>
            )}
          </section>
        </section>
        <footer className="footer-note">Private API responses only · no sample production data</footer>
      </main>
    </div>
  );
}
