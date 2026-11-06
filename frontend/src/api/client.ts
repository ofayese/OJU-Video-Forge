import type {
  AuthSession,
  CreateProjectInput,
  LoginInput,
  ModelList,
  Project,
  ProjectList,
  User,
  VideoJob,
} from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type UnauthorizedHandler = () => void;

export class ApiClient {
  private unauthorizedHandler?: UnauthorizedHandler;

  constructor(
    private readonly baseUrl: string = API_BASE_URL,
    private readonly fetcher: typeof fetch = fetch,
  ) {}

  setUnauthorizedHandler(handler: UnauthorizedHandler): void {
    this.unauthorizedHandler = handler;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
    token?: string,
  ): Promise<T> {
    const headers = new Headers(options.headers);
    if (options.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if (token) headers.set("Authorization", `Bearer ${token}`);

    let response: Response;
    try {
      response = await this.fetcher(`${this.baseUrl}${path}`, {
        ...options,
        headers,
      });
    } catch {
      throw new ApiError("Unable to reach the API. Check the API URL and connection.", 0);
    }

    if (!response.ok) {
      const errorMessage = await this.readError(response);
      if (response.status === 401) this.unauthorizedHandler?.();
      throw new ApiError(errorMessage, response.status);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  private async requestBlob(path: string, token: string): Promise<Blob> {
    const headers = new Headers();
    headers.set("Authorization", ["Bearer", token].join(" "));

    let response: Response;
    try {
      response = await this.fetcher(`${this.baseUrl}${path}`, { headers });
    } catch {
      throw new ApiError("Unable to reach the API. Check the API URL and connection.", 0);
    }

    if (!response.ok) {
      const errorMessage = await this.readError(response);
      if (response.status === 401) this.unauthorizedHandler?.();
      throw new ApiError(errorMessage, response.status);
    }
    return response.blob();
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
    return this.request<AuthSession>("/auth/login", {
      method: "POST",
      body: JSON.stringify(credentials),
    });
  }

  async logout(token: string): Promise<void> {
    await this.request<void>("/auth/logout", { method: "POST" }, token);
  }

  async currentUser(token: string): Promise<User> {
    return this.request<User>("/auth/me", {}, token);
  }

  async listProjects(token: string): Promise<Project[]> {
    const result = await this.request<Project[] | ProjectList>("/projects", {}, token);
    return Array.isArray(result) ? result : result.projects;
  }

  async createProject(input: CreateProjectInput, token: string): Promise<Project> {
    return this.request<Project>(
      "/projects",
      { method: "POST", body: JSON.stringify(input) },
      token,
    );
  }

  async listModels(token: string): Promise<ModelList> {
    return this.request<ModelList>("/v1/models", {}, token);
  }

  async getJob(jobId: string, token: string): Promise<VideoJob> {
    return this.request<VideoJob>(`/v1/jobs/${encodeURIComponent(jobId)}`, {}, token);
  }

  async getArtifact(jobId: string, token: string): Promise<Blob> {
    return this.requestBlob(
      `/v1/jobs/${encodeURIComponent(jobId)}/artifact`,
      token,
    );
  }
}

export const api = new ApiClient();
