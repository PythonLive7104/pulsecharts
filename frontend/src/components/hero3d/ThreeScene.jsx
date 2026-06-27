// The hero's 3D background. Lazy-loaded from LandingPage so three.js is split into
// its own chunk and never weighs down the trading app bundle.
//
// Reflections come from a PROCEDURAL environment (Lightformers) rather than an
// external HDR file, so nothing is fetched from a CDN at runtime.
import { Suspense } from "react";
import { Canvas } from "@react-three/fiber";
import { Environment, Lightformer } from "@react-three/drei";
import CryptoCoin from "./CryptoCoin";
import FloatingParticles from "./FloatingParticles";
import WireframeTerrain from "./WireframeTerrain";

export default function ThreeScene() {
  return (
    <Canvas
      className="hero-canvas"
      camera={{ position: [0, 0, 10], fov: 45 }}
      gl={{ antialias: false, alpha: true, powerPreference: "high-performance" }}
      dpr={[1, 1.8]}
    >
      <color attach="background" args={["#06070f"]} />
      <fog attach="fog" args={["#06070f", 9, 30]} />

      <ambientLight intensity={0.4} />
      <pointLight position={[10, 10, 10]} intensity={140} color="#7c3aed" />
      <pointLight position={[-10, -10, -10]} intensity={90} color="#3b82f6" />
      <directionalLight position={[0, 5, 5]} intensity={2.2} color="#ffffff" />

      <Suspense fallback={null}>
        <CryptoCoin />
        <FloatingParticles />
        <WireframeTerrain />
        {/* procedural reflections — no external HDR */}
        <Environment resolution={128} frames={1}>
          <Lightformer intensity={2.6} position={[10, 5, 8]} scale={[12, 12, 1]} color="#7c3aed" />
          <Lightformer intensity={2.2} position={[-12, -3, 6]} scale={[12, 12, 1]} color="#3b82f6" />
          <Lightformer intensity={1.8} position={[0, 6, -10]} scale={[14, 3, 1]} color="#ffffff" />
        </Environment>
      </Suspense>
    </Canvas>
  );
}
