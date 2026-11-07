import { ApiClient, ApiError } from "./client";
import type { JobReview, ReviewDecision, VideoJob } from "./types";

export interface ReviewState {
  job: VideoJob | null;
  history: JobReview[];
  busy: boolean;
  error: string;
  needsConfirmation?: boolean;
}

export class ReviewController {
  private state: ReviewState = { job: null, history: [], busy: false, error: "" };
  private listeners = new Set<() => void>();
  private token = "";
  private jobId = "";
  private generation = 0;

  constructor(private readonly client = new ApiClient()) {}

  getState = (): ReviewState => this.state;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private change(patch: Partial<ReviewState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  clear = () => {
    this.generation++;
    this.token = "";
    this.jobId = "";
    this.change({ job: null, history: [], busy: false, error: "", needsConfirmation: undefined });
  };

  acknowledge = () => this.change({ needsConfirmation: undefined });

  async load(token: string, jobId: string) {
    this.clear();
    this.token = token;
    this.jobId = jobId;
    const generation = this.generation;
    this.change({ busy: true });
    try {
      await this.refresh(generation);
    } catch (error) {
      this.failure(error, generation);
    } finally {
      if (generation === this.generation) this.change({ busy: false });
    }
  }

  private async refresh(generation: number) {
    const [job, history] = await Promise.all([
      this.client.getJob(this.jobId, this.token),
      this.client.getReviews(this.jobId, this.token),
    ]);
    if (generation === this.generation) this.change({ job, history });
  }

  private failure(error: unknown, generation: number) {
    if (generation !== this.generation) return;
    this.clear();
    this.change({ error: error instanceof ApiError ? error.message : "Review request failed." });
  }

  async decide(decision: ReviewDecision["decision"]) {
    const job = this.state.job;
    if (this.state.busy || this.state.needsConfirmation || !job?.review_enabled || !job.can_review || !job.review_artifact) return;
    const generation = this.generation;
    this.change({ busy: true, error: "" });
    try {
      await this.client.reviewJob(job.id, {
        decision, expected_version: job.review_version, expected_artifact: job.review_artifact,
      }, this.token);
      if (generation !== this.generation) return;
      await this.refresh(generation);
    } catch (error) {
      if (generation !== this.generation) return;
      if (error instanceof ApiError && (error.status === 409 || error.status === 503)) {
        try {
          await this.refresh(generation);
          if (generation === this.generation) this.change({
            error: `${error.message}. Refreshed; inspect before deciding again.`,
            needsConfirmation: true,
          });
        } catch (refreshError) {
          this.failure(refreshError, generation);
        }
      } else this.failure(error, generation);
    } finally {
      if (generation === this.generation) this.change({ busy: false });
    }
  }
}
