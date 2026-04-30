import { NexMemApiError, NexMemAuthError } from "./errors.js";
import type {
  Context,
  ExportData,
  JsonObject,
  MemoryClientConfig,
  Profile,
  RecallOptions,
  RememberOptions,
  Episode,
} from "./types.js";

const DEFAULT_BASE_URL = "https://nexmem-api.onrender.com";

export class MemoryClient {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private userId?: string;

  constructor(config: MemoryClientConfig) {
    if (!config.apiKey) {
      throw new Error("apiKey is required");
    }

    this.apiKey = config.apiKey;
    this.baseUrl = (config.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
  }

  async remember(text: string, options: RememberOptions = {}): Promise<Episode> {
    const data = await this.request<JsonObject>("/api/v1/memory/episode/write", {
      method: "POST",
      body: JSON.stringify({
        content: text,
        session_id: crypto.randomUUID(),
        app_id: options.appId ?? null,
        metadata: options.metadata ?? {},
        tags: [],
      }),
    });

    return {
      episodicId: asStringOrNull(data.episodic_id),
      semanticId: asStringOrNull(data.semantic_id),
      engramId: asStringOrNull(data.engram_id),
      nodesCreated: asNumber(data.nodes_created),
      edgesCreated: asNumber(data.edges_created),
      message: typeof data.message === "string" ? data.message : "",
      raw: data,
    };
  }

  async recall(query: string, options: RecallOptions = {}): Promise<Context> {
    const limit = options.limit ?? 5;
    const data = await this.request<JsonObject>("/api/v1/memory/context", {
      method: "POST",
      body: JSON.stringify({
        query,
        semantic_top_k: limit,
        episodic_limit: limit,
        app_id: options.appId ?? null,
      }),
    });

    const content = typeof data.assembled_context === "string" ? data.assembled_context : "";
    return {
      content,
      memories: {
        content,
        semanticHits: asObjectArray(data.semantic_hits),
        recentEpisodes: asObjectArray(data.recent_episodes),
      },
      engramContext: typeof data.engram_context === "string" ? data.engram_context : "",
      preferences: asObject(data.preferences),
      graphContext: asObject(data.graph_context),
      metadata: asObject(data.metadata),
      raw: data,
    };
  }

  async setProfile(key: string, value: unknown): Promise<void> {
    const userId = await this.getUserId();
    const profile = await this.getProfile();
    profile[key] = value;

    await this.request(`/api/v1/agents/${userId}/procedural/settings`, {
      method: "POST",
      body: JSON.stringify({
        settings: profile,
        workflows: [],
      }),
    });
  }

  async getProfile(): Promise<Profile> {
    const userId = await this.getUserId();
    const data = await this.request<JsonObject>(`/api/v1/agents/${userId}/procedural/settings`);
    return asObject(data.settings);
  }

  async link(entity1: string, relation: string, entity2: string): Promise<void> {
    const userId = await this.getUserId();
    const first = await this.request<JsonObject>(`/api/v1/agents/${userId}/graph/nodes`, {
      method: "POST",
      body: JSON.stringify({ label: entity1, type: "entity", properties: {} }),
    });
    const second = await this.request<JsonObject>(`/api/v1/agents/${userId}/graph/nodes`, {
      method: "POST",
      body: JSON.stringify({ label: entity2, type: "entity", properties: {} }),
    });

    await this.request(`/api/v1/agents/${userId}/graph/edges`, {
      method: "POST",
      body: JSON.stringify({
        from_node_id: first.id,
        to_node_id: second.id,
        relation,
        weight: 1,
        metadata: {},
      }),
    });
  }

  async forgetAll(confirm = false): Promise<void> {
    if (!confirm) {
      throw new Error("Pass confirm=true to permanently delete all memories");
    }

    const userId = await this.getUserId();
    await this.request(`/api/v1/memory/user/${userId}/all`, {
      method: "DELETE",
      headers: { "X-Confirm-Delete": "true" },
    });
    this.userId = undefined;
  }

  async export(): Promise<ExportData> {
    const userId = await this.getUserId();
    return this.request<ExportData>(`/api/v1/memory/user/${userId}/export`);
  }

  private async getUserId(): Promise<string> {
    if (!this.userId) {
      const data = await this.request<JsonObject>("/api/v1/auth/me");
      if (typeof data.id !== "string") {
        throw new Error("NexMem API did not return a user id");
      }
      this.userId = data.id;
    }
    return this.userId as string;
  }

  private async request<T = JsonObject>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `ApiKey ${this.apiKey}`);
    headers.set("Accept", "application/json");
    if (init.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }

    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers,
    });

    if (!response.ok) {
      const message = await errorMessage(response);
      const ErrorClass = response.status === 401 || response.status === 403
        ? NexMemAuthError
        : NexMemApiError;
      throw new ErrorClass(response.status, message, response);
    }

    if (response.status === 204) {
      return {} as T;
    }

    const text = await response.text();
    return (text ? JSON.parse(text) : {}) as T;
  }
}

async function errorMessage(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) {
    return response.statusText;
  }

  try {
    const data = JSON.parse(text) as JsonObject;
    const detail = data.detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail ?? data);
  } catch {
    return text;
  }
}

function asStringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown): number {
  return typeof value === "number" ? value : 0;
}

function asObject(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}
