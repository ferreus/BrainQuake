import { useMemo } from "react";
import { Billboard, Text } from "@react-three/drei";
import { schemeCategory10 } from "d3-scale-chromatic";
import { useLabelsSummary } from "../../api/queries/useElectrodes";

interface ClusterCentroidsProps {
  subjectId: number;
  excluded: Set<number>;
}

/**
 * Coarse preview of the GMM clusters detect() found, before segment() walks
 * each one into named contacts. The legacy app's equivalent
 * (client_elec.py's viewLabels/genLabelFinished) drew every cluster voxel in
 * a separate untransformed 2D matplotlib-in-Qt scatter; this instead plots
 * just the server-computed centroid (summarize_labels(), already mapped into
 * the same display space as BrainMesh/ElectrodeContacts) so it overlays
 * directly on the brain surface already in the scene. Excluded clusters (see
 * LabelReviewPanel) are dimmed rather than hidden, so unchecking a box gives
 * immediate visual feedback before committing.
 */
export function ClusterCentroids({ subjectId, excluded }: ClusterCentroidsProps) {
  const { data } = useLabelsSummary(subjectId, true);
  const clusters = useMemo(() => data?.clusters ?? [], [data]);

  if (clusters.length === 0) return null;

  return (
    <>
      {clusters.map((c, i) => {
        const isExcluded = excluded.has(c.label);
        const color = schemeCategory10[i % schemeCategory10.length];
        return (
          <group key={c.label} position={c.centroid}>
            <mesh>
              <sphereGeometry args={[3, 16, 16]} />
              <meshStandardMaterial color={color} transparent opacity={isExcluded ? 0.15 : 0.9} />
            </mesh>
            <Billboard position={[4, 4, 4]}>
              <Text fontSize={3.5} color={isExcluded ? "#888888" : "#ffffff"} anchorX="center" anchorY="middle">
                {`${c.label} (${c.voxel_count})`}
              </Text>
            </Billboard>
          </group>
        );
      })}
    </>
  );
}
