# encoding=utf-8
r"""
Fuse the ictal module's Epileptogenicity Index (EI) and the inter-ictal
module's High-Frequency Events Index (HI) onto the reconstructed brain
surface with electrode contacts marked -- the "Figure 8C" result described
in the BrainQuake paper (Cai et al., Frontiers in Neuroinformatics) that the
bundled tutorial (BrainQuake_Tutorial.ipynb) stops short of producing.

This step is pure fusion + visualization. It does not run the EI or HI
computations itself -- it loads whatever the Ictal and Inter-ictal module
GUIs (client_ictal.py / client_inter.py) already computed and saved.

Both modules now require picking a subject dir before importing an edf --
they copy the edf into <subject_dir>/edf/ and save their results next to
that copy, under <subject_dir>/edf/EIdets/ and <subject_dir>/edf/HFOdets/
respectively. So with one ictal + one inter-ictal recording per subject (the
normal case), this script only needs --subject-dir: the electrode xyz, EI
result and HI result are all found automatically underneath it.

So the normal workflow is: run the Ictal module GUI, pick the subject dir,
import the ictal edf, click 'ei'; run the Inter-ictal module GUI, pick the
same subject dir, import the inter-ictal edf, click 'HFO detection'; then
run this script (or the 'SOZ Result' button on the main window) pointing at
that subject dir.

Run from inside the BrainQuake/ directory (same convention as client_main.py):

    python soz_result.py --subject-dir ..\data\S1 --out soz_result_S1

--subject-dir must contain surf/lh.pial and surf/rh.pial (the freesurfer
recon output copied alongside the BrainQuake dataset in this repo's data/
folder). --elec-xyz/--ei-result/--hi-result can still be passed explicitly
to override the automatic lookup.
"""
import argparse
import csv
import os

import nibabel as nib
import numpy as np


def default_elec_xyz_path(subject_dir):
    """The electrode module always saves chnXyzDict.npy under <subject_dir>/fslresults
    (savenpy() in utils/elec_utils.py is called with the CT dir, which lives there) --
    so it never needs to be picked separately from the subject dir."""
    return os.path.join(subject_dir, 'fslresults', 'chnXyzDict.npy')


def find_result_npz(subject_dir, subfolder, suffix):
    """Ictal/inter-ictal edf files get copied into <subject_dir>/edf/ on import (see
    client_ictal.py / client_inter.py's dialog_inputedfdata), and their EI/HI results are
    saved next to that copy under edf/EIdets or edf/HFOdets. With one ictal + one
    inter-ictal recording per subject (the normal case) there's exactly one match here,
    so the subject dir alone is enough -- no need to browse for the file separately."""
    result_dir = os.path.join(subject_dir, 'edf', subfolder)
    if not os.path.isdir(result_dir):
        raise FileNotFoundError(
            f'{result_dir} does not exist yet -- run the {"Ictal" if subfolder == "EIdets" else "Inter-ictal"} '
            f'module for this subject first (pick this subject dir there, import the edf, then compute).')
    matches = [f for f in os.listdir(result_dir) if f.endswith(suffix)]
    if len(matches) == 0:
        raise FileNotFoundError(f'No {suffix} files found in {result_dir}.')
    if len(matches) > 1:
        raise ValueError(f'Found {len(matches)} {suffix} files in {result_dir} ({matches}) -- '
                          f'expected exactly one recording of this type for this subject.')
    return os.path.join(result_dir, matches[0])


def load_contact_xyz(elec_xyz_path):
    elec_dict = np.load(elec_xyz_path, allow_pickle=True)[()]
    contact_xyz = {}
    for label, xyz in elec_dict.items():
        for i in range(xyz.shape[0]):
            contact_xyz[f"{label}{i + 1}"] = xyz[i]
    return contact_xyz


def load_ei_result(ei_result_path):
    """Load EI results saved by client_ictal.py's ei_computation_func (save_ei_result)."""
    data = np.load(ei_result_path, allow_pickle=True)
    chn_names = [str(n) for n in data['chn_names']]
    return dict(zip(chn_names, data['ei']))


def load_hi_result(hi_result_path):
    """Load HI results saved by utils/HI_apis.py's HI_count_highEvents_chns."""
    data = np.load(hi_result_path, allow_pickle=True)
    chn_names = [str(n) for n in data['file_chnsNames']]
    return dict(zip(chn_names, data['file_highEventsCount']))


def rank_pct(values):
    values = np.asarray(values, dtype=float)
    order = np.argsort(values)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(len(values))
    return ranks / max(len(values) - 1, 1)


def build_result_table(contact_xyz, ei_by_chan, hi_by_chan):
    names = sorted(contact_xyz.keys())
    ei_vals = np.array([ei_by_chan.get(n, np.nan) for n in names])
    hi_vals = np.array([hi_by_chan.get(n, np.nan) for n in names])

    ei_mask = ~np.isnan(ei_vals)
    hi_mask = ~np.isnan(hi_vals)
    ei_pct = np.full(len(names), np.nan)
    hi_pct = np.full(len(names), np.nan)
    if ei_mask.any():
        ei_pct[ei_mask] = rank_pct(ei_vals[ei_mask])
    if hi_mask.any():
        hi_pct[hi_mask] = rank_pct(hi_vals[hi_mask])

    stacked = np.vstack([ei_pct, hi_pct])
    valid_counts = np.sum(~np.isnan(stacked), axis=0)
    sums = np.nansum(stacked, axis=0)
    combined = np.divide(sums, valid_counts, out=np.zeros_like(sums), where=valid_counts > 0)

    ei_thresh = np.nanmean(ei_vals) + np.nanstd(ei_vals) if ei_mask.any() else np.inf
    hi_thresh = np.nanmean(hi_vals) + np.nanstd(hi_vals) if hi_mask.any() else np.inf
    suspect_ei = ei_vals > ei_thresh
    suspect_hi = hi_vals > hi_thresh

    rows = []
    for i, name in enumerate(names):
        rows.append({
            'contact': name,
            'x': contact_xyz[name][0], 'y': contact_xyz[name][1], 'z': contact_xyz[name][2],
            'ei': ei_vals[i], 'hi': hi_vals[i],
            'ei_percentile': ei_pct[i], 'hi_percentile': hi_pct[i],
            'combined_score': combined[i],
            'suspect_ei': bool(suspect_ei[i]), 'suspect_hi': bool(suspect_hi[i]),
        })
    rows.sort(key=lambda r: r['combined_score'], reverse=True)
    return rows


def resolve_out_prefix(subject_dir, out_prefix):
    """Results belong with the patient's own data, not wherever this script happened to be
    launched from. A bare prefix (e.g. 'soz_result') is placed inside subject_dir; an
    already-rooted path (absolute, or starting with ./ or ../) is left as given."""
    if os.path.isabs(out_prefix) or out_prefix.startswith('.'):
        return out_prefix
    return os.path.join(subject_dir, out_prefix)


def save_csv(rows, out_csv):
    with open(out_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_3d(subject_dir, rows, top_n, out_png, show):
    from mayavi import mlab

    verl, facel = nib.freesurfer.read_geometry(os.path.join(subject_dir, 'surf', 'lh.pial'))
    verr, facer = nib.freesurfer.read_geometry(os.path.join(subject_dir, 'surf', 'rh.pial'))
    all_ver = np.concatenate([verl, verr], axis=0)
    all_face = np.concatenate([facel, facer + verl.shape[0]], axis=0)

    xs = np.array([r['x'] for r in rows])
    ys = np.array([r['y'] for r in rows])
    zs = np.array([r['z'] for r in rows])
    scores = np.array([r['combined_score'] for r in rows])

    mlab.figure(bgcolor=(0.9, 0.9, 0.9), size=(1200, 1200))
    mesh = mlab.triangular_mesh(all_ver[:, 0], all_ver[:, 1], all_ver[:, 2], all_face,
                                 color=(1., 1., 1.), opacity=0.35, line_width=1.)
    mesh.actor.property.backface_culling = True

    # 'hot' runs to pure white at the top of the range, which vanishes against the pale
    # translucent brain and light background right where it matters most (highest-score
    # contacts). 'plasma' stays dark-to-bright without ever hitting white.
    pts = mlab.points3d(xs, ys, zs, scores, scale_mode='none', scale_factor=2.5,
                        colormap='plasma', vmin=0.0, vmax=1.0)
    mlab.colorbar(pts, title='SOZ suspicion score', orientation='vertical')

    for r in rows[:top_n]:
        mlab.text3d(r['x'] + 3, r['y'] + 3, r['z'] + 3, r['contact'], scale=1.8, color=(0, 0, 1))

    if out_png:
        mlab.savefig(out_png)
    if show:
        mlab.show()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--subject-dir', required=True, help='freesurfer recon dir containing surf/lh.pial, surf/rh.pial')
    p.add_argument('--elec-xyz', default=None,
                    help='path to chnXyzDict.npy; defaults to <subject-dir>/fslresults/chnXyzDict.npy')
    p.add_argument('--ei-result', default=None,
                    help='path to <ictal>_ei.npz; defaults to the single match under <subject-dir>/edf/EIdets/')
    p.add_argument('--hi-result', default=None,
                    help='path to <interictal>_events.npz; defaults to the single match under '
                         '<subject-dir>/edf/HFOdets/')
    p.add_argument('--top-n', type=int, default=10, help='number of highest-scoring contacts to label in the plot')
    p.add_argument('--out', default='soz_result',
                    help='output file prefix for <out>.csv / <out>.png; a bare name (default) is '
                         'placed inside --subject-dir, pass ./name or an absolute path to override')
    p.add_argument('--no-plot', action='store_true', help='skip the mayavi 3D render, only write the CSV')
    p.add_argument('--no-show', action='store_true', help='render/save the plot but do not open an interactive window')
    args = p.parse_args()

    elec_xyz_path = args.elec_xyz or default_elec_xyz_path(args.subject_dir)
    ei_result_path = args.ei_result or find_result_npz(args.subject_dir, 'EIdets', '_ei.npz')
    hi_result_path = args.hi_result or find_result_npz(args.subject_dir, 'HFOdets', '_events.npz')

    print(f'Loading electrode contact coordinates from {elec_xyz_path} ...')
    contact_xyz = load_contact_xyz(elec_xyz_path)

    print(f'Loading EI results from {ei_result_path} ...')
    ei_by_chan = load_ei_result(ei_result_path)

    print(f'Loading HI results from {hi_result_path} ...')
    hi_by_chan = load_hi_result(hi_result_path)

    rows = build_result_table(contact_xyz, ei_by_chan, hi_by_chan)

    out_prefix = resolve_out_prefix(args.subject_dir, args.out)
    out_csv = out_prefix + '.csv'
    save_csv(rows, out_csv)
    print(f'\nWrote {out_csv} ({len(rows)} contacts, ranked by combined score).')

    print(f'\nTop {args.top_n} suspect contacts:')
    print(f"{'contact':<10}{'EI':>10}{'HI':>10}{'combined':>12}{'suspect(EI/HI)':>18}")
    for r in rows[:args.top_n]:
        print(f"{r['contact']:<10}{r['ei']:>10.3f}{r['hi']:>10.0f}{r['combined_score']:>12.3f}"
              f"{str(r['suspect_ei'])+'/'+str(r['suspect_hi']):>18}")

    if not args.no_plot:
        out_png = out_prefix + '.png'
        print(f'\nRendering 3D result onto {args.subject_dir}/surf/{{lh,rh}}.pial ...')
        plot_3d(args.subject_dir, rows, args.top_n, out_png, show=not args.no_show)
        print(f'Saved {out_png}')


if __name__ == '__main__':
    main()
