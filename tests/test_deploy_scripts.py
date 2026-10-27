"""Exercise the deployment scripts without starting any container.

A fake `docker` on PATH accepts pull/tag/save, so the offline packager runs
end to end and its archive can be inspected and fed to Compose interpolation.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_BIN = REPO_ROOT / "tests" / "fixtures" / "offline-package" / "bin"


def _compose_command():
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    for runtime in ("docker", "podman"):
        if shutil.which(runtime):
            probe = subprocess.run([runtime, "compose", "version"], capture_output=True)
            if probe.returncode == 0:
                return [runtime, "compose"]
    return None


def test_compose_project_does_not_collide_with_other_deploy_directories():
    compose = (REPO_ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    # Both this repo and OpenDataWorks keep compose files under deploy/; the project
    # name must be pinned or the two stacks share volumes such as deploy_mysql-data.
    assert "\nname: opendataagent\n" in compose
    # Offline hosts load images locally, so Compose must not force a registry pull.
    assert "pull_policy" not in compose


def test_offline_package_carries_template_but_not_runtime_env(tmp_path: Path):
    if shutil.which("xz") is None:
        pytest.skip("xz is required to exercise the offline packager")

    output_path = tmp_path / "oda-test.tar.xz"
    env = {**os.environ, "PATH": f"{FAKE_BIN}{os.pathsep}{os.environ['PATH']}", "ODA_XZ_LEVEL": "0"}
    subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "create-offline-package.sh"), "--tag", "env-policy-test",
         "--output", str(output_path)],
        cwd=REPO_ROOT, env=env, check=True, capture_output=True, text=True,
    )

    with tarfile.open(output_path, mode="r:*") as archive:
        names = archive.getnames()
        template = archive.extractfile(next(n for n in names if n.endswith("/deploy/.env.example"))).read().decode()
        compose_member = next(n for n in names if n.endswith("/deploy/docker-compose.yml"))
        compose_bytes = archive.extractfile(compose_member).read()

    paths = [Path(name) for name in names]
    assert not any(path.name == ".env" for path in paths)
    for script in ("start.sh", "stop.sh", "restart.sh", "dump-logs.sh", "load-images.sh",
                   "load-package-and-start.sh", "lib/container-runtime.sh"):
        assert any(name.endswith(f"/scripts/{script}") for name in names), script
    assert not any("/scripts/run-dataagent" in name for name in names)
    for image in ("oda-frontend", "oda-backend", "oda-runner"):
        var = f"ODA_{image.split('-')[1].upper()}_IMAGE"
        assert f"{var}={image}:env-policy-test" in template

    compose = _compose_command()
    if compose:
        config_dir = tmp_path / "packaged"
        config_dir.mkdir()
        (config_dir / ".env").write_text(template, encoding="utf-8")
        (config_dir / "docker-compose.yml").write_bytes(compose_bytes)
        rendered = subprocess.run(
            [*compose, "--env-file", str(config_dir / ".env"), "-f", str(config_dir / "docker-compose.yml"), "config"],
            cwd=config_dir, capture_output=True, text=True, check=True,
        ).stdout
        assert "image: oda-backend:env-policy-test" in rendered
        assert "mikefan2019/oda-" not in rendered


def test_offline_loader_preserves_existing_runtime_env(tmp_path: Path):
    package_dir = tmp_path / "package"
    deploy_dir = package_dir / "deploy"
    (deploy_dir / "docker-images").mkdir(parents=True)
    (package_dir / "scripts").mkdir()
    (package_dir / "scripts" / "load-images.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (deploy_dir / ".env").write_text("KEEP_EXISTING_CONFIG=true\n", encoding="utf-8")
    (deploy_dir / ".env.example").write_text("KEEP_EXISTING_CONFIG=false\n", encoding="utf-8")

    subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "load-package-and-start.sh"), "--package", str(package_dir), "--no-start"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )

    assert (deploy_dir / ".env").read_text(encoding="utf-8") == "KEEP_EXISTING_CONFIG=true\n"


def test_start_exports_an_absolute_runtime_root_for_relative_settings(tmp_path: Path):
    # Copy the scripts and deploy files so the test controls deploy/.env.
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts")
    (root / "deploy").mkdir()
    shutil.copy(REPO_ROOT / "deploy" / "docker-compose.yml", root / "deploy" / "docker-compose.yml")
    (root / "deploy" / ".env").write_text("DATAAGENT_HOST_ROOT=../runtime/./data\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    record = tmp_path / "compose.log"
    # A recording docker-compose stands in for the real CLI; `docker info` must succeed.
    (fake_bin / "docker-compose").write_text(
        f'#!/bin/sh\necho "$DATAAGENT_HOST_ROOT $*" >> "{record}"\n', encoding="utf-8")
    (fake_bin / "docker").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for tool in fake_bin.iterdir():
        tool.chmod(0o755)

    subprocess.run(
        ["bash", str(root / "scripts" / "start.sh")],
        env={**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        check=True, capture_output=True, text=True, timeout=60,
    )

    up_call = next(line for line in record.read_text().splitlines() if " up -d" in line)
    assert up_call.split()[0] == str(root / "runtime" / "data")
    assert (root / "deploy" / "logs").is_dir()


def test_offline_upgrade_takes_image_tags_from_the_package_and_keeps_other_settings(tmp_path: Path):
    package = tmp_path / "oda-deployment"
    deploy_dir = package / "deploy"
    (deploy_dir / "docker-images").mkdir(parents=True)
    (package / "scripts").mkdir()
    (package / "scripts" / "load-images.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (deploy_dir / ".env").write_text("DATAAGENT_MYSQL_PASSWORD=secret\nODA_BACKEND_IMAGE=oda-backend:1.0.0\n", encoding="utf-8")
    (deploy_dir / ".env.example").write_text(
        "DATAAGENT_MYSQL_PASSWORD=dataagent123\nODA_BACKEND_IMAGE=oda-backend:1.1.0\nODA_RUNNER_IMAGE=oda-runner:1.1.0\n",
        encoding="utf-8")
    archive = tmp_path / "upgrade.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(package, arcname="oda-deployment")
    # Extract into the default relative target from a different working directory.
    workdir = tmp_path / "work"
    workdir.mkdir()

    subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "load-package-and-start.sh"), "--package", str(archive), "--no-start"],
        cwd=workdir, check=True, capture_output=True, text=True,
    )

    env = (workdir / "oda-deployment" / "deploy" / ".env").read_text(encoding="utf-8")
    assert env == "DATAAGENT_MYSQL_PASSWORD=secret\nODA_BACKEND_IMAGE=oda-backend:1.1.0\nODA_RUNNER_IMAGE=oda-runner:1.1.0\n"
