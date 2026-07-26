import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { Billboard, Text } from "@react-three/drei";
import { interpolatePlasma } from "d3-scale-chromatic";
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
 * Per-instance color needs a vertex-colored material, so this sets instanceColor.
 */
export function SozContacts({ rows, topN }: SozContactsProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null);

  const labels = useMemo(
    () =>
      rows.slice(0, Math.max(0, topN)).map((r) => ({
        text: r.contact,
        pos: [r.x + 3, r.y + 3, r.z + 3] as [number, number, number],
      })),
    [rows, topN],
  );

  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh || rows.length === 0) return;
    const m = new THREE.Matrix4();
    const color = new THREE.Color();
    rows.forEach((r, i) => {
      m.makeTranslation(r.x, r.y, r.z);
      mesh.setMatrixAt(i, m);
      const score = Math.min(1, Math.max(0, r.combined_score));
      color.set(interpolatePlasma(score));
      mesh.setColorAt(i, color);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [rows]);

  if (rows.length === 0) return null;

  return (
    <>
      <instancedMesh ref={meshRef} args={[undefined, undefined, rows.length]}>
        <sphereGeometry args={[2.5, 16, 16]} />
        <meshStandardMaterial />
      </instancedMesh>
      {labels.map((l) => (
        <Billboard key={l.text} position={l.pos}>
          <Text fontSize={3} color="#2244ff" anchorX="center" anchorY="middle">
            {l.text}
          </Text>
        </Billboard>
      ))}
    </>
  );
}
