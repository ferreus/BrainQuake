import { useMemo } from "react";
import { useChnXyz } from "../../api/queries/useChnXyz";
import { LabeledSpheres } from "./LabeledSpheres";
import type { SphereLabel } from "./LabeledSpheres";

interface ElectrodeContactsProps {
  subjectId: number;
}

/**
 * Plain electrode-contact view: solid black spheres per contact plus a blue
 * billboard label near each shaft's deepest contact -- matches the legacy
 * mayavi rendering in client_elec.py's vis3D() (mlab.points3d(color=(0,0,0),
 * scale_factor=1.5) + mlab.text3d labels, orient_to_camera=True). The
 * scalar-colored SOZ-suspicion variant is SozContacts.
 */
export function ElectrodeContacts({ subjectId }: ElectrodeContactsProps) {
  const { data } = useChnXyz(subjectId);

  const { positions, labels } = useMemo(() => {
    const positions: [number, number, number][] = [];
    const labels: SphereLabel[] = [];
    for (const [shaftLabel, contacts] of Object.entries(data ?? {})) {
      contacts.forEach(([x, y, z]) => positions.push([x, y, z]));
      const last = contacts[contacts.length - 1];
      if (last) {
        labels.push({ text: shaftLabel, pos: [last[0] + 4, last[1] + 4, last[2] + 4] });
      }
    }
    return { positions, labels };
  }, [data]);

  return (
    <LabeledSpheres positions={positions} radius={1.5} color="black" labels={labels} labelColor="#3355ee" />
  );
}
