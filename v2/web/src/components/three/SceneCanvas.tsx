import type { ReactNode } from "react";
import { Canvas } from "@react-three/fiber";
import { Bounds, OrbitControls } from "@react-three/drei";

interface SceneCanvasProps {
  children: ReactNode;
}

/**
 * Shared 3D scene chrome: lighting approximating mayavi's phong material
 * params (ambient~0.4225, specular_power=20) and free-orbit camera controls --
 * there is no scripted camera anywhere in the legacy app, just the default
 * VTK/mayavi trackball interaction, which OrbitControls matches directly.
 * <Bounds fit clip observe> auto-frames the camera on whatever geometry is
 * loaded instead of a hardcoded position, since subject head/brain scale
 * varies.
 */
export function SceneCanvas({ children }: SceneCanvasProps) {
  return (
    <Canvas camera={{ position: [0, 0, 200], fov: 50, near: 0.1, far: 5000 }} style={{ background: "#1a1b1e" }}>
      <ambientLight intensity={0.6} />
      <directionalLight position={[100, 200, 100]} intensity={0.8} />
      <directionalLight position={[-100, -100, -100]} intensity={0.3} />
      <Bounds fit clip observe margin={1.3}>
        {children}
      </Bounds>
      <OrbitControls makeDefault />
    </Canvas>
  );
}
