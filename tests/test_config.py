"""配置中心测试：默认值 / YAML / 环境变量三级优先级。"""

from pathlib import Path

import pytest

from app.config import Settings, get_server_configs


def test_defaults_when_yaml_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """YAML 不存在时回落到代码默认值。"""
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(tmp_path / "not-exist.yaml"))
    settings = Settings()
    assert settings.app_name == "mcp-gateway"
    assert settings.port == 8000
    assert settings.log_level == "INFO"


def test_yaml_config_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """YAML 中的 gateway 节与 servers 声明被正确读取。"""
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text(
        "gateway:\n"
        "  port: 1234\n"
        "servers:\n"
        "  - name: demo_sql\n"
        "    transport: stdio\n"
        "    command: uv\n"
        "    args: ['run', 'server.py']\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(cfg))

    assert Settings().port == 1234

    servers = get_server_configs()
    assert len(servers) == 1
    assert servers[0].name == "demo_sql"
    assert servers[0].transport == "stdio"
    assert servers[0].namespace == "demo_sql"


def test_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量优先级高于 YAML。"""
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text("gateway:\n  port: 1234\n", encoding="utf-8")
    monkeypatch.setenv("GATEWAY_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("GATEWAY_PORT", "9000")

    assert Settings().port == 9000
