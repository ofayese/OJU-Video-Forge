export interface User {
  name: string;
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
  size_bytes: number;
  media_type: string;
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
}

export type RenderPreset = "preview" | "hd";

export interface CreateRenderInput {
  timeline: TimelineV1;
  preset: RenderPreset;
}

export interface VideoJob {
  id: string;
  operation?: string;
  status: string;
  artifact?: string | null;
  artifact_id?: string | null;
  error?: string | null;
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
