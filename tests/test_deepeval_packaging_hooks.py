from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_eval_images_are_built_by_independent_workflow():
    workflow = (REPO_ROOT / ".github/workflows/docker-build.yml").read_text()
    for kind in ("builtin", "deepeval"):
        assert f"oda-evals-{kind}" in workflow
        assert (REPO_ROOT / f"tools/dataagent-evals/{kind}/Dockerfile").is_file()


def test_compose_binds_skills_and_owns_session_database():
    compose = (REPO_ROOT / "deploy/docker-compose.yml").read_text()
    assert "SKILLS_ROOT_DIR: /app/.claude/skills" in compose
    assert "SESSION_MYSQL_DATABASE" in compose


def test_deepeval_eval_wrapper_keeps_volume_array_non_empty_for_bash_3():
    run_wrapper = (REPO_ROOT / "scripts" / "run-dataagent-deepeval-evals.sh").read_text(encoding="utf-8")

    assert "VOLUMES=(-v \"$REPO_ROOT:/workspace\")" in run_wrapper
    assert "EXTRA_VOLUMES=()" not in run_wrapper
    assert "\"${VOLUMES[@]}\"" in run_wrapper
