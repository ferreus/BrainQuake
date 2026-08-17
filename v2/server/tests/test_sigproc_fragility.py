"""Unit tests for the Neural Fragility sigproc module."""

import numpy as np
import pytest

from app.sigproc.fragility import (
    _CUDA_AVAILABLE,
    compute_fragility_pipeline,
    compute_min_perturbations,
    compute_min_perturbations_gpu,
    compute_window_fragility,
    fit_ltv_model,
)


@pytest.mark.skipif(not _CUDA_AVAILABLE, reason="CUDA not available")
def test_cuda_and_numpy_parity():
    """Verify that PyTorch CUDA and NumPy LAPACK produce identical outputs."""
    rng = np.random.default_rng(42)
    n_channels = 20
    n_windows = 4
    A_batch = rng.normal(scale=0.05, size=(n_windows, n_channels, n_channels))

    frag_gpu = compute_min_perturbations_gpu(A_batch, radius=1.0, num_freqs=16)

    frag_np = np.zeros((n_channels, n_windows))
    for w in range(n_windows):
        deltas = compute_min_perturbations(A_batch[w], radius=1.0, num_freqs=16)
        frag_np[:, w] = 1.0 - deltas / np.max(deltas)

    # Max difference should be at machine precision
    diff = np.max(np.abs(frag_gpu - frag_np))
    assert diff < 1e-12


def test_fit_ltv_model_exact_recovery():
    """Verify that a known linear system is accurately recovered from clean data."""
    rng = np.random.default_rng(42)
    n_channels = 4
    n_samples = 200

    # Create a stable transition matrix with spectral radius < 1
    A_true = np.array([
        [0.6, 0.1, 0.0, 0.0],
        [0.0, 0.5, 0.2, 0.0],
        [0.0, 0.0, 0.7, 0.1],
        [0.1, 0.0, 0.0, 0.4],
    ])

    # Generate autonomous trajectory: x[t+1] = A x[t]
    X = np.zeros((n_channels, n_samples))
    X[:, 0] = rng.normal(size=n_channels)
    for t in range(n_samples - 1):
        X[:, t + 1] = A_true @ X[:, t]

    A_est, r2 = fit_ltv_model(X, l2_reg=1e-8)

    assert r2 > 0.9999
    np.testing.assert_allclose(A_est, A_true, atol=1e-3)


def test_perturbation_destabilizes_system():
    """Verify that the minimum column perturbation moves the spectral radius of the system to >= 1.0."""
    n_channels = 5
    A = np.diag([0.7, 0.5, 0.4, 0.3, 0.2])
    A[0, 1] = 0.2
    A[1, 2] = 0.2

    num_freqs = 64
    radius = 1.0
    perturbation_norms = compute_min_perturbations(A, radius=radius, num_freqs=num_freqs)

    assert len(perturbation_norms) == n_channels
    assert np.all(perturbation_norms > 0)
    assert 0.25 < perturbation_norms[0] < 0.30

    # Test pure diagonal decoupled node
    A_diag = np.diag([0.8, 0.5, 0.4, 0.3, 0.2])
    norms_diag = compute_min_perturbations(A_diag, radius=1.0, num_freqs=64)
    assert pytest.approx(0.2, abs=1e-3) == norms_diag[0]


def test_compute_window_fragility_bounds_and_ranking():
    """Verify single-window fragility output properties and ranking."""
    rng = np.random.default_rng(123)
    n_channels = 6
    n_samples = 250

    # Node 0 has strong self-excitation close to instability (0.95), while other nodes are well-damped (0.2)
    A = np.diag([0.95, 0.2, 0.2, 0.2, 0.2, 0.2])
    X = np.zeros((n_channels, n_samples))
    X[:, 0] = rng.normal(size=n_channels)
    for t in range(n_samples - 1):
        X[:, t + 1] = A @ X[:, t] + rng.normal(scale=0.01, size=n_channels)

    frag, r2 = compute_window_fragility(X, radius=1.0, num_freqs=16, l2_reg=1e-5)

    assert frag.shape == (n_channels,)
    assert 0.0 <= np.min(frag)
    assert np.max(frag) <= 1.0
    assert r2 > 0.8
    assert np.argmax(frag) == 0


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
