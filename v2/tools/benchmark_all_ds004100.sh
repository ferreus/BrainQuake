#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="/home/ferreus/dev/BrainQuake/v2/verification_results"
mkdir -p "$OUTPUT_DIR"

WORKERS="${1:-12}"

echo "======================================================================"
echo "   Running BrainQuake Comprehensive Benchmark on ds004100"
echo "   Parallel Workers: $WORKERS"
echo "======================================================================"

/home/ferreus/dev/BrainQuake/v2/server/.venv/bin/python "$SCRIPT_DIR/run_ds004100_comprehensive_benchmark.py" \
    --dataset "/media/data/eeg/ds004100" \
    --jobs "$WORKERS" \
    --out-csv "$OUTPUT_DIR/ds004100_comprehensive_benchmark.csv" \
    --out-html "$OUTPUT_DIR/ds004100_comprehensive_report.html" \
    --out-md "/home/ferreus/dev/BrainQuake/docs/ds004100_comprehensive_benchmark_report.md"

echo ""
echo "======================================================================"
echo "   Benchmark Finished! Report generated."
echo "======================================================================"
