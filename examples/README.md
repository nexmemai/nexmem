# Examples

Copy-pasteable, runnable examples for the Nexmem SDKs against a *local*
backend. None of these examples talk to a production deployment by
default, none hard-code real credentials, and none install the SDKs
from PyPI / npm — they import the SDK source directly out of this
repository.

## What's here

| File | What it does |
|---|---|
| [`python_quickstart.py`](./python_quickstart.py) | Bootstraps a throwaway demo account against a local backend, mints an `nxm_`-prefixed API key, then drives the `nexmem-py` SDK end-to-end (`remember` + `recall`). |
| [`javascript_quickstart.mjs`](./javascript_quickstart.mjs) | Same flow, using the `nexmem-js` SDK from `nexmem-js/dist/` (Node 18+, no extra dependencies). |

## Prereqs

You need one terminal running the backend in **demo mode** (no
Postgres or Redis required):

```bash
# from the repo root
pip install -r requirements.txt
DEMO_MODE=true uvicorn app.main:app --reload --port 8000
```

Leave that running. In a second terminal, run whichever quickstart
matches your language.

### Python

```bash
# from the repo root
pip install -e nexmem-py     # install the SDK from source
pip install httpx            # used by the quickstart's auth bootstrap

python examples/python_quickstart.py
# or:
python examples/python_quickstart.py --url http://localhost:8000 \
                                     --email demo-foo@example.local
```

### JavaScript / TypeScript

```bash
# from the repo root, build the SDK once:
( cd nexmem-js && npm install && npm run build )

# then run the quickstart with Node 18+:
node examples/javascript_quickstart.mjs
# or:
node examples/javascript_quickstart.mjs --url http://localhost:8000 \
                                        --email demo-foo@example.local
```

## What "local" means here

* The backend runs in `DEMO_MODE=true`, which uses in-memory stores —
  no Postgres, no Redis, no OpenAI key required for the quickstart's
  `remember` / `recall` path.
* The quickstart registers a fresh user per run (random email
  suffix), so re-running won't collide.
* The API key the script mints is `nxm_`-prefixed (Rule #15) and is
  shown only once by the backend; the script keeps it in memory and
  never writes it to disk.

## Local development vs. publishing

These examples deliberately **do not** install the published
`nexmem-py` / `nexmem-js` packages — the packages are not yet
published to PyPI / npm at the time of writing. The Python script
imports `nexmem` from a local `pip install -e nexmem-py`, and the
JavaScript script imports `MemoryClient` from `../nexmem-js/dist/`
directly. Once the packages ship, the imports can be flipped to the
published names without changing any other line.

The publish-readiness work is tracked separately:

* `KIRO_WORK_LOG.md` Section 9 operator action #11 (PyPI)
* `KIRO_WORK_LOG.md` Section 9 operator action #12 (npm)
