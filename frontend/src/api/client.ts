import type {
  AuthSession,
  CreateWanGenerationInput,
  CreateRenderInput,
  CreateProjectInput,
  LoginInput,
  MediaAsset,
  ModelList,
  Project,
  ProjectList,
  User,
  VideoJob,
  JobReview,
  ReviewDecision,
} from "./types";

// Production reaches the private API only through nginx on the page's origin.
// An explicit origin override is a development-only convenience.
const API_BASE_URL = import.meta.env.PROD
  ? ""
  : (import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type UnauthorizedHandler = (token?: string) => void;

export class ApiClient {
  private unauthorizedHandler?: UnauthorizedHandler;

  constructor(
    private readonly baseUrl: string = API_BASE_URL,
    // Native browser fetch must not receive an ApiClient instance as `this`.
    private readonly fetcher: typeof fetch = (...args) => globalThis.fetch(...args),
  ) {}

  setUnauthorizedHandler(handler: UnauthorizedHandler): void {
    this.unauthorizedHandler = handler;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
    token?: string,
    responseType: "json" | "blob" = "json",
  ): Promise<T> {
    const headers = new Headers(options.headers);
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    if (options.body && !isFormData && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (token) headers.set("Authorization", `Bearer ${token}`);

    let response: Response;
    try {
      response = await this.fetcher(`${this.baseUrl}${path}`, {
        ...options,
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        headers,
      });
    } catch {
      throw new ApiError("Unable to reach the API. Check the API URL and connection.", 0);
    }

    if (!response.ok) {
      const errorMessage = await this.readError(response);
      if (response.status === 401) this.unauthorizedHandler?.(token);
      throw new ApiError(errorMessage, response.status);
    }
    if (response.status === 204) return undefined as T;
    if (responseType === "blob") return (await response.blob()) as T;
    return (await response.json()) as T;
  }

  private async readError(response: Response): Promise<string> {
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        const detail = body.detail;
        if (typeof detail === "string") return detail;
      }
    } catch {
      // Fall through to an HTTP status message for non-JSON responses.
    }
    return `Request failed (${response.status} ${response.statusText || "API error"}).`;
  }

  async login(credentials: LoginInput): Promise<AuthSession> {
    return this.request<AuthSession>("/v1/auth/session", {
      method: "POST",
      body: JSON.stringify(credentials),
    });
  }

  async register(credentials: LoginInput): Promise<User> {
    return this.request<User>("/v1/auth/register", {
      method: "POST", body: JSON.stringify(credentials),
    });
  }

  async logout(token: string): Promise<void> {
    await this.request<void>("/v1/auth/session", { method: "DELETE" }, token);
  }

  async currentUser(token: string): Promise<User> {
    return this.request<User>("/v1/auth/session", {}, token);
  }

  async listProjects(token: string): Promise<Project[]> {
    const result = await this.request<Project[] | ProjectList>("/v1/projects", {}, token);
    return Array.isArray(result) ? result : result.projects;
  }

  async createProject(input: CreateProjectInput, token: string): Promise<Project> {
    return this.request<Project>(
      "/v1/projects",
      { method: "POST", body: JSON.stringify(input) },
      token,
    );
  }

  async uploadMedia(projectId: string, file: File, token: string): Promise<MediaAsset> {
    const body = new FormData();
    body.append("file", file);
    return this.request<MediaAsset>(
      `/v1/projects/${encodeURIComponent(projectId)}/media`,
      { method: "POST", body },
      token,
    );
  }

  async listMedia(projectId: string, token: string): Promise<MediaAsset[]> {
    return this.request<MediaAsset[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/media`, {}, token,
    );
  }

  async createRenderJob(
    projectId: string,
    input: CreateRenderInput,
    idempotencyKey: string,
    token: string,
  ): Promise<VideoJob> {
    return this.request<VideoJob>(
      `/v1/projects/${encodeURIComponent(projectId)}/jobs`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(input),
      },
      token,
    );
  }

  async createWanGenerationJob(
    projectId: string,
    input: CreateWanGenerationInput,
    idempotencyKey: string,
    token: string,
  ): Promise<VideoJob> {
    return this.request<VideoJob>(
      "/v1/jobs",
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ ...input, project_id: projectId }),
      },
      token,
    );
  }

  async listModels(token: string): Promise<ModelList> {
    return this.request<ModelList>("/v1/models", {}, token);
  }

  async getJob(jobId: string, token: string): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/jobs/${encodeURIComponent(jobId)}`, {}, token);
  }

  async getReviews(jobId: string, token: string): Promise<JobReview[]> {
    return this.request<JobReview[]>(`/v1/jobs/${encodeURIComponent(jobId)}/reviews`, {}, token);
  }

  async reviewJob(jobId: string, input: ReviewDecision, token: string): Promise<VideoJob> {
    return this.request<VideoJob>(
      `/v1/jobs/${encodeURIComponent(jobId)}/review`,
      { method: "POST", body: JSON.stringify(input) },
      token,
    );
  }

  async getArtifact(jobId: string, token: string): Promise<Blob> {
    return this.request<Blob>(
      `/v1/jobs/${encodeURIComponent(jobId)}/artifact`,
      {},
      token,
      "blob",
    );
  }

  async getProjectArtifact(projectId: string, artifactId: string, token: string): Promise<Blob> {
    return this.request<Blob>(
      `/v1/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`,
      {},
      token,
      "blob",
    );
  }
}

export const api = new ApiClient();
