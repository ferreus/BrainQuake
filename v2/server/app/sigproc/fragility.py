"""State-space Neural Fragility analysis for SEEG/iEEG recordings.

Mathematical formulation based on Li et al. 2021 (Nature Neuroscience,
doi:10.1038/s41593-021-00901-w) and Gunnarsdottir et al. 2022 (Brain).

Backends:
1. PyTorch CUDA (GPU batched tensor inversion on NVIDIA GPU, ~1.4s per full seizure)
2. Pure NumPy LAPACK (universal CPU batched matrix inversion fallback)

Zero dependencies on FastAPI, SQLAlchemy, or Pydantic.
"""

import math
import warnings
from typing import Any
import numpy as np
import scipy.linalg

_CUDA_AVAILABLE = False
try:
    import torch
    _CUDA_AVAILABLE = torch.cuda.is_available()
except ImportError:
    pass

# Below this |sin(theta)| the contour point is real, so the imaginary constraint
# vanishes instead of pinning Delta to a rounding-error direction.
_REAL_Z_TOL = 1e-12


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
        L2 Ridge regularization, expressed as a fraction of the mean covariance
        eigenvalue so the fit is invariant to the signal's units (V vs uV).
        Needed whenever T <= N or channels are collinear -- note a common-average
        montage makes X1 rank-deficient by construction.

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
        cov_scale = float(np.trace(cov)) / n_channels
        if cov_scale > 0:
            cov += (l2_reg * cov_scale) * np.eye(n_channels, dtype=cov.dtype)

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


def compute_fragility_batch_gpu(
    A_batch: np.ndarray,
    radius: float = 1.0,
    num_freqs: int = 16,
    batch_size: int = 16,
) -> np.ndarray:
    """Batched, GPU-accelerated equivalent of `compute_min_perturbations`, normalized.

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
        Per-window normalized fragility, shape (n_channels, n_windows).
    """
    import torch

    n_windows, n_channels, _ = A_batch.shape
    device = torch.device("cuda")

    thetas = torch.linspace(0.0, math.pi, num_freqs, dtype=torch.float64, device=device)
    z_points = torch.polar(torch.full_like(thetas, radius), thetas)  # (M,)
    is_real_z = torch.abs(torch.sin(thetas)) < _REAL_Z_TOL  # (M,)
    I_mat = torch.eye(n_channels, dtype=torch.complex128, device=device)

    all_frags = []
    for start in range(0, n_windows, batch_size):
        end = min(start + batch_size, n_windows)
        sub_A = torch.tensor(A_batch[start:end], dtype=torch.complex128, device=device)

        # M_mats has shape (cur_b, M, N, N)
        M_mats = z_points[None, :, None, None] * I_mat[None, None, :, :] - sub_A[:, None, :, :]
        M_inv = torch.linalg.inv(M_mats)

        # Rows of the resolvent: a + jb = e_k^T (zI - A)^-1, shape (cur_b, M, N)
        a, b = M_inv.real, M_inv.imag
        aa = torch.sum(a * a, dim=-1)
        bb = torch.sum(b * b, dim=-1)
        ab = torch.sum(a * b, dim=-1)

        det = aa * bb - ab * ab
        deltas = torch.full_like(aa, float("inf"))
        ok = det > 0
        deltas[ok] = torch.sqrt(bb[ok] / det[ok])

        real_rows = is_real_z[None, :, None].expand_as(deltas) & (aa > 0)
        deltas = torch.where(real_rows, torch.rsqrt(torch.clamp(aa, min=1e-300)), deltas)

        min_deltas = torch.min(deltas, dim=1).values  # (cur_b, N)
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

    For each channel k, finds the smallest REAL column perturbation Delta_k such that
    A + Delta_k e_k^T has an eigenvalue on the contour |lambda| = radius. Writing
    a + jb = e_k^T (z I - A)^(-1) at z = radius * e^(j*theta), the determinant lemma
    turns that into two real constraints, a.Delta = 1 and b.Delta = 0, whose
    minimum-norm solution has

        ||Delta_k(theta)|| = sqrt( (b.b) / ((a.a)(b.b) - (a.b)^2) )

    and ||Delta_k|| is the minimum over theta. Real z (theta = 0, pi) drops the second
    constraint, giving 1/||a||. Restricting Delta to the reals matters: the complex
    relaxation 1/||a + jb|| is strictly smaller whenever the critical theta is
    interior, i.e. exactly the oscillatory onsets of interest.

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

    # One Schur factorization per matrix, reused across frequencies: with A = Q T Q^H,
    # (zI - A)^-1 = Q (zI - T)^-1 Q^H is a triangular solve plus a matmul. Both are
    # BLAS3, where a complex LU per frequency is not -- ~65x faster here, and unitary
    # so the resolvent's conditioning is untouched.
    T_mat, Q = scipy.linalg.schur(A, output="complex")
    QH = Q.conj().T
    I_mat = np.eye(n_channels, dtype=np.complex128)

    perturbation_norms = np.full(n_channels, np.inf)
    for theta in thetas:
        z = radius * np.exp(1j * theta)
        try:
            M_inv = Q @ scipy.linalg.solve_triangular(z * I_mat - T_mat, QH)
        except (scipy.linalg.LinAlgError, ValueError):
            continue  # z sits on an eigenvalue; neighbouring frequencies cover it

        a, b = M_inv.real, M_inv.imag
        aa = np.einsum("kn,kn->k", a, a)

        if abs(math.sin(theta)) < _REAL_Z_TOL:
            # z is real, so the imaginary constraint vanishes and ||Delta|| = 1/||a||
            deltas = np.where(aa > 0, 1.0 / np.sqrt(np.maximum(aa, 1e-300)), np.inf)
        else:
            bb = np.einsum("kn,kn->k", b, b)
            ab = np.einsum("kn,kn->k", a, b)
            det = aa * bb - ab * ab  # >= 0 by Cauchy-Schwarz; 0 means z is unreachable
            deltas = np.full(n_channels, np.inf)
            ok = det > 0
            deltas[ok] = np.sqrt(bb[ok] / det[ok])

        np.minimum(perturbation_norms, deltas, out=perturbation_norms)

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

    if win_samples - 1 <= n_channels:
        warnings.warn(
            f"Window has {win_samples - 1} regressors for {n_channels} channels; the LTV fit "
            f"is underdetermined and driven by l2_reg. Lengthen win_s or reduce channels.",
            stacklevel=2,
        )

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
            frag_matrix = compute_fragility_batch_gpu(
                A_batch,
                radius=radius,
                num_freqs=num_freqs,
                batch_size=16,
            )
        except Exception as exc:
            warnings.warn(f"GPU fragility failed ({exc!r}); falling back to CPU.", stacklevel=2)
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
