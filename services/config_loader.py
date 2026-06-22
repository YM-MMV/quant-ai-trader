"""Configuration loading for quant-ai-trader.

Two kinds of configuration live here:

* **YAML config** (``config/*.yaml``) — non-secret, version-controlled
  defaults: symbols + broker mappings, risk limits, timeframes, app settings.
* **Environment settings** (``.env`` / process env) — secrets and per-machine
  toggles, loaded into ``Settings``. Real secrets never live in YAML or code.

Everything is validated through Pydantic models, so a malformed config fails
fast and loudly rather than producing surprising behaviour at trade time.

No real external service is contacted here (no MT5, OpenBB, Kronos).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from services.models import AssetClass, TradingMode

# Project root = parent of the ``services`` package directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"


# --------------------------------------------------------------------------- #
# YAML config schemas
# --------------------------------------------------------------------------- #
class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SymbolSpec(_Config):
    """One canonical symbol and how it maps to broker-specific names."""

    asset_class: AssetClass
    broker_aliases: list[str] = Field(..., min_length=1)
    digits: int = Field(..., ge=0)
    point: float = Field(..., gt=0)
    enabled: bool = True


class SymbolsConfig(_Config):
    symbols: dict[str, SymbolSpec]
    allowlist: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _allowlist_known(self) -> "SymbolsConfig":
        unknown = [s for s in self.allowlist if s not in self.symbols]
        if unknown:
            raise ValueError(f"allowlist references unknown symbols: {unknown}")
        return self

    def broker_name(self, symbol: str) -> str:
        """Primary broker alias for a canonical symbol."""
        return self.symbols[symbol].broker_aliases[0]


class RiskConfig(_Config):
    """Deterministic risk limits enforced by the RiskManager (see SAFETY.md)."""

    require_stop_loss: bool = True
    require_take_profit: bool = True
    max_daily_loss: float = Field(..., ge=0)
    max_open_trades: int = Field(..., ge=0)
    max_spread_points: int = Field(..., ge=0)
    max_risk_per_trade_pct: float = Field(..., ge=0, le=100)
    max_total_exposure_pct: float = Field(..., ge=0, le=100)
    kill_switch_enabled: bool = True


class TimeframeSpec(_Config):
    minutes: int = Field(..., gt=0)


class TimeframesConfig(_Config):
    default: str
    supported: dict[str, TimeframeSpec]

    @model_validator(mode="after")
    def _default_supported(self) -> "TimeframesConfig":
        if self.default not in self.supported:
            raise ValueError(f"default timeframe {self.default!r} not in supported set")
        return self


class AppPaths(_Config):
    raw: str
    processed: str
    features: str
    predictions: str


class AppConfig(_Config):
    app_name: str
    environment: str = "development"
    default_mode: TradingMode = TradingMode.PAPER
    data_dir: str = "data"
    log_level: str = "INFO"
    paths: AppPaths


# --------------------------------------------------------------------------- #
# Environment settings (.env)
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """Per-machine settings and secrets loaded from environment / ``.env``.

    Placeholders are documented in ``.env.example``. Live trading stays off
    unless a human explicitly flips both ``TRADING_MODE=live`` and
    ``ALLOW_LIVE_TRADING=true`` — and the validator below refuses any other
    combination so the safety lock cannot be half-enabled by accident.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    trading_mode: TradingMode = Field(TradingMode.PAPER, alias="TRADING_MODE")
    allow_live_trading: bool = Field(False, alias="ALLOW_LIVE_TRADING")

    max_daily_loss: float = Field(0.0, alias="MAX_DAILY_LOSS", ge=0)
    max_open_trades: int = Field(0, alias="MAX_OPEN_TRADES", ge=0)
    max_spread_points: int = Field(0, alias="MAX_SPREAD_POINTS", ge=0)

    symbol_allowlist_raw: str = Field("", alias="SYMBOL_ALLOWLIST")

    mt5_login: Optional[str] = Field(None, alias="MT5_LOGIN")
    mt5_password: Optional[str] = Field(None, alias="MT5_PASSWORD")
    mt5_server: Optional[str] = Field(None, alias="MT5_SERVER")
    mt5_terminal_path: Optional[str] = Field(None, alias="MT5_TERMINAL_PATH")

    openbb_pat: Optional[str] = Field(None, alias="OPENBB_PAT")

    @field_validator("symbol_allowlist_raw", mode="before")
    @classmethod
    def _coerce_none(cls, value: object) -> str:
        return "" if value is None else str(value)

    @property
    def symbol_allowlist(self) -> list[str]:
        """Allowlist parsed from the comma-separated ``SYMBOL_ALLOWLIST``."""
        return [s.strip() for s in self.symbol_allowlist_raw.split(",") if s.strip()]

    @model_validator(mode="after")
    def _enforce_live_lock(self) -> "Settings":
        if self.trading_mode is TradingMode.LIVE and not self.allow_live_trading:
            raise ValueError(
                "TRADING_MODE=live requires ALLOW_LIVE_TRADING=true (see SAFETY.md)"
            )
        return self


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def load_yaml(path: Path) -> dict:
    """Read a YAML file into a dict, with clear errors."""
    if not path.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"expected a mapping at top level of {path}, got {type(data)}")
    return data


def load_symbols_config(config_dir: Path = CONFIG_DIR) -> SymbolsConfig:
    return SymbolsConfig.model_validate(load_yaml(config_dir / "symbols.yaml"))


def load_risk_config(config_dir: Path = CONFIG_DIR) -> RiskConfig:
    return RiskConfig.model_validate(load_yaml(config_dir / "risk.yaml"))


def load_timeframes_config(config_dir: Path = CONFIG_DIR) -> TimeframesConfig:
    return TimeframesConfig.model_validate(load_yaml(config_dir / "timeframes.yaml"))


def load_app_config(config_dir: Path = CONFIG_DIR) -> AppConfig:
    return AppConfig.model_validate(load_yaml(config_dir / "app.yaml"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached process settings loaded from the environment / ``.env``."""
    return Settings()
