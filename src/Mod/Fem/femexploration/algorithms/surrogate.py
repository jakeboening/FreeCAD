# ***************************************************************************
# *   Copyright (c) 2026 FreeCAD Project Association                        *
# *                                                                         *
# *   This file is part of the FreeCAD CAx development system.              *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# ***************************************************************************

"""Surrogate models over a design point table.

Three approximations of an expensive response, cheapest first:

``PolynomialResponseSurface``
    Least squares on standardised inputs, degree one or two.  Its
    normalised linear coefficients are the sensitivity numbers the charts
    show, and its lack of flexibility is a feature when there are few
    points.

``RBFSurrogate``
    Radial basis interpolation with a polynomial tail.  Passes through the
    data; good between points, unreliable outside them.

``Kriging``
    Ordinary Kriging with an anisotropic Gaussian correlation, its length
    scales chosen by maximum likelihood using the differential evolution
    already in this package, and a closed-form leave-one-out error.  The
    one of the three with a variance: it can say "I do not know here",
    which is what makes a response surface usable for anything more than
    decoration - a candidate the surrogate is unsure about is worth a real
    solve, one it is sure about is not.

numpy only.  Everything works in the unit cube, and every model records
how it was fitted so a study can report it.
"""

__title__ = "FreeCAD FEM design exploration surrogates"
__author__ = "FreeCAD Project Association"
__url__ = "https://www.freecad.org"

## \addtogroup FEM
#  @{

import math

POLYNOMIAL = "Polynomial"
RBF = "RBF"
KRIGING = "Kriging"
SURROGATES = (KRIGING, POLYNOMIAL, RBF)


class Surrogate:
    """Fit on unit-cube inputs X (n, k) and responses y (n,)."""

    name = "base"

    def fit(self, x, y):
        raise NotImplementedError

    def predict(self, x):
        """Mean prediction at rows of x; shape (m,)."""
        raise NotImplementedError

    def predict_std(self, x):
        """Predictive standard deviation, or zeros when the model has none."""
        import numpy as np

        return np.zeros(len(x))

    def loocv(self):
        """Leave-one-out RMS error, refitting when there is no shortcut."""
        import numpy as np

        x, y = self._x, self._y
        n = len(y)
        if n < 3:
            return float("nan")
        errors = []
        for i in range(n):
            keep = np.arange(n) != i
            other = type(self)(**self._params())
            other.fit(x[keep], y[keep])
            errors.append(float(other.predict(x[i : i + 1])[0] - y[i]))
        return float(np.sqrt(np.mean(np.square(errors))))

    def _params(self):
        return {}

    def summary(self):
        return {"model": self.name, "points": int(len(self._y)), "loocv_rmse": self.loocv()}


class PolynomialResponseSurface(Surrogate):
    """Least squares polynomial on standardised inputs."""

    name = POLYNOMIAL

    def __init__(self, degree=2):
        self.degree = int(degree)
        self.coefficients = None

    def _params(self):
        return {"degree": self.degree}

    def _features(self, x):
        import numpy as np

        x = np.asarray(x, dtype=float)
        n, k = x.shape
        cols = [np.ones(n)]
        cols += [x[:, j] for j in range(k)]
        if self.degree >= 2:
            for i in range(k):
                for j in range(i, k):
                    cols.append(x[:, i] * x[:, j])
        return np.column_stack(cols)

    def fit(self, x, y):
        import numpy as np

        self._x = np.asarray(x, dtype=float)
        self._y = np.asarray(y, dtype=float).reshape(-1)
        self._mean = self._x.mean(axis=0)
        self._scale = np.where(self._x.std(axis=0) > 0, self._x.std(axis=0), 1.0)
        features = self._features((self._x - self._mean) / self._scale)
        if len(self._y) < features.shape[1] and self.degree > 1:
            # not enough points for the quadratic; a linear fit is honest
            self.degree = 1
            features = self._features((self._x - self._mean) / self._scale)
        self.coefficients, _res, _rank, _sv = np.linalg.lstsq(features, self._y, rcond=None)
        return self

    def predict(self, x):
        import numpy as np

        z = (np.asarray(x, dtype=float) - self._mean) / self._scale
        return self._features(z) @ self.coefficients

    def linear_coefficients(self):
        """The standardised first-order terms: the sensitivity numbers."""
        k = self._x.shape[1]
        return list(self.coefficients[1 : 1 + k])


class RBFSurrogate(Surrogate):
    """Thin-plate spline interpolation with a linear tail."""

    name = RBF

    def __init__(self, kernel="thin_plate", smoothing=1e-10):
        self.kernel = kernel
        self.smoothing = float(smoothing)

    def _params(self):
        return {"kernel": self.kernel, "smoothing": self.smoothing}

    def _phi(self, r):
        import numpy as np

        if self.kernel == "multiquadric":
            return np.sqrt(r * r + 0.01)
        if self.kernel == "gaussian":
            return np.exp(-(r * r) / 0.1)
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(r > 0, r * r * np.log(np.where(r > 0, r, 1.0)), 0.0)
        return out

    def fit(self, x, y):
        import numpy as np

        self._x = np.asarray(x, dtype=float)
        self._y = np.asarray(y, dtype=float).reshape(-1)
        n, k = self._x.shape
        r = np.linalg.norm(self._x[:, None, :] - self._x[None, :, :], axis=2)
        a = self._phi(r) + self.smoothing * np.eye(n)
        p = np.hstack([np.ones((n, 1)), self._x])
        system = np.zeros((n + k + 1, n + k + 1))
        system[:n, :n] = a
        system[:n, n:] = p
        system[n:, :n] = p.T
        rhs = np.concatenate([self._y, np.zeros(k + 1)])
        solution = np.linalg.lstsq(system, rhs, rcond=None)[0]
        self.weights, self.tail = solution[:n], solution[n:]
        return self

    def predict(self, x):
        import numpy as np

        x = np.asarray(x, dtype=float)
        r = np.linalg.norm(x[:, None, :] - self._x[None, :, :], axis=2)
        return self._phi(r) @ self.weights + np.hstack([np.ones((len(x), 1)), x]) @ self.tail


class Kriging(Surrogate):
    """Ordinary Kriging, anisotropic Gaussian correlation, MLE length scales."""

    name = KRIGING

    def __init__(self, theta=None, nugget=1e-8, seed=0, mle_evaluations=400):
        self.theta = None if theta is None else list(theta)
        self.nugget = float(nugget)
        self.seed = int(seed)
        self.mle_evaluations = int(mle_evaluations)
        self.log_likelihood = None

    def _params(self):
        return {"theta": self.theta, "nugget": self.nugget, "seed": self.seed,
                "mle_evaluations": self.mle_evaluations}

    def _correlation(self, a, b, theta):
        import numpy as np

        d2 = ((a[:, None, :] - b[None, :, :]) ** 2) * np.asarray(theta)[None, None, :]
        return np.exp(-d2.sum(axis=2))

    def _fit_with(self, theta):
        """Everything the likelihood needs for one theta; None if singular."""
        import numpy as np

        n = len(self._y)
        r = self._correlation(self._x, self._x, theta) + self.nugget * np.eye(n)
        try:
            chol = np.linalg.cholesky(r)
        except np.linalg.LinAlgError:
            return None
        ones = np.ones(n)
        rinv_y = np.linalg.solve(chol.T, np.linalg.solve(chol, self._y))
        rinv_1 = np.linalg.solve(chol.T, np.linalg.solve(chol, ones))
        mu = float(ones @ rinv_y / (ones @ rinv_1))
        resid = self._y - mu
        rinv_resid = np.linalg.solve(chol.T, np.linalg.solve(chol, resid))
        sigma2 = float(resid @ rinv_resid / n)
        if sigma2 <= 0:
            return None
        log_det = 2.0 * float(np.sum(np.log(np.diag(chol))))
        ll = -0.5 * (n * math.log(sigma2) + log_det)
        return {"theta": list(theta), "chol": chol, "mu": mu, "sigma2": sigma2,
                "rinv_resid": rinv_resid, "rinv_1": rinv_1, "ll": ll}

    def fit(self, x, y):
        import numpy as np

        self._x = np.asarray(x, dtype=float)
        self._y = np.asarray(y, dtype=float).reshape(-1)
        n, k = self._x.shape
        if self.theta is not None:
            state = self._fit_with(self.theta)
        else:
            state = self._mle(k)
        if state is None:
            # fall back to a mild, isotropic correlation rather than fail
            state = self._fit_with([1.0] * k)
            if state is None:
                self.nugget = max(self.nugget, 1e-4)
                state = self._fit_with([1.0] * k)
        self._state = state
        self.theta = state["theta"]
        self.log_likelihood = state["ll"]
        return self

    def _mle(self, k):
        """Maximise the concentrated likelihood over log10(theta) with DE."""
        import numpy as np

        rng = np.random.default_rng(self.seed)
        lo, hi = -2.0, 2.0
        pop_size = max(8, 5 * k)
        pop = rng.random((pop_size, k)) * (hi - lo) + lo
        best = None

        def score(logt):
            state = self._fit_with(10.0 ** np.asarray(logt))
            return (-state["ll"] if state is not None else float("inf")), state

        scores = [score(row) for row in pop]
        evaluations = pop_size
        while evaluations < self.mle_evaluations:
            for i in range(pop_size):
                a, b, c = rng.choice([j for j in range(pop_size) if j != i], 3, replace=False)
                mutant = pop[a] + 0.7 * (pop[b] - pop[c])
                cross = rng.random(k) < 0.9
                cross[rng.integers(k)] = True
                trial = np.clip(np.where(cross, mutant, pop[i]), lo, hi)
                s, state = score(trial)
                evaluations += 1
                if s <= scores[i][0]:
                    pop[i], scores[i] = trial, (s, state)
            if evaluations >= self.mle_evaluations:
                break
        for s, state in scores:
            if state is not None and (best is None or s < best[0]):
                best = (s, state)
        return best[1] if best else None

    def predict(self, x):
        import numpy as np

        x = np.asarray(x, dtype=float)
        r = self._correlation(x, self._x, self._state["theta"])
        return self._state["mu"] + r @ self._state["rinv_resid"]

    def predict_std(self, x):
        import numpy as np

        x = np.asarray(x, dtype=float)
        st = self._state
        r = self._correlation(x, self._x, st["theta"])
        chol = st["chol"]
        rinv_r = np.linalg.solve(chol.T, np.linalg.solve(chol, r.T))
        ones = np.ones(len(self._y))
        u = (ones @ rinv_r) - 1.0
        var = st["sigma2"] * (1.0 - np.einsum("ij,ji->i", r, rinv_r) + u * u / (ones @ st["rinv_1"]))
        return np.sqrt(np.maximum(var, 0.0))

    def loocv(self):
        """Closed-form leave-one-out error: no refitting.

        Dubrule's identity for ordinary Kriging: with the augmented system
        ``K = [[R, 1], [1^T, 0]]`` and ``z = K^-1 [y; 0]``, the residual of
        leaving point i out and re-estimating the mean is ``z_i / (K^-1)_ii``.
        One inverse instead of n fits, and exact, not the simple-Kriging
        shortcut that quietly assumes a known mean.
        """
        import numpy as np

        st = self._state
        n = len(self._y)
        if n < 3:
            return float("nan")
        r = self._correlation(self._x, self._x, st["theta"]) + self.nugget * np.eye(n)
        k = np.zeros((n + 1, n + 1))
        k[:n, :n] = r
        k[:n, n] = 1.0
        k[n, :n] = 1.0
        try:
            k_inv = np.linalg.inv(k)
        except np.linalg.LinAlgError:
            return super().loocv()
        z = k_inv @ np.concatenate([self._y, [0.0]])
        residuals = z[:n] / np.diag(k_inv)[:n]
        return float(np.sqrt(np.mean(residuals**2)))

    def summary(self):
        out = super().summary()
        out["theta"] = [float(t) for t in self.theta]
        out["log_likelihood"] = float(self.log_likelihood) if self.log_likelihood is not None else None
        return out


def make_surrogate(name, **kwargs):
    if name == POLYNOMIAL:
        return PolynomialResponseSurface(**kwargs)
    if name == RBF:
        return RBFSurrogate(**kwargs)
    if name == KRIGING:
        return Kriging(**kwargs)
    raise ValueError(f"unknown surrogate {name!r}; available: {SURROGATES}")


def fit_table(rows, input_keys, output_keys, variables, name=KRIGING, **kwargs):
    """One surrogate per output, over the completed rows, in the unit cube.

    Returns ``{output_key: surrogate}``; inputs are encoded through the
    variables so every model sees the same [0,1]^k the planners use.
    """
    import numpy as np

    from ..stats import table_arrays
    from . import sampling

    x, y, _kept = table_arrays(rows, input_keys, output_keys)
    by_name = {v.name: v for v in variables}
    u = np.array([[sampling.encode_one(x[i, j], by_name[k]) for j, k in enumerate(input_keys)]
                  for i in range(len(x))]).reshape(len(x), len(input_keys))
    models = {}
    for j, key in enumerate(output_keys):
        models[key] = make_surrogate(name, **kwargs).fit(u, y[:, j])
    return models


##  @}
