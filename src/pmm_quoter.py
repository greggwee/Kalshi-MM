"""Runs the Feil-Nendel model (src/pmm.py) against one live or replayed market.

Holds the market's estimators and its solved value surface. Quoting is a table
lookup plus one maximization, cheap enough for every cycle. Solving takes a second
or so, so it happens only when the estimates have moved or the surface has aged.

Parameters come in two layers. A series' calibration (pmm_tool.py calibrate) sets
the shape over time to close; what the market itself is doing rescales the levels.
A series with no calibration starts from flat priors and takes its k from the
quoted spread, on the reasoning that makers at an exponential-intensity optimum
sit 1/k from mid.

The clock is time to the stand-down, not to the close. The bot stops quoting
stand_down_h before close and holds what it has to settlement, which is exactly
the q^2 p(1-p) exposure the terminal penalty prices, so that moment is the model's T.
"""
import json
import logging
import math
import os
import time
from typing import Iterable, Optional

from src.pmm import ModelParams, solve
from src.pmm_calibrate import FlowEstimator, K_MAX, K_MIN, LogitVol

DEFAULT_PMM = {
    # risk preference in 1/$: the cost charged per $^2 of variance while holding
    # inventory (gamma) and of settlement variance left at stand-down (gamma_T)
    "gamma": 0.2,
    "gamma_T": 0.05,
    # priors for a series with no calibration: flat over time, hours as the unit
    "T": 48.0, "sigma0": 0.15, "sigma1": 0.0, "sigma2": 0.0, "eta": 3.0,
    "A0": 2.0, "A1": 0.0, "xi": 2.0, "nu": 1.0, "k0": 80.0, "k1": 80.0, "kappa": 1.5,
    "min_offset": 0.005,            # no closer to mid than half a tick
    "queue_aware": True,            # choose the price by real queue position (not in the paper)
    "calibration_file": None,       # from pmm_tool.py calibrate
    "flow_window_h": 2.0,
    "vol_sample_s": 120.0,
    "resolve_every_s": 3600.0,      # re-solve at least this often
    "resolve_min_s": 300.0,         # and on drift, but no more often than this
    "resolve_change": 0.3,          # relative move in an estimate that counts as drift
    "scale_limit": 8.0,             # how far live estimates may move the priors
    "n_p": 60,
    "growth": 1.1,
}
MODEL_KEYS = ("T", "sigma0", "sigma1", "sigma2", "eta", "A0", "A1", "xi", "nu", "k0", "k1",
              "kappa")
TICK = 0.01
KALSHI_MAKER_FEE = 0.25 * 0.07    # maker share of the quadratic fee, per p(1-p) per contract


def maker_fee_rate(fee_type: Optional[str], fee_multiplier: Optional[float]) -> float:
    """The c in fee = c * price * (1 - price), zero on series without maker fees."""
    return KALSHI_MAKER_FEE * (fee_multiplier or 1.0) if "maker" in (fee_type or "") else 0.0


def load_calibration(path: Optional[str]) -> dict:
    """A configured file that is missing is an error, not an empty calibration: the
    bot would otherwise start on flat priors and a shorter stand-down without saying so."""
    if not path:
        return {}
    if not os.path.exists(path):
        raise ValueError(f"calibration file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class PMMQuoter:
    def __init__(self, ticker: str, delta: float, Q: float, cfg: Optional[dict] = None,
                 stand_down_h: float = 0.0, maker_fee: float = 0.0,
                 logger: Optional[logging.Logger] = None, on_solve=None):
        self.ticker = ticker
        self.cfg = {**DEFAULT_PMM, **(cfg or {})}
        self.logger = logger or logging.getLogger(__name__)
        self.on_solve = on_solve
        self.delta = float(delta)
        self.Q = float(Q)
        self.maker_fee = maker_fee
        self.series = ticker.split("-")[0]

        calibration = load_calibration(self.cfg["calibration_file"])
        fitted = (calibration.get("series") or {}).get(self.series)
        self.calibrated = fitted is not None
        self.prior_values = {k: self.cfg[k] for k in MODEL_KEYS}
        if fitted:
            self.prior_values.update({k: fitted["params"][k] for k in MODEL_KEYS})
        self.stand_down_h = max(stand_down_h, float(calibration.get("stand_down_h") or 0.0)
                                if fitted else stand_down_h)
        # raises on a size and limit the inventory grid cannot represent
        self.prior = self._params(1.0, 1.0, 1.0)

        self.vol = LogitVol(sample_s=self.cfg["vol_sample_s"])
        self.flow = FlowEstimator(window_h=self.cfg["flow_window_h"])
        self.half_spread = 0.005
        self.surface = None
        self.solved_at: Optional[float] = None
        self.solved_scales = None
        self.solves = 0
        self.last_solve_ms = 0.0

    def _params(self, s_sigma: float, s_a: float, s_k: float) -> ModelParams:
        v = self.prior_values
        return ModelParams(
            T=v["T"], sigma0=v["sigma0"] * s_sigma, sigma1=v["sigma1"] * s_sigma,
            sigma2=v["sigma2"] * s_sigma, eta=v["eta"], A0=v["A0"] * s_a, A1=v["A1"] * s_a,
            xi=v["xi"], nu=v["nu"], k0=min(max(v["k0"] * s_k, K_MIN), K_MAX),
            k1=min(max(v["k1"] * s_k, K_MIN), K_MAX), kappa=v["kappa"],
            gamma=self.cfg["gamma"], gamma_T=self.cfg["gamma_T"], Q=self.Q, delta=self.delta,
            maker_fee=self.maker_fee, min_offset=self.cfg["min_offset"])

    def set_maker_fee(self, rate: float):
        self.maker_fee = rate
        self.prior = self._params(1.0, 1.0, 1.0)

    def describe(self) -> dict:
        return {"calibrated": self.calibrated, "stand_down_h": self.stand_down_h,
                "maker_fee": self.maker_fee, "gamma": self.cfg["gamma"],
                "gamma_T": self.cfg["gamma_T"], "min_offset": self.cfg["min_offset"],
                "prior": dict(self.prior_values)}

    # ------------------------------------------------------------------
    # market data
    # ------------------------------------------------------------------

    def seed_tape(self, trades: Iterable[dict], since: float):
        self.flow.seed(trades, since)

    def observe_book(self, now: float, mid: float, best_bid: Optional[float],
                     best_ask: Optional[float], bid_depth: Optional[float],
                     ask_depth: Optional[float]):
        if best_bid is not None and best_ask is not None:
            self.half_spread = max(0.5 * (best_ask - best_bid), 0.005)
        self.vol.add(now, mid)
        self.flow.observe_book(now, mid, self.half_spread, bid_depth, ask_depth)

    def observe_trade(self, ts: float, price: Optional[float], qty: float,
                      taker_side: Optional[str]):
        if price is not None:
            self.flow.add_trade(ts, price, qty, taker_side)

    # ------------------------------------------------------------------
    # model
    # ------------------------------------------------------------------

    def tau(self, tth: float) -> float:
        return max(tth - self.stand_down_h, 0.0)

    def _scales(self, now: float, tau: float, p: float):
        """How far this market currently sits from its priors."""
        prior = self.prior
        k_prior = float(prior.k(tau))
        k_anchor = k_prior if self.calibrated else min(max(1.0 / self.half_spread, K_MIN), K_MAX)
        k_hat = self.flow.k(now, k_anchor)
        rate_prior = float(prior.activity(tau, p)) * math.exp(-k_hat * self.half_spread)
        rate_hat = self.flow.fill_rate(now, rate_prior, self.delta)
        sigma_prior = float(prior.sigma_tilde(tau, p))
        limit = self.cfg["scale_limit"]

        def bounded(x):
            return min(max(x, 1.0 / limit), limit)
        return (bounded(self.vol.sigma(sigma_prior) / sigma_prior),
                bounded(rate_hat / rate_prior), bounded(k_hat / k_prior))

    def _needs_solve(self, now: float, tau: float, scales) -> bool:
        if self.surface is None or tau > self.surface.stats["horizon"]:
            return True
        age = now - self.solved_at
        if age >= self.cfg["resolve_every_s"]:
            return True
        if age < self.cfg["resolve_min_s"]:
            return False
        limit = math.log(1.0 + self.cfg["resolve_change"])
        return any(abs(math.log(a / b)) > limit for a, b in zip(scales, self.solved_scales))

    def _solve(self, now: float, tau: float, scales):
        t0 = time.perf_counter()
        params = self._params(*scales)
        self.surface = solve(params, horizon=tau + 1.0, n_p=self.cfg["n_p"],
                             growth=self.cfg["growth"])
        self.last_solve_ms = (time.perf_counter() - t0) * 1000.0
        self.solved_at = now
        self.solved_scales = scales
        self.solves += 1
        if self.on_solve:
            self.on_solve({"ticker": self.ticker, "tau": round(tau, 3),
                           "scales": [round(x, 3) for x in scales],
                           "ms": round(self.last_solve_ms, 1), **self.surface.stats})

    def quote(self, now: float, mid: float, q: float, tth: float, allow_solve: bool = True,
              ladders: Optional[tuple] = None) -> Optional[dict]:
        """Tick-rounded best quotes, or None if no surface could be solved. A
        missing first surface is always solved; allow_solve only defers re-solves.
        ladders is (yes bids, no bids) as price -> contracts, without our own orders."""
        tau = self.tau(tth)
        scales = self._scales(now, tau, mid)
        solved = False
        if self._needs_solve(now, tau, scales) and (allow_solve or self.surface is None):
            try:
                self._solve(now, tau, scales)
                solved = True
            except (RuntimeError, ValueError) as e:
                self.logger.warning(f"{self.ticker}: model solve failed: {e}")
                if self.surface is None:
                    return None
        if self.cfg["queue_aware"] and ladders is not None:
            out = self.surface.queue_quotes(tau, mid, q, ladders[0], ladders[1], tick=TICK)
        else:
            out = self.surface.tick_quotes(tau, mid, q, tick=TICK)
            out["model_bid"], out["model_ask"] = out["bid"], out["ask"]
        params = self.surface.params
        z_b, z_a = out["z_b"], out["z_a"]
        if z_b is not None and z_a is not None:
            indifference = mid - 0.5 * (z_b - z_a)
        elif z_b is not None:
            indifference = mid - z_b
        else:
            indifference = mid + (z_a or 0.0)
        out.update(tau=tau, solved=solved, indifference=indifference,
                   sigma_latent=float(params.sigma_tilde(tau, mid)),
                   price_vol=float(params.varsigma(tau, mid)),
                   fill_rate=float(params.activity(tau, mid)) * math.exp(-out["k"] * self.half_spread),
                   scales=[round(x, 3) for x in self.solved_scales],
                   surface_age_s=now - self.solved_at)
        return out
