# API Versioning Policy (P12-J6)

## URL prefix

Every public route lives under `/api/v<major>/...`. Today the only
shipped major is `v1` — every router in `app/routers/*.py` is
included with `prefix="/api/v1"`.

The version segment is **mandatory** for every request to a versioned
route. There is no implicit "current" alias. The unversioned root
(`/`), `/health/*`, and `/metrics` are infrastructure endpoints and
are explicitly NOT versioned.

## Semantic Versioning of the API surface

We follow Semantic Versioning for the URL major number, not for the
service's pip / npm package versions:

* **Patch**: bug fixes, performance fixes, internal refactors. No
  client change required.
* **Minor**: additive changes — new routes, new optional fields on
  responses, new optional request fields. Existing clients keep
  working.
* **Major**: any of the following triggers a new major (`/api/v2/`):
  * Removing a route, query parameter, or response field.
  * Changing the type of a request or response field.
  * Tightening validation in a way that previously-accepted requests
    are now rejected.
  * Renaming an enum value, error code, or HTTP status mapping.
  * Changing authentication or authorisation semantics.

## Compatibility window

When a major bump happens, the previous major is supported in
parallel for **6 months from the release of the new major**. Both
`/api/v1/...` and `/api/v2/...` are reachable during the window.
After the window closes, the older prefix returns `410 Gone` with a
JSON body pointing to the new prefix.

## Deprecation signalling

Routes that will be removed at the next major bump emit a
`Deprecation: true` and `Sunset: <RFC 9745 timestamp>` response
header for the entire compatibility window. Clients should log on
seeing `Deprecation: true` and migrate before `Sunset`.

## Field-level deprecation

Within a major version, a field can be deprecated without a major
bump if both:

1. The field continues to be returned with its previous semantics,
   AND
2. A successor field is added at the same time.

The OpenAPI schema marks the deprecated field with
`"deprecated": true`. A field that is removed (rather than
deprecated) requires a major bump.

## Internal vs. public routes

The `/api/v1/auth/...` routes that are intended for internal /
operator use (`/auth/api-keys`, `/auth/sessions`,
`/auth/revoke-current-token`, `/auth/logout-all`) follow the same
versioning rules as customer-facing routes. We do not maintain a
separate "admin" API surface today; admin tooling will be added
under `/api/v1/admin/...` in a future PR (P11-I1) and will then be
held to the same compatibility commitments.

## Breaking change checklist

Before merging a PR that introduces a breaking change to the v1
surface, the author MUST either:

* (a) Adjust the change to be additive and refile, or
* (b) Open a tracking issue titled "v2 candidate: …" so the next
  major bump can collect the change with others.

The CI lint job will not catch breaking changes — review is the
primary control. Reviewers are explicitly asked to look for
removed routes, removed/typechanged response fields, and tightened
validation.

## SDK alignment

`nexmem-py` and `nexmem-js` follow the API major number: SDK
`1.x.x` targets `/api/v1/...`. SDK majors are bumped only when the
API major bumps. Inside an API major, SDKs follow regular
SemVer semantics.

## Schema source of truth

The OpenAPI schema is generated from the FastAPI route definitions
on every CI run. It is the source of truth for "what is the v1
surface". Hand-written API docs MUST link back to this generated
schema rather than re-stating field shapes, to avoid drift.
