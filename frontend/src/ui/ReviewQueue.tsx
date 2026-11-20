import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { NarrationListing } from "../api/types";

const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  transcribing: "Transcribing",
  analyzing_scenes: "Analyzing scenes",
  drafting_script: "Drafting script",
  synthesizing_audio: "Synthesizing audio",
  mixing_audio: "Mixing audio",
  verifying_artifact: "Verifying artifact",
  awaiting_procedure_review: "Awaiting procedure review",
  evidence_approved: "Evidence approved",
  awaiting_script_approval: "Awaiting script approval",
  script_approved: "Script approved — ready to render",
  script_rejected: "Script rejected",
  awaiting_review: "Final video awaiting review",
  failed: "Failed",
};

const REVIEW_ACTION_LABELS: Record<string, string> = {
  awaiting_procedure_review: "Review procedure evidence",
  awaiting_script_approval: "Review and approve script",
  script_approved: "Render approved script",
  awaiting_review: "Review final video",
  script_rejected: "Revise rejected script",
};

function needsAction(listing: NarrationListing): boolean {
  const stage = listing.stage;
  return stage === "awaiting_procedure_review"
    || stage === "awaiting_script_approval"
    || stage === "script_approved"
    || stage === "awaiting_review"
    || stage === "script_rejected"
    || stage === "failed";
}

export function ReviewQueue({ projectId, token }: { projectId: string; token: string }) {
  const [listings, setListings] = useState<NarrationListing[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setBusy(true); setError("");
    api.listNarrations(projectId, token).then((result) => {
      if (active) setListings(result);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Failed to load review queue.");
    }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [projectId, token]);

  const pending = listings.filter(needsAction);
  const approved = listings.filter((item) => item.stage === "awaiting_review" && item.has_approved_script);

  if (busy) return <section className="panel" aria-label="Review queue"><p role="status">Loading review queue…</p></section>;

  return <section className="panel" aria-label="Review queue">
    <h2>Project review queue</h2>
    {error && <p role="alert" className="notice error">{error}</p>}
    <p>{listings.length} narration(s) · {pending.length} pending reviewer action</p>
    {pending.length === 0 && listings.length > 0 && <p>No items pending review.</p>}
    <table aria-label="Narration review status">
      <thead>
        <tr>
          <th>Narration</th><th>Stage</th><th>Revision</th><th>Approved script</th><th>Render profile</th><th>Action</th>
        </tr>
      </thead>
      <tbody>
        {listings.map((listing) => <tr key={listing.id}>
          <td>
            <strong>{listing.media_filename ?? listing.id}</strong>
            {listing.error && (
              <>
                <br /><span role="alert">{listing.error}</span>
              </>
            )}
          </td>
          <td>{STAGE_LABELS[listing.stage] ?? listing.stage}</td>
          <td>{listing.revision}</td>
          <td>{listing.has_approved_script ? "Yes" : "—"}</td>
          <td>{listing.render_profile}</td>
          <td>
            {needsAction(listing) && <button className="primary" onClick={() => {}}>
              {REVIEW_ACTION_LABELS[listing.stage] ?? "Review"}
            </button>}
            {!needsAction(listing) && <span style={{ color: "#666" }}>—</span>}
          </td>
        </tr>)}
      </tbody>
    </table>
    {approved.length > 0 && <p>
      {approved.length} narration(s) have an approved script ready for rendering.
    </p>}
  </section>;
}
