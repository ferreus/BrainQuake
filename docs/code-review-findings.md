# v2 code review findings

Running log for the module-by-module review described in
[cleanup-plan.md](cleanup-plan.md). One row per finding.

**Severity**: `high` = can silently corrupt results; `medium` = fragile,
unclear, or wasteful; `low` = style/robustness.

**Status**: `fixed` = corrected in the review commit; `marked` = left in place
with a `FIXME`/`NOTE` in the code because changing it changes scientific output
and needs a deliberate decision; `open` = neither, still to triage.

A note on the split: this review deliberately does **not** silently change
numeric behaviour. Anything that would move an EI value is marked, not fixed,
so the change can be made consciously and re-verified. Bugs, crashes, waste and
missing diagnostics are fixed.

---

## services/ictal.py + services/signal_filters.py

Reviewed 2026-08-09. Ported from `client_ictal.py` at tag `legacy-final`.

### high

| # | Finding | Where | Status |
|---|---|---|---|
| 1 | EI weights channels by `1/ordinal_rank`, not the published `1/(detection_delay + tau)`. With ~200 channels, contacts onsetting milliseconds apart are divided by 1, 2, 3 … 200 purely from sample-level noise in the threshold crossing. `test_ranking_of_a_late_channel_comes_entirely_from_the_rank_term` demonstrates the consequence: a channel whose measured energy is *indistinguishable from a non-seizing channel* still ranks 2nd, on rank alone. Most likely single cause of the ranking disagreeing with visual review. | `compute_ei_index` | marked |
| 2 | Non-SEEG channels (EKG, REF, DC, annotations) are neither excluded from the EI ranking nor from the common-average reference, so auxiliary traces are ranked as contacts *and* subtracted into every real channel. A `REF2` channel placed 6th in the Bella clip-17 ranking. | `run_ei_compute_job`, `filter_for_display` | marked (now detected + warned) |
| 3 | Onset threshold is `baseline_max + 20*sigma`. One artifact anywhere in the baseline raises a channel's bar out of reach for the entire seizure. `test_baseline_artifact_hides_a_real_onset` pins this: a single-sample pop suppresses a channel that is otherwise detected. | `determine_threshold_onset` | marked |
| 4 | EI is divided by its own maximum, so the top channel is always exactly 1.0 regardless of how unremarkable it is. Destroys the literature's absolute `EI > 0.3` criterion and makes values incomparable across seizures — which blocks the cross-seizure aggregation that fragility analysis relies on. | `compute_ei_index` | marked |
| 5 | The energy term is integrated in a fixed 0.25 s window anchored to the *earliest* channel's onset, the same window for every channel, rather than each channel's own detection time as published. | `compute_ei_index` | marked |
| 6 | Default mains frequency is 50 Hz, which is wrong for this project's own data (Bella: 60 Hz). Getting it wrong is silent — it notches clean signal and leaves the real interference. The justification was legacy bit-parity, now retired. | `DEFAULT_MAINS_FREQ` | marked |

### medium

| # | Finding | Where | Status |
|---|---|---|---|
| 7 | `convolve2d(..., 'same')` zero-pads both ends, so the first/last 0.25 s of each window has artificially suppressed energy — and onset detection scans the target window from sample 0, i.e. straight through the attenuated region. | `compute_hfer` | marked |
| 8 | Whole recording was loaded (`preload=True`) and filtered in full when only two windows are used. Bella's 1447 s × 203 ch × 1 kHz clip is ~2.3 GB, which is why analysis had to be cropped to 200 s. Now loads only the needed span plus 10 s of filter runway. | `run_ei_compute_job` | **fixed** |
| 9 | Window validation ran *after* the expensive load and filter. Now validated against the header before any samples are read. | `run_ei_compute_job` | **fixed** |
| 10 | No amplifier-saturation screening, though all 203 channels rail from ~240 s in Bella clip 17. Clipped signal is flat-topped, so its windowed energy is meaningless. Added `find_saturated_channels`, reported as a warning. | `run_ei_compute_job` | **fixed** |
| 11 | Channels with zero baseline energy produced `inf`/`nan` silently, zeroed much later. Now named in the job log. | `compute_hfer` | **fixed** |
| 12 | `KMeans` ran with sklearn's default `n_init`, which is version-dependent (`'auto'` = 1 on sklearn ≥1.4, 10 before) and unseeded, so results changed between runs and between environments. Pinned to `n_init=10, random_state=0`. **This is a deliberate behaviour change** — on the installed sklearn 1.9 it moves from 1 initialisation to 10. Affects nothing reachable today (see #16). | `choose_kmeans_k`, `compute_full_band` | **fixed** |
| 13 | Channels that never cross threshold all tie at `n_samples`; `argsort` then breaks the tie by channel index, so file order silently decides their relative EI. Now logged. | `determine_threshold_onset` | marked (now logged) |

### low

| # | Finding | Where | Status |
|---|---|---|---|
| 14 | `f_cut = f[:freq_range]` sliced a frequency array by a value in **Hz** as if it were a bin count. Latent — every caller discarded `f`. | `cal_specs_matrix` | **fixed** |
| 15 | `np.where(pre_labels == cluster_ind_ratio)` compared an (n,) label array against a (k,) array of chosen clusters; only coincidentally correct for k=1. Now `np.isin`. `None` (no dominant cluster) also fell through to an elementwise `== None`. | `compute_full_band` | **fixed** |
| 16 | `compute_full_band`, `choose_kmeans_k`, `find_ei_cluster_ratio`, `cal_specs_matrix`, `norm_specs`, `pad_zero`, `cal_zscore` — ~100 lines reachable from no router endpoint. Ported for parity with a baseline since retired. Candidate for deletion (recoverable at `legacy-final`). | `ictal.py` | open |
| 17 | The elbow search indexes the SSE *differences*, one shorter than `k_range`, so callers passing `range(2, 8)` can only ever get k=2..6 from the elbow path. Also raised `IndexError` when SSE improved monotonically; now falls back to the largest k. | `choose_kmeans_k` | **fixed** + marked |
| 18 | The `k` range and `find_ei_cluster_ratio`'s majority threshold are coupled and undocumented: more clusters fragment the top-10 EI channels, making "no dominant cluster" (a null result) more likely. | `find_ei_cluster_ratio` | marked |
| 19 | Dead `np.zeros([1, N])` initialisations immediately overwritten; scalar Python loop for nan/inf scrubbing; a `.astype(np.float32)` that downcast only the divisor, injecting float32 rounding into a float64 division. | `compute_ei_index`, `compute_hfer` | **fixed** |
| 20 | `cal_specs_matrix` grew its matrix one row at a time via `vstack` (O(n²) copying) and fell through to `NameError` for any `method` other than `'STFT'`. | `cal_specs_matrix` | **fixed** |

### Numerical impact of the "fixed" items

Items 8/9/10/11/14/15/17/19/20 are intended to be behaviour-preserving for the
EI numbers, with two known exceptions worth re-running before comparing against
any earlier result:

- **Item 19** removed a spurious float32 downcast of the divisor, so EI values
  shift in roughly the 7th significant figure.
- **Item 8** filters a cropped span rather than the whole recording. With 10 s
  of runway on each side the filter transient does not reach the analysis
  windows, but this is not bit-identical to filtering the full file.

Item 12 changes clustering behaviour, but only in code nothing calls.

### Tests added

`tests/test_ictal_numeric.py` — 27 synthetic-signal tests, no golden outputs:
EI must rank an injected early-onset channel first and follow it when moved;
dead channels must yield 0 rather than `nan`; notch must remove the frequency
it is given and *not* remove it when handed the wrong mains value; bandpass
must reject out-of-band; auxiliary channel names must be recognised and real
SEEG names (including primed, `X'12`) must not be; clipped channels detected,
flat channels not misreported as clipped.

Four are explicitly labelled CHARACTERISATION: they assert current, deviant
behaviour so that fixing findings 1, 3, 5 or 6 fails loudly instead of
silently changing every result.
