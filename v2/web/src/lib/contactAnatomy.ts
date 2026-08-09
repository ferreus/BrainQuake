import type { ContactAnatomy } from "../api/endpoints";

/** Human-readable lines for one contact's anatomy, most specific first. Shared
 * by the 3D billboard and the sidebar table so a contact never reads one way
 * in the scene and another way in the list.
 *
 * Deliberately keeps the exact-voxel label and the nearest-structure fallback
 * as separate lines rather than collapsing them into one "answer": for a
 * contact in white matter next to hippocampus, both facts matter and picking
 * one silently would be the misleading part. */
export function describeContact(c: ContactAnatomy): string[] {
  if (c.out_of_volume) return ["outside the segmentation"];

  const lines: string[] = [c.label_name ?? "unlabeled"];
  const nearest = c.nearest_structure;
  // distance 0 means the exact voxel already is that structure -- repeating it
  // as a "nearest" line would just be the same name twice.
  if (nearest && nearest.distance_mm > 0) {
    lines.push(`→ ${nearest.label_name} ${nearest.distance_mm.toFixed(1)} mm`);
  }
  return lines;
}

/** True when the contact's own voxel is already a grey-matter structure, i.e.
 * the label needs no "nearest" qualifier. Mirrors the server's is_structure()
 * without duplicating its label tables: the server already answered this by
 * reporting a nearest structure at distance 0. */
export function isInStructure(c: ContactAnatomy): boolean {
  return c.nearest_structure != null && c.nearest_structure.distance_mm === 0;
}
