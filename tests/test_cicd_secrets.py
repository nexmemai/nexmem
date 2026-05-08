"""Regression tests for committed deployment secrets."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_SCANNED_FILES = [
    ROOT / "alembic" / "env.py",
    ROOT / "render.yaml",
    ROOT / "Dockerfile",
    ROOT / "docker-compose.prod.yml",
    ROOT / "README_DEPLOY.md",
    ROOT / "DEPLOY.md",
    ROOT / "deploy.py",
    ROOT / "deploy.sh",
    ROOT / "scripts" / "migrate_to_uuid.py",
    ROOT / "scripts" / "clear_keys.py",
    ROOT / "scripts" / "apply_migrations.py",
]


def test_production_database_secret_is_not_committed():
    leaked_password_fragment = "Does" + "itmatter"
    leaked_project_ref = "qvl" + "qhpgp" + "ghcrie" + "ajxrfv"

    for path in SECRET_SCANNED_FILES:
        text = path.read_text(encoding="utf-8")
        assert leaked_password_fragment not in text
        assert leaked_project_ref not in text
        assert "pooler.supabase.com" not in text


def test_render_database_url_is_dashboard_secret():
    lines = (ROOT / "render.yaml").read_text(encoding="utf-8").splitlines()
    database_key_lines = [
        index for index, line in enumerate(lines) if line.strip() == "- key: DATABASE_URL"
    ]

    assert len(database_key_lines) == 2
    for index in database_key_lines:
        env_var_block = "\n".join(lines[index : index + 3])
        assert "sync: false" in env_var_block
        assert "value:" not in env_var_block


def test_alembic_database_url_is_env_only():
    text = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")

    assert 'os.environ["DATABASE_URL"]' in text
    assert "os.getenv" not in text
    assert "Forcing Tokyo pooler override" not in text


def test_deployment_docs_do_not_generate_fake_production_database_urls():
    forbidden = [
        "DATABASE_URL=postgresql+asyncpg://postgres:password",
        "ALLOWED_ORIGINS=*",
        "Fail-Safe Override",
        'Start Command**: `alembic upgrade head && uvicorn',
        "The Dockerfile runs `alembic upgrade head` automatically on startup.",
    ]

    for path in (
        ROOT / "README_DEPLOY.md",
        ROOT / "DEPLOY.md",
        ROOT / "deploy.py",
        ROOT / "deploy.sh",
    ):
        text = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text


def test_web_runtime_startup_does_not_run_migrations():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert 'releaseCommand: "alembic upgrade head"' in render
    assert 'startCommand: "uvicorn app.main:app --host 0.0.0.0 --port $PORT"' in render
    assert "alembic upgrade head && uvicorn" not in dockerfile
    assert "alembic upgrade head && uvicorn" not in compose
    assert "CMD [\"uvicorn\"" in dockerfile


def test_frontend_workflow_is_preview_first():
    workflow = (ROOT / ".github" / "workflows" / "deploy-frontend.yml").read_text(
        encoding="utf-8"
    )

    assert 'vercel-args: ""' in workflow
    assert "vercel-args: \"--prod\"" not in workflow
