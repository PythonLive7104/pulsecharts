// 200 emissive crystals scattered in depth, drifting on sin/cos and shifting as a
// group in opposition to the pointer for parallax. One instancedMesh for perf.
import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";

const COUNT = 200;

export default function FloatingParticles() {
  const mesh = useRef();
  const group = useRef();
  const dummy = useMemo(() => new THREE.Object3D(), []);

  // Random field generated once (stable across renders — hook-pure).
  const data = useMemo(
    () =>
      Array.from({ length: COUNT }, () => ({
        x: (Math.random() - 0.5) * 35,
        y: (Math.random() - 0.5) * 35,
        z: (Math.random() - 0.5) * 30 - 5,
        scale: 0.04 + Math.random() * 0.13,
        speed: 0.2 + Math.random() * 0.6,
        phase: Math.random() * Math.PI * 2,
      })),
    []
  );

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    const m = mesh.current;
    if (!m) return;
    for (let i = 0; i < COUNT; i++) {
      const p = data[i];
      dummy.position.set(
        p.x + Math.sin(t * p.speed + p.phase) * 1.2,
        p.y + Math.cos(t * p.speed * 0.8 + p.phase) * 1.2,
        p.z
      );
      dummy.scale.setScalar(p.scale);
      dummy.rotation.set(t * p.speed, t * p.speed * 0.5, 0);
      dummy.updateMatrix();
      m.setMatrixAt(i, dummy.matrix);
    }
    m.instanceMatrix.needsUpdate = true;

    if (group.current) {
      group.current.position.x = THREE.MathUtils.lerp(group.current.position.x, -state.pointer.x * 1.5, 0.04);
      group.current.position.y = THREE.MathUtils.lerp(group.current.position.y, -state.pointer.y * 1.5, 0.04);
    }
  });

  return (
    <group ref={group}>
      <instancedMesh ref={mesh} args={[undefined, undefined, COUNT]}>
        <icosahedronGeometry args={[1, 0]} />
        <meshStandardMaterial
          color="#05051a"
          emissive="#5b21b6"
          emissiveIntensity={0.6}
          roughness={0.1}
          metalness={0.9}
        />
      </instancedMesh>
    </group>
  );
}
