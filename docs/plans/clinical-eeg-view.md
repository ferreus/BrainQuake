# Clinical EEG View — a separate tab

## Context

The goal was a clinical review screen modelled on `v2/tools/show_edf.py` (Nihon Kohden
sensitivity / time constant / high cut / page / clock). It was built by rewriting the *shared*
EEG viewer that the Ictal and Interictal analysis modules use — and that is the mistake.

The shared viewer is not just a display: its channel list feeds `remain_chns` for the EI and
HFO jobs, and its filter band seeds the HFO ripple band. Reworking it for clinical review
perturbed analysis inputs and produced live regressions:

- `IctalPage.tsx:43` / `InterictalPage.tsx:49` still derive `remainChannels` from
  `excludedChannels`, which nothing writes any more — **unchecking a channel removes it from
  the plot and leaves it in the job.**
- The interictal display default fell to 1–70 Hz and `HfoComputeForm.tsx:53-54` seeds the
  ripple band from it, so **HFO detection now defaults to a band below the ripples.**
- Canvas clicks are off by the 90px label gutter (`EegCanvas.tsx:193-198`), so **EI
  baseline/target picks land ~1s late** on a 10s window.

**The plan is therefore: revert the analysis viewer to HEAD, and build the clinical viewer as
its own tab with its own components and its own state.** Nothing in the clinical tab feeds a
computation. The analysis modules go back to being byte-identical to what produced the
existing results.

This also deletes the riskiest work from the earlier draft: because EI and HFO are untouched,
there is no need to widen their `reference`, add a montage to the HFO pipeline, or add a
contact projection to SOZ fusion (`load_hi_result` has none, so a bipolar HFO run would have
silently degraded the fused ranking to EI-only).

---

## Step 1 — Revert the analysis viewer

```bash
cd /home/ferreus/dev/BrainQuake
git checkout -- v2/web/src/components/eeg/EegCanvas.tsx \
                v2/web/src/components/eeg/EegChannelList.tsx \
                v2/web/src/components/eeg/EegToolbar.tsx \
                v2/web/src/components/eeg/useEegViewerState.ts \
                v2/web/src/features/ictal/IctalPage.tsx \
                v2/web/src/features/interictal/InterictalPage.tsx
```

That restores `excludedChannels` + Delete/Restore, the mode-specific filter defaults
(60–140 Hz ictal / 80–250 Hz interictal), the relative gain model and the unshifted click
mapping — closing all three regressions above by construction.

**Keep** the two server files and `endpoints.ts` as they stand in the working tree. Those
changes only *accept* `reference="none"` on the window endpoint and add the
`EdfDisplayReference` type; they are additive, the clinical view needs them, and they do not
touch EI or HFO.

**Not bundled here, deliberately:** the reverted canvas requests `reference="car"` computed
over just the ~20 visible channels, so traces change as you page — a real bug the working
tree had fixed by sending `"none"`. It is a display-only change (the canvas never feeds
`remain_chns`), but it alters what an operator sees while picking baseline/target, so it
deserves its own decision rather than riding along in this change.

## Step 2 — Server, additive only

### 2a. `filter_for_review` — `app/sigproc/filters.py`

**Do not touch `filter_for_display`.** It is shared with the EI numerics (`ei.py:426`), and
the mandate here is that computation results cannot move. Write the review filter standalone
and accept ~8 duplicated lines of CAR+notch, with a comment on each pointing at the other.
(The tidier move is extracting a shared `_reference_and_notch` prologue; it is a pure
refactor and the existing tests would gate it, but it puts a shared edit in the EI path for
no reviewer-visible gain. Overrule this if you'd rather have the single implementation.)

```python
def rc_highpass(data, fs, tc):
    """NK's TC filter: causal one-pole RC high-pass, -6 dB/oct, corner 1/(2*pi*tc)
    (0.1 s = 1.6 Hz). Not zero-phase: a 4-pole zero-phase high-pass at the same
    corner eats far more slow activity and the traces come out flat."""
    a = tc / (tc + 1.0 / fs)
    b, ac = np.array([a, -a]), np.array([1.0, -a])
    x2 = np.atleast_2d(np.asarray(data, dtype=float))
    zi = np.outer(x2[:, 0], lfilter_zi(b, ac))   # warm start: DC gain is 0, so a
    y, _ = lfilter(b, ac, x2, axis=-1, zi=zi)    # contact's offset makes no step
    return y.reshape(np.shape(data))


def filter_for_review(data, fs, tc=None, hicut=None,
                      mains_freq=DEFAULT_MAINS_FREQ, reference="car"):
    """Reference, mains notch, causal TC high-pass, then an independent high cut."""
```

- `tc=None` → low cut off; `hicut=None` → high cut off. **Both None still references and
  notches** — if all-off returned raw samples, switching the high cut off would make the CAR
  vanish and every trace jump. Disable the notch with `mains_freq=0` (`mains_harmonics`
  already returns empty there), not a new parameter.
- Do **not** call `clamp_band` — it requires `low < high` and cannot express a lone high cut.
  Use `max_band_high(fs)` and follow `show_edf.py:155-158`: a high cut at/above usable
  Nyquist is *disabled* with a warning, not clamped. The TC needs no clamping.
- High cut is `butter(4)` + `sosfiltfilt`, matching what MNE's browser applies.

### 2b. `tc` on the window endpoint — `app/services/edf.py`, `app/routers/edf.py`

One new param. `band_high` doubles as the high cut in both modes — the same physical
quantity — so `band_low` and `tc` are the two ways to say "low cut". `tc == 0` means **TC off
but still review mode**, the sentinel `show_edf.py:50` already uses; without it an
all-filters-off review request is indistinguishable from a raw one and loses its CAR.

| `band_low` | `band_high` | `tc` | behaviour |
|---|---|---|---|
| set | set | – | display path, **byte-identical to today** (SpectrogramModal, analysis canvas) |
| – | – | – | raw slice, unchanged |
| – | set/– | ≥0 | **review**: low cut `tc or None`, high cut `band_high` |
| set | any | ≥0 | **400** — "band_low and tc are two ways to ask for a low cut; send one" |

**Padding** — the current `pad=2.0` suits the zero-phase stages but not a long TC, whose
transient decays as `exp(-t/tc)`:

```python
pad_left = pad if not review else min(30.0, max(pad, 5.0 * (tc or 0.0)))
pad_right = pad            # only the zero-phase stages need a right pad
```

The existing trim already handles asymmetric pads. With the `lfilter_zi` warm start, adjacent
panned windows stay seamless at TC = 2 s. Do not expose `pad` — it is derivable from `tc`.

**No wire-format change.** In review mode return `band_low = 1/(2*pi*tc)` — the equivalent
corner in Hz, exactly what that header slot already declares — so `BQEDFW01`,
`parseEdfWindowBinary.ts` and the test decoder are untouched. `filtered` is 1 in review mode
even with both cuts off, because the data was referenced and notched.

### 2c. `meas_date` in EDF meta — `app/services/edf.py`

One field, ISO-8601 string or `null`. Not seconds-since-midnight, and not both — the client
derives that in one line and two fields invite disagreement.

```python
def _meas_date_iso(raw):
    dt = raw.info.get("meas_date")     # tz-aware UTC datetime, or None
    return dt.isoformat() if dt is not None else None
```

Bump `_META_VERSION` 2 → 3 so existing entries miss and recompute. The merge at
`edf.py:110-114` is `{**old, **new}` and the key is always present (value `None` when the
header has none), so `None` correctly overwrites. **Cost:** the bump forces a full amplitude
rescan on the first `/meta` per recording after deploy — seconds to a minute on a multi-GB
clip. Accept it; one self-healing path beats a header-only top-up branch.

Midnight crossing and clock→offset seeking stay client-side (`show_edf.py:160-172`).

**Not needed any more** (was in the earlier draft, dropped with the separation): EI/HFO
`reference` widening, the HFO montage, the SOZ contact projection.

## Step 3 — The Clinical EEG View tab

### Registration

`src/components/subjectViews.ts` — one entry, after `interictal`:

```ts
{ value: "clinical", label: "Clinical EEG", icon: IconHeartRateMonitor },
```

`src/routes/SubjectLayoutPage.tsx` — one block matching the existing pattern
(`viewStyle("clinical")` + `isVisited("clinical")` + `key={id}`).

### New files, all under `src/features/clinical/`

Nothing here is shared with `components/eeg/`. Reuse is limited to neutral infrastructure that
carries no analysis state: `useEdfMeta` / `useEdfWindow` (`api/queries/useEdf.ts`),
`EdfRecordingBar`, `EdfLoadErrorPanel`, `AnnotationsPanel`, `lib/parseEdfWindowBinary.ts`.

| File | Contents |
|---|---|
| `ClinicalEegPage.tsx` | Recording picker + toolbar + canvas + shaft/channel list. Read-only: no markers, no overlays, no compute forms. |
| `useClinicalViewState.ts` | Own reducer, below. |
| `ClinicalToolbar.tsx` | NK preset dropdowns. |
| `ClinicalCanvas.tsx` | The renderer. |
| `ClinicalChannelList.tsx` | Shaft-grouped selection + search. |

```ts
interface ClinicalViewState {
  dispChansNum; dispChansStart;
  sensitivity;                          // µV/mm
  pageSeconds; timeStart;
  selectedChannels: Set<string>;        // display only — feeds no job
  loadedEdfId;
  montage: "none" | "car" | "bipolar";  // default "none" (the NK review look)
  timeConstant: number | null;          // seconds; null = off
  highCut: number | null;               // Hz; null = off
  mainsFreq;
  negativeUp: boolean;                  // default true
}
```

No `filterEnabled` — TC and high cut each have their own "off", which is how NK expresses it.

### Toolbar presets

No invalid state is reachable, so no clamping and no error states.

| Control | Values |
|---|---|
| Sens | 2, 5, 10, 20, 50, **75**, 100, 150, 200, 300 µV/mm |
| TC | off, 0.003, 0.01, 0.03, **0.1**, 0.3, 1, 2 s — label the corner: `0.1 s (1.6 Hz)` |
| High cut | off, 15, 30, 60, **70**, 120, 300 Hz |
| Page | 5, **10**, 15, 20, 30 s |
| Montage | Referential / CAR / Bipolar |
| Polarity | negative-up toggle (default on) |
| Mains | 50 / 60 Hz |

### Canvas

Built correctly from the start rather than inheriting the analysis canvas's shape:

- **Row pitch from `dispChansNum`**, not the returned channel count:
  `rowHeight = height / dispChansNum`, `pixelsPerUv = rowHeight / (sensitivity * 12)`,
  matching `show_edf.py`'s `ROW_MM = 12` — one row spans `sensitivity × 12` µV. A short last
  page keeps the same µV-per-row instead of silently rescaling.
- **Negative up**: `y = rowCenter + v * 1e6 * pixelsPerUv` when `negativeUp`.
- **Label gutter**: hoist `LABEL_WIDTH` to a module const and use it consistently in the
  trace, gridline, and any pointer mapping — the shifted-click bug in the analysis canvas
  came from using it in one place and not the other.
- **Clock axis** when `meta.meas_date` is set: `(midnightSeconds(meas_date) + t) % 86400`
  formatted `HH:MM:SS`, mirroring `show_edf.py:42-44`. Fall back to `{n}s` when absent.
- **Request**: `{ tc: timeConstant ?? 0, bandHigh: highCut ?? undefined, mainsFreq,
  reference: montage, channels: visible }`.
- **Bipolar paging** reuses `useBipolarPreview(subjectId, edfId, selectedContacts, montage
  === "bipolar")`, whose response already carries `pairs: string[]`
  (`routers/ictal.py:164`, typed at `endpoints.ts:422`). Page over `preview.pairs` under
  bipolar, over selected contacts otherwise. No new endpoint, no client-side pairing rule.

### Channel list

Group by shaft, mirroring `show_edf.py`'s `SHAFTS` argument (`pick_shafts`, `:73-86`) — the
common review action is "show me G′ and L′", not clicking twelve boxes. Search box plus
per-shaft toggles; Select all / Clear all must respect the active search filter.

### `endpoints.ts`

Add `tc` to `EdfWindowParams` and `meas_date` to `EdfMeta`. In `useEdf.ts`, add `tc` to
`edfWindowQueryKey` **and** to the `stableParams` dep list — miss the second and panning
serves stale windows.

---

## Sequencing

1. Revert the six web files (Step 1). Independently verifiable: `git diff` shows only the
   server files and `endpoints.ts` remaining.
2. `filters.py` — `rc_highpass` + `filter_for_review`. `filter_for_display` untouched.
3. Window endpoint `tc` param + pad rule.
4. `meas_date` + `_META_VERSION` bump.
5. The clinical tab: registration + page shell + canvas, then toolbar presets, then shaft
   list and bipolar paging.

## Verification

**Server** (`cd v2/server && source .venv/bin/activate`):

```bash
pytest      # must pass unchanged — filter_for_display and every analysis path are untouched
```

New tests:
- `test_edf_window_review_filter_matches_the_nk_recipe` — independently reproduce reference →
  notch → `lfilter([a,-a],[1,-a])` → `butter(4)` and assert equality, pinning the coefficients
  against `show_edf.py:147-148`.
- `test_edf_window_tc_keeps_slow_activity_the_bandpass_eats` — synth 0.5 Hz + 10 Hz; the
  0.5 Hz amplitude under `tc=0.1` must be several times what `band_low=1.6&band_high=70`
  leaves. This is the reason the review filter exists.
- `test_edf_window_review_seam_is_continuous_for_a_long_tc` — same span from two window
  starts at `tc=2`, `allclose` on the overlap. Regression test for the pad rule.
- `test_edf_window_rejects_band_low_with_tc` (400); review high-cut-only; hicut ≥ Nyquist ignored.
- `test_edf_meta_reports_the_recording_start_time`, plus a `None` unit test of
  `_meas_date_iso` (mne's EDF *exporter* substitutes a default date, so a synthetic file
  cannot produce None).

**Web** (no test runner exists; `package.json` has only dev/build/lint):

```bash
cd v2/web && npm run build && npm run lint
```

**End-to-end.** Run API + worker + web, open the Clinical EEG tab on a Bella recording, and
put the reference tool beside it on the same file:

```bash
python v2/tools/show_edf.py <same.edf> 75 0.1 70 10 <HH:MM:SS>
```

The two screens should agree on trace shape, amplitude, polarity and clock position at
matching Sens/TC/HC/page settings. That comparison is the acceptance test.

**Then confirm the separation held**, which is the point of the whole change: on the Ictal
tab, delete a channel in the list and check the EI form's channel count drops with it; on the
Interictal tab, confirm a fresh recording still defaults the ripple band to 80–250 Hz. Both
should behave exactly as they did before any of this work.

## Deliberately not in this change

- **Any handoff from the clinical tab to the analysis tabs** (e.g. "send this range to Ictal
  as the seizure onset"). Useful later, but it is exactly the coupling this change exists to
  remove — it should be designed on its own terms once the tab is in use.
- **The analysis canvas's paging-dependent CAR** — a real display bug, but its fix belongs
  with the analysis viewer, not here (see Step 1).
- **HiDPI crispness.** No canvas honours `devicePixelRatio` today, so traces are soft on
  retina displays. The new clinical canvas can adopt it later without touching anything else.
- **`amplitude_range`** now has no consumer in the clinical path and costs a full decode pass
  per recording; the analysis canvas still references it, so leave it.
