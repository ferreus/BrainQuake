import { useMemo } from "react";
import { interpolatePlasma } from "d3-scale-chromatic";
import { LabeledSpheres } from "./LabeledSpheres";
import type { SozResultRow } from "../../api/endpoints";

interface SozContactsProps {
  rows: SozResultRow[];
  /** How many top-ranked contacts get a text label, matching client_soz.py's
   * plot_3d(top_n=...). rows are already sorted by combined_score desc. */
  topN: number;
}

/**
 * SOZ-suspicion contact overlay: one sphere per contact colored by
 * combined_score on the plasma colormap (vmin=0, vmax=1), with billboard text
 * labels on the top-N ranked contacts -- the r3f equivalent of client_soz.py's
 * plot_3d (mlab.points3d(colormap='plasma', vmin=0, vmax=1) + mlab.text3d).
 */
export function SozContacts({ rows, topN }: SozContactsProps) {
  const positions = useMemo(
    () => rows.map((r) => [r.x, r.y, r.z] as [number, number, number]),
    [rows],
  );

  const colors = useMemo(
    () => rows.map((r) => interpolatePlasma(Math.min(1, Math.max(0, r.combined_score)))),
    [rows],
  );

  const labels = useMemo(
    () =>
      rows.slice(0, Math.max(0, topN)).map((r) => ({
        text: r.contact,
        pos: [r.x + 3, r.y + 3, r.z + 3] as [number, number, number],
      })),
    [rows, topN],
  );

  return (
    <LabeledSpheres positions={positions} radius={2.5} colors={colors} labels={labels} labelColor="#2244ff" />
  );
}
