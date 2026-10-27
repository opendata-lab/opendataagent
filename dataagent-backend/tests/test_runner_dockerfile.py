from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "dataagent-backend"
BACKEND_DOCKERFILE = BACKEND_ROOT / "Dockerfile"
RUNNER_DOCKERFILE = BACKEND_ROOT / "Dockerfile.runner"
OLD_SANDBOX_ROOT_ENV = "DATAAGENT_" "SANDBOX_ROOT"
OLD_WORKSPACES_ROOT = "/" "workspaces"


def test_runner_dockerfile_uses_runner_entrypoint():
    content = RUNNER_DOCKERFILE.read_text(encoding="utf-8")

    assert "ENV HOME=/dataagent_runtime" in content
    assert OLD_SANDBOX_ROOT_ENV not in content
    assert "mkdir -p /dataagent_runtime /app/.claude/skills" in content
    assert OLD_WORKSPACES_ROOT not in content
    assert "WORKDIR /opt/dataagent-backend" in content
    assert "COPY dataagent-backend /opt/dataagent-backend" in content
    assert "COPY dataagent/.claude" not in content
    assert "sandbox_runner_main:app" in content
    assert 'EXPOSE 8910' in content
    assert "alembic upgrade head" not in content
    assert '"main:app"' not in content


def test_backend_dockerfile_uses_opt_backend_and_no_bundled_skills():
    content = BACKEND_DOCKERFILE.read_text(encoding="utf-8")

    assert "ENV HOME=/dataagent_runtime" in content
    assert "ENV DATAAGENT_RUNTIME_ROOT=/dataagent_runtime" in content
    assert OLD_SANDBOX_ROOT_ENV not in content
    assert "mkdir -p /dataagent_runtime /app/.claude/skills" in content
    assert OLD_WORKSPACES_ROOT not in content
    assert "WORKDIR /opt/dataagent-backend" in content
    assert "COPY dataagent-backend /opt/dataagent-backend" in content
    assert "COPY dataagent/.claude" not in content
    assert "alembic upgrade head" in content
    assert 'EXPOSE 8900' in content


def test_dockerfiles_support_pip_mirror_build_args():
    python_dockerfiles = [
        BACKEND_DOCKERFILE,
        RUNNER_DOCKERFILE,
        REPO_ROOT / "tools" / "dataagent-evals" / "deepeval" / "Dockerfile",
        REPO_ROOT / "tools" / "dataagent-evals" / "opik" / "Dockerfile",
    ]

    for dockerfile in python_dockerfiles:
        content = dockerfile.read_text(encoding="utf-8")
        assert "ARG PIP_INDEX_URL" in content, f"Missing ARG PIP_INDEX_URL in {dockerfile.name}"
        assert "ARG PIP_TRUSTED_HOST" in content, f"Missing ARG PIP_TRUSTED_HOST in {dockerfile.name}"
        assert 'PIP_INDEX_URL="${PIP_INDEX_URL}"' in content
        assert 'PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST}"' in content
        assert "ENV PIP_INDEX_URL" not in content
        assert "ENV PIP_TRUSTED_HOST" not in content




def test_pip_mirror_configuration_is_not_forwarded_to_runtime():
    sandbox_runner = (BACKEND_ROOT / "sandbox_runner_main.py").read_text(encoding="utf-8")
    requirements = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    compose_files = [
        REPO_ROOT / "deploy" / "docker-compose.yml",
        REPO_ROOT / "deploy" / "docker-compose.yml",
    ]

    assert '"PIP_",' not in sandbox_runner
    assert "mcp[cli]==" not in requirements
    for compose_file in compose_files:
        content = compose_file.read_text(encoding="utf-8")
        assert "PIP_INDEX_URL" not in content
        assert "PIP_TRUSTED_HOST" not in content
