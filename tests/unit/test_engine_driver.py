"""Hosted driver binding and host-side provisioning contracts."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mootloop.conversion import FOLIO_ENRICH_COMMIT
from mootloop.engine import driver
from mootloop.errors import ConversionError, DriverError, VaultBoundaryError
from mootloop.registry import MatterRegistry
from mootloop.runtime import RuntimeMode
from tests.conftest import make_matter

IMAGE = "ghcr.io/alea-institute/folio-enrich@sha256:" + "a" * 64


def _hosted_matter(tmp_path: Path, matter_id: str = "2026-08-21-acme-test") -> tuple[Path, Path]:
    root = tmp_path / "matters"
    vault = MatterRegistry(root=root).create(make_matter(matter_id))
    return root, vault


def _proxy_files(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "proxy-password"
    legal = tmp_path / "legal-proxy-password"
    model.write_text("model-proxy-secret\n", encoding="utf-8")
    legal.write_text("legal-proxy-secret\n", encoding="utf-8")
    model.chmod(0o600)
    legal.chmod(0o600)
    return model, legal


def _proxy_secret(key: str) -> str:
    return "legal-proxy-secret" if "LEGAL" in key else "model-proxy-secret"


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
    password, legal_password = _proxy_files(tmp_path)
    monkeypatch.setattr(driver.secrets, "load_secret", _proxy_secret)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    driver.start_matter_worker(
        root,
        "2026-08-21-acme-test",
        compose_file=compose,
        proxy_password_file=password,
        legal_proxy_password_file=legal_password,
        engine_config_root=engine_config_root,
        folio_enrich_image=IMAGE,
        folio_enrich_commit=FOLIO_ENRICH_COMMIT,
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
    assert environment["MOOTLOOP_LEGAL_EGRESS_PROXY_PASSWORD_FILE"] == str(
        legal_password.resolve()
    )
    assert environment["MOOTLOOP_FOLIO_ENRICH_IMAGE"] == IMAGE
    assert environment["MOOTLOOP_FOLIO_ENRICH_COMMIT"] == FOLIO_ENRICH_COMMIT
    assert command[-5:] == ["up", "-d", "driver", "egress-proxy", "folio-enrich"]
    assert kwargs["check"] is True and kwargs["text"] is True
    assert (engine_config_root / "2026-08-21-acme-test").stat().st_mode & 0o077 == 0


def test_start_matter_worker_reuses_durable_per_matter_engine_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _vault = _hosted_matter(tmp_path)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    password, legal_password = _proxy_files(tmp_path)
    engine_config_root = tmp_path / "engine-config"
    engine_config_root.mkdir(mode=0o700)
    monkeypatch.setattr(driver.secrets, "load_secret", _proxy_secret)
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
            legal_proxy_password_file=legal_password,
            engine_config_root=engine_config_root,
            folio_enrich_image=IMAGE,
            folio_enrich_commit=FOLIO_ENRICH_COMMIT,
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
    monkeypatch.setattr(driver.secrets, "load_secret", _proxy_secret)
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
            legal_proxy_password_file=tmp_path / "legal-proxy-password",
            engine_config_root=tmp_path / "engine-config",
            folio_enrich_image=IMAGE,
            folio_enrich_commit=FOLIO_ENRICH_COMMIT,
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
            legal_proxy_password_file=tmp_path / "legal-proxy-password",
            engine_config_root=tmp_path / "engine-config",
            folio_enrich_image=IMAGE,
            folio_enrich_commit=FOLIO_ENRICH_COMMIT,
        )


def test_start_matter_worker_rejects_shared_model_and_legal_proxy_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _vault = _hosted_matter(tmp_path)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    model = tmp_path / "model-password"
    legal = tmp_path / "legal-password"
    model.write_text("shared-secret\n", encoding="utf-8")
    legal.write_text("shared-secret\n", encoding="utf-8")
    model.chmod(0o600)
    legal.chmod(0o600)
    monkeypatch.setattr(driver.secrets, "load_secret", lambda key: "shared-secret")

    with pytest.raises(DriverError, match="must differ"):
        driver.start_matter_worker(
            root,
            "2026-08-21-acme-test",
            compose_file=compose,
            proxy_password_file=model,
            legal_proxy_password_file=legal,
            engine_config_root=tmp_path / "engine-config",
            folio_enrich_image=IMAGE,
            folio_enrich_commit=FOLIO_ENRICH_COMMIT,
        )


@pytest.mark.parametrize(
    ("image", "commit"),
    [
        ("ghcr.io/alea-institute/folio-enrich:latest", FOLIO_ENRICH_COMMIT),
        (IMAGE, "0" * 40),
    ],
)
def test_start_matter_worker_rejects_unreviewed_converter_before_runner(
    tmp_path: Path,
    image: str,
    commit: str,
) -> None:
    called = False

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(ConversionError):
        driver.start_matter_worker(
            tmp_path / "matters",
            "2026-08-21-acme-test",
            compose_file=tmp_path / "compose.yaml",
            proxy_password_file=tmp_path / "password",
            legal_proxy_password_file=tmp_path / "legal-password",
            engine_config_root=tmp_path / "engine-config",
            folio_enrich_image=image,
            folio_enrich_commit=commit,
            runner=runner,
        )
    assert called is False
