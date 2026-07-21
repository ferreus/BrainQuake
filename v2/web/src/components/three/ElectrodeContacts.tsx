import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { Billboard, Text } from "@react-three/drei";
import { useChnXyz } from "../../api/queries/useChnXyz";

interface ElectrodeContactsProps {
  subjectId: number;
}

/**
 * Plain electrode-contact view: solid black spheres per contact plus a blue
 * billboard label near each shaft's deepest contact -- matches the legacy
 * mayavi rendering in client_elec.py's vis3D() (mlab.points3d(color=(0,0,0),
 * scale_factor=1.5) + mlab.text3d labels, orient_to_camera=True). The
 * scalar-colored SOZ-suspicion variant is a separate component (Phase 5).
 */
export function ElectrodeContacts({ subjectId }: ElectrodeContactsProps) {
  const { data } = useChnXyz(subjectId);
  const meshRef = useRef<THREE.InstancedMesh>(null);

  const { positions, labels } = useMemo(() => {
    const positions: [number, number, number][] = [];
    const labels: { text: string; pos: [number, number, number] }[] = [];
    if (data) {
      for (const [shaftLabel, contacts] of Object.entries(data)) {
        contacts.forEach(([x, y, z]) => positions.push([x, y, z]));
        const last = contacts[contacts.length - 1];
        if (last) {
          labels.push({ text: shaftLabel, pos: [last[0] + 4, last[1] + 4, last[2] + 4] });
        }
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
        <meshStandardMaterial color="black" />
      </instancedMesh>
      {labels.map((l) => (
        <Billboard key={l.text} position={l.pos}>
          <Text fontSize={3} color="#3355ee" anchorX="center" anchorY="middle">
            {l.text}
          </Text>
        </Billboard>
      ))}
    </>
  );
}
