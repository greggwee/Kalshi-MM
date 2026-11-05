"""Optimal quoting for binary prediction markets.

Implements Feil & Nendel, "Optimal Market Making in Prediction Markets"
(arXiv:2607.17991, July 2026). Equation numbers below refer to that paper.

Unlike Avellaneda-Stoikov there is no closed form. The price is a martingale
in (0, 1) driven by a latent log-odds belief, the contract settles to 0 or 1,
and the quotes come from a numerically solved value function V(t, p, q):

    dp = p(1-p) * sigma~(t, p) dW                                   (sec. 4.1)
    0 = -V_t - 1/2 varsigma^2 V_pp + gamma q^2 varsigma^2 - H^b - H^a  (HJB)
    V(T, p, q) = -gamma_T q^2 p(1-p)                                  (SV, TC)
    H^b(t, p; z) = Delta sup_pi Lambda^b(t, p, pi) (p - pi - z)
    H^a(t, p; z) = Delta sup_pi Lambda^a(t, p, pi) (pi - p - z)
    z_b = (V(q) - V(q + Delta)) / Delta,  z_a = (V(q) - V(q - Delta)) / Delta

z is the value lost per contract by taking on the fill, so it plays the role
A-S's reservation price plays: the optimal bid and ask are the maximizers of
Lambda * (edge - z). Prop. 3.2 gives them; thm. 3.3 proves they are optimal.

Three departures from the paper, all inactive at the paper's settings:

  * Time. The paper's t/T runs from 0 to 1 over the contract's life. A live
    market has no meaningful start, so everything here is indexed by tau, the
    time left until close, and `T` becomes the window before close over which
    volatility, activity and k ramp up (t/T = clip(1 - tau/T, 0, 1)). The
    solver runs backward from close and stops once the quotes stop changing,
    which on a long-dated market happens long before tau reaches the close.
  * Fees. `maker_fee` subtracts maker_fee * pi * (1 - pi) from the edge, the
    form Kalshi charges on series with maker fees. The paper has no fees.
  * Quote bound. `min_offset` stops a quote from coming closer than that to the
    price. Lambda grows without bound as a quote goes through the mid, which
    the paper's z never asks for but large inventory on a small account does,
    and a post-only maker cannot buy unlimited fills that way anyway. The
    maximizer is simply clamped: the objective is unimodal, so past the bound
    its best remaining point is the bound. This also restores the bounded
    intensity the paper's assumption (A3)(i) calls for.

The model assumes fills carry no information: arrivals are independent of the
price process. Adverse selection is therefore not priced here at all, and has
to be handled by market selection and stand-down rules around this module.
"""
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from scipy.linalg import lapack

P_MIN = 1e-3
P_MAX = 1.0 - 1e-3
EDGE = 1e-9
TICK = 0.01


@dataclass(frozen=True)
class ModelParams:
    """Defaults are the paper's Table 1, which is illustrative and not
    calibrated. Units follow the time unit chosen for T (hours when live)."""
    T: float = 1.0
    # latent volatility: sigma0 + sigma1 (t/T)^eta + sigma2 / (1 + x^2)
    sigma0: float = 0.6
    sigma1: float = 0.3
    sigma2: float = 0.1
    eta: float = 3.0
    # activity: (A0 + A1 (e^(xi t/T) - 1) / (e^xi - 1)) sqrt(p(1-p))
    A0: float = 100.0
    A1: float = 150.0
    xi: float = 2.0
    # intensity shape: boundary decay nu, spread sensitivity k(t) from k0 to k1
    nu: float = 1.0
    k0: float = 35.0
    k1: float = 50.0
    kappa: float = 1.5
    # risk: running gamma on q^2 varsigma^2, terminal gamma_T on q^2 p(1-p)
    gamma: float = 4e-3
    gamma_T: float = 1e-3
    # inventory grid -Q, -Q + delta, ..., Q and fixed trade size delta
    Q: float = 100.0
    delta: float = 10.0
    maker_fee: float = 0.0
    min_offset: Optional[float] = None

    def __post_init__(self):
        if self.T <= 0 or self.delta <= 0 or self.Q < self.delta:
            raise ValueError("need T > 0, delta > 0 and Q >= delta")
        ratio = self.Q / self.delta
        if abs(ratio - round(ratio)) > 1e-9:
            raise ValueError("Q must be a multiple of delta")
        if min(self.sigma0, self.sigma1, self.sigma2) < 0:
            raise ValueError("volatility terms must be non-negative")
        if self.sigma0 + self.sigma1 + self.sigma2 <= 0:
            raise ValueError("volatility must be positive")
        if min(self.A0, self.A1) < 0 or self.A0 <= 0:
            raise ValueError("activity must be positive")
        if self.nu <= 0 or min(self.k0, self.k1) <= 0:
            raise ValueError("need nu > 0 and k > 0")
        if self.gamma < 0 or self.gamma_T < 0 or self.maker_fee < 0:
            raise ValueError("risk aversion and fees must be non-negative")
        if self.min_offset is not None and self.min_offset < 0:
            raise ValueError("min_offset must be non-negative")

    def phase(self, tau):
        """The paper's t/T, held at 0 further than T from close."""
        return np.clip(1.0 - np.asarray(tau, dtype=float) / self.T, 0.0, 1.0)

    def sigma_tilde(self, tau, p):
        x = np.log(p / (1.0 - p))
        return self.sigma0 + self.sigma1 * self.phase(tau) ** self.eta \
            + self.sigma2 / (1.0 + x * x)

    def varsigma(self, tau, p):
        """Price volatility: dp = varsigma dW (eq. 4 with a logistic f)."""
        return p * (1.0 - p) * self.sigma_tilde(tau, p)

    def activity(self, tau, p):
        ramp = np.expm1(self.xi * self.phase(tau)) / math.expm1(self.xi)
        return (self.A0 + self.A1 * ramp) * np.sqrt(p * (1.0 - p))

    def k(self, tau):
        ramp = np.expm1(self.kappa * self.phase(tau)) / math.expm1(self.kappa)
        return self.k0 + (self.k1 - self.k0) * ramp

    def intensity_bid(self, tau, p, pi):
        """Lambda^b: fills per unit time for a bid at pi."""
        shape = (2.0 * pi / (pi + p)) ** self.nu
        return self.activity(tau, p) * shape * np.exp(-self.k(tau) * (p - pi))

    def intensity_ask(self, tau, p, pi):
        """Lambda^a: the mirror image, Lambda^a(p, pi) = Lambda^b(1-p, 1-pi)."""
        return self.intensity_bid(tau, 1.0 - p, 1.0 - pi)

    def fee(self, pi):
        return self.maker_fee * pi * (1.0 - pi)

    def max_intensity(self, tau: float = 0.0) -> float:
        """Fill rate for a quote at mid at p = 1/2, the scale that bounds the
        fixed point's contraction."""
        return max(float(self.activity(tau, np.array(0.5))), 1e-6)


def optimal_bid(params: ModelParams, k, p, z, start=None):
    """The maximizer of Lambda^b * (p - pi - fee - z) over [0, 1] (prop. 3.2).
    The first-order condition (k + nu p / (pi (pi + p))) edge(pi) + edge'(pi) = 0,
    multiplied through by pi (pi + p) to remove its pole at 0, is a polynomial
    with a single sign change on (0, 1), solved by bracketed Newton. At the
    corners there is no root: pi = 0 (Lambda^b vanishes, so do not bid) when
    p - z <= 0, and pi = 1 when the polynomial is still positive at 1."""
    p = np.asarray(p, dtype=float)
    m = p - np.asarray(z, dtype=float)
    k = np.broadcast_to(np.asarray(k, dtype=float), p.shape)
    nu, c = params.nu, params.maker_fee

    at_zero = m <= 0.0
    at_one = (k * (1.0 + p) + nu * p) * (m - 1.0) - (1.0 + p) * (1.0 - c) >= 0.0
    if start is None:
        pi = m - 1.0 / (k + nu / (2.0 * p))
    else:
        pi = start
    pi = np.where((pi > 0.0) & (pi < 1.0), pi, np.clip(0.5 * m, 1e-6, 1.0 - 1e-6))
    lo = np.zeros(p.shape)
    hi = np.ones(p.shape)
    for _ in range(60):
        a = pi * (pi + p)
        d_a = 2.0 * pi + p
        slope = k * a + nu * p
        edge = m - pi - c * pi * (1.0 - pi)
        d = 1.0 + c * (1.0 - 2.0 * pi)
        g = slope * edge - a * d
        d_g = k * d_a * edge - slope * d - d_a * d + 2.0 * c * a
        up = g > 0.0
        lo = np.where(up, pi, lo)
        hi = np.where(up, hi, pi)
        step = pi - g / d_g
        new = np.where((step >= lo) & (step <= hi), step, 0.5 * (lo + hi))
        new[at_zero] = 0.0
        new[at_one] = 1.0
        done = np.abs(new - pi).max() < 1e-9
        pi = new
        if done:
            break
    if params.min_offset is not None:
        pi = np.clip(np.minimum(pi, p - params.min_offset), 0.0, 1.0)
    return pi


def optimal_ask(params: ModelParams, k, p, z, start=None):
    """Lambda^a * (pi - p - fee - z) is the bid problem seen from the NO side."""
    p = np.asarray(p, dtype=float)
    return 1.0 - optimal_bid(params, k, 1.0 - p, z,
                             None if start is None else 1.0 - start)


def _policy(params: ModelParams, tau: float, p_col, V, starts):
    """Best quotes for V held fixed, as what each (p, q) earns under them: fill
    intensities on each side and the dollar rate from edge. p_col has shape
    (n_p, 1), V shape (n_p, n_q). The q-boundary sides stay at zero intensity."""
    d = params.delta
    k = params.k(tau)
    lam_b, lam_a, earn = np.zeros_like(V), np.zeros_like(V), np.zeros_like(V)

    z = (V[:, :-1] - V[:, 1:]) / d
    pb = optimal_bid(params, k, np.broadcast_to(p_col, z.shape), z, starts[0])
    lam_b[:, :-1] = params.intensity_bid(tau, p_col, pb)
    earn[:, :-1] += d * lam_b[:, :-1] * (p_col - pb - params.fee(pb))

    z = (V[:, 1:] - V[:, :-1]) / d
    pa = optimal_ask(params, k, np.broadcast_to(p_col, z.shape), z, starts[1])
    lam_a[:, 1:] = params.intensity_ask(tau, p_col, pa)
    earn[:, 1:] += d * lam_a[:, 1:] * (pa - p_col - params.fee(pa))
    return lam_b, lam_a, earn, (pb, pa)


def _solve_inventory(lam_b, lam_a, rhs, h: float):
    """(1 + h (lam_b + lam_a)) V(q) - h lam_b V(q + d) - h lam_a V(q - d) = rhs,
    tridiagonal along q and vectorized over p. Diagonally dominant for any h."""
    upper = np.empty_like(rhs)
    x = np.empty_like(rhs)
    pivot = 1.0 + h * (lam_b[:, 0] + lam_a[:, 0])
    upper[:, 0] = -h * lam_b[:, 0] / pivot
    x[:, 0] = rhs[:, 0] / pivot
    n_q = rhs.shape[1]
    for i in range(1, n_q):
        lower = -h * lam_a[:, i]
        pivot = 1.0 + h * (lam_b[:, i] + lam_a[:, i]) - lower * upper[:, i - 1]
        upper[:, i] = -h * lam_b[:, i] / pivot
        x[:, i] = (rhs[:, i] - lower * x[:, i - 1]) / pivot
    for i in range(n_q - 2, -1, -1):
        x[:, i] -= upper[:, i] * x[:, i + 1]
    return x


class ValueSurface:
    """V(tau, p, q) on a grid, with quote lookup by interpolation (sec. 4.2)."""

    def __init__(self, params: ModelParams, taus, p, q, V, stats: dict):
        self.params = params
        self.taus = np.asarray(taus, dtype=float)    # ascending, 0 = close
        self.p = np.asarray(p, dtype=float)
        self.q = np.asarray(q, dtype=float)
        self.V = np.asarray(V, dtype=float)          # (n_tau, n_p, n_q)
        self.stats = stats
        self.dp = self.p[1] - self.p[0]

    def slice_at(self, tau: float):
        """V at tau over (p, q). Beyond the last solved tau the quotes were
        already stationary, so the last slice is used."""
        tau = float(tau)
        if tau >= self.taus[-1]:
            return self.V[-1]
        i = max(int(np.searchsorted(self.taus, tau, side="right")) - 1, 0)
        w = (tau - self.taus[i]) / (self.taus[i + 1] - self.taus[i])
        return (1.0 - w) * self.V[i] + w * self.V[i + 1]

    def _p_weights(self, p):
        p = np.clip(np.asarray(p, dtype=float), self.p[0], self.p[-1])
        j = np.minimum(((p - self.p[0]) / self.dp).astype(int), len(self.p) - 2)
        return j, (p - self.p[j]) / self.dp

    def values(self, tau: float, p, q_index):
        """V at (p_i, q_i) for arrays of prices and integer q-grid indices."""
        s = self.slice_at(tau)
        j, w = self._p_weights(p)
        return (1.0 - w) * s[j, q_index] + w * s[j + 1, q_index]

    def value(self, tau: float, p: float, q: float) -> float:
        """V for any inventory inside the grid (linear in q, as in sec. 4.2)."""
        s = self.slice_at(tau)
        j, w = self._p_weights(p)
        j, w = int(j), float(w)
        at_p = (1.0 - w) * s[j] + w * s[j + 1]
        return float(np.interp(q, self.q, at_p))

    def shadow_costs(self, tau: float, p: float, q: float) -> Tuple[Optional[float], Optional[float]]:
        """(z_b, z_a), the value given up per contract by buying or selling
        one more lot. None on a side whose inventory limit is reached."""
        Q, d = self.params.Q, self.params.delta
        base = self.value(tau, p, q)
        z_b = z_a = None
        up = min(q + d, Q)
        if up > q + EDGE:
            z_b = (base - self.value(tau, p, up)) / (up - q)
        down = max(q - d, -Q)
        if down < q - EDGE:
            z_a = (base - self.value(tau, p, down)) / (q - down)
        return z_b, z_a

    def continuous_quotes(self, tau: float, p: float, q: float) -> dict:
        """The paper's optimal quotes, on no tick grid."""
        k = self.params.k(tau)
        z_b, z_a = self.shadow_costs(tau, p, q)
        bid = ask = None
        if z_b is not None:
            bid = float(optimal_bid(self.params, k, np.array([p]), np.array([z_b]))[0])
        if z_a is not None:
            ask = float(optimal_ask(self.params, k, np.array([p]), np.array([z_a]))[0])
        return {"bid": bid, "ask": ask, "z_b": z_b, "z_a": z_a, "k": float(k)}

    def tick_quotes(self, tau: float, p: float, q: float, tick: float = TICK) -> dict:
        """The same maximization restricted to prices the exchange accepts. A
        side whose best achievable value is not positive is left empty: at
        that inventory and price no fill is worth having."""
        P = self.params
        z_b, z_a = self.shadow_costs(tau, p, q)
        n = int(round(1.0 / tick))
        grid = np.arange(1, n) / n
        out = {"bid": None, "ask": None, "z_b": z_b, "z_a": z_a, "k": float(P.k(tau)),
               "lambda_b": None, "lambda_a": None}
        gap = P.min_offset if P.min_offset is not None else -1.0
        if z_b is not None:
            lam = P.intensity_bid(tau, p, grid)
            value = lam * (p - grid - P.fee(grid) - z_b)
            value[grid > p - gap + EDGE] = -np.inf
            i = int(np.argmax(value))
            if value[i] > 0.0:
                out["bid"], out["lambda_b"] = round(float(grid[i]), 4), float(lam[i])
        if z_a is not None:
            lam = P.intensity_ask(tau, p, grid)
            value = lam * (grid - p - P.fee(grid) - z_a)
            value[grid < p + gap - EDGE] = -np.inf
            i = int(np.argmax(value))
            if value[i] > 0.0:
                out["ask"], out["lambda_a"] = round(float(grid[i]), 4), float(lam[i])
        return out

    def queue_quotes(self, tau: float, p: float, q: float, yes_bids: dict, no_bids: dict,
                     tick: float = TICK) -> dict:
        """Prices chosen with the book in view. Lambda(t, p, pi) has no way to see that
        a quote at a touch holding 200 contracts waits behind all of them, while one
        tick inside it is first in line. Under FIFO a quote behind D contracts fills
        at a rate proportional to 1 / (D + delta), so each candidate scores
            Lambda(t, p, pi) / (D(pi) + delta) * (edge - z).
        Keeping Lambda keeps the quote responsive to z inside the spread, where the
        queue is the same at every price. This replaces only the last maximization,
        not the solved surface."""
        P = self.params
        out = self.tick_quotes(tau, p, q, tick)
        out["model_bid"], out["model_ask"] = out["bid"], out["ask"]
        n = int(round(1.0 / tick))
        grid = np.arange(1, n) / n
        gap = P.min_offset if P.min_offset is not None else 0.0
        bids = _ladder_depths(yes_bids, grid, flip=False)
        asks = _ladder_depths(no_bids, grid, flip=True)
        best_bid = grid[np.nonzero(bids)[0][-1]] if bids.any() else None
        best_ask = grid[np.nonzero(asks)[0][0]] if asks.any() else None
        if out["z_b"] is not None:
            ahead = np.cumsum(bids[::-1])[::-1]
            allowed = grid <= p - gap + EDGE
            if best_ask is not None:
                allowed &= grid < best_ask - EDGE
            i = _queue_value(P.intensity_bid(tau, p, grid) / (ahead + P.delta),
                             p - grid - P.fee(grid) - out["z_b"], allowed)
            out["bid"] = None if i is None else round(float(grid[i]), 4)
        if out["z_a"] is not None:
            ahead = np.cumsum(asks)
            allowed = grid >= p + gap - EDGE
            if best_bid is not None:
                allowed &= grid > best_bid + EDGE
            i = _queue_value(P.intensity_ask(tau, p, grid) / (ahead + P.delta),
                             grid - p - P.fee(grid) - out["z_a"], allowed)
            out["ask"] = None if i is None else round(float(grid[i]), 4)
        return out


def _time_grid(params: ModelParams, horizon: float, n_t: Optional[int],
               step_factor: float, growth: float) -> List[float]:
    """Uniform for a given n_t. Otherwise fine at close, where the solution has
    its boundary layer, then growing geometrically up to what resolves the ramps."""
    if n_t is not None:
        return [horizon * i / n_t for i in range(n_t + 1)]
    largest = min(params.T, horizon) / 40.0
    h = min(step_factor / params.max_intensity(0.0), largest)
    taus = [0.0]
    while taus[-1] < horizon:
        taus.append(min(taus[-1] + h, horizon))
        h = min(h * growth, largest)
    return taus


def _ladder_depths(ladder: dict, grid, flip: bool):
    """Displayed contracts at each grid price. A NO bid at x is a yes ask at 1 - x."""
    depth = np.zeros_like(grid)
    n = len(grid) + 1
    for price, qty in (ladder or {}).items():
        i = int(round((1.0 - price if flip else price) * n)) - 1
        if 0 <= i < len(grid):
            depth[i] += qty
    return depth


def _queue_value(fill_weight, value, allowed):
    value = np.where(allowed, fill_weight * value, -np.inf)
    i = int(np.argmax(value))
    return i if value[i] > 0.0 else None


def solve(params: ModelParams, horizon: Optional[float] = None, n_p: int = 200,
          n_t: Optional[int] = None, tol: float = 1e-8, max_iterations: int = 50,
          stationary_tol: Optional[float] = 1e-6, max_slices: int = 240,
          step_factor: float = 0.25, growth: float = 1.05,
          p_min: float = P_MIN, p_max: float = P_MAX) -> ValueSurface:
    """Solves (HJB) backward from close, out to `horizon` (default T).

    Sec. 4.2 steps eq. (8) by a fixed-point iteration, which only contracts for
    time steps below about 1/(4 * intensity). At Kalshi's flow that means tens of
    thousands of steps per solve, too slow to run live. This uses the same
    implicit Euler in time, but each step splits into two exact solves:
      1. Inventory: policy iteration on the Hamiltonians. With quotes frozen the
         equation is linear and tridiagonal in q, and it stays stable at any h.
      2. Diffusion: (I - h/2 diag(varsigma^2) D2) V = V*, with Neumann boundaries.
    This is first-order consistent with eq. (8), and is checked against the paper's
    figures and Table 2 in tests/test_pmm.py and `pmm_tool.py reproduce`.

    Past T the parameters stop changing, and the quotes settle within a few fill
    times. The solve stops there and the last slice is used beyond it."""
    horizon = params.T if horizon is None else float(horizon)
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    grid = _time_grid(params, horizon, n_t, step_factor, growth)
    n_steps = len(grid) - 1
    p = np.linspace(p_min, p_max, n_p + 1)
    p_col = p[:, None]
    dp = p[1] - p[0]
    n_q = int(round(2.0 * params.Q / params.delta)) + 1
    q = np.linspace(-params.Q, params.Q, n_q)
    q2 = q * q

    V = -params.gamma_T * q2[None, :] * p_col * (1.0 - p_col)
    keep = set(np.unique(np.round(np.geomspace(1, n_steps, max_slices)).astype(int)).tolist())
    taus: List[float] = [0.0]
    slices = [V.copy()]
    z_prev = V[:, 1:] - V[:, :-1]
    starts = (None, None)
    worst = 0
    unconverged = 0
    stopped_early = False
    n = 0
    tau = 0.0
    for n in range(1, n_steps + 1):
        tau = grid[n]
        h = tau - grid[n - 1]
        s2 = params.varsigma(tau, p) ** 2
        rhs = V - h * params.gamma * q2[None, :] * s2[:, None]

        V_k = V
        err = float("inf")
        for it in range(1, max_iterations + 1):
            lam_b, lam_a, earn, starts = _policy(params, tau, p_col, V_k, starts)
            V_new = _solve_inventory(lam_b, lam_a, rhs + h * earn, h)
            err = float(np.max(np.abs(V_new - V_k)))
            V_k = V_new
            if err < tol:
                break
        worst = max(worst, it)
        unconverged += err >= tol

        a = 0.5 * h * s2 / (dp * dp)
        # homogeneous Neumann boundaries via a mirrored ghost point
        dl, du = -a[1:], -a[:-1]
        du[0] *= 2.0
        dl[-1] *= 2.0
        V, info = lapack.dgtsv(dl, 1.0 + 2.0 * a, du, V_k)[3:]
        if info != 0 or not np.isfinite(V).all():
            raise RuntimeError(f"solve failed at tau={tau:g} (info={info})")

        z_now = V[:, 1:] - V[:, :-1]
        settled = (stationary_tol is not None and tau >= params.T
                   and float(np.max(np.abs(z_now - z_prev))) / params.delta < stationary_tol)
        z_prev = z_now
        if n in keep or settled or n == n_steps:
            taus.append(tau)
            slices.append(V.copy())
        if settled:
            stopped_early = True
            break
    stats = {"steps": n, "horizon": horizon, "reached_tau": tau, "worst_iterations": worst,
             "unconverged_steps": int(unconverged), "stopped_early": stopped_early}
    return ValueSurface(params, taus, p, q, np.stack(slices), stats)


def simulate(surface: Optional[ValueSurface], params: ModelParams, n_paths: int = 10000,
             n_steps: int = 1000, p0: float = 0.5, seed: int = 0,
             bound_baseline: bool = False) -> dict:
    """Monte Carlo of sec. 4.4: Euler-Maruyama price, Poisson fills, Bernoulli
    settlement. surface=None is the paper's myopic baseline, which maximizes
    instantaneous mark-to-market profit (z = 0). bound_baseline holds it to the
    same inventory limit the optimal strategy has."""
    rng = np.random.default_rng(seed)
    d, Q = params.delta, params.Q
    T = params.T
    h = T / n_steps
    p = np.full(n_paths, float(p0))
    q = np.zeros(n_paths)
    cash = np.zeros(n_paths)
    for n in range(n_steps):
        tau = T - n * h
        k = params.k(tau)
        if surface is None:
            z_b = np.zeros(n_paths)
            z_a = np.zeros(n_paths)
            can_buy = q < Q - EDGE if bound_baseline else np.ones(n_paths, dtype=bool)
            can_sell = q > -Q + EDGE if bound_baseline else np.ones(n_paths, dtype=bool)
        else:
            i = np.rint((q + Q) / d).astype(int)
            can_buy, can_sell = q < Q - EDGE, q > -Q + EDGE
            base = surface.values(tau, p, i)
            z_b = (base - surface.values(tau, p, np.minimum(i + 1, len(surface.q) - 1))) / d
            z_a = (base - surface.values(tau, p, np.maximum(i - 1, 0))) / d
        bid = optimal_bid(params, k, p, z_b)
        ask = optimal_ask(params, k, p, z_a)
        buys = rng.poisson(params.intensity_bid(tau, p, bid) * h) * can_buy
        sells = rng.poisson(params.intensity_ask(tau, p, ask) * h) * can_sell
        if surface is not None or bound_baseline:
            buys = np.minimum(buys, np.rint((Q - q) / d).astype(int))
            sells = np.minimum(sells, np.rint((q + Q) / d).astype(int))
        cash += d * (sells * ask - buys * bid)
        q += d * (buys - sells)
        vol = params.varsigma(tau, p)
        p = np.clip(p + vol * math.sqrt(h) * rng.standard_normal(n_paths), P_MIN, P_MAX)
    pnl = cash + q * (rng.random(n_paths) <= p)
    tail = np.sort(pnl)[: max(int(0.05 * n_paths), 1)]
    return {"mean_pnl": float(pnl.mean()), "std_pnl": float(pnl.std(ddof=1)),
            "mean_abs_q": float(np.abs(q).mean()), "var5": float(-np.quantile(pnl, 0.05)),
            "es5": float(-tail.mean()), "se_mean": float(pnl.std(ddof=1) / math.sqrt(n_paths))}


