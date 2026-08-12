#!/usr/bin/env bash
# ==============================================================================
# BrainQuake v2 — Multi-Mode Dataset Verification Runner on OpenNeuro ds004100
#
# Executes verify_ds004100_full.py across all signal processing configurations:
# 1. EI Band Ratio (Bartolomei et al. 2008 sub-bands)
# 2. EI Broadband (High-Frequency Energy Ratio)
# 3. Interictal HFO Events
# 4. Fused (EI Band Ratio + Interictal HFO)
# 5. Fused (EI Broadband + Interictal HFO)
#
# All CSV and HTML report outputs are stored with unique, distinct filenames
# in the project output directory for comparison and analysis.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUTPUT_DIR="$PROJECT_ROOT/verification_results"

mkdir -p "$OUTPUT_DIR"

PYTHON_BIN="python3"
VERIFY_SCRIPT="$SCRIPT_DIR/verify_ds004100_full.py"

echo "=============================================================================="
echo "          BRAINQUAKE v2 — MULTI-MODE VERIFICATION BENCHMARK SUITE             "
echo "=============================================================================="
echo "Timestamp    : $(date)"
echo "Output Dir   : $OUTPUT_DIR"
echo "Verify Script: $VERIFY_SCRIPT"
echo "=============================================================================="
echo ""

run_benchmark() {
    local label="$1"
    local mode="$2"
    local ei_method="$3"
    local csv_out="$OUTPUT_DIR/ds004100_${label}.csv"
    local html_out="$OUTPUT_DIR/ds004100_${label}.html"
    local start_t=$(date +%s)

    echo "------------------------------------------------------------------------------"
    echo "▶ RUNNING CONFIGURATION: $label"
    echo "  Mode: $mode | EI Method: $ei_method"
    echo "  CSV Output : $csv_out"
    echo "  HTML Report: $html_out"
    echo "------------------------------------------------------------------------------"

    $PYTHON_BIN "$VERIFY_SCRIPT" \
        --mode "$mode" \
        --ei-method "$ei_method" \
        --output-csv "$csv_out" \
        --output-html "$html_out"

    local end_t=$(date +%s)
    local duration=$((end_t - start_t))
    echo "✔ COMPLETED $label in ${duration}s"
    echo ""
}

# 1. EI Band Ratio
run_benchmark "ei_band_ratio" "ei" "band_ratio"

# 2. EI Broadband
run_benchmark "ei_broadband" "ei" "broadband"

# 3. Interictal HFO
run_benchmark "hfo" "hfo" "band_ratio"

# 4. Fused (EI Band Ratio + HFO)
run_benchmark "fused_band_ratio" "fused" "band_ratio"

# 5. Fused (EI Broadband + HFO)
run_benchmark "fused_broadband" "fused" "broadband"

echo "=============================================================================="
echo "                      ALL VERIFICATION RUNS COMPLETE!                         "
echo "=============================================================================="
echo "Generated CSV and HTML reports in: $OUTPUT_DIR"
ls -lh "$OUTPUT_DIR"
echo "=============================================================================="
