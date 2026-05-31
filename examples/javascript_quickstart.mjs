// Nexmem JavaScript SDK — local end-to-end quickstart.
//
// This script bootstraps a fresh account against a *local* Nexmem
// backend, mints an API key, then drives the published ``nexmem-js``
// SDK end-to-end (remember + recall). Everything runs against
// http://localhost:8000 by default, which is the port a fresh
// ``uvicorn app.main:app --reload`` listens on.
//
// Prereqs (one terminal, from the repo root):
//
//     # build the SDK from source so this script can import it:
//     ( cd nexmem-js && npm install && npm run build )
//
//     # start the backend in DEMO_MODE so no Postgres / Redis is needed:
//     DEMO_MODE=true uvicorn app.main:app --reload --port 8000
//
// In a second terminal:
//
//     node examples/javascript_quickstart.mjs
//
// Flags:
//
//     --url    base URL of the backend          (default: http://localhost:8000)
//     --email  email to register / log in as    (default: a unique demo address)
//
// This script never hard-codes a real API key or password. The
// password used for the throwaway demo account is intentionally weak
// and is only suitable for ``DEMO_MODE=true`` local runs. Do not point
// this script at a production deployment.
//
// Requires Node 18+ (built-in ``fetch`` and ``crypto.randomUUID``).

import { randomBytes } from "node:crypto";

import { MemoryClient } from "../nexmem-js/dist/index.js";

const DEFAULT_BASE_URL = "http://localhost:8000";

function banner(step, title) {
  const bar = "=".repeat(60);
  console.log(`\n${bar}\nSTEP ${step}: ${title}\n${bar}`);
}

function parseArgs(argv) {
  const out = { url: DEFAULT_BASE_URL, email: `demo-${randomBytes(4).toString("hex")}@example.local` };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === "--url") {
      out.url = argv[i + 1];
      i += 1;
    } else if (flag === "--email") {
      out.email = argv[i + 1];
      i += 1;
    } else if (flag === "-h" || flag === "--help") {
      console.log("Usage: node examples/javascript_quickstart.mjs [--url URL] [--email EMAIL]");
      process.exit(0);
    }
  }
  return out;
}

async function postJson(baseUrl, path, body, headers = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${path} → ${response.status} ${text}`);
  }
  return response.json();
}

async function bootstrapApiKey(baseUrl, email, password) {
  banner(1, "Register user");
  await postJson(baseUrl, "/api/v1/auth/register", { email, password });
  console.log(`registered ${email}`);

  banner(2, "Log in (get short-lived JWT)");
  const login = await postJson(baseUrl, "/api/v1/auth/login", { email, password });
  const accessToken = login.access_token;
  // Do not log the token or its length — CodeQL flags any sensitive
  // value reaching a logging sink as clear-text logging.
  console.log("access_token received [REDACTED — do not log in production]");

  banner(3, "Create API key (returned once, prefix nxm_)");
  const keyData = await postJson(
    baseUrl,
    "/api/v1/auth/api-keys",
    { name: "local-quickstart" },
    { Authorization: `Bearer ${accessToken}` },
  );
  const rawKey = keyData.api_key;
  if (!rawKey.startsWith("nxm_")) {
    // Report only a static message; never echo any portion of the key.
    throw new Error("unexpected API key prefix from backend (expected nxm_)");
  }
  console.log("api_key created (prefix nxm_) [REDACTED — do not log in production]");
  return rawKey;
}

async function run({ url, email }) {
  // Throwaway password for DEMO_MODE only. Generated fresh per run so
  // nothing identifying ever lands in the script source.
  const password = `demo-pw-${randomBytes(8).toString("hex")}`;

  const apiKey = await bootstrapApiKey(url, email, password);

  banner(4, "Open SDK client pointed at local backend");
  const client = new MemoryClient({ apiKey, baseUrl: url });

  banner(5, "remember(): write an episodic memory");
  const episode = await client.remember(
    "User prefers TypeScript for frontend work and concise answers.",
    { metadata: { source: "javascript_quickstart" } },
  );
  console.log(`episodicId = ${episode.episodicId}`);
  console.log(`semanticId = ${episode.semanticId}`);
  console.log(`engramId   = ${episode.engramId}`);

  banner(6, "remember(): write a second episode");
  await client.remember(
    "User is building a memory layer with FastAPI and pgvector.",
    { metadata: { source: "javascript_quickstart" } },
  );
  console.log("second episode written");

  banner(7, "recall(): retrieve assembled context for a query");
  const context = await client.recall(
    "what is the user working on and how do they like to be answered?",
    { limit: 5 },
  );
  const preview = context.content.slice(0, 400);
  const suffix = context.content.length > 400 ? "..." : "";
  console.log(`\nAssembled context (${context.content.length} chars):`);
  console.log("-".repeat(40));
  console.log(preview + suffix);
  console.log("-".repeat(40));
  console.log(`semantic hits: ${context.memories.semanticHits.length}`);
  console.log(`recent episodes: ${context.memories.recentEpisodes.length}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  try {
    await run(args);
  } catch (err) {
    if (err && typeof err === "object" && "cause" in err && err.cause && err.cause.code === "ECONNREFUSED") {
      console.error(
        `\nCould not reach the backend at ${args.url}. Is ` +
        "`uvicorn app.main:app --reload --port 8000` running?\n" +
        `(${err})`,
      );
      process.exit(2);
    }
    console.error(err);
    process.exit(1);
  }
  console.log("\nDone. Local quickstart succeeded.");
}

main();
