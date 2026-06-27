// A flat wireframe grid that scrolls toward the camera for a sense of forward
// momentum beneath the coin.
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";

export default function WireframeTerrain() {
  const mesh = useRef();
  useFrame((state) => {
    if (mesh.current) {
      // looping z drift creates the infinite-scroll illusion
      mesh.current.position.z = (state.clock.elapsedTime * 2) % 2;
    }
  });
  return (
    <mesh ref={mesh} rotation={[-Math.PI / 2, 0, 0]} position={[0, -4, 0]}>
      <planeGeometry args={[100, 100, 50, 50]} />
      <meshBasicMaterial color="#3b82f6" wireframe transparent opacity={0.12} />
    </mesh>
  );
}
