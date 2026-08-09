import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Billboard, Text } from "@react-three/drei";
import type { ThreeEvent } from "@react-three/fiber";
import { useChnXyz } from "../../api/queries/useChnXyz";
import { useContactAnatomy } from "../../api/queries/useContactAnatomy";
import { describeContact } from "../../lib/contactAnatomy";

interface ElectrodeContactsProps {
  subjectId: number;
  /** Contact name (e.g. "K'7") currently selected, or null. Owned by
   * ElectrodesPage so the sidebar table and this view stay in sync. */
  selected?: string | null;
  onSelect?: (name: string | null) => void;
}

/**
 * Plain electrode-contact view: solid black spheres per contact plus a blue
 * billboard label near each shaft's deepest contact -- matches the legacy
 * mayavi rendering in client_elec.py's vis3D() (mlab.points3d(color=(0,0,0),
 * scale_factor=1.5) + mlab.text3d labels, orient_to_camera=True). The
 * scalar-colored SOZ-suspicion variant is a separate component (Phase 5).
 *
 * Clicking a contact selects it and shows which anatomical structure it sits
 * in (see services/anatomy.py). Contacts are keyed by name rather than by
 * their index in this component's flattened position array, because the
 * anatomy endpoint returns electrodes in its own sorted order -- keying on
 * position would silently mislabel every contact if either order changed.
 */
export function ElectrodeContacts({ subjectId, selected, onSelect }: ElectrodeContactsProps) {
  const { data } = useChnXyz(subjectId);
  const { data: anatomy } = useContactAnatomy(subjectId);
  const meshRef = useRef<THREE.InstancedMesh>(null);
  const [hovered, setHovered] = useState<number | null>(null);

  const { positions, names, labels } = useMemo(() => {
    const positions: [number, number, number][] = [];
    const names: string[] = [];
    const labels: { text: string; pos: [number, number, number] }[] = [];
    if (data) {
      for (const [shaftLabel, contacts] of Object.entries(data)) {
        contacts.forEach(([x, y, z], i) => {
          positions.push([x, y, z]);
          names.push(`${shaftLabel}${i + 1}`); // 1-based, same row-order-is-contact-number invariant the server uses
        });
        const last = contacts[contacts.length - 1];
        if (last) {
          labels.push({ text: shaftLabel, pos: [last[0] + 4, last[1] + 4, last[2] + 4] });
        }
      }
    }
    return { positions, names, labels };
  }, [data]);

  const anatomyByName = useMemo(
    () => new Map((anatomy?.contacts ?? []).map((c) => [c.name, c])),
    [anatomy],
  );

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

  // Pointer feedback has to be undone on unmount too -- switching tabs while
  // hovering a contact would otherwise leave the whole app with a pointer cursor.
  useEffect(() => {
    document.body.style.cursor = hovered != null ? "pointer" : "auto";
    return () => {
      document.body.style.cursor = "auto";
    };
  }, [hovered]);

  if (positions.length === 0) return null;

  const selectedIndex = selected ? names.indexOf(selected) : -1;
  const activeIndex = selectedIndex >= 0 ? selectedIndex : hovered;
  const activeName = activeIndex != null && activeIndex >= 0 ? names[activeIndex] : null;
  const activePos = activeIndex != null && activeIndex >= 0 ? positions[activeIndex] : null;
  const activeAnatomy = activeName ? anatomyByName.get(activeName) : undefined;

  function handleClick(e: ThreeEvent<MouseEvent>) {
    e.stopPropagation(); // don't also hit the brain mesh behind the contact
    const i = e.instanceId;
    if (i == null) return;
    onSelect?.(names[i] === selected ? null : names[i]);
  }

  return (
    <>
      <instancedMesh
        ref={meshRef}
        args={[undefined, undefined, positions.length]}
        onClick={handleClick}
        onPointerOver={(e) => {
          e.stopPropagation();
          setHovered(e.instanceId ?? null);
        }}
        onPointerOut={() => setHovered(null)}
      >
        <sphereGeometry args={[1.5, 16, 16]} />
        <meshStandardMaterial color="black" />
      </instancedMesh>

      {/* Drawn as a separate, slightly larger sphere rather than by recoloring
          the instance: a per-instance color attribute would have to be rebuilt
          and re-uploaded on every hover, for one contact out of hundreds. */}
      {activePos && (
        <mesh position={activePos}>
          <sphereGeometry args={[2.2, 16, 16]} />
          <meshStandardMaterial
            color={selectedIndex >= 0 ? "#e03131" : "#fab005"}
            transparent
            opacity={0.85}
          />
        </mesh>
      )}

      {activePos && activeName && (
        <Billboard position={[activePos[0], activePos[1] + 6, activePos[2]]}>
          <Text fontSize={3} color="#e03131" anchorX="center" anchorY="bottom" outlineWidth={0.15} outlineColor="white">
            {activeName}
          </Text>
          {activeAnatomy && (
            <Text
              fontSize={2.4}
              color="#212529"
              anchorX="center"
              anchorY="top"
              position={[0, -0.6, 0]}
              outlineWidth={0.12}
              outlineColor="white"
            >
              {describeContact(activeAnatomy).join("\n")}
            </Text>
          )}
        </Billboard>
      )}

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
