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

export interface VideoJob {
  id: string;
  operation: string;
  status: string;
  artifact: string | null;
  error: string | null;
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
