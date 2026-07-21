import { useMemo } from "react";
import * as THREE from "three";
import { useSurfaceMesh } from "../../api/queries/useSurfaceMesh";

interface HemisphereMeshProps {
  subjectId: number;
  hemi: "lh" | "rh";
}

function HemisphereMesh({ subjectId, hemi }: HemisphereMeshProps) {
  const { data } = useSurfaceMesh(subjectId, hemi);

  const geometry = useMemo(() => {
    if (!data) return null;
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(data.vertices, 3));
    geom.setIndex(new THREE.BufferAttribute(data.faces, 1));
    geom.computeVertexNormals();
    return geom;
  }, [data]);

  if (!geometry) return null;

  return (
    <mesh geometry={geometry}>
      {/* Translucent white surface, matching client_elec.py/client_soz.py's
       * mayavi material (opacity 0.35-0.4, phong-ish shading, backface
       * culling on, depth peeling for correct transparency ordering).
       * depthWrite=false is the standard cheap stand-in for depth peeling
       * with a single translucent shell. */}
      <meshPhysicalMaterial color="#ffffff" opacity={0.38} transparent depthWrite={false} roughness={0.6} side={THREE.FrontSide} />
    </mesh>
  );
}

interface BrainMeshProps {
  subjectId: number;
  hemispheres?: ("lh" | "rh")[];
}

export function BrainMesh({ subjectId, hemispheres = ["lh", "rh"] }: BrainMeshProps) {
  return (
    <>
      {hemispheres.map((hemi) => (
        <HemisphereMesh key={hemi} subjectId={subjectId} hemi={hemi} />
      ))}
    </>
  );
}
