/**
 * Jest tests for nexmem-js MemoryClient (P12-J2, Block 8).
 *
 * Strategy: stub the global ``fetch`` rather than the SDK's wrapper so we
 * exercise the same code path real callers do. Each test asserts:
 *
 *   1. the URL the client hit matches the expected backend route, AND
 *   2. the request carried the ``Authorization: ApiKey <key>`` header, AND
 *   3. for the body-bearing routes, the JSON shape matches the spec.
 *
 * The four spec-named tests live here. The fifth (forgetAll requires
 * confirm=true) is a pure-arg-validation check that does not even touch
 * fetch — it stays in this file so all SDK behaviour is covered in one
 * spot.
 */
import { afterEach, beforeEach, describe, expect, jest, test } from "@jest/globals";

import { MemoryClient } from "../client.js";
import { NexMemAuthError } from "../errors.js";


type FetchCall = {
  url: string;
  init: RequestInit;
};

function makeFetchMock(handler: (call: FetchCall) => Response | Promise<Response>) {
  const calls: FetchCall[] = [];
  const mockFn = (async (input: string | URL, init: RequestInit = {}) => {
    const call: FetchCall = { url: typeof input === "string" ? input : input.toString(), init };
    calls.push(call);
    return handler(call);
  }) as unknown as typeof fetch;

  // Cast keeps TypeScript happy — global.fetch is typed as ``typeof fetch``.
  (globalThis as unknown as { fetch: typeof fetch }).fetch = mockFn;
  return calls;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}


describe("MemoryClient", () => {
  let originalFetch: typeof fetch;

  beforeEach(() => {
    // Save and restore so a test that forgets to set fetch doesn't poison
    // the next test or any unrelated process global.
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    (globalThis as unknown as { fetch: typeof fetch }).fetch = originalFetch;
    jest.restoreAllMocks();
  });

  test("remember calls correct endpoint with auth header", async () => {
    const calls = makeFetchMock((call) => {
      if (call.url.endsWith("/api/v1/memory/episode/write")) {
        return jsonResponse({
          episodic_id: "ep_1",
          semantic_id: "sem_1",
          engram_id: "eng_1",
          nodes_created: 0,
          edges_created: 0,
          message: "ok",
        });
      }
      throw new Error(`unexpected url ${call.url}`);
    });

    const client = new MemoryClient({
      apiKey: "nxm_test",
      baseUrl: "https://api.test",
    });
    const episode = await client.remember("hello world", {
      appId: "app_1",
      metadata: { who: "test" },
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("https://api.test/api/v1/memory/episode/write");
    expect(calls[0].init.method).toBe("POST");
    const headers = new Headers(calls[0].init.headers);
    expect(headers.get("authorization")).toBe("ApiKey nxm_test");
    expect(headers.get("content-type")).toBe("application/json");

    const body = JSON.parse(String(calls[0].init.body));
    expect(body.content).toBe("hello world");
    expect(body.app_id).toBe("app_1");
    expect(body.metadata).toEqual({ who: "test" });
    expect(typeof body.session_id).toBe("string");

    expect(episode.episodicId).toBe("ep_1");
    expect(episode.message).toBe("ok");
  });

  test("recall calls correct endpoint and parses memories", async () => {
    const calls = makeFetchMock((call) => {
      if (call.url.endsWith("/api/v1/memory/context")) {
        return jsonResponse({
          assembled_context: "User prefers Python.",
          engram_context: "Known: Python",
          semantic_hits: [{ content_preview: "Python" }],
          recent_episodes: [],
          preferences: {},
          graph_context: {},
          metadata: { total_tokens: 4 },
        });
      }
      throw new Error(`unexpected url ${call.url}`);
    });

    const client = new MemoryClient({
      apiKey: "nxm_test",
      baseUrl: "https://api.test",
    });
    const context = await client.recall("language?", { limit: 7 });

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("https://api.test/api/v1/memory/context");
    const body = JSON.parse(String(calls[0].init.body));
    expect(body.query).toBe("language?");
    expect(body.semantic_top_k).toBe(7);
    expect(body.episodic_limit).toBe(7);

    expect(context.content).toBe("User prefers Python.");
    expect(context.memories.semanticHits).toHaveLength(1);
  });

  test("forgetAll sends X-Confirm-Delete header", async () => {
    const calls = makeFetchMock((call) => {
      if (call.url.endsWith("/api/v1/auth/me")) {
        return jsonResponse({ id: "user_1" });
      }
      if (call.url.endsWith("/api/v1/memory/user/user_1/all")) {
        return new Response(null, { status: 204 });
      }
      throw new Error(`unexpected url ${call.url}`);
    });

    const client = new MemoryClient({
      apiKey: "nxm_test",
      baseUrl: "https://api.test",
    });
    await client.forgetAll(true);

    // Two calls: /auth/me to discover user_id, then DELETE /user/{id}/all.
    expect(calls).toHaveLength(2);
    expect(calls[0].url).toBe("https://api.test/api/v1/auth/me");
    expect(calls[1].url).toBe("https://api.test/api/v1/memory/user/user_1/all");
    expect(calls[1].init.method).toBe("DELETE");
    const headers = new Headers(calls[1].init.headers);
    expect(headers.get("x-confirm-delete")).toBe("true");
  });

  test("forgetAll without confirm refuses without making any HTTP call", async () => {
    let calls = 0;
    makeFetchMock(() => {
      calls += 1;
      return jsonResponse({});
    });
    const client = new MemoryClient({
      apiKey: "nxm_test",
      baseUrl: "https://api.test",
    });
    await expect(client.forgetAll()).rejects.toThrow(/confirm=true/);
    expect(calls).toBe(0);
  });

  test("invalid api key throws NexMemAuthError", async () => {
    makeFetchMock(() =>
      jsonResponse({ detail: "Invalid API key" }, 401),
    );
    const client = new MemoryClient({
      apiKey: "nxm_bad",
      baseUrl: "https://api.test",
    });
    await expect(client.remember("ignored")).rejects.toThrow(NexMemAuthError);
  });

  test("constructor rejects empty api key", () => {
    expect(() => new MemoryClient({ apiKey: "" })).toThrow(/apiKey is required/);
  });
});
