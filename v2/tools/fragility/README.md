# fragility — neural fragility via EZFragility

Computes **neural fragility** (Li et al. 2021, *Nature Neuroscience*,
doi:10.1038/s41593-021-00901-w) over peri-ictal windows, using the
[EZFragility](https://cran.r-project.org/package=EZFragility) R package.

## Why this exists

Nothing here reimplements the method. That is the point. The project's
numeric-correctness strategy is cross-validation against **independent
implementations** rather than against the legacy app — see
[docs/project-direction.md](../../../docs/project-direction.md). EI and HFO are
computed by this repository's own ported code; fragility is computed by
somebody else's, from the same recording. Where the two agree, the agreement
means something.

This directory is the seam between them: a Python exporter that turns an EDF
window into plain files, and the R script that consumes them.

## Pipeline

```
recording.edf ──export_edf.py──▶ SZ1P.csv + SZ1P_ch.txt ──run_frag.R──▶ frag_scores.rds
                                                                        + shaft ranking
```

### 1. Export

```bash
pip install mne numpy

# onset taken straight from the EDF+ annotations
python export_edf.py recording.edf --onset "@^SZ 1P$" --label SZ1P -o outdir

# or as a plain number of seconds
python export_edf.py recording.edf --onset 120 --label SZ1P -o outdir

# what marks does this file actually carry?
python export_edf.py recording.edf --list-annotations

# several seizures across several clips in one run
python export_edf.py --manifest seizures.csv -o outdir
```

The manifest is `label,edf_path,onset` per line, `#` comments allowed, where
`onset` is either seconds or `@<regex>` matched against the annotations.
Relative EDF paths resolve against the manifest's own directory. See
[seizures.example.csv](seizures.example.csv).

`--onset "@REGEX"` requires **exactly one** matching annotation and fails
otherwise. A regex that also catches the `clinical onset` note a second later
would silently move t = 0, and every window index downstream with it.

### 2. Analyse

```r
install.packages(c("EZFragility", "Epoch"))
```

```bash
Rscript run_frag.R outdir                                  # every *_ch.txt found
Rscript run_frag.R outdir SZ1P SZ2P                        # named seizures only
Rscript run_frag.R outdir --onset-shafts=A,I --spread-shafts=N,P,G,L,K,Q,S
```

`--onset-shafts` / `--spread-shafts` only annotate the final table with the
clinical read for that case; they change nothing that is computed.

## File format

Two files per seizure, and the contract between them is positional:

| file | contents |
|---|---|
| `{label}.csv` | contacts × samples, no header, `%.4f`, comma-separated |
| `{label}_ch.txt` | one contact name per line, **same order as the CSV's rows** |

- **Units are microvolts.** MNE returns volts; the exporter scales by 1e6.
- **Common-average referenced** across the exported contacts, by default.
  `--no-car` turns it off.
- **Auxiliary channels are dropped**, by the same contact-naming rule the
  server uses (`REF1`, `DC01`, `EKG1`, `UNUSED248`, `MARK`, a bare `E` all
  appear in one Nihon Kohden export). This is not cosmetic: those traces would
  be ranked as if they were contacts, and the DC inputs are stored in **mV**
  and the mark word carries **no unit at all**, so they arrive 10³–10⁶ times
  larger than a microvolt contact and would dominate the common average
  outright.
- The window is **inclusive of both endpoints**: `(pre + post) * fs + 1`
  samples. `run_frag.R` rebuilds its time axis as `(seq_len(ncol) - 1)/fs - PRE`,
  which puts the onset at exactly `t = 0`.
- 4 decimal places is below the amplifier's 0.098 µV/LSB step, so the rounding
  is lossless in practice.

The contact-naming rule is duplicated here on purpose — this script needs only
mne + numpy, while importing the server's copy would pull in pydantic-settings
and SQLAlchemy. The canonical definition is `find_non_seeg_channels` in
[v2/server/app/services/edf_common.py](../../server/app/services/edf_common.py).
Change both.

## Parameters

Set at the top of `run_frag.R`:

| name | value | meaning |
|---|---|---|
| `FS` | 1000 | Hz; must match the exported recording |
| `PRE` | 20 | s of pre-ictal signal in the window; defines `t = 0` |
| `WIN` | 250 | samples per fragility window (250 ms at 1 kHz) |
| `STEP` | 125 | samples between windows |
| `ICTAL_END` | 5 | scores average the windows starting in `[0, 5]` s |
| `TOP_N` | 20 | contacts per seizure that vote for their shaft |

`WIN`/`STEP` follow the package's own reference example. Note that a 250-sample
window fits a 184×184 transition matrix from 249 transitions — barely
overdetermined. `calcAdjFrag` reports a per-window R², and the script prints its
median per seizure; a low median means the ranking is describing noise rather
than dynamics, so check it before reading anything into the output.

## A bug worth knowing about

The original version of this script aggregated contacts to shafts with:

```r
shaft <- function(x) sub("^([A-Za-z]+'?)[0-9]+$", "\1", x)   # WRONG
```

R reads `"\1"` as the **octal escape for character code 1**, not as a regex
backreference — that needs `"\\1"`. Verified on R 4.6.1:

```
as written:   "\001" "\001" "\001" "\001" "\001"   → 1 distinct group
corrected:    "G"    "G'"   "A"    "X'"   "P"      → 5 distinct groups
```

Every contact collapsed into a single bucket, so the `=== NEURAL FRAGILITY:
shaft ranking ===` table was meaningless, and `ord[1:12]` indexed past the end
of a length-1 vector. **Any shaft ranking produced before this was fixed should
be discarded.** The per-seizure `top 10:` lines were never affected — they use
the contact names directly and never call `shaft()`.

## Verification

`export_edf.py` reproduces a previously exported window **bit-for-bit**:
re-exporting the first seizure from its source EDF, via the annotation lookup
rather than a hardcoded onset, gives `max abs diff 0.0` over 184 × 30001 values
and an identical channel-name file.

That checks the export path only. Nothing here verifies EZFragility itself,
which is the whole reason it is used.
