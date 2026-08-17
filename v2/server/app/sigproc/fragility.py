"""State-space Neural Fragility analysis for SEEG/iEEG recordings.

Mathematical formulation based on Li et al. 2021 (Nature Neuroscience,
doi:10.1038/s41593-021-00901-w) and Gunnarsdottir et al. 2022 (Brain).

Backends:
1. PyTorch CUDA (GPU batched tensor inversion on NVIDIA GPU, ~1.4s per full seizure)
2. Pure NumPy LAPACK (universal CPU batched matrix inversion fallback)

Zero dependencies on FastAPI, SQLAlchemy, or Pydantic.
"""

import math
import os
import sys
from typing import Any
import numpy as np
import scipy.linalg

# Check for PyTorch CUDA support (including discovering local virtualenv if present)
_CUDA_AVAILABLE = False
try:
    import torch
    _CUDA_AVAILABLE = torch.cuda.is_available()
except ImportError:
    fastapi_site = os.path.expanduser("~/virtualenv/fastapi/lib/python3.12/site-packages")
    if os.path.exists(fastapi_site) and fastapi_site not in sys.path:
        sys.path.append(fastapi_site)
        try:
            import torch
            _CUDA_AVAILABLE = torch.cuda.is_available()
        except Exception:
            pass


def fit_ltv_model(
    X: np.ndarray,
    l2_reg: float = 1e-5,
) -> tuple[np.ndarray, float]:
    """Fit a discrete-time Linear Time-Varying (LTV) dynamical system model.

    Models the windowed multi-channel signal X as:
        x[t+1] = A x[t] + w[t]

    where X has shape (N_channels, T_samples).

    Parameters
    ----------
    X : np.ndarray
        Multi-channel time series window of shape (N, T).
    l2_reg : float
        L2 Ridge regularization parameter to ensure stable matrix inversion
        when T <= N or when channels are collinear.

    Returns
    -------
    A : np.ndarray
        Estimated system transition matrix of shape (N, N).
    r2 : float
        Global coefficient of determination (R^2) measuring fit quality.
    """
    if X.ndim != 2:
        raise ValueError(f"X must be 2D of shape (N_channels, T_samples), got {X.shape}")

    n_channels, n_samples = X.shape
    if n_samples < 2:
        raise ValueError(f"Window must contain at least 2 samples, got {n_samples}")

    X1 = X[:, :-1]  # Shape (N, T-1)
    X2 = X[:, 1:]   # Shape (N, T-1)

    cov = X1 @ X1.T
    if l2_reg > 0:
        cov += l2_reg * np.eye(n_channels, dtype=cov.dtype)

    try:
        A_T = scipy.linalg.solve(cov, X1 @ X2.T, assume_a="pos")
        A = A_T.T
    except (scipy.linalg.LinAlgError, ValueError):
        A = X2 @ np.linalg.pinv(X1)

    X2_pred = A @ X1
    ss_res = float(np.sum((X2 - X2_pred) ** 2))
    x2_centered = X2 - np.mean(X2, axis=1, keepdims=True)
    ss_tot = float(np.sum(x2_centered ** 2))

    if ss_tot > 1e-12:
        r2 = max(0.0, 1.0 - ss_res / ss_tot)
    else:
        r2 = 0.0

    return A, r2


def compute_min_perturbations_gpu(
    A_batch: np.ndarray,
    radius: float = 1.0,
    num_freqs: int = 16,
    batch_size: int = 16,
) -> np.ndarray:
    """Fast GPU-accelerated batched fragility computation using PyTorch CUDA.

    Parameters
    ----------
    A_batch : np.ndarray
        Batch of system matrices of shape (n_windows, n_channels, n_channels).
    radius : float
        Target spectral radius boundary (default 1.0).
    num_freqs : int
        Number of frequency points in [0, pi].
    batch_size : int
        Sub-batch size to prevent GPU VRAM out-of-memory.

    Returns
    -------
    frag_matrix : np.ndarray
        Fragility matrix of shape (n_channels, n_windows).
    """
    import torch

    n_windows, n_channels, _ = A_batch.shape
    device = torch.device("cuda")

    thetas = torch.linspace(0.0, math.pi, num_freqs, dtype=torch.float64, device=device)
    z_points = torch.polar(torch.full_like(thetas, radius), thetas)  # (M,)
    I_mat = torch.eye(n_channels, dtype=torch.complex128, device=device)

    all_frags = []
    for start in range(0, n_windows, batch_size):
        end = min(start + batch_size, n_windows)
        sub_A = torch.tensor(A_batch[start:end], dtype=torch.complex128, device=device)

        # M_mats has shape (cur_b, M, N, N)
        M_mats = z_points[None, :, None, None] * I_mat[None, None, :, :] - sub_A[:, None, :, :]
        M_inv = torch.linalg.inv(M_mats)

        # 2-norm of each row: shape (cur_b, M, N)
        row_norms = torch.linalg.vector_norm(M_inv, dim=-1)
        max_row_norms = torch.max(row_norms, dim=1).values  # (cur_b, N)

        min_deltas = 1.0 / torch.clamp(max_row_norms, min=1e-12)  # (cur_b, N)
        max_deltas = torch.max(min_deltas, dim=-1, keepdim=True).values

        sub_frag = 1.0 - (min_deltas / torch.clamp(max_deltas, min=1e-12))
        all_frags.append(sub_frag.cpu().numpy())

    # Concatenate along window axis -> transpose to (n_channels, n_windows)
    return np.concatenate(all_frags, axis=0).T


def compute_min_perturbations(
    A: np.ndarray,
    radius: float = 1.0,
    num_freqs: int = 16,
) -> np.ndarray:
    """Compute the minimum column perturbation norm required to destabilize the system.

    For each channel k in {0, ..., N-1}, finds the minimum Euclidean norm of the column
    perturbation Delta_k such that the perturbed system matrix A + Delta_k e_k^T has an
    eigenvalue on the contour |lambda| = radius:

        ||Delta_k*||_2 = 1 / max_{theta in [0, pi]} ||e_k^T (radius * e^(j*theta) * I - A)^(-1)||_2

    Parameters
    ----------
    A : np.ndarray
        Square system transition matrix of shape (N, N).
    radius : float
        Target spectral radius boundary (default 1.0 for unit circle stability).
    num_freqs : int
        Number of discrete frequency points in [0, pi] for grid search.

    Returns
    -------
    perturbation_norms : np.ndarray
        Minimum perturbation 2-norm for each channel, shape (N,).
    """
    n_channels = A.shape[0]
    if A.shape != (n_channels, n_channels):
        raise ValueError(f"A must be square, got {A.shape}")

    thetas = np.linspace(0.0, np.pi, num_freqs)
    z_points = radius * np.exp(1j * thetas)

    I_mat = np.eye(n_channels, dtype=np.complex128)
    M_mats = z_points[:, None, None] * I_mat[None, :, :] - A[None, :, :]

    try:
        M_inv = np.linalg.inv(M_mats)
    except np.linalg.LinAlgError:
        M_inv = np.linalg.pinv(M_mats)

    row_norms = np.linalg.norm(M_inv, axis=-1)
    max_inv_row_norms = np.max(row_norms, axis=0)

    perturbation_norms = np.zeros(n_channels, dtype=np.float64)
    valid_mask = max_inv_row_norms > 1e-12
    perturbation_norms[valid_mask] = 1.0 / max_inv_row_norms[valid_mask]
    return perturbation_norms


def compute_window_fragility(
    X_win: np.ndarray,
    radius: float = 1.0,
    num_freqs: int = 16,
    l2_reg: float = 1e-5,
) -> tuple[np.ndarray, float]:
    """Compute neural fragility vector for a single time window.

    Parameters
    ----------
    X_win : np.ndarray
        Multi-channel time series window of shape (N, T).
    radius : float
        Target spectral radius boundary (default 1.0).
    num_freqs : int
        Number of discrete frequencies in [0, pi].
    l2_reg : float
        Ridge regularizer for LTV model fitting.

    Returns
    -------
    fragility : np.ndarray
        Normalized fragility scores in [0, 1] for each channel, shape (N,).
        Higher score = more fragile (requires smaller perturbation to destabilize).
    r2 : float
        LTV model goodness-of-fit R^2.
    """
    A, r2 = fit_ltv_model(X_win, l2_reg=l2_reg)
    deltas = compute_min_perturbations(A, radius=radius, num_freqs=num_freqs)

    max_delta = float(np.max(deltas))
    if max_delta > 1e-12:
        fragility = 1.0 - (deltas / max_delta)
    else:
        fragility = np.zeros_like(deltas)

    return fragility, r2


def compute_fragility_pipeline(
    data: np.ndarray,
    fs: float,
    ch_names: list[str] | None = None,
    win_s: float = 0.25,
    step_s: float = 0.125,
    radius: float = 1.0,
    num_freqs: int = 16,
    l2_reg: float = 1e-5,
    eval_window_s: tuple[float, float] | None = None,
    onset_s: float | None = None,
    device: str = "auto",
) -> dict[str, Any]:
    """Sliding-window time-resolved neural fragility computation across an entire recording.

    Parameters
    ----------
    data : np.ndarray
        Multi-channel continuous data array of shape (N_channels, N_samples).
    fs : float
        Sampling frequency in Hz.
    ch_names : list[str] or None
        Channel labels. If None, default integer labels ['CH0', 'CH1', ...] are generated.
    win_s : float
        Duration of each analysis window in seconds (default 0.25 s = 250 ms).
    step_s : float
        Step between consecutive window starts in seconds (default 0.125 s = 125 ms).
    radius : float
        Target spectral radius boundary (default 1.0).
    num_freqs : int
        Number of frequency bins in [0, pi] for perturbation search.
    l2_reg : float
        Ridge regularization coefficient.
    eval_window_s : tuple[float, float] or None
        Optional (t_start, t_end) window in seconds (relative to onset_s if provided,
        or relative to recording start) over which to average fragility for the summary score.
    onset_s : float or None
        Seizure onset timestamp in seconds. If provided, `start_times` will be relative
        to `onset_s` (i.e. t=0 at seizure onset).
    device : str
        'auto', 'cuda', or 'cpu'.

    Returns
    -------
    dict
        Dictionary containing:
        - "fragility_matrix": np.ndarray of shape (N_channels, N_windows)
        - "r2_per_window": np.ndarray of shape (N_windows,)
        - "start_times": np.ndarray of shape (N_windows,) in seconds
        - "channel_scores": dict[str, float] mapping channel name to mean score
        - "ranked_channels": list[tuple[str, float]] sorted by fragility descending
        - "median_r2": float
        - "fs": float
        - "win_s": float
        - "step_s": float
    """
    n_channels, total_samples = data.shape
    if ch_names is None:
        ch_names = [f"CH{i}" for i in range(n_channels)]
    elif len(ch_names) != n_channels:
        raise ValueError(f"Length of ch_names ({len(ch_names)}) != n_channels ({n_channels})")

    win_samples = int(round(win_s * fs))
    step_samples = int(round(step_s * fs))

    if win_samples > total_samples:
        raise ValueError(f"Recording duration ({total_samples} samples) is shorter than window ({win_samples} samples)")

    starts = np.arange(0, total_samples - win_samples + 1, step_samples)
    n_windows = len(starts)

    start_times = (starts / fs) - (onset_s if onset_s is not None else 0.0)

    # 1. Fit LTV system models across all windows
    A_batch = np.zeros((n_windows, n_channels, n_channels), dtype=np.float64)
    r2_vector = np.zeros(n_windows, dtype=np.float64)

    for w_idx, start_idx in enumerate(starts):
        X_win = data[:, start_idx : start_idx + win_samples]
        A, r2 = fit_ltv_model(X_win, l2_reg=l2_reg)
        A_batch[w_idx] = A
        r2_vector[w_idx] = r2

    # 2. Compute Fragility using GPU if CUDA is active, else CPU
    use_gpu = (device == "cuda") or (device == "auto" and _CUDA_AVAILABLE)
    frag_matrix = None

    if use_gpu:
        try:
            frag_matrix = compute_min_perturbations_gpu(
                A_batch,
                radius=radius,
                num_freqs=num_freqs,
                batch_size=16,
            )
        except Exception:
            frag_matrix = None

    if frag_matrix is None:
        # Fallback to pure NumPy LAPACK window-by-window
        frag_matrix = np.zeros((n_channels, n_windows), dtype=np.float64)
        for w_idx in range(n_windows):
            deltas = compute_min_perturbations(A_batch[w_idx], radius=radius, num_freqs=num_freqs)
            max_delta = float(np.max(deltas))
            if max_delta > 1e-12:
                frag_matrix[:, w_idx] = 1.0 - (deltas / max_delta)

    # 3. Aggregate scores over evaluation window
    if eval_window_s is not None:
        t_start, t_end = eval_window_s
        sel = np.where((start_times >= t_start) & (start_times <= t_end))[0]
        if len(sel) > 0:
            mean_scores = np.mean(frag_matrix[:, sel], axis=1)
        else:
            mean_scores = np.mean(frag_matrix, axis=1)
    else:
        mean_scores = np.mean(frag_matrix, axis=1)

    channel_scores = {ch_names[i]: float(mean_scores[i]) for i in range(n_channels)}
    ranked_channels = sorted(channel_scores.items(), key=lambda item: item[1], reverse=True)

    return {
        "fragility_matrix": frag_matrix,
        "r2_per_window": r2_vector,
        "start_times": start_times,
        "channel_scores": channel_scores,
        "ranked_channels": ranked_channels,
        "median_r2": float(np.median(r2_vector)) if n_windows > 0 else 0.0,
        "fs": fs,
        "win_s": win_s,
        "step_s": step_s,
    }
