"""
colorimeter_calibration.py

Processes calibration images for a Bayer-camera colorimeter and saves
all derived quantities to a single HDF5 file.

KEY DESIGN POINT
----------------
Images captured under different LED colours or at different LED temperatures
are NOT interchangeable.  Each (led_color, led_temperature) pair defines a
distinct colour space and is calibrated independently.  The HDF5 file stores
one calibration group per condition.

If a combined multi-LED measurement is desired, images from several LEDs
captured at the *same* temperature can be concatenated into a single
N-channel vector.  This is done explicitly in build_multiled_groups().

Assumes the following external callables are importable:
    get_calibration_data()  ->  list of tuples:
        (calibration_value, bayer_image_filename, LEDcolor,
         LEDtemperature, camera_exposure, camera_gain, timestamp)

    get_bayer_layers(bayer_image_filename)  ->  dict:
        {'B': ndarray, 'Gb': ndarray, 'Gr': ndarray, 'R': ndarray}

    rect_defs()  ->  dict:
        {'SAMPLE': rect, 'WHITE': rect, 'BLACK': rect,
         'YELLOW1': rect, 'YELLOW2': rect, 'BROWN': rect, 'RED': rect}
        where each rect is (row_start, row_end, col_start, col_end).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import h5py
import numpy as np
from sklearn.covariance import LedoitWolf
from scipy.interpolate import UnivariateSpline

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_LAYERS: tuple[str, ...] = ("B", "Gb", "Gr", "R")

# Reference patch names — every patch except SAMPLE and BLACK.
REFERENCE_PATCH_NAMES: tuple[str, ...] = ("WHITE", "YELLOW1", "YELLOW2", "BROWN", "RED")

# All patch names in a fixed order used for array indexing.
ALL_PATCH_NAMES: tuple[str, ...] = (
    "WHITE", "YELLOW1", "YELLOW2", "BROWN", "RED", "SAMPLE", "BLACK"
)

# SNR = white_mean / black_std.  Channels below this are excluded.
SNR_THRESHOLD: float = 10.0

# Pixel stride when building covariance observations (reduces spatial
# autocorrelation within a patch without losing much data).
COV_STRIDE: int = 4

# Ledoit-Wolf is always applied.  Set True to add a small diagonal ridge
# on top of it (rarely needed with 100x100 patches).
EXTRA_RIDGE: bool = False
RIDGE_ALPHA: float = 1e-3

HDF5_OUTPUT_PATH: str = "calibration.h5"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def extract_patch(layer: np.ndarray, rect: tuple) -> np.ndarray:
    """Return the 2-D float64 pixel array for one layer / rect combination."""
    r0, r1, c0, c1 = rect
    return layer[r0:r1, c0:c1].astype(np.float64)


def patch_stats(pixels: np.ndarray) -> tuple[float, float, int]:
    """Return (mean, std, n_pixels) for a 2-D patch array."""
    flat = pixels.ravel()
    return float(flat.mean()), float(flat.std(ddof=1)), int(flat.size)


def condition_key(led_color: str, led_temperature: float) -> str:
    """Canonical string key for a (led_color, led_temperature) condition."""
    return f"{led_color}_T{led_temperature:.1f}"


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class ImageRecord:
    """
    All information extracted from one calibration image.

    patch_stats[layer_name][patch_name] = (mean, std, n_pixels)
    pixels[layer_name][patch_name]      = 1-D float64 pixel array
    """
    calibration_value: float
    led_color: str
    led_temperature: float
    camera_exposure: float
    camera_gain: float
    timestamp: Any
    filename: str

    patch_stats: dict = field(default_factory=dict)
    pixels:      dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Step 1 — Load all calibration records
# ---------------------------------------------------------------------------

def load_all_records() -> list[ImageRecord]:
    """
    Iterate over every calibration image, extract per-layer per-patch
    statistics and pixel arrays.  One ImageRecord per image.
    """
    rects = rect_defs()
    raw_records = get_calibration_data()

    records: list[ImageRecord] = []

    for entry in raw_records:
        cal_value, filename, led_color, led_temp, exposure, gain, timestamp = entry

        log.info("Loading  %-40s  LED=%-6s  T=%5.1f  exp=%.5f",
                 filename, led_color, led_temp, exposure)

        layers = get_bayer_layers(filename)

        rec = ImageRecord(
            calibration_value=float(cal_value),
            led_color=led_color,
            led_temperature=float(led_temp),
            camera_exposure=float(exposure),
            camera_gain=float(gain),
            timestamp=timestamp,
            filename=filename,
        )

        for layer_name in ALL_LAYERS:
            layer_array = layers[layer_name]
            rec.patch_stats[layer_name] = {}
            rec.pixels[layer_name]      = {}

            for patch_name, rect in rects.items():
                px = extract_patch(layer_array, rect)
                mu, sigma, n = patch_stats(px)
                rec.patch_stats[layer_name][patch_name] = (mu, sigma, n)
                rec.pixels[layer_name][patch_name]      = px.ravel()

        records.append(rec)

    log.info("Loaded %d image records.", len(records))
    return records


# ---------------------------------------------------------------------------
# Step 2 — Group records by (led_color, led_temperature)
# ---------------------------------------------------------------------------

def group_by_condition(
    records: list[ImageRecord],
) -> dict[str, list[ImageRecord]]:
    """
    Return a dict keyed by condition_key() whose values are lists of
    ImageRecords sharing that (led_color, led_temperature) pair.

    Within each group the records differ only in calibration value.
    Their colour vectors are directly comparable because the illumination
    conditions (LED spectrum and intensity) are the same.

    Records from DIFFERENT groups are in DIFFERENT colour spaces and must
    never be mixed or averaged together.
    """
    groups: dict[str, list[ImageRecord]] = defaultdict(list)
    for rec in records:
        key = condition_key(rec.led_color, rec.led_temperature)
        groups[key].append(rec)

    for key, recs in groups.items():
        log.info("Condition %-20s :  %d images  (cal values: %s)",
                 key,
                 len(recs),
                 sorted({r.calibration_value for r in recs}))

    return dict(groups)


# ---------------------------------------------------------------------------
# Step 3 — Select responsive channels within a condition group
# ---------------------------------------------------------------------------

def select_channels(
    group: list[ImageRecord],
    snr_threshold: float = SNR_THRESHOLD,
) -> tuple[list[str], dict[str, float]]:
    """
    For each Bayer layer, compute SNR = white_mean / black_std averaged
    across all records in the group.

    The SNR is evaluated per-condition because the same layer may be
    responsive under one LED and near-zero under another.

    Returns:
        included    ordered list of layer names that pass the threshold
        snr_values  dict of all SNR values (for storage and diagnostics)
    """
    white_means: dict[str, list[float]] = defaultdict(list)
    black_stds:  dict[str, list[float]] = defaultdict(list)

    for rec in group:
        for layer in ALL_LAYERS:
            white_means[layer].append(rec.patch_stats[layer]["WHITE"][0])
            black_stds[layer].append( rec.patch_stats[layer]["BLACK"][1])

    snr_values: dict[str, float] = {}
    included:   list[str]        = []

    for layer in ALL_LAYERS:
        mean_white     = float(np.mean(white_means[layer]))
        mean_black_std = float(np.mean(black_stds[layer]))
        snr = mean_white / mean_black_std if mean_black_std > 0 else 0.0
        snr_values[layer] = snr

        if snr >= snr_threshold:
            included.append(layer)
            log.info("  Layer %-4s  SNR = %6.1f  INCLUDED", layer, snr)
        else:
            log.info("  Layer %-4s  SNR = %6.1f  excluded (< %.1f)",
                     layer, snr, snr_threshold)

    return included, snr_values


# ---------------------------------------------------------------------------
# Step 4 — Normalised colour vector for one patch in one record
# ---------------------------------------------------------------------------

def normalise_vector(
    rec: ImageRecord,
    patch_name: str,
    channels: list[str],
) -> np.ndarray:
    """
    Return the normalised channel vector for one patch in one image.

        x_norm[k] = raw_mean[k] / (exposure * white_mean[k])

    Both raw_mean and white_mean come from the SAME image, so the ratio
    is independent of LED drive current and exposure time.  It will still
    differ across LED colours and temperatures, which is why normalisation
    does not make cross-condition vectors comparable — it only makes vectors
    comparable within the same condition.
    """
    vec = np.empty(len(channels), dtype=np.float64)
    for k, layer in enumerate(channels):
        raw_mean   = rec.patch_stats[layer][patch_name][0]
        white_mean = rec.patch_stats[layer]["WHITE"][0]
        vec[k] = (raw_mean / (rec.camera_exposure * white_mean)
                  if white_mean > 0 else 0.0)
    return vec


# ---------------------------------------------------------------------------
# Step 5 — Reference colour vectors for a condition group
# ---------------------------------------------------------------------------

def build_reference_vectors(
    group: list[ImageRecord],
    channels: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """
    For each calibration value, compute the mean normalised SAMPLE vector
    across all records in the group with that calibration value.

    All records in the group share the same (led_color, led_temperature),
    so they are genuine replicates of the same physical measurement and
    averaging is appropriate.

    Returns:
        ref_vectors  shape (n_cal_values, n_channels)
        cal_values   shape (n_cal_values,)  sorted ascending
    """
    vectors_by_value: dict[float, list[np.ndarray]] = defaultdict(list)

    for rec in group:
        vec = normalise_vector(rec, "SAMPLE", channels)
        vectors_by_value[rec.calibration_value].append(vec)

    cal_values_sorted = sorted(vectors_by_value.keys())
    ref_vectors = np.array(
        [np.mean(vectors_by_value[v], axis=0) for v in cal_values_sorted]
    )
    return ref_vectors, np.array(cal_values_sorted)


# ---------------------------------------------------------------------------
# Step 6 — Pooled covariance estimation for a condition group
# ---------------------------------------------------------------------------

def estimate_covariance(
    group: list[ImageRecord],
    channels: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    """
    Pool per-pixel normalised observations from all REFERENCE patches across
    all records in the group, then apply Ledoit-Wolf shrinkage.

    Only REFERENCE_PATCH_NAMES are used (not SAMPLE, not BLACK):
    - SAMPLE is excluded because its colour varies by definition and would
      inflate the within-class variance estimate.
    - BLACK is excluded because it is near-zero and would distort the
      normalised values.

    Returns:
        S               (n_ch, n_ch) covariance
        S_inv           (n_ch, n_ch) inverse
        L               (n_ch, n_ch) lower Cholesky factor  S = L L^T
        condition_number
        lambda_lw       Ledoit-Wolf shrinkage coefficient
    """
    obs_rows: list[np.ndarray] = []

    for rec in group:
        white_means = {layer: rec.patch_stats[layer]["WHITE"][0]
                       for layer in channels}

        for patch_name in REFERENCE_PATCH_NAMES:
            n_px = len(rec.pixels[channels[0]][patch_name][::COV_STRIDE])
            patch_matrix = np.column_stack([
                rec.pixels[layer][patch_name][::COV_STRIDE]
                / (rec.camera_exposure * white_means[layer])
                if white_means[layer] > 0
                else np.zeros(n_px)
                for layer in channels
            ])
            obs_rows.append(patch_matrix)

    all_obs = np.vstack(obs_rows)
    log.info("  Covariance: %d observations x %d channels.",
             all_obs.shape[0], all_obs.shape[1])

    lw = LedoitWolf(assume_centered=False)
    lw.fit(all_obs)
    S         = lw.covariance_
    lambda_lw = float(lw.shrinkage_)

    if EXTRA_RIDGE:
        S += RIDGE_ALPHA * np.eye(S.shape[0])

    S_inv = np.linalg.inv(S)
    L     = np.linalg.cholesky(S)
    cond  = float(np.linalg.cond(S))

    log.info("  Covariance condition number: %.2e   LW shrinkage: %.4f",
             cond, lambda_lw)

    return S, S_inv, L, cond, lambda_lw


# ---------------------------------------------------------------------------
# Step 7 — PCA on the between-reference covariance
# ---------------------------------------------------------------------------

def compute_pca(
    ref_vectors: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Eigen-decompose the between-reference covariance matrix C.

    Returns:
        eigenvectors  (n_ch, n_ch)    columns sorted by descending eigenvalue
        eigenvalues   (n_ch,)         descending
        mean_vector   (n_ch,)         centroid of reference vectors
        projections   (n_cal_values,) projection of each reference onto v_1
    """
    mean_vec = ref_vectors.mean(axis=0)
    centered = ref_vectors - mean_vec

    C = (centered.T @ centered) / max(len(ref_vectors) - 1, 1)

    eigenvalues, eigenvectors = np.linalg.eigh(C)
    idx          = np.argsort(eigenvalues)[::-1]
    eigenvalues  = eigenvalues[idx]
    eigenvectors = eigenvectors[:, idx]

    projections = centered @ eigenvectors[:, 0]

    explained = (float(eigenvalues[0] / eigenvalues.sum())
                 if eigenvalues.sum() > 0 else 0.0)
    log.info("  PCA: leading axis explains %.1f%% of between-reference variance.",
             100.0 * explained)

    return eigenvectors, eigenvalues, mean_vec, projections


# ---------------------------------------------------------------------------
# Step 8 — Fit the concentration calibration curve
# ---------------------------------------------------------------------------

def fit_calibration_curve(
    projections: np.ndarray,
    cal_values: np.ndarray,
    spline_order: int = 3,
    smoothing: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """
    Fit a smoothing spline  c = f(s)  through the
    (principal-axis projection, calibration_value) pairs.

    Returns:
        knots        spline knot positions
        coefficients spline coefficients
        rmse
        residuals    c_i - f(s_i) in the original sort order of cal_values
    """
    sort_idx = np.argsort(projections)
    s_s = projections[sort_idx]
    c_s = cal_values[sort_idx]

    spl       = UnivariateSpline(s_s, c_s, k=spline_order, s=smoothing)
    c_fitted  = spl(s_s)
    res_s     = c_s - c_fitted
    rmse      = float(np.sqrt(np.mean(res_s ** 2)))

    restore   = np.argsort(sort_idx)
    residuals = res_s[restore]

    log.info("  Calibration curve RMSE: %.4f", rmse)
    return spl.get_knots(), spl.get_coeffs(), rmse, residuals


# ---------------------------------------------------------------------------
# Step 9 — Collect per-patch summary statistics for storage
# ---------------------------------------------------------------------------

def collect_patch_summary(
    group: list[ImageRecord],
    channels: list[str],
    cal_values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Average per-patch statistics over all records sharing each calibration
    value within this condition group.

    Returns arrays:
        means, stdevs, pixel_counts  shape (n_cal_values, n_channels, n_patches)
        white_means                  shape (n_cal_values, n_channels)
    """
    n_c  = len(cal_values)
    n_ch = len(channels)
    n_p  = len(ALL_PATCH_NAMES)

    means        = np.zeros((n_c, n_ch, n_p))
    stdevs       = np.zeros((n_c, n_ch, n_p))
    pixel_counts = np.zeros((n_c, n_ch, n_p), dtype=np.int64)
    white_means  = np.zeros((n_c, n_ch))
    counts       = np.zeros(n_c, dtype=int)

    val_to_idx = {v: i for i, v in enumerate(cal_values)}

    for rec in group:
        vi = val_to_idx[rec.calibration_value]
        counts[vi] += 1
        for ci, layer in enumerate(channels):
            white_means[vi, ci] += rec.patch_stats[layer]["WHITE"][0]
            for pi, pname in enumerate(ALL_PATCH_NAMES):
                mu, sigma, n = rec.patch_stats[layer][pname]
                means[vi, ci, pi]        += mu
                stdevs[vi, ci, pi]       += sigma
                pixel_counts[vi, ci, pi] += n

    for vi in range(n_c):
        if counts[vi] > 0:
            means[vi]        /= counts[vi]
            stdevs[vi]       /= counts[vi]
            white_means[vi]  /= counts[vi]
            pixel_counts[vi]  = (pixel_counts[vi] / counts[vi]).astype(np.int64)

    return means, stdevs, pixel_counts, white_means


# ---------------------------------------------------------------------------
# Step 10 — Write one condition group into an open HDF5 file
# ---------------------------------------------------------------------------

def write_condition_group(
    h5_parent: h5py.Group,
    condition_name: str,
    group: list[ImageRecord],
    channels: list[str],
    snr_values: dict[str, float],
    cal_values: np.ndarray,
    ref_vectors: np.ndarray,
    S: np.ndarray,
    S_inv: np.ndarray,
    L: np.ndarray,
    condition_number: float,
    lambda_lw: float,
    eigenvectors: np.ndarray,
    eigenvalues: np.ndarray,
    mean_vector: np.ndarray,
    projections: np.ndarray,
    spline_knots: np.ndarray,
    spline_coeffs: np.ndarray,
    rmse: float,
    residuals: np.ndarray,
    means: np.ndarray,
    stdevs: np.ndarray,
    pixel_counts: np.ndarray,
    white_means_by_calval: np.ndarray,
) -> None:
    """Write all calibration results for one condition into the HDF5 file."""

    cg = h5_parent.create_group(condition_name)
    sample_rec = group[0]

    cg.attrs["led_color"]         = sample_rec.led_color
    cg.attrs["led_temperature"]   = sample_rec.led_temperature
    cg.attrs["n_images"]          = len(group)
    cg.attrs["n_cal_values"]      = len(cal_values)
    cg.attrs["n_channels"]        = len(channels)
    cg.attrs["snr_threshold"]     = SNR_THRESHOLD
    cg.attrs["channels_included"] = str(channels)
    cg.attrs["snr_B"]             = snr_values.get("B",  0.0)
    cg.attrs["snr_Gb"]            = snr_values.get("Gb", 0.0)
    cg.attrs["snr_Gr"]            = snr_values.get("Gr", 0.0)
    cg.attrs["snr_R"]             = snr_values.get("R",  0.0)

    # Image-level records for this condition.
    img_g = cg.create_group("images")
    img_g.create_dataset("filenames",
                         data=np.array([r.filename       for r in group], dtype="S256"))
    img_g.create_dataset("timestamps",
                         data=np.array([str(r.timestamp) for r in group], dtype="S64"))
    img_g.create_dataset("exposures",
                         data=np.array([r.camera_exposure for r in group]))
    img_g.create_dataset("gains",
                         data=np.array([r.camera_gain     for r in group]))
    img_g.create_dataset("calibration_values", data=cal_values)

    # Per-patch statistics.
    pg = cg.create_group("patches")
    pg.attrs["axes"]          = "(cal_value_index, channel_index, patch_index)"
    pg.attrs["patch_names"]   = str(list(ALL_PATCH_NAMES))
    pg.attrs["channel_names"] = str(channels)
    pg.create_dataset("means",             data=means,                compression="gzip")
    pg.create_dataset("stdevs",            data=stdevs,               compression="gzip")
    pg.create_dataset("pixel_counts",      data=pixel_counts,         compression="gzip")
    pg.create_dataset("white_means",       data=white_means_by_calval,compression="gzip")
    pg.attrs["white_means_axes"] = "(cal_value_index, channel_index)"
    pg.create_dataset("reference_vectors", data=ref_vectors,          compression="gzip")
    pg.attrs["reference_vectors_axes"] = "(cal_value_index, channel_index)"

    # Covariance.
    sg = cg.create_group("covariance")
    sg.attrs["description"] = (
        "Pooled within-patch covariance (Ledoit-Wolf shrinkage). "
        "Observations are stride-subsampled per-pixel normalised vectors "
        "from REFERENCE patches only, within this condition."
    )
    sg.create_dataset("S",          data=S,     compression="gzip")
    sg.create_dataset("S_inv",      data=S_inv, compression="gzip")
    sg.create_dataset("L_cholesky", data=L,     compression="gzip")
    sg.attrs["condition_number"]    = condition_number
    sg.attrs["ledoit_wolf_lambda"]  = lambda_lw
    sg.attrs["extra_ridge_applied"] = EXTRA_RIDGE
    sg.attrs["ridge_alpha"]         = RIDGE_ALPHA if EXTRA_RIDGE else 0.0

    # PCA.
    eg = cg.create_group("pca")
    eg.attrs["description"] = (
        "Eigendecomposition of the between-reference covariance C. "
        "Eigenvectors stored as columns sorted by descending eigenvalue."
    )
    eg.create_dataset("eigenvectors", data=eigenvectors, compression="gzip")
    eg.create_dataset("eigenvalues",  data=eigenvalues)
    eg.create_dataset("mean_vector",  data=mean_vector)
    eg.create_dataset("projections",  data=projections)
    eg.attrs["explained_variance_ratio"] = (
        float(eigenvalues[0] / eigenvalues.sum()) if eigenvalues.sum() > 0 else 0.0
    )

    # Calibration curve.
    cc = cg.create_group("calibration_curve")
    cc.attrs["description"] = (
        "Smoothing spline c = f(s) mapping principal-axis projection s "
        "to chemical concentration c.  Valid only within [valid_s_min, valid_s_max]."
    )
    cc.create_dataset("calibration_values",  data=cal_values)
    cc.create_dataset("projections",         data=projections)
    cc.create_dataset("spline_knots",        data=spline_knots)
    cc.create_dataset("spline_coefficients", data=spline_coeffs)
    cc.create_dataset("residuals",           data=residuals)
    cc.attrs["rmse"]        = rmse
    cc.attrs["valid_s_min"] = float(projections.min())
    cc.attrs["valid_s_max"] = float(projections.max())
    cc.attrs["spline_order"]= 3


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("=== Colorimeter calibration pipeline start ===")

    # 1. Load every calibration image into a record.
    records = load_all_records()

    # 2. Partition records by (led_color, led_temperature).
    #    This is the central architectural decision: records in different
    #    groups are in incompatible colour spaces and are never combined.
    condition_groups = group_by_condition(records)

    with h5py.File(HDF5_OUTPUT_PATH, "w") as f:

        f.attrs["description"] = (
            "Colorimeter calibration file. "
            "One HDF5 group per (led_color, led_temperature) condition. "
            "Conditions are independent colour spaces; never mix them."
        )
        f.attrs["n_conditions"]  = len(condition_groups)
        f.attrs["snr_threshold"] = SNR_THRESHOLD
        f.attrs["all_conditions"]= str(sorted(condition_groups.keys()))

        for cond_key, group in sorted(condition_groups.items()):
            log.info("--- Processing condition: %s  (%d images) ---",
                     cond_key, len(group))

            # 3. Responsive channels for this LED colour and temperature.
            channels, snr_values = select_channels(group, SNR_THRESHOLD)
            if not channels:
                log.warning("  No channels passed SNR threshold — skipping %s.",
                            cond_key)
                continue

            # 4. Normalised reference colour vectors.
            #    Averaged only within this condition; never across conditions.
            ref_vectors, cal_values = build_reference_vectors(group, channels)

            # 5. Pooled within-patch covariance.
            S, S_inv, L, cond_num, lambda_lw = estimate_covariance(group, channels)

            # 6. PCA.
            eigenvectors, eigenvalues, mean_vector, projections = compute_pca(
                ref_vectors
            )

            # 7. Concentration calibration curve.
            spline_knots, spline_coeffs, rmse, residuals = fit_calibration_curve(
                projections, cal_values
            )

            # 8. Patch summary statistics.
            means, stdevs, pixel_counts, white_means_by_calval = (
                collect_patch_summary(group, channels, cal_values)
            )

            # 9. Write to HDF5.
            write_condition_group(
                h5_parent=f,
                condition_name=cond_key,
                group=group,
                channels=channels,
                snr_values=snr_values,
                cal_values=cal_values,
                ref_vectors=ref_vectors,
                S=S,
                S_inv=S_inv,
                L=L,
                condition_number=cond_num,
                lambda_lw=lambda_lw,
                eigenvectors=eigenvectors,
                eigenvalues=eigenvalues,
                mean_vector=mean_vector,
                projections=projections,
                spline_knots=spline_knots,
                spline_coeffs=spline_coeffs,
                rmse=rmse,
                residuals=residuals,
                means=means,
                stdevs=stdevs,
                pixel_counts=pixel_counts,
                white_means_by_calval=white_means_by_calval,
            )
            log.info("  Written condition group: %s", cond_key)

    log.info("HDF5 written to  %s", HDF5_OUTPUT_PATH)
    log.info("=== Calibration pipeline complete ===")


if __name__ == "__main__":
    main()