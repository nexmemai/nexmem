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

## Development

```bash
npm install
npm run build
npm pack
```

## Publishing

```bash
npm publish --access public
```

Package name: `nexmem-js`
