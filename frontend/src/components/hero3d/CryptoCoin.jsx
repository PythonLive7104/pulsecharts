// Spinning metallic coin — the hero centerpiece. The face emblem is drawn onto a
// canvas at runtime (PulseCharts mark, not an external image), so there's no CDN
// dependency. Bobs, spins, and tilts toward the pointer. Responsive via viewport.
import { useMemo, useRef } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import * as THREE from "three";

// Paint a gold coin face with the PulseCharts emblem (rising bars + pulse line).
function makeCoinTexture() {
  const S = 512;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const ctx = c.getContext("2d");
  const cx = S / 2;
  const cy = S / 2;
  const R = S * 0.47;

  // gold disc with an off-centre highlight for a metallic feel
  const g = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.35, R * 0.1, cx, cy, R);
  g.addColorStop(0, "#ffe9a8");
  g.addColorStop(0.55, "#e7b04e");
  g.addColorStop(1, "#a86f1c");
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(cx, cy, R, 0, Math.PI * 2);
  ctx.fill();

  // engraved rings
  ctx.strokeStyle = "rgba(255,255,255,0.35)";
  ctx.lineWidth = S * 0.012;
  ctx.beginPath();
  ctx.arc(cx, cy, R * 0.82, 0, Math.PI * 2);
  ctx.stroke();
  ctx.strokeStyle = "rgba(120,70,10,0.45)";
  ctx.lineWidth = S * 0.02;
  ctx.beginPath();
  ctx.arc(cx, cy, R * 0.9, 0, Math.PI * 2);
  ctx.stroke();

  // emblem: three rising bars + a pulse line (matches the PulseCharts logo)
  ctx.save();
  ctx.translate(cx, cy);
  const bw = S * 0.075;
  const gap = S * 0.05;
  const baseY = S * 0.17;
  const bars = [
    [-(bw + gap), S * 0.11],
    [0, S * 0.18],
    [bw + gap, S * 0.27],
  ];
  bars.forEach(([x, h], i) => {
    ctx.fillStyle = `rgba(40,25,5,${0.55 + 0.16 * i})`;
    ctx.fillRect(x - bw / 2, baseY - h, bw, h);
  });
  ctx.strokeStyle = "#23303f";
  ctx.lineWidth = S * 0.024;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(-S * 0.19, -S * 0.02);
  ctx.lineTo(-S * 0.07, -S * 0.1);
  ctx.lineTo(0, -S * 0.05);
  ctx.lineTo(S * 0.1, -S * 0.19);
  ctx.lineTo(S * 0.19, -S * 0.25);
  ctx.stroke();
  ctx.restore();

  const tex = new THREE.CanvasTexture(c);
  tex.anisotropy = 4;
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

export default function CryptoCoin() {
  const group = useRef();
  const { viewport } = useThree();
  const isMobile = viewport.width < 6;

  const texture = useMemo(() => makeCoinTexture(), []);
  const scale = isMobile ? 0.54 : 0.765;
  const baseX = isMobile ? 0 : 2.5;
  const baseY = isMobile ? -2.5 : 0;

  useFrame((state) => {
    const g = group.current;
    if (!g) return;
    const t = state.clock.elapsedTime;
    g.position.x = baseX;
    g.position.y = Math.sin(t * 0.5) * 0.2 + baseY;
    g.rotation.y = t * 0.3; // constant coin spin
    // subtle parallax tilt toward the pointer
    g.rotation.x = THREE.MathUtils.lerp(g.rotation.x, 0.15 + state.pointer.y * 0.2, 0.05);
    g.rotation.z = THREE.MathUtils.lerp(g.rotation.z, -state.pointer.x * 0.12, 0.05);
  });

  return (
    <group ref={group} position={[baseX, baseY, 0]} scale={scale}>
      {/* gold rim — cylinder rotated so its faces point at the camera */}
      <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
        <cylinderGeometry args={[2.8, 2.8, 0.4, 64]} />
        <meshStandardMaterial color="#d99f3c" metalness={0.95} roughness={0.18} envMapIntensity={1.4} />
      </mesh>
      {/* front + back faces */}
      <mesh position={[0, 0, 0.205]}>
        <planeGeometry args={[5.6, 5.6]} />
        <meshStandardMaterial transparent map={texture} metalness={0.7} roughness={0.25} envMapIntensity={1.5} />
      </mesh>
      <mesh position={[0, 0, -0.205]} rotation={[0, Math.PI, 0]}>
        <planeGeometry args={[5.6, 5.6]} />
        <meshStandardMaterial transparent map={texture} metalness={0.7} roughness={0.25} envMapIntensity={1.5} />
      </mesh>
    </group>
  );
}
