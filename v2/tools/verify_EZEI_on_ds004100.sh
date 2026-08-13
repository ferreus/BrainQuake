#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="/home/ferreus/dev/BrainQuake/v2/verification_results"
mkdir -p "$OUTPUT_DIR"

WORKERS="${1:-4}"

echo "======================================================================"
echo "   Running EZEI R Package Verification on OpenNeuro ds004100"
echo "   Parallel Workers: $WORKERS"
echo "======================================================================"

python3 "$SCRIPT_DIR/verify_ds004100_ezei.py" \
    --dataset-dir "/media/data/eeg/ds004100" \
    --output-csv "$OUTPUT_DIR/ds004100_ezei.csv" \
    --output-html "$OUTPUT_DIR/ds004100_ezei.html" \
    --workers "$WORKERS"

echo ""
echo "======================================================================"
echo "   EZEI Run Complete! Comparing Results vs BrainQuake EI Band Ratio"
echo "======================================================================"

python3 -c "
import csv
import os

def load_stats(csv_path):
    if not os.path.exists(csv_path):
        return None
    with open(csv_path, 'r', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    
    succ = [r for r in rows if r['status'] == 'SUCCESS']
    gt_soz = [r for r in succ if r['gt_soz_count'] and float(r['gt_soz_count']) > 0]
    gt_resect = [r for r in succ if r['gt_resect_count'] and float(r['gt_resect_count']) > 0]
    
    soz_recalls = [float(r['soz_recall']) for r in gt_soz if r['soz_recall'] != '']
    resect_concs = [float(r['resect_concordance']) for r in gt_resect if r['resect_concordance'] != '']
    soz_hits_gt1 = sum(1 for r in gt_soz if r['soz_hit_count'] and float(r['soz_hit_count']) > 0)
    resect_hits_gt1 = sum(1 for r in gt_resect if r['resect_hit_count'] and float(r['resect_hit_count']) > 0)
    
    return {
        'total': len(rows),
        'success': len(succ),
        'gt_soz_len': len(gt_soz),
        'mean_soz_recall': sum(soz_recalls)/len(soz_recalls) if soz_recalls else 0.0,
        'soz_hit_rate': soz_hits_gt1 / len(gt_soz) if gt_soz else 0.0,
        'mean_resect_conc': sum(resect_concs)/len(resect_concs) if resect_concs else 0.0,
        'resect_hit_rate': resect_hits_gt1 / len(gt_resect) if gt_resect else 0.0,
    }

ezei = load_stats('$OUTPUT_DIR/ds004100_ezei.csv')
bq_ei = load_stats('$OUTPUT_DIR/ds004100_ei_band_ratio.csv')

print('METRIC COMPARISON:')
print(f'{\"Metric\":<30} | {\"BrainQuake EI Band Ratio\":<25} | {\"EZEI R Package\":<20}')
print('-' * 82)
if bq_ei and ezei:
    print(f'{\"Evaluated Runs\":<30} | {bq_ei[\"success\"]:<25} | {ezei[\"success\"]:<20}')
    print(f'{\"Mean SOZ Recall\":<30} | {bq_ei[\"mean_soz_recall\"]*100:6.2f}%                    | {ezei[\"mean_soz_recall\"]*100:6.2f}%')
    print(f'{\"SOZ Top-4 Hit Rate (>=1 Hit)\":<30} | {bq_ei[\"soz_hit_rate\"]*100:6.2f}%                    | {ezei[\"soz_hit_rate\"]*100:6.2f}%')
    print(f'{\"Mean Resect Concordance\":<30} | {bq_ei[\"mean_resect_conc\"]*100:6.2f}%                    | {ezei[\"mean_resect_conc\"]*100:6.2f}%')
    print(f'{\"Resect Top-4 Hit Rate (>=1 Hit)\":<30} | {bq_ei[\"resect_hit_rate\"]*100:6.2f}%                    | {ezei[\"resect_hit_rate\"]*100:6.2f}%')
"
