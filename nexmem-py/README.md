# nexmem-py

Python SDK for the NexMem memory API.

## Install

```bash
pip install nexmem-py
```

For local development from this repository:

```bash
pip install -e .
```

## Quickstart

```python
from nexmem import MemoryClient

client = MemoryClient(api_key="nxm_your_key_here")

await client.remember("User prefers Python over JavaScript for backend.")

context = await client.recall("what language does this user prefer?")
print(context.memories.content)

await client.aclose()
```

Using an async context manager:

```python
from nexmem import MemoryClient

async with MemoryClient(api_key="nxm_your_key_here") as client:
    await client.remember("User prefers short, direct technical answers.")
    context = await client.recall("how should I answer this user?")
    print(context.content)
```

## Synchronous Usage

```python
from nexmem import SyncMemoryClient

with SyncMemoryClient(api_key="nxm_your_key_here") as client:
    client.remember("User prefers Python for backend work.")
    context = client.recall("what backend language does the user prefer?")
    print(context.memories.content)
```

## API

```python
await client.remember(text, app_id=None, metadata=None)
await client.recall(query, limit=5, app_id=None)
await client.set_profile(key, value)
await client.get_profile()
await client.link(entity1, relation, entity2)
await client.export()
await client.forget_all(confirm=True)
```

`forget_all(confirm=True)` permanently deletes the authenticated user's memories and invalidates authentication.

## Publishing

```bash
python -m build
twine upload dist/*
```

Package name: `nexmem-py`
