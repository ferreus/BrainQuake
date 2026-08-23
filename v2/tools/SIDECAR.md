# EDF sidecar schema `eeg2edf-sidecar/1`

`nk2edf`, `nicolet2edf` and `vwr2edf` each write `OUTPUT.json` next to `OUTPUT.edf`,
holding what the source format records and EDF has nowhere to put. All three emit
the same schema, built by `edfcommon.build_sidecar`, so one reader handles every
converter.

Every key below is always present. A format with no such concept emits `null`
or `[]` rather than dropping the key. Format-specific extras are appended after
the schema keys, both at the top level and inside `channels[]` / `events[]`.

None of the tools write the patient's name or record number.

## Top level

| key | type | meaning |
|---|---|---|
| `schema` | string | `"eeg2edf-sidecar/1"` |
| `source` | `{file, format}` | source filename and one of `nihon-kohden`, `nicolet-nervus`, `micromed-vwr` |
| `clip` | object | the slice of the recording *this* EDF holds |
| `device` | string/null | recording hardware, when the format names it |
| `reference` | string/null | system reference sensor, when the format names it |
| `patient` | `{sex, dob, age_at_recording}` | never a name |
| `channels` | array | one entry per signal **written to the EDF**, in EDF order |
| `montages` | array | every montage the source carries, applied or not |
| `montage_applied` | string/null | name of the montage the EDF signals were built from |
| `segments` | array | sub-segments of the stored stream; `[]` when the format has none |
| `events` | array | every marker the source carries |

## `clip`

`index` (null when the EDF covers the whole recording), `start` (ISO 8601),
`offset_s` into the stored stream, `duration_s`, `sfreq_hz` (null when the
format mixes rates -- read `channels[].sfreq_hz` instead).

## `channels[]`

`label`, `edf_label` (after de-duplication and the 16-byte EDF trim),
`source_index` (the format's own channel index), `unit`, `sfreq_hz`,
`reference`, `resolution_uv_per_lsb`, `low_cut`, `high_cut`, `notch`,
`derived` (true when the signal is a montage derivation of two stored channels).

## `montages[]`

`name`, and `channels[]` of `label`, `active`, `reference`, `active_index`,
`reference_index`. The two indices are in the format's own index space:
`.21E` electrode codes for nk2edf, channel-table positions for nicolet2edf,
LABCOD indices for vwr2edf.

## `segments[]`

`index`, `start` (ISO 8601 or null when the format does not timestamp the
segment), `offset_s` into the stream, `duration_s`.

## `events[]`

`onset_s` (seconds from **this clip's** start, matching the EDF+ annotation, or
null when the event falls outside the clip), `duration_s`, `label`, `type`,
`source` (which area or file it came from), `channel`.

## Per-format population

| | nk2edf | nicolet2edf | vwr2edf |
|---|---|---|---|
| `device` / `reference` | `.21E` | null | null |
| `patient` | `.PNT` | null | null |
| `clip.sfreq_hz` | per block | null (mixed rates) | per file |
| `montages` | every `.PTN` pattern | the one stored display montage | every `MONTAGE` slot |
| `segments` | `[]` (one EDF per block) | recorded segments | `TRONCA` acquisition parts |
| `events.source` | `LOG`, `sld`, `csv` | `Events` | `NOTE`, `TRIGGER`, `FLAGS`, `EVENT A`, `EVENT B` |
| top-level extras | `n_channels_in_file` | `polarity_inverted` | `source_sample_bytes`, `recorded_montage` |
| `channels[]` extras | -- | `active_sensor`, `trend` | `lmin`, `lmax`, `lground`, `pmin`, `pmax`, `factor`, `units` |
| `events[]` extras | -- | `when`, `stream_s`, `guid` | `frame` |

## Tests

`python test_sidecar_schema.py` drives all three `write_sidecar` implementations
over synthetic inputs and asserts they agree on the schema.
