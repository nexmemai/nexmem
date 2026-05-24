# nexmem-js

TypeScript SDK for the NexMem memory API.

## Install

```bash
npm install nexmem-js
```

## Quickstart

```ts
import { MemoryClient } from "nexmem-js";

const client = new MemoryClient({ apiKey: "nxm_your_key_here" });

await client.remember("User prefers TypeScript for frontend work.");

const context = await client.recall("what language does this user prefer?");
console.log(context.memories.content);
```

## API

```ts
const client = new MemoryClient({
  apiKey: "nxm_your_key_here",
  baseUrl: "https://nexmem-api.onrender.com",
});

await client.remember("User prefers concise answers.", {
  appId: "optional-app-id",
  metadata: { source: "node" },
});

const context = await client.recall("how should I answer?", {
  limit: 5,
  appId: "optional-app-id",
});

await client.setProfile("tone", "direct");
const profile = await client.getProfile();

await client.link("TypeScript", "preferred_for", "frontend");

const data = await client.export();

await client.forgetAll(true);
```

`forgetAll(true)` permanently deletes the authenticated user's memories and invalidates authentication.

## Local development

The SDK defaults to the hosted production backend. To point it at a
backend you are running locally on `http://localhost:8000`, pass
`baseUrl` to the constructor:

```ts
import { MemoryClient } from "nexmem-js";

const client = new MemoryClient({
  apiKey: "nxm_replace_with_your_local_key",
  baseUrl: "http://localhost:8000",
});

await client.remember("User prefers concise answers.");
const context = await client.recall("how should I answer this user?");
console.log(context.content);
```

For a complete copy-pasteable end-to-end run that registers a fresh
demo user, mints an `nxm_` API key, and drives `remember` + `recall`
against a local backend, see
[`examples/javascript_quickstart.mjs`](../examples/javascript_quickstart.mjs)
and the [`examples/README.md`](../examples/README.md) prereqs.

## Development

```bash
npm install
npm run build
npm pack
```

## Publishing

`nexmem-js` is not on npm yet. Until it ships, build it from this
repository and import from `dist/` (see the local quickstart above for
an example). Once the package is published, the install line will be
`npm install nexmem-js`. The publish step itself is tracked as operator
action #12 in `KIRO_WORK_LOG.md`:

```bash
npm publish --access public
```

Package name: `nexmem-js`
