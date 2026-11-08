import { ApiClient } from "./client";

/** Owns only an in-memory object URL; never a server URL or persisted bytes. */
export class MemoryArtifact {
  private url = "";
  private generation = 0;
  constructor(
    private readonly client: ApiClient,
    private readonly urls: Pick<typeof URL, "createObjectURL" | "revokeObjectURL"> = URL,
  ) {}

  clear() {
    this.generation++;
    if (this.url) this.urls.revokeObjectURL(this.url);
    this.url = "";
  }

  async load(jobId: string, token: string): Promise<string | null> {
    this.clear();
    const generation = this.generation;
    const blob = await this.client.getArtifact(jobId, token);
    if (generation !== this.generation) return null;
    this.url = this.urls.createObjectURL(blob);
    return this.url;
  }
}
