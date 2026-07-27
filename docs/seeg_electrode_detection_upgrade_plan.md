# SEEG Electrode Detection Analysis & Upgrade Plan

## Goal Description
Analyze the existing 5-year-old SEEG electrode detection pipeline (`run_elec_detect_job` in `v2/server/app/services/electrodes.py`), evaluate modern algorithms and state-of-the-art (SOTA) open-source approaches, and design an actionable upgrade plan to significantly improve electrode detection accuracy, performance, robustness, and developer maintainability in BrainQuake.

---

## Technical Analysis of Current Pipeline (`run_elec_detect_job`)

The current electrode detection implementation in `v2/server/app/services/electrodes.py` consists of three main stages:

```
[Post-CT + FreeSurfer Mask]
          │
          ▼
┌──────────────────────────────────┐
│  1. Preprocessing                │
│  - Erodes brain mask (scipy)     │
│  - Masks CT & applies HU thresh  │
└──────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────┐
│  2. Point Cloud & Hough 3D       │
│  - Dumps non-zero voxels to text │
│  - Calls external C++ binary     │
│    hough3dlines (-minvotes 5)    │
└──────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────┐
│  3. GMM Clustering               │
│  - Takes top K lines as means    │
│  - Fits GaussianMixture (sklearn)│
│  - Outputs Labels.npy            │
└──────────────────────────────────┘
```

### Key Flaws and Bottlenecks of the Legacy Approach

1. **Strict Straight-Line Assumption (`hough3dlines`)**:
   - `hough3dlines` finds pure geometric straight lines in 3D point cloud space.
   - **Clinical Reality**: Real SEEG depth electrodes bend slightly along their trajectory as they pass through complex brain tissues and skull entry points. Hough transform frequently misses bent electrodes or misattributes points from adjacent shafts.

2. **Extreme Sensitivity to Streaking & Metal Artifacts**:
   - Metallic SEEG contacts produce heavy CT blooming artifacts and high-HU streak lines.
   - Hough transforms and full-covariance Gaussian Mixture Models (GMM) treat streaking noise points as part of the electrode shaft, distorting cluster centroids and causing adjacent electrodes to merge into a single cluster.

3. **Manual Parameter Dependency ($K$ as strict input)**:
   - Requires the user to manually input the exact number of electrodes $K$ before running detection.
   - If `hough3dlines` fails to detect at least $K$ lines ($K_{check} < K$), the job throws a hard `RuntimeError` and terminates without providing usable intermediate results.

4. **External Binary Dependency & Disk File I/O Overhead**:
   - Requires compiling a vendored C++ binary `hough3dlines` and installing OpenMP (`libgomp1`) in Docker.
   - Dumps point clouds to a flat disk file (`3dPointClouds.dat`) and parses C++ stdout/text files via regex (`re.findall`).

5. **Hardcoded Array Dimensions**:
   - Hardcodes `Labels = np.zeros((256, 256, 256))`, assuming standard 1mm 256³ FreeSurfer volume space. If an input CT scan has non-standard dimensions or bounding boxes, index out-of-bounds or alignment mismatches occur.

---

## State-of-the-Art (SOTA) Algorithms & Alternatives (2021–2026)

Extensive clinical imaging research over the past 5 years has yielded significantly superior algorithms for SEEG electrode detection:

| Feature / Metric | Legacy (`hough3dlines` + GMM) | Modern CV (DBSCAN + RANSAC + PCA) | Deep Learning (3D U-Net / MONAI) |
| :--- | :--- | :--- | :--- |
| **Accuracy / Dice** | Moderate (~70-80% recall) | High (~90-95% recall) | Very High (>95% Dice, sub-mm error) |
| **Requires Manual $K$?** | **Yes** (Strict requirement) | **No** (Auto-detects $K$) | **No** (Auto-detects all electrodes) |
| **Handles Curved Shafts?**| **No** (Straight lines only) | **Yes** (Piecewise / polynomial RANSAC)| **Yes** (Arbitrary 3D shape) |
| **Streak Artifact Robustness**| Low (Distorted by streak lines) | High (DBSCAN filters sparse noise) | Highest (Learns spatial anatomy) |
| **Speed** | 10–30 seconds | 2–5 seconds | 5–15 seconds (GPU) / ~30s (CPU) |
| **Dependencies** | C++ binary + `scikit-learn` | Pure Python (`scipy`, `sklearn`) | PyTorch / MONAI + Pretrained weights |
| **Maintenance** | Medium (Vendored C++ code) | Low (Standard PyData stack) | Medium (Model weight management) |

### Option A: Deep Learning Pipeline (MONAI / 3D U-Net) — *Gold Standard Accuracy*
- **Examples in Literature**: *SlicerSEEG / SEEG_automatic_segmentation* (Rocio et al.), *seegloc* (GMI Lab).
- **How it Works**: A 3D U-Net convolutional neural network segments high-intensity voxels into electrode contact classes vs artifact/bone classes using spatial contextual features learned from hundreds of labeled clinical CTs.
- **Pros**: Insensitive to streaking artifacts; handles severe post-implant brain shift and electrode bending; zero manual parameter tuning required.
- **Cons**: Requires adding PyTorch/MONAI dependencies and downloading pretrained model weights (~50-200 MB).

### Option B: Modern Computer Vision Engine (DBSCAN + RANSAC + PCA) — *Best Lightweight Pure-Python Upgrade*
- **How it Works**:
  1. **Adaptive Thresholding**: Isolate metal voxels (Hounsfield Units HU > 2000–3000).
  2. **Density-Based Spatial Clustering (DBSCAN / HDBSCAN)**: Group voxels into clusters based on spatial proximity and density. Outliers (isolated streaking noise) are tagged as noise (`label = -1`) and dropped automatically.
  3. **PCA (Principal Component Analysis)**: Compute primary direction vector and shaft length for each cluster.
  4. **RANSAC 3D Curve/Line Fitting**: Fit linear or second-order polynomial trajectories through each cluster, filtering out skull entry bolt artifacts.
- **Pros**: Pure Python (no external C++ binaries); auto-detects number of electrodes $K$; extremely fast (2-5 sec); zero disk file I/O.

---

## User Review Required

> [!IMPORTANT]
> **Proposed Strategy**: We recommend adopting a **Dual-Engine Architecture** for BrainQuake:
> 1. **Default Modern CV Engine (DBSCAN + RANSAC + PCA)**: Built directly into `v2/server` using `scikit-learn` and `scipy`. Fast, lightweight, pure Python, auto-detects $K$, eliminates `hough3dlines` binary dependency.
> 2. **Legacy Compatibility Engine (`hough_gmm`)**: Maintained as an optional fallback parameter `method="hough_gmm"` to ensure zero breaking changes for existing test suites or workflows.
> 3. **Optional Deep Learning Engine (`monai_unet`)**: Pluggable inference runner when MONAI/PyTorch is present.

---

## Open Questions

> [!NOTE]
> 1. Should we set the new **DBSCAN + RANSAC** algorithm as the default detection engine for new jobs, while leaving `hough_gmm` available as a selectable option in job parameters?
> 2. Would you like us to implement automatic $K$ detection (where the user can leave $K$ blank or set to 0 to auto-detect electrode count), while still allowing explicit $K$ override when desired?

---

## Proposed Changes

### 1. Backend Core (`v2/server/app/services/electrodes.py`)

#### [MODIFY] `v2/server/app/services/electrodes.py`
- Add `detect_electrodes_dbscan_ransac(ct_data, affine, K=None, min_samples=10, eps=3.5)` implementing density clustering, PCA direction estimation, and RANSAC trajectory fitting.
- Update `generate_labels` to support engine selection (`method="dbscan_ransac"` vs `method="hough_gmm"`).
- Remove hardcoded `(256, 256, 256)` volume shapes; dynamically allocate label volumes based on the input CT shape (`np.zeros_like(data_ct, dtype=np.int32)`).
- Update `run_elec_detect_job` to pass optional `method` parameter and handle auto-detected $K$.

```python
# Conceptual snippet for DBSCAN + RANSAC algorithm
from sklearn.cluster import DBSCAN
from sklearn.decomposition import PCA
from sklearn.linear_model import RANSACRegressor

def detect_electrodes_dbscan_ransac(data_ct, threshold_val, min_cluster_size=50, eps=3.0):
    # 1. Extract 3D points above metal threshold
    xs, ys, zs = np.where(data_ct >= threshold_val)
    if len(xs) == 0:
        return np.zeros_like(data_ct), 0

    X = np.column_stack((xs, ys, zs))

    # 2. DBSCAN clustering to separate electrode shafts & reject noise
    db = DBSCAN(eps=eps, min_samples=15).fit(X)
    labels = db.labels_

    unique_labels = [l for l in set(labels) if l != -1]
    
    # 3. Fit RANSAC line/curve to each detected cluster & build label volume
    label_vol = np.zeros(data_ct.shape, dtype=np.int32)
    valid_k = 0
    
    for cluster_id in unique_labels:
        mask = (labels == cluster_id)
        pts = X[mask]
        if len(pts) < min_cluster_size:
            continue
            
        valid_k += 1
        label_vol[pts[:, 0], pts[:, 1], pts[:, 2]] = valid_k

    return label_vol, valid_k
```

#### [MODIFY] `v2/server/app/models/job.py` / API Schema
- Support `method` parameter (`dbscan_ransac`, `hough_gmm`) in `params_json` for `elec_detect` jobs.

---

## Verification Plan

### Automated Tests
1. **Unit Test for DBSCAN+RANSAC Detection**:
   - Run `pytest v2/server/tests/test_api.py` and dedicated electrode service unit tests.
   - Verify synthetic CT volume with metallic cylinder trajectories yields exact cluster count and label assignments.
2. **Regression Test for Legacy Hough+GMM**:
   - Ensure existing tests passing `method="hough_gmm"` continue to function identically.

### Manual & Integration Verification
1. Run `run_elec_detect_job` on test dataset volume, verify progress updates and artifact creation (`labels_npy`, `ct_intracranial_nii`).
2. Verify `GET /subjects/{id}/electrodes/labels-summary` returns accurate cluster centroids and voxel counts for the new algorithm.
3. Test `PUT /subjects/{id}/electrodes/labels` cluster commit and exclusion workflow.
