export interface User {
  id: string;
  username: string;
  roles?: string[];
  provider?: string;
}

export interface AuthSession {
  access_token: string;
  token_type?: string;
  user?: User;
}

export interface Project {
  id: string;
  name: string;
  created_at?: string;
}

export interface ProjectList {
  projects: Project[];
}

export interface CreateProjectInput {
  name: string;
}

export interface MediaAsset {
  id: string;
  project_id: string;
  filename: string;
  size_bytes: number;
  content_type: string;
  sha256: string;
}

export interface TimelineClip {
  media_id: string;
  in_ms: number;
  out_ms: number;
}

export interface TimelineV1 {
  version: 1;
  clips: TimelineClip[];
  output: { profile: "preview"; format: "mp4" };
  title?: TimelineOverlay;
  captions?: TimelineOverlay[];
  audio?: { media_id: string; in_ms: number; volume_percent: number };
}

export interface TimelineOverlay {
  text: string;
  start_ms: number;
  end_ms: number;
}

export interface CreateRenderInput {
  operation: "render_timeline_v1";
  timeline: TimelineV1;
}

export interface CreateWanGenerationInput {
  operation: "wan22_t2v";
  prompt: string;
  negative_prompt: string;
  width: number;
  height: number;
  frames: number;
  fps: number;
  seed: number;
}

export interface VideoJob {
  id: string;
  operation?: string;
  project_id?: string | null;
  media_id?: string | null;
  status: string;
  artifact: string | null;
  project_artifact_id?: string | null;
  error: string | null;
  dispatch_attempts?: number;
  dispatch_error?: string | null;
  review_contract_version: number;
  review_version: number;
  review_enabled: boolean;
  can_review: boolean;
  review_artifact: ArtifactIdentity | null;
}

export interface ArtifactIdentity {
  id: string;
  sha256: string;
}

export interface ReviewDecision {
  decision: "approved" | "rejected";
  expected_version: number;
  expected_artifact: ArtifactIdentity;
}

export interface JobReview {
  contract_version: number;
  job_id: string;
  actor: string;
  at: string;
  artifact_id: string;
  artifact_sha256: string;
  expected_version: number;
  version: number;
  decision: "approved" | "rejected";
}

export interface VideoModel {
  id: string;
  operation: string;
  status: string;
}

export interface ModelList {
  models: VideoModel[];
}

export interface LoginInput {
  username: string;
  password: string;
}

export interface NarrationSegment {
  id: string;
  start_ms: number;
  end_ms: number;
  text: string;
  evidence: string[];
  uncertainty: string;
  estimated_ms: number;
}

export interface TrainingProcedureStep {
  id: string;
  start_ms: number;
  end_ms: number;
  description: string;
  evidence: string[];
  uncertainty: ("click" | "keyboard" | "intent" | "visual")[];
  evidence_category: "direct_observation" | "supported_inference" | "unverified" | "missing";
  confidence: "unassessed" | "low" | "medium" | "high";
  decision: "pending" | "accepted" | "rejected";
}

export interface TrainingClaim {
  text: string;
  kind: "evidence" | "supplemental";
  evidence: string[];
}

export interface TrainingDraft {
  title: string;
  audience: string;
  prerequisites: TrainingClaim[];
  learning_objectives: TrainingClaim[];
  chapters: (TrainingClaim & { id: string; start_ms: number; end_ms: number })[];
  script: (TrainingClaim & { id: string; start_ms: number; end_ms: number })[];
  annotations: (TrainingClaim & { id: string; start_ms: number; end_ms: number })[];
  technical_explanations: TrainingClaim[];
  lab_steps: TrainingClaim[];
  validation_checkpoints: TrainingClaim[];
  troubleshooting: TrainingClaim[];
  assessment: TrainingClaim[];
  answer_explanations: TrainingClaim[];
}

export interface TrainingAgentStatus {
  status: "disabled" | "unconfigured" | "unavailable" | "degraded" | "ready";
  reason: string;
  provider: { provider: string; model: string; model_digest: string; endpoint_sha256: string } | null;
}

export interface NarrationSettings {
  voice: "en-us" | "en-gb";
  rate: number;
  audio_mode: "replace" | "mix" | "preserve";
  original_volume: number;
  narration_gain: number;
  render_annotations: boolean;
}

export interface NarrationOcrProvenance {
  provider: string;
  provider_version: string;
  engine: string;
  engine_version: string;
  runtime: string;
  model_sha256: Record<string, string>;
}

export interface NarrationResult {
  id: string;
  project_id: string;
  media_id: string;
  job_id: string;
  stage: string;
  error: string | null;
  revision: number;
  approved_revision: number | null;
  script: NarrationSegment[];
  options: Record<string, unknown>;
  analysis: {
    procedure?: { contract_version: number; status: string; steps: TrainingProcedureStep[] };
    training_module?: { contract_version: number; draft: TrainingDraft;
      provider: { provider: string; model: string; model_digest: string } };
    media?: { format: { duration: string } };
    transcript?: { status: string; language?: string | null };
    scene_analysis?: { status: string; error: string | null };
    ocr?: string;
    vision?: string;
    scenes?: { id: string; timestamp_ms: number; major_change: boolean; ocr: string | null;
      artifact_id?: string; frame_sha256?: string;
      ocr_provenance?: NarrationOcrProvenance | null;
      ocr_status?: string; ocr_error?: string | null;
      vision: { text: string; uncertain: boolean } | null;
      vision_status?: string; vision_error?: string | null }[];
  };
  artifacts: { id: string; job_id: string; filename: string; media_type: string;
    sha256: string; size_bytes: number; script_revision: number }[];
}
