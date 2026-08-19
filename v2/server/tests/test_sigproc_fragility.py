"""Unit tests for the Neural Fragility sigproc module."""

import numpy as np
import pytest

from app.sigproc.fragility import (
    _CUDA_AVAILABLE,
    _contour,
    fit_ltv_ez,
    standardize_ieeg,
    compute_fragility_batch_gpu,
    compute_fragility_pipeline,
    compute_min_perturbations,
    compute_window_fragility,
    fit_ltv_model,
)


def _oracle_min_real_perturbation(A, k, radius=1.0, num_freqs=512):
    """Independent min-norm real perturbation, via lstsq rather than the closed form.

    Searches `_contour`, not [0, pi], so it validates the closed form rather than the
    contour choice; no real z is sampled, so both constraints always apply.
    """
    n = A.shape[0]
    best_norm, best_delta = np.inf, None
    for z in _contour(num_freqs, quarter=False, radius=radius):
        r = np.linalg.inv(z * np.eye(n) - A)[k, :]
        B, c = np.vstack([r.real, r.imag]), np.array([1.0, 0.0])
        delta = np.linalg.lstsq(B, c, rcond=None)[0]
        norm = float(np.linalg.norm(delta))
        if norm < best_norm:
            best_norm, best_delta = norm, delta
    return best_norm, best_delta


def _stable_oscillatory_system(n=8, rho=0.85, seed=3):
    rng = np.random.default_rng(seed)
    A = rng.normal(scale=0.9 / np.sqrt(n), size=(n, n))
    return A * (rho / np.max(np.abs(np.linalg.eigvals(A))))


@pytest.mark.skipif(not _CUDA_AVAILABLE, reason="CUDA not available")
def test_cuda_and_numpy_parity():
    """Verify that PyTorch CUDA and NumPy LAPACK produce identical outputs."""
    rng = np.random.default_rng(42)
    n_channels = 20
    n_windows = 4
    A_batch = rng.normal(scale=0.05, size=(n_windows, n_channels, n_channels))

    frag_gpu = compute_fragility_batch_gpu(A_batch, radius=1.0, num_freqs=16)

    frag_np = np.zeros((n_channels, n_windows))
    for w in range(n_windows):
        deltas = compute_min_perturbations(A_batch[w], radius=1.0, num_freqs=16)
        frag_np[:, w] = 1.0 - deltas / np.max(deltas)

    # Max difference should be at machine precision
    diff = np.max(np.abs(frag_gpu - frag_np))
    assert diff < 1e-12


def test_fit_ltv_model_exact_recovery():
    """Verify a known linear system is recovered from persistently-excited data.

    A purely autonomous (noise-free) trajectory decays to numerical zero within a
    few dozen steps for a stable A_true, leaving nothing but rounding noise to fit
    against once the signal underflows. A driven VAR(1) process keeps every sample
    informative, matching what real SEEG looks like.
    """
    rng = np.random.default_rng(42)
    n_channels = 4
    n_samples = 5000

    # Create a stable transition matrix with spectral radius < 1
    A_true = np.array([
        [0.6, 0.1, 0.0, 0.0],
        [0.0, 0.5, 0.2, 0.0],
        [0.0, 0.0, 0.7, 0.1],
        [0.1, 0.0, 0.0, 0.4],
    ])

    X = np.zeros((n_channels, n_samples))
    X[:, 0] = rng.normal(size=n_channels)
    for t in range(n_samples - 1):
        X[:, t + 1] = A_true @ X[:, t] + rng.normal(size=n_channels)

    # l2_reg's default (0.3) is tuned to guarantee stability on real SEEG, which
    # biases a clean synthetic recovery test; push it near zero to isolate the
    # regression math itself from that production-tuned bias.
    A_est, r2 = fit_ltv_model(X, l2_reg=1e-6)

    assert r2 > 0.3
    np.testing.assert_allclose(A_est, A_true, atol=0.05)


def test_fit_ltv_model_enforces_stability():
    """The default fit must always be spectrally stable, even where an
    unregularized OLS fit would not be.

    "Minimum perturbation to destabilize" is undefined for an already-unstable
    A. A common-average montage makes X1 rank-deficient by construction, and
    real SEEG windows are barely overdetermined (T-1 samples vs N channels) --
    exactly the regime where a naive fit tends to land on the wrong side of
    the |eigenvalue| = 1 boundary.
    """
    rng = np.random.default_rng(7)
    n_channels, n_samples = 60, 62  # barely overdetermined, like a real window

    latent = rng.normal(size=(3, n_samples)).cumsum(axis=1)
    data = (rng.normal(size=(n_channels, 3)) @ latent) * 5 + rng.normal(scale=20.0, size=(n_channels, n_samples))
    data -= data.mean(axis=0, keepdims=True)  # CAR -> rank deficient by construction

    A_naive = data[:, 1:] @ np.linalg.pinv(data[:, :-1])
    assert np.max(np.abs(np.linalg.eigvals(A_naive))) >= 1.0, "test data must be pathological to start with"

    A, _ = fit_ltv_model(data)
    assert np.max(np.abs(np.linalg.eigvals(A))) < 1.0


def test_dc_only_system_is_unreachable():
    """Documents the accepted cost of excluding real z.

    Every mode here is real, so the cheap destabilisation is the DC route (0.3 for the
    0.7 mode). Off the real axis the resolvent rows are near-degenerate -- a and b are
    almost parallel -- so satisfying b.Delta = 0 as well costs orders of magnitude more.
    Real iEEG is not this system; see `test_no_contour_samples_a_real_z`.
    """
    A = np.diag([0.7, 0.5, 0.4, 0.3, 0.2])
    A[0, 1] = 0.2
    A[1, 2] = 0.2

    norms = compute_min_perturbations(A, radius=1.0, num_freqs=512)
    assert len(norms) == 5
    dc = 1.0 / np.linalg.norm(np.linalg.inv(np.eye(5) - A), axis=1)
    assert np.all(norms > dc), "off-axis always costs more than the DC route here"
    assert np.all(norms[2:] > 1e6), "a decoupled real mode is unreachable off the axis"


def test_perturbation_destabilizes_oscillatory_system():
    """The reported norm must be achievable by a REAL Delta that actually destabilizes.

    Guards the distinction from the complex relaxation, which the real-eigenvalue
    matrices above cannot detect (both formulas agree when the critical theta is 0 or pi).
    """
    A = _stable_oscillatory_system()
    n = A.shape[0]
    eigs = np.linalg.eigvals(A)
    assert np.max(np.abs(np.imag(eigs))) > 1e-3, "test system must be oscillatory"
    assert np.max(np.abs(eigs)) < 1.0, "test system must start stable"

    norms = compute_min_perturbations(A, radius=1.0, num_freqs=512)

    for k in range(n):
        ref_norm, delta = _oracle_min_real_perturbation(A, k, radius=1.0, num_freqs=512)
        assert pytest.approx(ref_norm, rel=1e-6) == norms[k]

        A_pert = A.copy()
        A_pert[:, k] += delta
        assert np.max(np.abs(np.linalg.eigvals(A_pert))) >= 1.0 - 1e-8


def test_complex_relaxation_would_underestimate():
    """Pin the size of the bug that was fixed: the complex bound is strictly smaller."""
    A = _stable_oscillatory_system()
    n = A.shape[0]
    thetas = np.linspace(0.0, np.pi, 512)

    complex_bound = np.array([
        1.0 / max(
            np.linalg.norm(np.linalg.inv(z * np.eye(n) - A)[k, :]) for z in np.exp(1j * thetas)
        )
        for k in range(n)
    ])
    real_norms = compute_min_perturbations(A, radius=1.0, num_freqs=512)

    assert np.all(real_norms >= complex_bound - 1e-12)
    assert np.max(real_norms / complex_bound) > 1.05  # ~7% on this system, more at higher N


def test_compute_window_fragility_bounds_and_ranking():
    """Verify single-window fragility output properties and ranking."""
    rng = np.random.default_rng(123)
    n_channels = 6
    n_samples = 250

    # Nodes 0-1 carry a rotational mode at |lambda| = 0.97, everything else is damped.
    # The mode is oscillatory on purpose: a real mode's cheapest destabilisation is the
    # DC route, which no contour here samples.
    A = np.diag([0.0, 0.0, 0.2, 0.2, 0.2, 0.2])
    th = 0.35
    A[:2, :2] = 0.97 * np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    X = np.zeros((n_channels, n_samples))
    X[:, 0] = rng.normal(size=n_channels)
    for t in range(n_samples - 1):
        X[:, t + 1] = A @ X[:, t] + rng.normal(scale=0.01, size=n_channels)

    frag, r2 = compute_window_fragility(X, radius=1.0, num_freqs=16, method="extended")

    assert frag.shape == (n_channels,)
    assert 0.0 <= np.min(frag)
    assert np.max(frag) <= 1.0
    assert r2 > 0.8
    assert np.argmax(frag) in (0, 1)


def test_compute_fragility_pipeline_shapes_and_timing():
    """Verify full sliding-window pipeline on synthetic continuous multi-channel data."""
    rng = np.random.default_rng(999)
    n_channels = 8
    fs = 1000.0
    duration_s = 2.0
    n_samples = int(fs * duration_s)

    # Create synthetic EEG
    data = rng.normal(scale=10.0, size=(n_channels, n_samples))
    ch_names = [f"A{i+1}" for i in range(n_channels)]

    res = compute_fragility_pipeline(
        data=data,
        fs=fs,
        ch_names=ch_names,
        win_s=0.25,
        step_s=0.125,
        radius=1.0,
        num_freqs=16,
        eval_window_s=(0.5, 1.5),
        onset_s=0.5,
    )

    assert "fragility_matrix" in res
    assert "r2_per_window" in res
    assert "start_times" in res
    assert "channel_scores" in res
    assert "ranked_channels" in res

    n_win = res["fragility_matrix"].shape[1]
    assert res["fragility_matrix"].shape == (n_channels, n_win)
    assert res["r2_per_window"].shape == (n_win,)
    assert res["start_times"].shape == (n_win,)

    idx_onset = np.argmin(np.abs(res["start_times"]))
    assert pytest.approx(0.0, abs=0.1) == res["start_times"][idx_onset]
    assert len(res["ranked_channels"]) == n_channels
    assert len(res["channel_scores"]) == n_channels


def test_fragility_is_invariant_to_signal_units():
    """Volts vs microvolts must not change the ranking (l2_reg is relative to cov scale)."""
    rng = np.random.default_rng(11)
    n_channels, fs, n_samples = 12, 1000.0, 2000

    # Correlated channels, as a real montage is: few latent sources + sensor noise
    latent = rng.normal(size=(3, n_samples)).cumsum(axis=1)
    data_uv = (rng.normal(size=(n_channels, 3)) @ latent) * 5 + rng.normal(scale=20.0, size=(n_channels, n_samples))
    data_uv -= data_uv.mean(axis=0, keepdims=True)  # CAR -> rank deficient by construction

    kw = dict(fs=fs, win_s=0.25, step_s=0.125, num_freqs=16, device="cpu")
    scores_uv = compute_fragility_pipeline(data=data_uv, **kw)["channel_scores"]
    scores_v = compute_fragility_pipeline(data=data_uv * 1e-6, **kw)["channel_scores"]

    np.testing.assert_allclose(
        [scores_uv[c] for c in scores_uv],
        [scores_v[c] for c in scores_uv],
        rtol=1e-6,
        atol=1e-9,
    )


def test_standardize_ieeg_matches_r_formula():
    """EZFragility divides by the power of ten just below the maximum."""
    data = np.array([[1.0, 250.0], [-40.0, 999.0]])
    np.testing.assert_allclose(standardize_ieeg(data), data / 100.0)
    assert standardize_ieeg(data * 1e-6).max() == pytest.approx(standardize_ieeg(data).max())


def test_contour_matches_r_grid():
    """R: omvec <- seq(0, 1, length.out = nSearch + 1)[-1]; z <- sqrt(1-om^2) + om*1i."""
    z = _contour(100, quarter=True)
    om = np.linspace(0.0, 1.0, 101)[1:]
    np.testing.assert_allclose(z, np.sqrt(1 - om**2) + 1j * om)
    assert np.all(z.imag > 0), "R's grid never samples a real z"


def test_fit_ltv_ez_is_per_row_ridge():
    """Each row is an independent ridge with penalty n * lambda / rms(that row's target)."""
    rng = np.random.default_rng(5)
    n_ch, n_t = 6, 80
    X = rng.normal(size=(n_ch, n_t))
    X[0] *= 50.0  # one loud channel, so the per-row penalties differ a lot
    lam = 1e-3

    A, _, used = fit_ltv_ez(X, lam=lam)
    assert used == lam

    X1, X2 = X[:, :-1], X[:, 1:]
    n = X1.shape[1]
    gram = X1 @ X1.T
    for i in range(n_ch):
        pen = n * lam / np.sqrt(np.sum(X2[i] ** 2) / n)
        expected = np.linalg.solve(gram + pen * np.eye(n_ch), X1 @ X2[i])
        np.testing.assert_allclose(A[i], expected, rtol=1e-8, atol=1e-10)


def test_no_contour_samples_a_real_z():
    """Both arcs must exclude real z, and neither may rank by DC gain.

    At real z the resolvent row is real, the imaginary constraint is vacuous, and the
    norm collapses to 1/||a|| -- a DC-gain ranking. On near-unit-root iEEG that route is
    common-mode across the implant, so sampling it cost 11-14 pp of SOZ recall on
    ds004100 versus excluding it. The synthetic system below is the case that argues the
    other way (a real mode nobody can see); real recordings do not look like it.
    """
    for num_freqs in (16, 100, 512):
        assert np.all(_contour(num_freqs, quarter=False).imag > 1e-9)
        assert np.all(_contour(num_freqs, quarter=True).imag > 1e-9)

    rng = np.random.default_rng(1)
    A = np.diag([0.95, 0.2, 0.2, 0.2, 0.2, 0.2]) + 0.02 * rng.normal(size=(6, 6))
    assert np.max(np.abs(np.linalg.eigvals(A))) < 1.0

    dc_gain = 1.0 / np.linalg.norm(np.linalg.inv(np.eye(6) - A), axis=1)
    assert np.argmin(dc_gain) == 0, "channel 0 is the DC-gain ranking's most fragile"
    for quarter in (False, True):
        norms = compute_min_perturbations(A, num_freqs=512, quarter=quarter)
        assert np.argmin(norms) != 0, "neither contour may rank by DC gain"


def test_highpass_default_is_method_aware():
    """extended filters by default; ezfragility must not, or it stops reproducing R."""
    rng = np.random.default_rng(21)
    fs, n_ch, n_t = 1000.0, 10, 4000
    drift = np.linspace(0.0, 300.0, n_t)  # the near-unit-root component being removed
    data = rng.normal(scale=20.0, size=(n_ch, n_t)) + drift
    data -= data.mean(axis=0, keepdims=True)

    kw = dict(fs=fs, win_s=0.25, step_s=0.125, device="cpu")
    assert compute_fragility_pipeline(data=data, method="extended", **kw)["highpass_hz"] == 0.5
    assert compute_fragility_pipeline(data=data, method="ezfragility", **kw)["highpass_hz"] is None

    # Explicitly off must differ from the filtered default, or the filter is a no-op.
    on = compute_fragility_pipeline(data=data, method="extended", **kw)["channel_scores"]
    off = compute_fragility_pipeline(data=data, method="extended", highpass_hz=None, **kw)["channel_scores"]
    assert not np.allclose([on[c] for c in on], [off[c] for c in on])
