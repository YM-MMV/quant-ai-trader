"""Functional tests: each research adapter computes a sensible output from inputs.

Uses deterministic sample candles / hand-built series — no network, no MT5.
"""
import numpy as np

from services.data_service.sample_data import generate_candles
from services.strategy_service.research_adapters import (
    MonteCarloProjectAdapter,
    OilMoneyProjectAdapter,
    OptionsStraddleAdapter,
    OreMoneyProjectAdapter,
    PairTradingAdapter,
    PortfolioOptimizationAdapter,
    SmartFarmersProjectAdapter,
    VIXCalculatorAdapter,
    WisdomOfCrowdAdapter,
)
from services.strategy_service.research_adapters.base import OutputType


# --------------------------------------------------------------------------- #
# Pair Trading
# --------------------------------------------------------------------------- #
def test_pair_trading_flags_stretched_spread():
    rng = np.random.default_rng(0)
    a = 100 + np.cumsum(rng.normal(0, 1, 40))
    b = a + rng.normal(0, 0.1, 40)
    a = list(a)
    b = list(b)
    a[-1] += 1.5  # spread outlier on a tight spread → large z
    out = PairTradingAdapter().run(prices_a=a, prices_b=b)
    assert out.output_type is OutputType.SIGNAL
    assert out.data["cointegrated"] is True
    assert abs(out.data["spread_zscore"]) >= 2.0
    assert out.data["leg_a_side"] in {"buy", "sell"}
    assert out.is_executable() is False  # adaptable, never DIRECT


def test_pair_trading_requirements_when_missing():
    out = PairTradingAdapter().run()
    assert out.data_available is False
    assert out.output_type is OutputType.REPORT


# --------------------------------------------------------------------------- #
# Options Straddle
# --------------------------------------------------------------------------- #
def test_options_straddle_breakevens():
    out = OptionsStraddleAdapter().run(strike=100.0, call_premium=2.0, put_premium=3.0)
    assert out.data["upper_breakeven"] == 105.0
    assert out.data["lower_breakeven"] == 95.0
    assert out.data["total_premium"] == 5.0


def test_options_straddle_requirements_when_missing():
    out = OptionsStraddleAdapter().run()
    assert out.data_available is False


# --------------------------------------------------------------------------- #
# VIX Calculator
# --------------------------------------------------------------------------- #
def test_vix_realized_vol_proxy():
    candles = generate_candles("EURUSD", "H1", n=120)
    out = VIXCalculatorAdapter().run(candles=candles)
    assert out.output_type is OutputType.FEATURE
    assert out.data["realized_vol_annualized"] > 0
    assert out.data["is_proxy"] is True


# --------------------------------------------------------------------------- #
# Monte Carlo
# --------------------------------------------------------------------------- #
def test_monte_carlo_var_and_determinism():
    candles = generate_candles("XAUUSD", "H1", n=200)
    a = MonteCarloProjectAdapter(n_paths=2000)
    out1 = a.run(candles=candles)
    out2 = a.run(candles=candles)
    assert out1.output_type is OutputType.RISK_CONTEXT
    assert out1.data["var_95"] >= 0
    assert 0.0 <= out1.data["prob_loss"] <= 1.0
    assert out1.data == out2.data  # seeded → deterministic


# --------------------------------------------------------------------------- #
# Oil / Ore money
# --------------------------------------------------------------------------- #
def test_oil_money_regression():
    fx = generate_candles("USDCAD", "H1", n=80, seed=1)
    oil = generate_candles("WTIUSD", "H1", n=80, seed=2)
    out = OilMoneyProjectAdapter().run(fx_candles=fx, oil_prices=oil)
    assert out.output_type is OutputType.REPORT
    assert "oil_beta" in out.data and "correlation" in out.data


def test_ore_money_regression():
    fx = generate_candles("AUDUSD", "H1", n=80, seed=1)
    ore = generate_candles("IRONUSD", "H1", n=80, seed=3)
    out = OreMoneyProjectAdapter().run(fx_candles=fx, ore_prices=ore)
    assert "ore_beta" in out.data and "correlation" in out.data


# --------------------------------------------------------------------------- #
# Smart Farmers
# --------------------------------------------------------------------------- #
def test_smart_farmers_momentum_ranking():
    wheat = list(np.linspace(100, 130, 30))   # strong uptrend
    corn = list(np.linspace(100, 95, 30))     # mild downtrend
    out = SmartFarmersProjectAdapter().run(
        commodity_prices={"wheat": wheat, "corn": corn}
    )
    ranked = out.data["trailing_return"]
    assert list(ranked)[0] == "wheat"  # best performer ranked first


# --------------------------------------------------------------------------- #
# Portfolio Optimization
# --------------------------------------------------------------------------- #
def test_portfolio_inverse_variance_weights_sum_to_one():
    calm = generate_candles("EURUSD", "H1", n=120, seed=1)
    wild = generate_candles("BTCUSD", "H1", n=120, seed=2, start_price=30000.0)
    out = PortfolioOptimizationAdapter().run(assets={"EURUSD": calm, "BTCUSD": wild})
    weights = out.data["weights"]
    assert out.output_type is OutputType.RANKING
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert all(w >= 0 for w in weights.values())  # long-only allocation


def test_portfolio_requires_two_assets():
    one = generate_candles("EURUSD", "H1", n=120)
    out = PortfolioOptimizationAdapter().run(assets={"EURUSD": one})
    assert out.data_available is False


# --------------------------------------------------------------------------- #
# Wisdom of Crowd
# --------------------------------------------------------------------------- #
def test_wisdom_of_crowd_consensus_ranking():
    out = WisdomOfCrowdAdapter().run(
        forecasts={"EURUSD": [0.8, 0.6, 0.7], "GBPUSD": [-0.2, 0.1, -0.1]}
    )
    consensus = out.data["consensus"]
    assert out.output_type is OutputType.RANKING
    assert list(consensus)[0] == "EURUSD"  # strongest bullish consensus first


def test_wisdom_requirements_when_missing():
    out = WisdomOfCrowdAdapter().run()
    assert out.data_available is False
