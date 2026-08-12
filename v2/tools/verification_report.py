"""Verification Report Generator.

Generates responsive, dark-themed HTML benchmark reports for BrainQuake dataset verification.
"""
import time


def generate_html_report(results, summary, html_filepath, mode="fused", ei_method="band_ratio"):
    """Generate a responsive HTML dashboard report."""
    table_rows = []
    for r in results:
        if r["status"] == "SUCCESS":
            rec_pct = f"{r['soz_recall']:.0%}"
            if r["gt_soz_count"] > 0:
                if r["soz_recall"] >= 0.5:
                    badge_cls = "badge-success"
                elif r["soz_recall"] > 0:
                    badge_cls = "badge-warning"
                else:
                    badge_cls = "badge-danger"
            else:
                badge_cls = "badge-secondary"
                rec_pct = "N/A (No GT)"

            res_conc_pct = f"{r['resect_concordance']:.0%}"

            pred_pairs = []
            pred_list = r["predicted_top_soz"].split(", ") if r["predicted_top_soz"] else []
            ei_list = r["predicted_top_ei"].split(", ") if r["predicted_top_ei"] else []
            for p_ch, p_ei in zip(pred_list, ei_list):
                if p_ch in r["soz_hits"].split(", "):
                    pred_pairs.append(f"<span class='hit-soz' title='Ground Truth SOZ Hit'>{p_ch} ({p_ei}) ★</span>")
                elif p_ch in r["resect_hits"].split(", "):
                    pred_pairs.append(f"<span class='hit-resect' title='Resected Zone Match'>{p_ch} ({p_ei})</span>")
                else:
                    pred_pairs.append(f"<span>{p_ch} ({p_ei})</span>")

            pred_html = ", ".join(pred_pairs)

            row_html = f"""
            <tr>
                <td><strong>{r['subject']}</strong></td>
                <td><span class="tag">{r['run_label']}</span></td>
                <td>{r['t_onset_sec']} s</td>
                <td><code class="gt-code">{r['gt_soz_channels'] if r['gt_soz_channels'] else 'None'}</code></td>
                <td><div class="pred-container">{pred_html}</div></td>
                <td><span class="badge {badge_cls}">{rec_pct} ({r['soz_hit_count']}/{r['gt_soz_count']})</span></td>
                <td><span class="badge badge-info">{res_conc_pct} ({r['resect_hit_count']}/{len(pred_list)})</span></td>
                <td><span class="status-ok">✔ Evaluated</span></td>
            </tr>
            """
        else:
            row_html = f"""
            <tr class="row-skipped">
                <td><strong>{r['subject']}</strong></td>
                <td><span class="tag">{r['run_id']}</span></td>
                <td>-</td>
                <td>-</td>
                <td>-</td>
                <td>-</td>
                <td>-</td>
                <td><span class="status-err" title="{r['error_message']}">❌ {r['error_message']}</span></td>
            </tr>
            """
        table_rows.append(row_html)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BrainQuake v2 - ds004100 Verification Report ({mode.upper()})</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --border-color: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent-blue: #38bdf8;
            --accent-green: #4ade80;
            --accent-yellow: #facc15;
            --accent-red: #f87171;
            --accent-purple: #c084fc;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 30px;
            line-height: 1.5;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--border-color);
            padding-bottom: 20px;
            margin-bottom: 30px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 1.8rem;
            font-weight: 700;
            background: linear-gradient(to right, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .header .meta {{
            color: var(--text-muted);
            font-size: 0.9rem;
        }}
        .grid-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 35px;
        }}
        .card {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }}
        .card .title {{
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}
        .card .value {{
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--accent-blue);
        }}
        .card .value.green {{ color: var(--accent-green); }}
        .card .value.yellow {{ color: var(--accent-yellow); }}
        .card .value.purple {{ color: var(--accent-purple); }}

        .search-bar {{
            margin-bottom: 20px;
            display: flex;
            gap: 15px;
        }}
        .search-bar input {{
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            color: var(--text-main);
            padding: 10px 16px;
            border-radius: 8px;
            font-size: 0.95rem;
            width: 300px;
        }}

        table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            background-color: var(--card-bg);
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid var(--border-color);
        }}
        th, td {{
            padding: 14px 18px;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
            font-size: 0.92rem;
        }}
        th {{
            background-color: #090d16;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.05em;
        }}
        tr:last-child td {{
            border-bottom: none;
        }}
        tr:hover td {{
            background-color: #26334d;
        }}
        .tag {{
            background: #334155;
            color: #cbd5e1;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 0.82rem;
        }}
        .badge-success {{ background: rgba(74, 222, 128, 0.15); color: var(--accent-green); border: 1px solid rgba(74, 222, 128, 0.3); }}
        .badge-warning {{ background: rgba(250, 204, 21, 0.15); color: var(--accent-yellow); border: 1px solid rgba(250, 204, 21, 0.3); }}
        .badge-danger {{ background: rgba(248, 113, 113, 0.15); color: var(--accent-red); border: 1px solid rgba(248, 113, 113, 0.3); }}
        .badge-info {{ background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); border: 1px solid rgba(56, 189, 248, 0.3); }}
        .badge-secondary {{ background: #334155; color: #94a3b8; }}

        .gt-code {{
            background: #0f172a;
            color: #38bdf8;
            padding: 4px 8px;
            border-radius: 6px;
            font-family: monospace;
        }}
        .pred-container {{
            font-family: monospace;
            font-size: 0.88rem;
        }}
        .hit-soz {{
            color: #4ade80;
            font-weight: 700;
            background: rgba(74, 222, 128, 0.15);
            padding: 2px 4px;
            border-radius: 4px;
        }}
        .hit-resect {{
            color: #38bdf8;
            background: rgba(56, 189, 248, 0.1);
            padding: 2px 4px;
            border-radius: 4px;
        }}
        .status-ok {{ color: var(--accent-green); font-weight: 600; }}
        .status-err {{ color: var(--text-muted); font-size: 0.85rem; }}
        .row-skipped td {{ opacity: 0.55; }}

        .footer {{
            margin-top: 40px;
            text-align: center;
            color: var(--text-muted);
            font-size: 0.85rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>BrainQuake v2 — Verification Report ({mode.upper()})</h1>
                <div style="color: var(--text-muted); margin-top: 4px;">OpenNeuro Dataset: ds004100 (HUP iEEG Epilepsy Dataset)</div>
            </div>
            <div class="meta">
                Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}<br>
                Engine: Mode: <strong>{mode}</strong> | EI Method: <strong>{ei_method}</strong>
            </div>
        </div>

        <div class="grid-stats">
            <div class="card">
                <div class="title">Total Subjects</div>
                <div class="value">{summary['total_subjects']}</div>
            </div>
            <div class="card">
                <div class="title">Downloaded Subjects</div>
                <div class="value purple">{summary['downloaded_subjects']}</div>
            </div>
            <div class="card">
                <div class="title">Evaluated Runs</div>
                <div class="value">{summary['evaluated_runs']}</div>
            </div>
            <div class="card">
                <div class="title">Mean SOZ Recall @ K</div>
                <div class="value green">{summary['mean_soz_recall']:.1%}</div>
            </div>
            <div class="card">
                <div class="title">Resection Concordance</div>
                <div class="value yellow">{summary['mean_resect_concordance']:.1%}</div>
            </div>
        </div>

        <div class="search-bar">
            <input type="text" id="searchInput" onkeyup="filterTable()" placeholder="Search Subject or Channel...">
        </div>

        <table id="resultsTable">
            <thead>
                <tr>
                    <th>Subject</th>
                    <th>Run</th>
                    <th>Onset Time</th>
                    <th>Ground-Truth SOZ</th>
                    <th>Predicted Top SOZ (Score)</th>
                    <th>SOZ Recall @ K</th>
                    <th>Resection Concordance</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {''.join(table_rows)}
            </tbody>
        </table>

        <div class="footer">
            BrainQuake v2 Verification Suite • Multi-Modal Signal Processing Engine
        </div>
    </div>

    <script>
        function filterTable() {{
            var input, filter, table, tr, td, i, j, txtValue;
            input = document.getElementById("searchInput");
            filter = input.value.toUpperCase();
            table = document.getElementById("resultsTable");
            tr = table.getElementsByTagName("tr");
            for (i = 1; i < tr.length; i++) {{
                var match = false;
                td = tr[i].getElementsByTagName("td");
                for (j = 0; j < td.length; j++) {{
                    if (td[j]) {{
                        txtValue = td[j].textContent || td[j].innerText;
                        if (txtValue.toUpperCase().indexOf(filter) > -1) {{
                            match = true;
                            break;
                        }}
                    }}
                }}
                tr[i].style.display = match ? "" : "none";
            }}
        }}
    </script>
</body>
</html>
"""
    with open(html_filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
