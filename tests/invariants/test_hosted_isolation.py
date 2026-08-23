"""Cross-boundary invariants for the hosted per-matter execution topology."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_driver_mounts_one_matter_and_reaches_network_only_through_proxy() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.worker.yaml").read_text(encoding="utf-8"))
    services = compose["services"]
    driver = services["driver"]
    proxy = services["egress-proxy"]
    converter = services["folio-enrich"]

    string_mounts = [mount for mount in driver["volumes"] if isinstance(mount, str)]
    bind_mounts = [mount for mount in driver["volumes"] if isinstance(mount, dict)]
    mounts = "\n".join(string_mounts)
    assert "/srv/mootloop-matters:/srv/mootloop-matters" not in mounts
    assert bind_mounts == [
        {
            "type": "bind",
            "source": "${MOOTLOOP_MATTER_SOURCE:?validated matter source required}",
            "target": "/srv/mootloop-worker/matter",
            "bind": {"create_host_path": False},
        },
        {
            "type": "bind",
            "source": "${MOOTLOOP_ENGINE_CONFIG_SOURCE:?validated engine config source required}",
            "target": (
                "/var/lib/mootloop-engine-config/"
                "${MOOTLOOP_MATTER_ID:?bound matter required}"
            ),
            "bind": {"create_host_path": False},
        },
    ]
    assert ".canaries.json:/srv/mootloop-worker/.canaries.json:ro" in mounts
    assert driver["environment"]["MOOTLOOP_RUNTIME_MODE"] == "hosted"
    assert driver["environment"]["MOOTLOOP_ENGINE_CONFIG_DIR"] == (
        "/var/lib/mootloop-engine-config"
    )
    assert driver["networks"] == ["driver-egress", "driver-conversion"]
    assert set(proxy["networks"]) == {"driver-egress", "proxy-outbound"}
    assert driver["depends_on"]["egress-proxy"]["condition"] == "service_healthy"
    assert driver["depends_on"]["folio-enrich"]["condition"] == "service_healthy"
    assert proxy["secrets"] == ["egress_proxy_password", "legal_egress_proxy_password"]
    assert "volumes" not in proxy
    assert compose["networks"]["driver-egress"]["internal"] is True
    assert compose["networks"]["driver-conversion"]["internal"] is True
    assert converter["networks"] == ["driver-conversion"]
    assert converter["read_only"] is True
    assert converter["cap_drop"] == ["ALL"]
    assert converter["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in converter and "volumes" not in converter
    assert "proxy-outbound" not in converter["networks"]
    assert converter["image"].startswith("${MOOTLOOP_FOLIO_ENRICH_IMAGE:?")
    converter_env = converter["environment"]
    assert converter_env["FOLIO_ENRICH_EMBEDDING_DISABLED"] == "true"
    assert converter_env["FOLIO_ENRICH_FOLIO_AUTO_UPDATE"] == "false"
    assert converter_env["FOLIO_ENRICH_OLLAMA_AUTO_MANAGE"] == "false"
    assert not any(
        marker in key
        for key in converter_env
        for marker in ("API_KEY", "TOKEN", "PASSWORD", "SECRET")
    )
    dockerfile = (ROOT / "Dockerfile.driver").read_text(encoding="utf-8")
    assert "USER mootloop" in dockerfile
    assert "bubblewrap" not in dockerfile
    assert "cap_add" not in driver and "privileged" not in driver


def test_driver_entrypoint_execs_worker_as_pid_one() -> None:
    dockerfile = (ROOT / "Dockerfile.driver").read_text(encoding="utf-8")

    assert 'CMD ["sh", "-c", "exec mootloop driver serve ' in dockerfile


def test_coolify_compose_excludes_dynamic_worker_mounts() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.matter.yaml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"web", "api"}
    for service in compose["services"].values():
        for mount in service.get("volumes", []):
            source = mount.split(":", maxsplit=1)[0] if isinstance(mount, str) else mount["source"]
            assert "${" not in source


def test_proxy_config_is_destination_allowlisted_and_authenticated() -> None:
    config = (ROOT / "deploy" / "egress" / "squid.conf").read_text(encoding="utf-8")
    lines = [line.strip() for line in config.splitlines() if line.strip()]
    model_host_lines = [line for line in lines if line.startswith("acl model_hosts ")]
    legal_host_lines = [line for line in lines if line.startswith("acl legal_hosts ")]
    assert len(model_host_lines) == len(legal_host_lines) == 1
    [model_hosts] = model_host_lines
    [legal_hosts] = legal_host_lines
    assert model_hosts.split()[3:] == ["api.anthropic.com"]
    assert legal_hosts.split()[3:] == [
        "www.courtlistener.com",
        "api.courtlistener.com",
        "www.revisor.mn.gov",
    ]
    assert lines.count("acl model_identity proxy_auth mootloop-model") == 1
    assert lines.count("acl legal_identity proxy_auth mootloop-legal") == 1
    assert [line for line in lines if line.startswith("http_access ")] == [
        "http_access deny CONNECT !SSL_ports",
        "http_access allow model_identity model_hosts CONNECT SSL_ports",
        "http_access allow legal_identity legal_hosts CONNECT SSL_ports",
        "http_access deny all",
    ]


def test_sse_uses_the_shared_outbound_serializer() -> None:
    source = (ROOT / "src" / "mootloop" / "web" / "api" / "sse.py").read_text(encoding="utf-8")
    assert "serialize_outbound" in source
