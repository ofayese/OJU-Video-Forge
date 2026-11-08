import { useEffect, useState, useSyncExternalStore, type FormEvent } from "react";
import { ReviewController, type ReviewState } from "../api/reviewController";
import type { ReviewDecision } from "../api/types";
import { api } from "../api/client";

export function ReviewEvidence({ state, onDecision, onAcknowledge }: {
  state: ReviewState;
  onDecision: (decision: ReviewDecision["decision"]) => void;
  onAcknowledge?: () => void;
}) {
  const { job, history, busy, error } = state;
  const disabled = busy || state.needsConfirmation || !job?.review_enabled || !job.can_review || !job.review_artifact;
  return <>
    {busy && <p role="status">Loading review…</p>}
    {error && <p role="alert">{error}</p>}
    {job && <>
      <p>Job {job.id}: {job.status} · Review version {job.review_version}</p>
      {!job.review_enabled && <p role="status">Review disabled. History remains available.</p>}
      {job.review_enabled && !job.can_review && <p>Read-only: this job or actor is not eligible for decisions.</p>}
      {job.review_artifact && <p>Artifact {job.review_artifact.id}<br />SHA-256: {job.review_artifact.sha256}</p>}
      <p>No media preview in this bridge. Verify output through authorized local operations before deciding.</p>
      {state.needsConfirmation && <button disabled={busy} onClick={onAcknowledge}>
        I reviewed the refreshed artifact details
      </button>}
      <button disabled={disabled} onClick={() => onDecision("approved")}>Approve</button>
      <button disabled={disabled} onClick={() => onDecision("rejected")}>Reject</button>
      <h3>Immutable review history</h3>
      {history.length === 0 ? <p>No decisions recorded.</p> : <ol>
        {history.map((review) => <li key={review.version}>
          {review.decision} by {review.actor} at <time dateTime={review.at}>{review.at}</time>
          {" · "}Version {review.expected_version} → {review.version}
          {" · "}Artifact {review.artifact_id} · SHA-256 {review.artifact_sha256}
        </li>)}
      </ol>}
    </>}
  </>;
}

export function ReviewPanel() {
  const [controller] = useState(() => new ReviewController());
  const state = useSyncExternalStore(controller.subscribe, controller.getState, controller.getState);
  const [token, setToken] = useState("");
  const [jobId, setJobId] = useState("");
  useEffect(() => {
    function disconnect() {
      controller.clear();
      setToken("");
      setJobId("");
    }
    window.addEventListener("offline", disconnect);
    return () => {
      window.removeEventListener("offline", disconnect);
      controller.clear();
    };
  }, [controller]);
  useEffect(() => {
    if (state.error && !state.job) {
      setToken("");
      setJobId("");
    }
  }, [state.error, state.job]);
  async function load(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const activeToken = token;
    setToken(""); // Credentials live only in the controller until clear/401/page close.
    await controller.load(activeToken, jobId.trim());
  }
  return <section className="login-card" aria-labelledby="review-heading">
    <h2 id="review-heading">Local service-token artifact review</h2>
    <p>Owner-scoped service-token bridge. Use the configured local bearer token
      that owns the job, or sign in above for account/project rendering.</p>
    <p>Service-account self-review, not independent human signoff.</p>
    <form onSubmit={load}>
      <label htmlFor="review-token">Local bearer token (never stored)</label>
      <input id="review-token" type="password" autoComplete="off" required value={token}
        onChange={(event) => { controller.clear(); setJobId(""); setToken(event.target.value); }} />
      <label htmlFor="review-job">Job ID</label>
      <input id="review-job" required value={jobId}
        onChange={(event) => { controller.clear(); setJobId(event.target.value); }} />
      <button type="submit" disabled={state.busy}>Load job and history</button>
      <button type="button" onClick={() => { controller.clear(); setToken(""); setJobId(""); }}>Clear review session</button>
    </form>
    <ReviewEvidence state={state} onDecision={(decision) => void controller.decide(decision)}
      onAcknowledge={controller.acknowledge} />
  </section>;
}

export function AuthenticatedReview({ token, jobId }: { token: string; jobId: string }) {
  const [controller] = useState(() => new ReviewController(api));
  const state = useSyncExternalStore(controller.subscribe, controller.getState, controller.getState);
  useEffect(() => () => controller.clear(), [controller]);
  return <section aria-label="Authenticated artifact review">
    <button type="button" disabled={state.busy} onClick={() => void controller.load(token, jobId)}>
      Load review details/history
    </button>
    <ReviewEvidence state={state} onDecision={(decision) => void controller.decide(decision)}
      onAcknowledge={controller.acknowledge} />
  </section>;
}
