export type JsonObject = Record<string, unknown>;

export interface Episode {
  episodicId?: string | null;
  semanticId?: string | null;
  engramId?: string | null;
  nodesCreated: number;
  edgesCreated: number;
  message: string;
  raw: JsonObject;
}

export interface ContextMemories {
  content: string;
  semanticHits: JsonObject[];
  recentEpisodes: JsonObject[];
}

export interface Context {
  content: string;
  memories: ContextMemories;
  engramContext: string;
  preferences: JsonObject;
  graphContext: JsonObject;
  metadata: JsonObject;
  raw: JsonObject;
}

export type Profile = JsonObject;

export interface ExportData {
  exported_at?: string;
  user_id?: string;
  episodic?: JsonObject[];
  semantic?: JsonObject[];
  procedural?: JsonObject[];
  graph?: {
    nodes?: JsonObject[];
    edges?: JsonObject[];
  };
  engrams?: JsonObject[];
  [key: string]: unknown;
}

export interface MemoryClientConfig {
  apiKey: string;
  baseUrl?: string;
}

export interface RememberOptions {
  appId?: string;
  metadata?: JsonObject;
}

export interface RecallOptions {
  limit?: number;
  appId?: string;
}
