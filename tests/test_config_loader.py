"""Tests for config loading (services/config_loader.py).

Exercises the real ``config/*.yaml`` files plus the env-driven Settings,
including the SAFETY.md live-trading lock. No real services are contacted.
"""
import pytest
from pydantic import ValidationError

from services.config_loader import (
    CONFIG_DIR,
    Settings,
    load_app_config,
    load_risk_config,
    load_symbols_config,
    load_timeframes_config,
    load_yaml,
)
from services.models import TradingMode


# --------------------------------------------------------------------------- #
# YAML configs (real files in config/)
# --------------------------------------------------------------------------- #
def test_symbols_config_loads():
    cfg = load_symbols_config()
    assert "EURUSD" in cfg.symbols
    assert cfg.symbols["EURUSD"].digits == 5
    # Broker-name mapping returns the primary alias.
    assert cfg.broker_name("EURUSD") == "EURUSD"
    # Allowlist is a subset of declared symbols.
    assert set(cfg.allowlist).issubset(set(cfg.symbols))


def test_symbols_allowlist_must_be_known():
    data = {
        "symbols": {
            "EURUSD": {
                "asset_class": "forex",
                "broker_aliases": ["EURUSD"],
                "digits": 5,
                "point": 0.00001,
            }
        },
        "allowlist": ["GBPUSD"],  # not declared
    }
    from services.config_loader import SymbolsConfig

    with pytest.raises(ValidationError):
        SymbolsConfig.model_validate(data)


def test_risk_config_loads():
    cfg = load_risk_config()
    assert cfg.require_stop_loss is True
    assert cfg.require_take_profit is True
    assert cfg.max_open_trades >= 0
    assert cfg.kill_switch_enabled is True


def test_timeframes_config_loads():
    cfg = load_timeframes_config()
    assert cfg.default in cfg.supported
    assert cfg.supported["H1"].minutes == 60


def test_app_config_loads():
    cfg = load_app_config()
    assert cfg.app_name == "quant-ai-trader"
    assert cfg.default_mode is TradingMode.PAPER
    assert cfg.paths.raw == "data/raw"


def test_all_expected_config_files_present():
    for name in ("symbols.yaml", "risk.yaml", "timeframes.yaml", "app.yaml"):
        assert (CONFIG_DIR / name).is_file(), f"missing config: {name}"


def test_load_yaml_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_yaml(tmp_path / "nope.yaml")


def test_load_yaml_non_mapping_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_yaml(bad)


def test_loaders_accept_custom_dir(tmp_path):
    # A minimal valid risk.yaml in a custom directory loads correctly.
    (tmp_path / "risk.yaml").write_text(
        "require_stop_loss: true\n"
        "require_take_profit: true\n"
        "max_daily_loss: 50\n"
        "max_open_trades: 2\n"
        "max_spread_points: 20\n"
        "max_risk_per_trade_pct: 0.5\n"
        "max_total_exposure_pct: 2.0\n"
        "kill_switch_enabled: true\n",
        encoding="utf-8",
    )
    cfg = load_risk_config(config_dir=tmp_path)
    assert cfg.max_daily_loss == 50
    assert cfg.max_open_trades == 2


# --------------------------------------------------------------------------- #
# Settings (.env / environment)
# --------------------------------------------------------------------------- #
def _settings(env: dict[str, str]) -> Settings:
    # Disable .env file reading so tests are hermetic; feed values directly.
    return Settings(_env_file=None, **env)


def test_settings_defaults_are_safe():
    s = _settings({})
    assert s.trading_mode is TradingMode.PAPER
    assert s.allow_live_trading is False


def test_settings_allowlist_parsing():
    s = _settings({"SYMBOL_ALLOWLIST": "EURUSD, GBPUSD , XAUUSD"})
    assert s.symbol_allowlist == ["EURUSD", "GBPUSD", "XAUUSD"]


def test_settings_empty_allowlist():
    s = _settings({"SYMBOL_ALLOWLIST": ""})
    assert s.symbol_allowlist == []


def test_settings_live_without_allow_is_rejected():
    with pytest.raises(ValidationError):
        _settings({"TRADING_MODE": "live", "ALLOW_LIVE_TRADING": "false"})


def test_settings_live_with_allow_is_permitted():
    s = _settings({"TRADING_MODE": "live", "ALLOW_LIVE_TRADING": "true"})
    assert s.trading_mode is TradingMode.LIVE
    assert s.allow_live_trading is True


def test_settings_paper_mode_ignores_allow_flag():
    # paper mode is fine regardless of the live flag value.
    s = _settings({"TRADING_MODE": "paper", "ALLOW_LIVE_TRADING": "true"})
    assert s.trading_mode is TradingMode.PAPER
