# Nexmem Production Readiness Implementation Plan

This document outlines the final steps and engineering tasks required to take the Nexmem (formerly NexMem) AI Memory Layer from its current development/MVP state to a highly available, secure, and scalable production deployment.

---

## Phase 1: Security, Authentication, and Multi-Tenancy

Currently, the system has basic `app_id` scoping. For production, we must strictly enforce authentication and authorization.

- [ ] **Task 1.1: API Key Management**
  - Implement a secure API key generation and validation system using hashed keys stored in the database.
  - Create a FastAPI middleware/dependency (`Depends(verify_api_key)`) to protect all `/memory/*` endpoints.
- [ ] **Task 1.2: Rate Limiting and Quotas**
  - Integrate Redis-based rate limiting (e.g., `slowapi`) to restrict API calls based on subscription tiers (e.g., 1,000 free writes/month).
- [ ] **Task 1.3: Secrets Management**
  - Transition from local `.env` files to a secure secrets manager (AWS Secrets Manager, Doppler, or HashiCorp Vault) for production deployments.
  - Ensure `Pydantic BaseSettings` enforces strict validation of all required production variables.

## Phase 2: Database Scalability and Performance

As an AI memory layer, the database will experience high write throughput and require sub-millisecond retrieval times.

- [ ] **Task 2.1: PgBouncer / Connection Pooling**
  - Implement connection pooling (PgBouncer) to prevent FastAPI workers from exhausting PostgreSQL connections during high concurrency.
- [ ] **Task 2.2: pgvector Indexing Optimization**
  - Add HNSW (Hierarchical Navigable Small World) indexes to the embedding columns in the `semantic` and `engrams` tables to ensure vector similarity search (`<=>`) remains fast as the dataset grows.
- [ ] **Task 2.3: Database Migrations**
  - Audit all Alembic scripts (`alembic/versions/*`) to ensure they run idempotently in production.
  - Verify that the `CREATE EXTENSION IF NOT EXISTS vector;` step executes securely on the production RDS/Cloud SQL instance.

## Phase 3: Background Processing and Reliability

The background consolidation engine (extracting engrams and graphs via spaCy/NetworkX) must not block the main thread or be lost during server restarts.

- [x] **Task 3.1: Message Queue Integration**
  - Migrate the background `trigger_consolidation` tasks from FastAPI `BackgroundTasks` to a robust message queue like Celery (with Redis/RabbitMQ) or AWS SQS.
- [x] **Task 3.2: LLM Resiliency**
  - Add exponential backoff and retry mechanisms (using the `tenacity` library) for any external LLM calls (e.g., OpenAI/Claude) to handle rate limits or transient API outages.
- [x] **Task 3.3: Dead Letter Queues (DLQ)**
  - Implement DLQs for failed consolidation tasks to ensure no user memories are permanently lost if processing fails.

## Phase 4: Observability and Monitoring

You cannot fix what you cannot see. Production requires strict tracking of latency, errors, and LLM token usage.

- [x] **Task 4.1: Structured Logging**
  - Replace `print()` and standard python logging with structured JSON logging (e.g., `structlog`) to enable easy querying in Datadog or ELK.
- [x] **Task 4.2: Application Performance Monitoring (APM)**
  - Integrate Prometheus metrics (via `prometheus-fastapi-instrumentator`) to track endpoint latency (especially `get_memory_context`).
- [x] **Task 4.3: Error Tracking**
  - Integrate Sentry for real-time unhandled exception tracking and alert routing.
- [x] **Task 4.4: Cost/Token Tracking**
  - Implement a mechanism to log token usage per `app_id` to track LLM costs and bill users accurately.

## Phase 5: CI/CD and Deployment Automation

Ensure code can be deployed reliably and rollbacks are instantaneous.

- [x] **Task 5.1: Containerization**
  - Finalize `Dockerfile` (multi-stage build to reduce image size) and `docker-compose.prod.yml`.
- [x] **Task 5.2: CI/CD Pipeline (GitHub Actions)**
  - Create a pipeline that runs on every PR:
    - Code linting (`flake8`, `black`, `mypy`).
    - Automated tests.
    - Docker image build and push to a container registry (ECR, Docker Hub).
- [x] **Task 5.3: Backend Deployment (Render / AWS / GCP)**
  - Set up continuous deployment to a cloud provider. For Render, configure the `render.yaml` blueprint.
- [x] **Task 5.4: Frontend Deployment (Vercel)**
  - Connect the `nexmem-landing` Next.js repository to Vercel.
  - Map the custom domain (`nexmem.ai`).

## Phase 6: Testing and Quality Assurance

- [x] **Task 6.1: Unit & Integration Tests**
  - Write Pytest suites covering the entire core logic: unified write logic, cross-encoder reranking, and `app_id` context isolation.
  - Mock the database (using testcontainers or SQLite/pglite) and LLM responses.
- [x] **Task 6.2: Load Testing**
  - Use `Locust` or `k6` to simulate high-traffic agent interactions and ensure the FastAPI backend scales horizontally.
- [x] **Task 6.3: Security Audit**
  - Run SAST tools (e.g., `bandit`) to scan the codebase for vulnerabilities.
