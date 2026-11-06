import { useEffect, useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type { Project, User, VideoJob, VideoModel } from "../api/types";
import { ReviewPanel } from "./ReviewPanel";

const SESSION_KEY = "ovf.session";

function readSavedSession(): string {
  try {
    return sessionStorage.getItem(SESSION_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveSession(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(SESSION_KEY, token);
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // The app remains usable for this page session when browser storage is disabled.
  }
}

function errorText(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "Something went wrong while contacting the API.";
}

export function App() {
  return <><ReviewPanel /><AccountApp /></>;
}

function AccountApp() {
  const [token, setToken] = useState(readSavedSession);
  const [user, setUser] = useState<User | null>(null);
  const [checkingSession, setCheckingSession] = useState(Boolean(token));
  const [sessionError, setSessionError] = useState("");

  useEffect(() => {
    api.setUnauthorizedHandler(() => {
      saveSession(null);
      setToken("");
      setUser(null);
      setSessionError("Your session expired. Sign in again to continue.");
    });
    return () => api.setUnauthorizedHandler(() => undefined);
  }, []);

  useEffect(() => {
    if (!token) {
      setCheckingSession(false);
      return;
    }

    let active = true;
    setCheckingSession(true);
    api.currentUser(token)
      .then((currentUser) => {
        if (active) {
          setUser(currentUser);
          setSessionError("");
        }
      })
      .catch((error: unknown) => {
        if (active && !(error instanceof ApiError && error.status === 401)) {
          setSessionError(errorText(error));
          setToken("");
          saveSession(null);
        }
      })
      .finally(() => {
        if (active) setCheckingSession(false);
      });

    return () => {
      active = false;
    };
  }, [token]);

  async function handleLogin(username: string, password: string): Promise<void> {
    const session = await api.login({ username, password });
    if (!session.access_token) throw new Error("The API login response did not include an access token.");
    saveSession(session.access_token);
    setUser(session.user ?? null);
    setSessionError("");
    setToken(session.access_token);
    if (!session.user) {
      const currentUser = await api.currentUser(session.access_token);
      setUser(currentUser);
    }
  }

  async function handleLogout(): Promise<void> {
    const activeToken = token;
    saveSession(null);
    setToken("");
    setUser(null);
    setSessionError("");
    if (activeToken) {
      try {
        await api.logout(activeToken);
      } catch {
        // Local sign-out still happens if the remote logout endpoint is unavailable.
      }
    }
  }

  if (checkingSession) {
    return <main className="centered"><p role="status">Checking your session…</p></main>;
  }

  if (!token || !user) {
    return (
      <LoginScreen
        error={sessionError}
        onLogin={handleLogin}
      />
    );
  }

  return <Workspace user={user} token={token} onLogout={handleLogout} />;
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
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<VideoJob | null>(null);
  const [jobError, setJobError] = useState("");
  const [jobLoading, setJobLoading] = useState(false);

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

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreating(true);
    setCreateError("");
    try {
      const created = await api.createProject({ name: projectName.trim() }, token);
      setProjects((current) => [created, ...current.filter((project) => project.id !== created.id)]);
      setProjectName("");
    } catch (error) {
      setCreateError(errorText(error));
    } finally {
      setCreating(false);
    }
  }

  async function lookupJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setJobLoading(true);
    setJobError("");
    setJob(null);
    try {
      setJob(await api.getJob(jobId.trim(), token));
    } catch (error) {
      setJobError(errorText(error));
    } finally {
      setJobLoading(false);
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
          <button className="secondary" type="button" onClick={() => void refreshWorkspace()} disabled={loading}>
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
              <button className="primary" type="submit" disabled={creating || !projectName.trim()}>
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
              <ul className="project-list">
                {projects.map((project) => (
                  <li key={project.id}>
                    <span className="project-icon" aria-hidden="true">{project.name.slice(0, 1).toUpperCase()}</span>
                    <span className="project-detail"><strong>{project.name}</strong><small>{project.id}</small></span>
                  </li>
                ))}
              </ul>
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

          <section className="panel jobs-panel" aria-labelledby="jobs-heading">
            <div className="panel-heading">
              <div><p className="eyebrow">ACTIVITY</p><h2 id="jobs-heading">Job status</h2></div>
            </div>
            <p className="muted">Look up a job using its ID. The API currently exposes per-job status, not a job collection.</p>
            <form className="inline-form" onSubmit={lookupJob}>
              <label className="sr-only" htmlFor="job-id">Job ID</label>
              <input
                id="job-id"
                value={jobId}
                onChange={(event) => setJobId(event.target.value)}
                placeholder="Paste a job ID"
                required
              />
              <button className="secondary" type="submit" disabled={jobLoading || !jobId.trim()}>
                {jobLoading ? "Looking…" : "Look up"}
              </button>
            </form>
            {jobError && <p className="notice error" role="alert">{jobError}</p>}
            {job && (
              <dl className="job-result" aria-live="polite">
                <div><dt>Job</dt><dd>{job.id}</dd></div>
                <div><dt>Operation</dt><dd>{job.operation}</dd></div>
                <div><dt>Status</dt><dd><span className="tag">{job.status}</span></dd></div>
                {job.artifact && <div><dt>Artifact</dt><dd>{job.artifact}</dd></div>}
                {job.error && <div><dt>Reported error</dt><dd>{job.error}</dd></div>}
              </dl>
            )}
          </section>
        </section>
        <footer className="footer-note">Live API responses only · no sample production data</footer>
      </main>
    </div>
  );
}
