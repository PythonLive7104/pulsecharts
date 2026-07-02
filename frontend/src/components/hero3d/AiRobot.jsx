// AI robot — the hero centerpiece, signalling that PulseCharts is AI-powered.
// Built entirely from primitives (no external model/CDN), so it stays in the same
// lazy three.js chunk. Floats, gently looks around, tilts toward the pointer, and
// pulses/blinks its glowing eyes + antenna. Same group-animation contract as the
// old CryptoCoin so it slots into the existing camera and lighting.
import { useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { RoundedBox } from "@react-three/drei";
import * as THREE from "three";

const ACCENT = "#3b82f6"; // brand blue — matches --accent
const CYAN = "#38e1ff";
const BODY = "#e7ecf6"; // light brushed-metal shell

export default function AiRobot() {
  const group = useRef();
  const eyeL = useRef();
  const eyeR = useRef();
  const mouth = useRef();
  const tip = useRef();
  const core = useRef();

  const { viewport } = useThree();
  const isMobile = viewport.width < 6;
  const scale = isMobile ? 0.62 : 0.92;
  const baseX = isMobile ? 0 : 2.4;
  const baseY = isMobile ? -2.4 : 0.1;

  // Shared materials (memoised so we don't rebuild them each frame).
  const shell = useMemo(
    () => ({ color: BODY, metalness: 0.62, roughness: 0.32, envMapIntensity: 1.4 }),
    []
  );
  const glow = useMemo(
    () => ({ color: ACCENT, emissive: ACCENT, emissiveIntensity: 2.4, metalness: 0.3, roughness: 0.4 }),
    []
  );

  useFrame((state) => {
    const g = group.current;
    if (!g) return;
    const t = state.clock.elapsedTime;

    // Float + a gentle "looking around" turn (keeps the face toward the viewer,
    // unlike a full spin), plus subtle pointer parallax.
    g.position.x = baseX;
    g.position.y = Math.sin(t * 0.6) * 0.18 + baseY;
    g.rotation.y = THREE.MathUtils.lerp(
      g.rotation.y, Math.sin(t * 0.35) * 0.45 + state.pointer.x * 0.35, 0.06
    );
    g.rotation.x = THREE.MathUtils.lerp(g.rotation.x, -state.pointer.y * 0.18, 0.06);
    g.rotation.z = Math.sin(t * 0.5) * 0.03;

    // Eyes: steady glow with a quick periodic blink (dim to near-off briefly).
    const blink = t % 3.4 < 0.12 ? 0.06 : 1;
    const eye = 2.2 * blink;
    if (eyeL.current) eyeL.current.emissiveIntensity = eye;
    if (eyeR.current) eyeR.current.emissiveIntensity = eye;
    if (mouth.current) mouth.current.emissiveIntensity = (1.4 + Math.sin(t * 6) * 0.5) * blink;
    // Antenna tip + chest core pulse like a heartbeat / live signal.
    const pulse = 1.8 + Math.sin(t * 3) * 1.0;
    if (tip.current) tip.current.emissiveIntensity = pulse;
    if (core.current) core.current.emissiveIntensity = pulse;
  });

  return (
    <group ref={group} position={[baseX, baseY, 0]} scale={scale}>
      {/* Head */}
      <RoundedBox args={[2.3, 1.95, 1.6]} radius={0.34} smoothness={5} castShadow>
        <meshStandardMaterial {...shell} />
      </RoundedBox>

      {/* Dark face panel */}
      <RoundedBox args={[1.75, 1.28, 0.2]} radius={0.16} smoothness={4} position={[0, 0.05, 0.82]}>
        <meshStandardMaterial color="#0a1020" metalness={0.5} roughness={0.35} />
      </RoundedBox>

      {/* Eyes */}
      <mesh position={[-0.42, 0.18, 0.95]}>
        <sphereGeometry args={[0.2, 24, 24]} />
        <meshStandardMaterial ref={eyeL} {...glow} />
      </mesh>
      <mesh position={[0.42, 0.18, 0.95]}>
        <sphereGeometry args={[0.2, 24, 24]} />
        <meshStandardMaterial ref={eyeR} {...glow} />
      </mesh>

      {/* Mouth / status bar */}
      <RoundedBox args={[0.8, 0.14, 0.12]} radius={0.06} smoothness={3} position={[0, -0.35, 0.95]}>
        <meshStandardMaterial ref={mouth} color={CYAN} emissive={CYAN} emissiveIntensity={1.4} roughness={0.4} />
      </RoundedBox>

      {/* Ears / side sensors */}
      {[-1.28, 1.28].map((x) => (
        <mesh key={x} position={[x, 0.05, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.28, 0.28, 0.28, 24]} />
          <meshStandardMaterial color={BODY} metalness={0.7} roughness={0.28} />
        </mesh>
      ))}
      {[-1.28, 1.28].map((x) => (
        <mesh key={`g${x}`} position={[x, 0.05, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.12, 0.12, 0.32, 20]} />
          <meshStandardMaterial color={ACCENT} emissive={ACCENT} emissiveIntensity={1.6} />
        </mesh>
      ))}

      {/* Antenna + pulsing tip */}
      <mesh position={[0, 1.2, 0]}>
        <cylinderGeometry args={[0.05, 0.05, 0.55, 12]} />
        <meshStandardMaterial color={BODY} metalness={0.7} roughness={0.3} />
      </mesh>
      <mesh position={[0, 1.6, 0]}>
        <sphereGeometry args={[0.16, 20, 20]} />
        <meshStandardMaterial ref={tip} color={CYAN} emissive={CYAN} emissiveIntensity={2} />
      </mesh>

      {/* Body */}
      <RoundedBox args={[1.85, 1.35, 1.1]} radius={0.28} smoothness={4} position={[0, -1.75, 0]} castShadow>
        <meshStandardMaterial {...shell} />
      </RoundedBox>
      {/* Glowing chest core ("live" AI signal) */}
      <mesh position={[0, -1.65, 0.6]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.32, 0.32, 0.12, 32]} />
        <meshStandardMaterial ref={core} color={ACCENT} emissive={ACCENT} emissiveIntensity={2} metalness={0.3} roughness={0.4} />
      </mesh>

      {/* Arms */}
      {[-1.15, 1.15].map((x) => (
        <mesh key={`a${x}`} position={[x, -1.7, 0]} rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[0.16, 0.16, 0.5, 16]} />
          <meshStandardMaterial color={BODY} metalness={0.65} roughness={0.3} />
        </mesh>
      ))}
    </group>
  );
}
