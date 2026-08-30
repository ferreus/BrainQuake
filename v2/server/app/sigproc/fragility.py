"""State-space Neural Fragility analysis for SEEG/iEEG recordings.

Mathematical formulation based on Li et al. 2021 (Nature Neuroscience,
doi:10.1038/s41593-021-00901-w) and Gunnarsdottir et al. 2022 (Brain).

Backends:
1. PyTorch CUDA (GPU batched tensor inversion on NVIDIA GPU, ~1.4s per full seizure)
2. Pure NumPy LAPACK (universal CPU batched matrix inversion fallback)

Zero dependencies on FastAPI, SQLAlchemy, or Pydantic.
"""

import json
import math
import os
import warnings
from typing import Any
import numpy as np
import scipy.linalg

from .filters import bandpass

_CUDA_AVAILABLE = False
try:
    import torch
    _CUDA_AVAILABLE = torch.cuda.is_available()
except ImportError:
    pass

# Neither arc may sample a real z: there b vanishes, the imaginary constraint is
# vacuous, and the resulting 1/||a|| ranks channels by DC gain instead of by minimum
# perturbation. Li et al. / EZFragility take the quarter arc uniform in Im(z) dropping
# omega = 0; "extended" sweeps the upper half circle uniform in angle, endpoints
# excluded. Measured on ds004100, including z = 1 costs 11-14 pp of SOZ recall.
def _contour(num_freqs: int, quarter: bool, radius: float = 1.0) -> np.ndarray:
    if quarter:
        om = np.linspace(0.0, 1.0, num_freqs + 1)[1:]
        return radius * (np.sqrt(1.0 - om ** 2) + 1j * om)
    return radius * np.exp(1j * np.linspace(0.0, np.pi, num_freqs + 2)[1:-1])


def _spectral_radius(A: np.ndarray) -> float:
    return float(np.max(np.abs(scipy.linalg.eigvals(A))))


def standardize_ieeg(data: np.ndarray) -> np.ndarray:
    """EZFragility's pre-scaling: divide by the power of ten below the maximum.

    Required by `fit_ltv_ez`, whose penalty is not scale-invariant.
    """
    peak = float(np.max(data))
    if not np.isfinite(peak) or peak <= 0:
        return data
    return data / 10.0 ** math.floor(math.log10(peak))


def fit_ltv_ez(
    X: np.ndarray,
    lam: float | None = None,
    max_bisect: int = 20,
) -> tuple[np.ndarray, float, float]:
    """EZFragility's estimator: a per-row ridge penalised by n * lam / rms(target).

    Each row of A is fit separately, so a high-amplitude channel is shrunk less than
    a quiet one. That differential is not scale-invariant, so `standardize_ieeg` must
    have been applied first. With `lam=None` the penalty is searched exactly as
    EZFragility's ridgeSearch does: start at 1e-4, and if A is unstable bisect
    towards 10, keeping the smallest stable value found in 20 steps.

    Returns (A, r2, lam_used).
    """
    if X.ndim != 2:
        raise ValueError(f"X must be 2D of shape (N_channels, T_samples), got {X.shape}")
    X1, X2 = X[:, :-1], X[:, 1:]
    n = X1.shape[1]
    if n < 1:
        raise ValueError(f"Window must contain at least 2 samples, got {X.shape[1]}")

    U, d, Vt = np.linalg.svd(X1, full_matrices=False)
    rms = np.sqrt(np.sum(X2 ** 2, axis=1) / n)
    W = Vt @ X2.T

    def fit_at(value: float) -> np.ndarray:
        pen = (n * value) / np.maximum(rms, 1e-300)
        dw = d[:, None] / (d[:, None] ** 2 + pen[None, :])
        return (U @ (dw * W)).T

    lam_used = 1e-4 if lam is None else lam
    A = fit_at(lam_used)
    if lam is None and _spectral_radius(A) >= 1.0:
        lo, hi = 1e-4, 10.0
        for _ in range(max_bisect):
            mid = (lo + hi) * 0.5
            A_try = fit_at(mid)
            if _spectral_radius(A_try) < 1.0:
                hi = lam_used = mid
                A = A_try
            else:
                lo = mid

    ss_res = float(np.sum((X2 - A @ X1) ** 2))
    ss_tot = float(np.sum((X2 - np.mean(X2, axis=1, keepdims=True)) ** 2))
    r2 = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 1e-12 else 0.0
    return A, r2, lam_used


def fit_ltv_model(
    X: np.ndarray,
    l2_reg: float = 0.3,
    max_bisect: int = 20,
) -> tuple[np.ndarray, float]:
    """Fit a discrete-time Linear Time-Varying (LTV) dynamical system model.

    Models the windowed multi-channel signal X as:
        x[t+1] = A x[t] + w[t]

    where X has shape (N_channels, T_samples).

    Ridge regression via the normal equations. `l2_reg` is expressed as a
    fraction of the mean covariance eigenvalue, so the fit is invariant to the
    signal's units (V vs uV) and to channel count.

    The fit must be spectrally stable (every |eigenvalue(A)| < 1): "minimum
    perturbation to destabilize" is undefined for an A that's already
    unstable, and SEEG dynamics are inherently near-unit-root at kHz sampling
    rates, so a too-small l2_reg leaves most windows on the wrong side of that
    boundary. `l2_reg`'s default already clears that bar on real Bella
    recordings; if a window still isn't stable, regularization is grown
    geometrically and then bisected until it is.

    Parameters
    ----------
    X : np.ndarray
        Multi-channel time series window of shape (N, T).
    l2_reg : float
        Starting L2 ridge fraction (see above).
    max_bisect : int
        Bisection iterations to refine the escalated regularization, if
        `l2_reg` alone isn't stable.

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
    cross = X1 @ X2.T
    eye = np.eye(n_channels, dtype=cov.dtype)
    cov_scale = float(np.trace(cov)) / n_channels

    def fit_at(l2: float) -> np.ndarray:
        c = cov + (l2 * cov_scale) * eye if (l2 > 0 and cov_scale > 0) else cov
        try:
            return scipy.linalg.solve(c, cross, assume_a="pos").T
        except (scipy.linalg.LinAlgError, ValueError):
            return X2 @ np.linalg.pinv(X1)

    def spectral_radius(A: np.ndarray) -> float:
        return float(np.max(np.abs(scipy.linalg.eigvals(A))))

    A = fit_at(l2_reg)
    rho = spectral_radius(A)
    if rho >= 1.0:
        lo, hi = l2_reg, max(l2_reg, 1e-6)
        while rho >= 1.0 and hi < 1e6:
            lo, hi = hi, hi * 10.0
            A = fit_at(hi)
            rho = spectral_radius(A)
        for _ in range(max_bisect):
            mid = (lo + hi) * 0.5
            A_try = fit_at(mid)
            rho_try = spectral_radius(A_try)
            if rho_try < 1.0:
                hi, A, rho = mid, A_try, rho_try
            else:
                lo = mid

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
    quarter: bool = False,
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

    z_np = _contour(num_freqs, quarter, radius)
    z_points = torch.tensor(z_np, dtype=torch.complex128, device=device)  # (M,)
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
    quarter: bool = False,
) -> np.ndarray:
    """Compute the minimum column perturbation norm required to destabilize the system.

    For each channel k, finds the smallest REAL column perturbation Delta_k such that
    A + Delta_k e_k^T has an eigenvalue on the contour |lambda| = radius. Writing
    a + jb = e_k^T (z I - A)^(-1) at z = radius * e^(j*theta), the determinant lemma
    turns that into two real constraints, a.Delta = 1 and b.Delta = 0, whose
    minimum-norm solution has

        ||Delta_k(theta)|| = sqrt( (b.b) / ((a.a)(b.b) - (a.b)^2) )

    and ||Delta_k|| is the minimum over theta, taken over a contour that excludes real
    z -- see `_contour`. Restricting Delta to the reals matters: the complex
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

    z_points = _contour(num_freqs, quarter, radius)

    # One Schur factorization per matrix, reused across frequencies: with A = Q T Q^H,
    # (zI - A)^-1 = Q (zI - T)^-1 Q^H is a triangular solve plus a matmul. Both are
    # BLAS3, where a complex LU per frequency is not -- ~65x faster here, and unitary
    # so the resolvent's conditioning is untouched.
    T_mat, Q = scipy.linalg.schur(A, output="complex")
    QH = Q.conj().T
    I_mat = np.eye(n_channels, dtype=np.complex128)

    perturbation_norms = np.full(n_channels, np.inf)
    for z in z_points:
        try:
            M_inv = Q @ scipy.linalg.solve_triangular(z * I_mat - T_mat, QH)
        except (scipy.linalg.LinAlgError, ValueError):
            continue  # z sits on an eigenvalue; neighbouring frequencies cover it

        a, b = M_inv.real, M_inv.imag
        aa = np.einsum("kn,kn->k", a, a)
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
    num_freqs: int | None = None,
    l2_reg: float = 0.3,
    method: str = "extended",
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
        Ridge regularizer for `fit_ltv_model`'s stability search.

    Returns
    -------
    fragility : np.ndarray
        Normalized fragility scores in [0, 1] for each channel, shape (N,).
        Higher score = more fragile (requires smaller perturbation to destabilize).
    r2 : float
        LTV model goodness-of-fit R^2.
    """
    quarter = method == "ezfragility"
    num_freqs = (100 if quarter else 16) if num_freqs is None else num_freqs
    if quarter:
        A, r2, _ = fit_ltv_ez(standardize_ieeg(X_win))
    else:
        A, r2 = fit_ltv_model(X_win, l2_reg=l2_reg)
    deltas = compute_min_perturbations(A, radius=radius, num_freqs=num_freqs, quarter=quarter)

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
    num_freqs: int | None = None,
    l2_reg: float = 0.3,
    method: str = "extended",
    highpass_hz: float | str | None = "auto",
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
        Ridge regularizer for `fit_ltv_model`'s stability search.
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
    if method not in ("ezfragility", "extended"):
        raise ValueError(f"method must be 'ezfragility' or 'extended', got {method!r}")
    quarter = method == "ezfragility"
    if num_freqs is None:
        num_freqs = 100 if quarter else 16

    # Unfiltered, near-unit-root drift dominates the fit and the shaft ranking ends up
    # tracking contact count (rank-vs-size rho 0.71, versus 0.08 filtered). "ezfragility"
    # stays unfiltered so it keeps reproducing R, which runs on raw data.
    if highpass_hz == "auto":
        highpass_hz = None if quarter else 0.5
    if highpass_hz:
        # Whole recording, before windowing: a 0.5 Hz filter is meaningless inside a
        # 250 ms window, which is why compute_window_fragility has no equivalent.
        data = bandpass(data, fs, float(highpass_hz), fs / 2.0, order=4, context="fragility")
    if quarter:
        data = standardize_ieeg(data)

    n_channels, total_samples = data.shape
    if ch_names is None:
        ch_names = [f"CH{i}" for i in range(n_channels)]
    elif len(ch_names) != n_channels:
        raise ValueError(f"Length of ch_names ({len(ch_names)}) != n_channels ({n_channels})")

    win_samples = int(round(win_s * fs))
    step_samples = int(round(step_s * fs))

    if win_samples > total_samples:
        raise ValueError(f"Recording duration ({total_samples} samples) is shorter than window ({win_samples} samples)")

    # Both estimators share this: Li et al.'s 250 ms window gives ~1.3 observations per
    # parameter on a modern implant, and fewer than 1 below ~512 Hz. Lengthening the
    # window does not fix it -- 61x more observations still left 63% of windows unstable,
    # because the process has a unit root (see docs/plans/next_steps.md); the high-pass is
    # the lever. Reported so a run's numbers can be read with that in mind.
    if win_samples - 1 <= n_channels:
        warnings.warn(
            f"Window has {win_samples - 1} regressors for {n_channels} channels: the LTV fit "
            f"is underdetermined and rests on the ridge. Raising win_s trades time resolution "
            f"for conditioning without fixing the near-unit root.",
            stacklevel=2,
        )

    starts = np.arange(0, total_samples - win_samples + 1, step_samples)
    n_windows = len(starts)

    start_times = (starts / fs) - (onset_s if onset_s is not None else 0.0)

    # 1. Fit LTV system models across all windows
    A_batch = np.zeros((n_windows, n_channels, n_channels), dtype=np.float64)
    r2_vector = np.zeros(n_windows, dtype=np.float64)

    lambdas = np.zeros(n_windows, dtype=np.float64)
    for w_idx, start_idx in enumerate(starts):
        X_win = data[:, start_idx : start_idx + win_samples]
        if quarter:
            A, r2, lambdas[w_idx] = fit_ltv_ez(X_win)
        else:
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
                quarter=quarter,
            )
        except Exception as exc:
            warnings.warn(f"GPU fragility failed ({exc!r}); falling back to CPU.", stacklevel=2)
            frag_matrix = None

    if frag_matrix is None:
        # Fallback to pure NumPy LAPACK window-by-window
        frag_matrix = np.zeros((n_channels, n_windows), dtype=np.float64)
        for w_idx in range(n_windows):
            deltas = compute_min_perturbations(
                A_batch[w_idx], radius=radius, num_freqs=num_freqs, quarter=quarter)
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
        "lambdas": lambdas,
        "method": method,
        "highpass_hz": highpass_hz,
        "fs": fs,
        "win_s": win_s,
        "step_s": step_s,
    }


def save_fragility_result(edf_filename, result, suffix=""):
    """Persist a fragility result next to the edf in a FRAGdets/ folder, mirroring
    where ei.py saves EIdets/ and hfo.py HFOdets/.

    `result` is a `compute_fragility_pipeline` return value. Scalars go in as one
    JSON blob rather than separate arrays so adding a field needs no reader change.

    `suffix` distinguishes runs on the same recording: one clip can hold several
    seizures, and each gets its own 30s window, so a single `<stem>_frag.npz`
    would let the second overwrite the first.
    """
    ch_names = list(result["channel_scores"])
    duplicates = {n for n in ch_names if ch_names.count(n) > 1}
    if duplicates:
        raise ValueError(f"fragility result: duplicate channel names {sorted(duplicates)}")
    results_dir = os.path.join(os.path.dirname(os.path.abspath(edf_filename)), "FRAGdets")
    os.makedirs(results_dir, exist_ok=True)
    file_pre_ext = os.path.basename(edf_filename).split(".")[0]
    out_path = os.path.join(results_dir, file_pre_ext + "_frag" + suffix + ".npz")
    meta = {
        "median_r2": float(result["median_r2"]),
        "method": result["method"],
        "highpass_hz": result["highpass_hz"],
        "fs": float(result["fs"]),
        "win_s": float(result["win_s"]),
        "step_s": float(result["step_s"]),
    }
    np.savez(
        out_path,
        fragility_matrix=np.asarray(result["fragility_matrix"], dtype=float),
        r2_per_window=np.asarray(result["r2_per_window"], dtype=float),
        start_times=np.asarray(result["start_times"], dtype=float),
        chn_names=np.array(ch_names),
        channel_scores=np.array([result["channel_scores"][n] for n in ch_names], dtype=float),
        meta=np.array(json.dumps(meta)),
    )
    return out_path


def load_fragility_result(path):
    data = np.load(path, allow_pickle=True)
    meta = json.loads(str(data["meta"])) if "meta" in data.files else {}
    chn_names = [str(n) for n in data["chn_names"]]
    scores = data["channel_scores"].tolist()
    channel_scores = dict(zip(chn_names, scores))
    return {
        "chn_names": chn_names,
        "channel_scores": channel_scores,
        "ranked_channels": sorted(channel_scores.items(), key=lambda kv: kv[1], reverse=True),
        "fragility_matrix": data["fragility_matrix"].tolist(),
        "r2_per_window": data["r2_per_window"].tolist(),
        "start_times": data["start_times"].tolist(),
        **meta,
    }
