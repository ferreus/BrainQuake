# Comprehensive Benchmark Report: PyFragility & ComputeEI vs R Packages (OpenNeuro ds004100)

**Dataset:** OpenNeuro `ds004100` (Intracranial EEG from focal epilepsy patients)  
**Evaluated Cohort:** 213 ictal seizure recordings across 57 subjects (208 runs with ground truth SOZ)  
**Benchmarked Implementations:**
1. **PyFragility (ezfragility port):** Python reproduction of Li et al. 2021
2. **PyFragility (extended):** BrainQuake scale-invariant LTV estimator + 0.5 Hz high-pass
3. **R EZFragility Package:** Reference R implementation (`calcAdjFrag`)
4. **BrainQuake ComputeEI (CAR):** Epileptogenicity Index under Common Average Reference
5. **BrainQuake ComputeEI (Bipolar):** Epileptogenicity Index under adjacent shaft Bipolar derivation
6. **R EZEI Package:** Reference Bartolomei et al. Multitaper EI implementation

Every figure below is computed from `ds004100_comprehensive_benchmark.csv` at report time.

---

## 1. Executive Summary & Question Answers

### Q1: Our PyFragility (`ezfragility` estimator) accuracy compared to R EZFragility
- **Parity ($n = 43$ of the 208 runs R completed -- the rest have R scores but no
  stored Python per-channel scores to correlate against):**
  - Mean Spearman rank correlation: $\rho = \mathbf{1.0000}$ across all contacts
  - Top-4 channel overlap: $\mathbf{100.0\%}$
  - SOZ recall @ K: 33.35% (Python) vs 33.35% (R)
  - SOZ Top-4 hit rate: 71.15% (Python) vs 71.15% (R)
- **Full dataset:** Python `ezfragility` completed all 208 runs at 33.35% mean SOZ
  recall and 71.15% Top-4 hit rate. R `EZFragility` also completed all 208 runs (median 278s per run).


### Q2: How our own estimator (`extended`) compares with the `ezfragility` estimator
- **SOZ localization across all 208 runs:** `extended` achieves
  **39.69%** SOZ recall and **75.96%**
  Top-4 hit rate, versus 33.35% and 71.15%
  for `ezfragility` (paired Wilcoxon on recall, $p = 8.57e-07$).
- **Resection overlap:** 29.75% vs 27.89%.
- **Rank agreement between the two estimators:** mean Spearman $\rho = 0.8773$.
- **Surgical outcome separation ($S$ vs $F$), Li et al. interpretability ratio $I$:**
  - `extended`: Cohen's $d = -0.347$, Mann-Whitney $p = 0.9701$
    ($I_{Success} = 1.180$ vs $I_{Failure} = 1.274$)
  - `ezfragility`: Cohen's $d = +0.215$, Mann-Whitney $p = 0.5841$
    ($I_{Success} = 1.091$ vs $I_{Failure} = 1.048$)
  - Neither estimator separates surgical successes from failures on this cohort. Localization
    accuracy and outcome prognosis are separate claims; only the former is supported here.
- **Speed:** 13.18 s mean latency vs
  61.60 s for `ezfragility`.

### Q3: `compute_ei` vs R `EZEI`
- **SEEG sub-cohort ($n = 138$):** BrainQuake EI (Bipolar) 35.95%
  SOZ recall vs 17.85% for R EZEI
  (+18.10 pp, paired Wilcoxon $p = 4.28e-10$).
- **Overall (208 runs):** 32.51% (Bipolar) and
  26.51% (CAR) vs 20.57% for R EZEI.
- **Speed:** 8.12 s per run vs 74.38 s
  for R EZEI (9.2$\times$).

---

## 2. Head-to-Head Comparison on the Runs R Completed ($n = 208$)

| Method / Package | Evaluated Runs | Spearman vs R ($\rho$) | Top-4 Overlap vs R | SOZ Recall @ K | Top-4 Hit Rate | Resection Concordance |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **PyFragility (ezfragility)** *(Python Port)* | 208 | **1.0000** (n=43) | **100.0%** | 33.35% | 71.15% | 27.89% |
| **R EZFragility Package** *(Reference R)* | 208 | — | — | 33.35% | 71.15% | 27.89% |
| **PyFragility (extended)** *(Ours)* | 208 | 0.8648 (n=43) | 61.0% | 39.69% | 75.96% | 29.75% |

`extended` deliberately differs from Li et al., so its two parity columns measure divergence
from R, not a target it is failing to hit. Its mean rank correlation with the `ezfragility`
port on this subset is $\rho = 0.8749$.
R completed every run, so this is the full cohort, not a subset.

---

## 3. Full Cohort Benchmark Table (All 208 Seizure Runs)

| Method / Implementation | Evaluated Runs | Mean Latency | SOZ Recall @ K | Top-4 Hit Rate ($\ge 1$ Hit) | Resection Concordance | Li et al. Ratio ($I$) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BrainQuake EI (Bipolar)** *(Ours)* | 208 | 8.12s | 32.51% | 59.13% | 34.28% | — |
| **BrainQuake EI (CAR)** | 208 | 9.36s | 26.51% | 59.62% | 28.95% | — |
| **R EZEI Package** | 208 | 74.38s | 20.57% | 44.23% | 21.94% | — |
| **PyFragility (ezfragility)** *(Python)* | 208 | 61.60s | 33.35% | 71.15% | 27.89% | 1.076 |
| **PyFragility (extended)** *(Python)* | 208 | 13.18s | 39.69% | 75.96% | 29.75% | 1.213 |
| **R EZFragility Package** | 208 | 384.66s | 33.35% | 71.15% | 27.89% | — |

---

## 4. Sub-Cohort Analysis (SEEG vs ECoG)

Mean SOZ recall @ K.

| Modality | Cohort Size | BrainQuake EI (Bipolar) | BrainQuake EI (CAR) | R EZEI | PyFragility (extended) | PyFragility (ezfragility) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **SEEG (Depths)** | 138 runs | 35.95% | 27.66% | 17.85% | 35.32% | 31.55% |
| **ECoG (Grids)** | 70 runs | 25.72% | 24.25% | 25.91% | 48.30% | 36.89% |

---

## 5. Execution Time & Performance Benchmark

| Pipeline | Mean Latency | Median Latency | 95th Percentile | Throughput |
| :--- | :---: | :---: | :---: | :---: |
| **BrainQuake EI (Bipolar)** | 8.122s | 5.099s | 26.384s | 7.4 runs/min |
| **BrainQuake EI (CAR)** | 9.359s | 5.850s | 30.088s | 6.4 runs/min |
| **R EZEI** | 74.379s | 76.034s | 98.829s | 0.8 runs/min |
| **PyFragility (extended)** | 13.180s | 10.395s | 30.838s | 4.6 runs/min |
| **PyFragility (ezfragility)** | 61.596s | 51.036s | 140.267s | 1.0 runs/min |
| **R EZFragility** *(completed runs only)* | 384.657s | 278.322s | 988.908s | 0.2 runs/min |

R EZFragility's timings cover only the 208 runs it finished; the 0 it abandoned
at the 300 s cap are excluded, so its true mean is higher than shown.
