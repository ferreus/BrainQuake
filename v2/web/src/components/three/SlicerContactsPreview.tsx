import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { Billboard, Text } from "@react-three/drei";
import { useSlicerMrbPreview } from "../../api/queries/useElectrodes";
import type { ImportContactItem } from "../../api/endpoints";

interface SlicerContactsPreviewProps {
  subjectId: number;
}

/** Orange preview of a pending (not yet approved) 3D Slicer .mrb import --
 * deliberately a different color from ElectrodeContacts' black spheres for
 * already-committed contacts, so it's visually obvious this data hasn't been
 * written yet and is still awaiting review (see SlicerImportReviewPanel). */
export function SlicerContactsPreview({ subjectId }: SlicerContactsPreviewProps) {
  const { data } = useSlicerMrbPreview(subjectId, true);
  const meshRef = useRef<THREE.InstancedMesh>(null);

  const { positions, labels } = useMemo(() => {
    const positions: [number, number, number][] = [];
    const labels: { text: string; pos: [number, number, number] }[] = [];
    if (data) {
      const byElectrode = new Map<string, ImportContactItem[]>();
      for (const c of data.contacts) {
        const list = byElectrode.get(c.electrode) ?? [];
        list.push(c);
        byElectrode.set(c.electrode, list);
      }
      for (const [electrode, contacts] of byElectrode) {
        const sorted = [...contacts].sort((a, b) => a.contact_index - b.contact_index);
        sorted.forEach((c) => positions.push([c.x, c.y, c.z]));
        const last = sorted[sorted.length - 1];
        if (last) labels.push({ text: electrode, pos: [last.x + 4, last.y + 4, last.z + 4] });
      }
    }
    return { positions, labels };
  }, [data]);

  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh || positions.length === 0) return;
    const m = new THREE.Matrix4();
    positions.forEach((p, i) => {
      m.makeTranslation(p[0], p[1], p[2]);
      mesh.setMatrixAt(i, m);
    });
    mesh.instanceMatrix.needsUpdate = true;
  }, [positions]);

  if (positions.length === 0) return null;

  return (
    <>
      <instancedMesh ref={meshRef} args={[undefined, undefined, positions.length]}>
        <sphereGeometry args={[1.5, 16, 16]} />
        <meshStandardMaterial color="orange" />
      </instancedMesh>
      {labels.map((l) => (
        <Billboard key={l.text} position={l.pos}>
          <Text fontSize={3} color="#cc6600" anchorX="center" anchorY="middle">
            {l.text}
          </Text>
        </Billboard>
      ))}
    </>
  );
}
