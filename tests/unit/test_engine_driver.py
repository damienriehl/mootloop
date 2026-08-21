"""Hosted driver binding and host-side provisioning contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mootloop.engine import driver
from mootloop.errors import DriverError, VaultBoundaryError
from mootloop.registry import MatterRegistry
from mootloop.runtime import RuntimeMode
from tests.conftest import make_matter


def _hosted_matter(tmp_path: Path, matter_id: str = "2026-08-21-acme-test") -> tuple[Path, Path]:
    root = tmp_path / "matters"
    vault = MatterRegistry(root=root).create(make_matter(matter_id))
    return root, vault


def test_hosted_binding_uses_one_exact_preflighted_vault(tmp_path: Path) -> None:
    root, vault = _hosted_matter(tmp_path)

    binding = driver.resolve_driver_binding(
        root,
        RuntimeMode.HOSTED,
        "2026-08-21-acme-test",
        matter_vault=vault,
    )

    assert binding.matter_id == "2026-08-21-acme-test"
    assert binding.vault == vault.resolve()


def test_hosted_binding_rejects_mounted_matter_identity_mismatch(tmp_path: Path) -> None:
    root, vault = _hosted_matter(tmp_path)

    with pytest.raises(VaultBoundaryError, match="identity"):
        driver.resolve_driver_binding(
            root,
            RuntimeMode.HOSTED,
            "2026-08-21-other-test",
            matter_vault=vault,
        )


def test_start_matter_worker_validates_then_uses_fixed_mount_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, vault = _hosted_matter(tmp_path)
    engine_config_root = tmp_path / "engine-config"
    engine_config_root.mkdir(mode=0o700)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    password = tmp_path / "proxy-password"
    password.write_text("shared-proxy-secret\n", encoding="utf-8")
    password.chmod(0o600)
    monkeypatch.setattr(driver.secrets, "load_secret", lambda key: "shared-proxy-secret")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    driver.start_matter_worker(
        root,
        "2026-08-21-acme-test",
        compose_file=compose,
        proxy_password_file=password,
        engine_config_root=engine_config_root,
        runner=runner,
    )

    [(command, kwargs)] = calls
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["MOOTLOOP_MATTER_SOURCE"] == str(vault.resolve())
    assert environment["MOOTLOOP_ENGINE_CONFIG_SOURCE"] == str(
        engine_config_root / "2026-08-21-acme-test"
    )
    assert environment["MOOTLOOP_MATTER_ID"] == "2026-08-21-acme-test"
    assert environment["MOOTLOOP_EGRESS_PROXY_PASSWORD_FILE"] == str(password.resolve())
    assert command[-4:] == ["up", "-d", "driver", "egress-proxy"]
    assert kwargs["check"] is True and kwargs["text"] is True
    assert (engine_config_root / "2026-08-21-acme-test").stat().st_mode & 0o077 == 0


def test_start_matter_worker_reuses_durable_per_matter_engine_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _vault = _hosted_matter(tmp_path)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    password = tmp_path / "proxy-password"
    password.write_text("shared-proxy-secret\n", encoding="utf-8")
    password.chmod(0o600)
    engine_config_root = tmp_path / "engine-config"
    engine_config_root.mkdir(mode=0o700)
    monkeypatch.setattr(driver.secrets, "load_secret", lambda key: "shared-proxy-secret")
    sources: list[str] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        sources.append(str(environment["MOOTLOOP_ENGINE_CONFIG_SOURCE"]))
        return subprocess.CompletedProcess(command, 0, "", "")

    for _ in range(2):
        driver.start_matter_worker(
            root,
            "2026-08-21-acme-test",
            compose_file=compose,
            proxy_password_file=password,
            engine_config_root=engine_config_root,
            runner=runner,
        )

    expected = engine_config_root / "2026-08-21-acme-test"
    assert sources == [str(expected), str(expected)]
    assert expected.is_dir()


def test_start_matter_worker_rejects_invalid_id_before_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "matters"
    root.mkdir()
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    password = tmp_path / "proxy-password"
    password.write_text("shared-proxy-secret\n", encoding="utf-8")
    password.chmod(0o600)
    monkeypatch.setattr(driver.secrets, "load_secret", lambda key: "shared-proxy-secret")
    called = False

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(VaultBoundaryError):
        driver.start_matter_worker(
            root,
            "../sibling",
            compose_file=compose,
            proxy_password_file=password,
            engine_config_root=tmp_path / "engine-config",
            runner=runner,
        )
    assert called is False


@pytest.mark.parametrize("mode", [0o644, 0o600])
def test_start_matter_worker_rejects_unsafe_or_mismatched_proxy_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    root, _vault = _hosted_matter(tmp_path)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    password = tmp_path / "proxy-password"
    password.write_text("file-secret\n", encoding="utf-8")
    password.chmod(mode)
    monkeypatch.setattr(driver.secrets, "load_secret", lambda key: "different-secret")

    with pytest.raises(DriverError, match="accessible|does not match"):
        driver.start_matter_worker(
            root,
            "2026-08-21-acme-test",
            compose_file=compose,
            proxy_password_file=password,
            engine_config_root=tmp_path / "engine-config",
        )
