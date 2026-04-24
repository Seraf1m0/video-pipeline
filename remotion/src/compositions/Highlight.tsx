import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne    } from "@remotion/google-fonts/Syne";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { noise2D } from "@remotion/noise";
import { seededRand } from "../utils/seeded";

const { fontFamily: SYNE    } = loadSyne();
const { fontFamily: MONTSERRAT } = loadMontserrat();

export interface HighlightProps {
  value: string;
  label: string;
  sub?: string;
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

// ─── BACKGROUND AURA ──────────────────────────────────────────────────────────
const Aura: React.FC<{ frame: number; color: string }> = ({ frame, color }) => {
  // Multiple breathing layers
  const s1 = 1.0 + Math.sin(frame * 0.04) * 0.08;
  const s2 = 1.0 + Math.sin(frame * 0.06 + 1) * 0.06;
  const s3 = 1.0 + Math.sin(frame * 0.03 + 2) * 0.10;

  const op1 = 0.14 + Math.sin(frame * 0.04) * 0.05;
  const op2 = 0.08 + Math.sin(frame * 0.05 + 1) * 0.03;

  const nx = noise2D("ax", frame * 0.002, 0) * 4;
  const ny = noise2D("ay", 0, frame * 0.002) * 3;

  return (
    <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
      {/* Outer halo */}
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%, -50%) scale(${s3})`,
        width: 900, height: 900, borderRadius: "50%",
        background: color, filter: "blur(180px)", opacity: op2,
      }} />
      {/* Mid glow */}
      <div style={{
        position: "absolute",
        left: `${50 - nx * 0.5}%`, top: `${50 - ny * 0.5}%`,
        transform: `translate(-50%, -50%) scale(${s1})`,
        width: 560, height: 560, borderRadius: "50%",
        background: color, filter: "blur(110px)", opacity: op1,
      }} />
      {/* Core hotspot */}
      <div style={{
        position: "absolute",
        left: "50%", top: "50%",
        transform: `translate(-50%, -50%) scale(${s2})`,
        width: 260, height: 260, borderRadius: "50%",
        background: color, filter: "blur(60px)",
        opacity: 0.18 + Math.sin(frame * 0.05) * 0.06,
      }} />
    </AbsoluteFill>
  );
};

// ─── SPARK PARTICLES ──────────────────────────────────────────────────────────
const Sparks: React.FC<{ frame: number; color: string; valueReady: boolean }> = ({
  frame, color, valueReady,
}) => {
  if (!valueReady) return null;

  const sparks = Array.from({ length: 12 }, (_, i) => {
    const seed  = i * 137.5;
    const angle = (seed % 360) * (Math.PI / 180);
    const speed = 0.8 + (i % 3) * 0.4;
    const life  = 40 + (i % 4) * 15;
    const startFrame = 8 + (i % 5) * 3;
    const age   = frame - startFrame;

    if (age < 0 || age > life) return null;

    const t   = age / life;
    const r   = (80 + speed * 200 * t) * (1 - t * 0.3);
    const x   = Math.cos(angle) * r;
    const y   = Math.sin(angle) * r * 0.6;
    const op  = interpolate(t, [0, 0.2, 0.7, 1], [0, 0.8, 0.5, 0]);
    const sz  = interpolate(t, [0, 0.15, 1], [0, 3, 1.5]);

    return { x, y, op, sz, i };
  }).filter(Boolean);

  return (
    <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
      {sparks.map(s => s && (
        <div key={s.i} style={{
          position: "absolute",
          left: `calc(50% + ${s.x}px)`,
          top:  `calc(50% + ${s.y}px)`,
          width: s.sz, height: s.sz,
          borderRadius: "50%",
          background: color,
          boxShadow: `0 0 ${s.sz * 3}px ${color}`,
          opacity: s.op,
          transform: "translate(-50%, -50%)",
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── RING EXPAND ──────────────────────────────────────────────────────────────
const RingExpand: React.FC<{ frame: number; color: string }> = ({ frame, color }) => {
  // Three rings that expand outward on impact
  const rings = [0, 8, 16].map((offset, i) => {
    const age = frame - 6 - offset;
    if (age < 0) return null;
    const t  = Math.min(age / 50, 1);
    const r  = interpolate(t, [0, 1], [40, 340], { easing: Easing.out(Easing.cubic) });
    const op = interpolate(t, [0, 0.1, 0.6, 1], [0, 0.4, 0.15, 0]);
    return { r, op, i };
  }).filter(Boolean);

  return (
    <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
      {rings.map(ring => ring && (
        <div key={ring.i} style={{
          position: "absolute",
          left: "50%", top: "50%",
          transform: "translate(-50%, -50%)",
          width:  ring.r * 2,
          height: ring.r * 2,
          borderRadius: "50%",
          border: `1.5px solid ${color}`,
          opacity: ring.op,
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── SCAN LINE ────────────────────────────────────────────────────────────────
const ScanLine: React.FC<{ frame: number; color: string }> = ({ frame, color }) => {
  const y  = interpolate(frame, [0, 16], [0, 110], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const op = interpolate(frame, [0, 3, 13, 20], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0,
      height: 2,
      background: `linear-gradient(to right, transparent, ${color}CC, transparent)`,
      boxShadow: `0 0 24px ${color}99, 0 0 48px ${color}33`,
      opacity: op,
    }} />
  );
};

// ─── MAIN VALUE ───────────────────────────────────────────────────────────────
const MainValue: React.FC<{
  value: string; color: string;
  frame: number; totalFrames: number; glowMult?: number;
}> = ({ value, color, frame, totalFrames, glowMult = 1 }) => {
  const { fps } = useVideoConfig();

  // Massive crash from above
  const crashSpring = spring({
    frame: frame - 6, fps,
    config: { damping: 7, stiffness: 500, mass: 1.2 },
  });
  const crashPrev = spring({
    frame: frame - 7, fps,
    config: { damping: 7, stiffness: 500, mass: 1.2 },
  });
  const velocity = Math.abs((crashSpring - crashPrev) * 300);
  const blurPx   = Math.min(velocity * 0.45, 30);

  const y       = interpolate(crashSpring, [0, 1], [-380, 0]);
  const opacity = interpolate(frame, [6, 14], [0, 1], { extrapolateRight: "clamp" });

  // breathe + glow after landing
  const breathe = 1 + Math.sin(frame * 0.045) * 0.012;
  const glow    = (0.5 + Math.sin(frame * 0.06) * 0.5) * glowMult;

  const len  = value.length;
  const size = len > 8 ? 160 : len > 5 ? 200 : len > 3 ? 240 : 280;

  return (
    <div style={{
      opacity,
      transform: `translateY(${y}px) scale(${breathe})`,
      filter: blurPx > 0.8 ? `blur(${blurPx}px)` : undefined,
      fontFamily: SYNE,
      fontSize: size,
      fontWeight: "800",
      letterSpacing: "-0.04em",
      lineHeight: 1,
      color: color,
      textShadow: `
        0 0 ${80 * glow}px ${color}CC,
        0 0 ${160 * glow}px ${color}55,
        0 0 ${260 * glow}px ${color}22
      `,
      textAlign: "center",
      userSelect: "none",
    }}>
      {value}
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const Highlight: React.FC<HighlightProps> = ({
  value,
  label,
  sub,
  accent_color = "#00C8FF",
  duration_s   = 7,
  bg_color     = "#020218",
  seed         = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);

  const rand = seededRand(seed);
  const glowMult = 0.8 + rand() * 0.4;

  const fadeIn  = interpolate(frame, [0, 6],  [0, 1], { extrapolateRight: "clamp" });
  const fadeOut = interpolate(frame, [totalFrames - 10, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // label slides up after value lands
  const lblEnter = 28;
  const { fps: _fps } = useVideoConfig();
  const lblProg = spring({ frame: frame - lblEnter, fps, config: { damping: 22, stiffness: 260 } });
  const lblY    = interpolate(lblProg, [0, 1], [20, 0]);
  const lblOp   = interpolate(frame, [lblEnter, lblEnter + 12], [0, 1], { extrapolateRight: "clamp" });

  // sub fades in
  const subEnter = lblEnter + 16;
  const subOp    = interpolate(frame, [subEnter, subEnter + 14], [0, 1], { extrapolateRight: "clamp" });

  // label accent line
  const lineW = interpolate(frame, [lblEnter, lblEnter + 30], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.exp),
  });

  const valueReady = frame >= 6;

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * fadeOut, width: "100%", height: "100%" }}>
        <Aura frame={frame} color={accent_color} />
        <ScanLine frame={frame} color={accent_color} />
        <RingExpand frame={frame} color={accent_color} />
        <Sparks frame={frame} color={accent_color} valueReady={valueReady} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.20} />
        </Sequence>
        <Sequence from={4} durationInFrames={25}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.16} />
        </Sequence>
        {/* Massive impact when value lands */}
        <Sequence from={10} durationInFrames={25}>
          <Audio src={staticFile("sfx/impact.wav")} volume={0.30} />
        </Sequence>
        <Sequence from={12} durationInFrames={30}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.20} />
        </Sequence>
        {/* Stinger when label appears */}
        <Sequence from={lblEnter} durationInFrames={30}>
          <Audio src={staticFile("sfx/stinger.wav")} volume={0.16} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 24, padding: "0 80px",
        }}>
          {/* Main value — giant */}
          <MainValue value={value} color={accent_color} frame={frame} totalFrames={totalFrames} glowMult={glowMult} />

          {/* Label block */}
          <div style={{
            display: "flex", flexDirection: "column",
            alignItems: "center", gap: 12,
          }}>
            <div style={{
              opacity: lblOp,
              transform: `translateY(${lblY}px)`,
              fontFamily: SYNE,
              fontSize: 14,
              fontWeight: "700",
              letterSpacing: "0.36em",
              textTransform: "uppercase",
              color: `${accent_color}CC`,
              textAlign: "center",
            }}>
              {label}
            </div>

            {/* Accent line */}
            <div style={{
              width: `${lineW}%`,
              maxWidth: 180,
              height: 1.5,
              background: `linear-gradient(to right, transparent, ${accent_color}66, transparent)`,
              borderRadius: 1,
            }} />

            {/* Sub */}
            {sub && (
              <div style={{
                opacity: subOp,
                fontFamily: MONTSERRAT,
                fontSize: 15,
                fontWeight: "600",
                color: "#FFFFFF55",
                textAlign: "center",
                letterSpacing: "0.05em",
                lineHeight: 1.5,
                maxWidth: 480,
              }}>
                {sub}
              </div>
            )}
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
