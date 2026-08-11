import type { CSSProperties, ReactNode } from "react";
import { Bounds, OrbitControls, PerspectiveCamera, View } from "@react-three/drei";

interface SceneViewProps {
  children: ReactNode;
  style?: CSSProperties;
}

/**
 * Shared 3D scene chrome: lighting approximating mayavi's phong material
 * params (ambient~0.4225, specular_power=20) and free-orbit camera controls --
 * there is no scripted camera anywhere in the legacy app, just the default
 * VTK/mayavi trackball interaction, which OrbitControls matches directly.
 * <Bounds fit clip observe> auto-frames the camera on whatever geometry is
 * loaded instead of a hardcoded position, since subject head/brain scale
 * varies.
 *
 * A <View>, not its own <Canvas>: the single canvas lives in App.tsx and
 * outlives every route, so switching subject or view no longer tears down and
 * rebuilds the WebGL context. This element reserves the layout box and the
 * scene is drawn into it, scissored to its bounds.
 */
export function SceneView({ children, style }: SceneViewProps) {
  return (
    <View style={{ background: "#1a1b1e", ...style }}>
      <PerspectiveCamera makeDefault position={[0, 0, 200]} fov={50} near={0.1} far={5000} />
      <ambientLight intensity={0.6} />
      <directionalLight position={[100, 200, 100]} intensity={0.8} />
      <directionalLight position={[-100, -100, -100]} intensity={0.3} />
      <Bounds fit clip observe margin={1.3}>
        {children}
      </Bounds>
      <OrbitControls makeDefault />
    </View>
  );
}
