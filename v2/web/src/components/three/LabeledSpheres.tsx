import { useEffect, useRef } from "react";
import * as THREE from "three";
import { Billboard, Text } from "@react-three/drei";

export interface SphereLabel {
  text: string;
  pos: [number, number, number];
}

interface LabeledSpheresProps {
  positions: [number, number, number][];
  radius: number;
  /** Uniform sphere color. Ignored when `colors` is given. */
  color?: string;
  /** Per-instance CSS colors, one per position -- sets instanceColor. */
  colors?: string[];
  labels: SphereLabel[];
  labelColor: string;
  labelSize?: number;
}

/**
 * One instanced sphere per 3D point plus camera-facing text labels -- the
 * shared rendering behind the electrode-contact and SOZ-suspicion overlays,
 * both of which reproduce mayavi's `mlab.points3d` + `mlab.text3d` from the
 * legacy Qt client. Instancing matters here: an SEEG implant is a few hundred
 * contacts, which is far too many individual meshes to render smoothly.
 */
export function LabeledSpheres({
  positions,
  radius,
  color,
  colors,
  labels,
  labelColor,
  labelSize = 3,
}: LabeledSpheresProps) {
  const meshRef = useRef<THREE.InstancedMesh>(null);

  useEffect(() => {
    const mesh = meshRef.current;
    if (!mesh || positions.length === 0) return;
    const matrix = new THREE.Matrix4();
    const instanceColor = new THREE.Color();
    positions.forEach((p, i) => {
      matrix.makeTranslation(p[0], p[1], p[2]);
      mesh.setMatrixAt(i, matrix);
      if (colors) {
        instanceColor.set(colors[i]);
        mesh.setColorAt(i, instanceColor);
      }
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [positions, colors]);

  if (positions.length === 0) return null;

  return (
    <>
      <instancedMesh ref={meshRef} args={[undefined, undefined, positions.length]}>
        <sphereGeometry args={[radius, 16, 16]} />
        <meshStandardMaterial color={colors ? undefined : color} />
      </instancedMesh>
      {labels.map((l) => (
        <Billboard key={l.text} position={l.pos}>
          <Text fontSize={labelSize} color={labelColor} anchorX="center" anchorY="middle">
            {l.text}
          </Text>
        </Billboard>
      ))}
    </>
  );
}
