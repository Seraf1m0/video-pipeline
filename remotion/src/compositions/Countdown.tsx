import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne    } from "@remotion/google-fonts/Syne";
import { loadFont as loadManrope } from "@remotion/google-fonts/Manrope";
import { noise2D } from "@remotion/noise";
import { seededRand } from "../utils/seeded";

const { fontFamily: SYNE    } = loadSyne();
const { fontFamily: MANROPE } = loadManrope();

export interface CountdownProps {
  from?: number;
  label?: string;
  accent_color?: number;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
  accent_color_str?: string;
}

const EXIT_DUR = 44;

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; color: string; total: number; intensity: number }> = ({
  frame, color, total, intensity,
}) => {
  const inOp  = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;

  const s1 = 1 + Math.sin(frame * 0.040) * 0.12 + intensity * 0.15;
  const s2 = 1 + Math.sin(frame * 0.060 + 1) * 0.08;
  const nx  = noise2D("cdx", frame * 0.002, 0) * 4;
  const ny  = noise2D("cdy", 0, frame * 0.002) * 3;

  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 800, height: 800, borderRadius: "50%",
        background: `radial-gradient(circle, ${color} 0%, transparent 65%)`,
        filter: "blur(140px)",
        opacity: op * (0.16 + intensity * 0.12),
      }} />
      <div style={{
        position: "absolute",
        left: `${50 - nx * 0.5}%`, top: `${50 - ny * 0.5}%`,
        transform: `translate(-50%,-50%) scale(${s2})`,
        width: 400, height: 400, borderRadius: "50%",
        background: color, filter: "blur(80px)",
        opacity: op * (0.20 + intensity * 0.15),
      }} />
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 90% 85% at 50% 50%, transparent 15%, #000000E0 100%)",
      }} />
    </AbsoluteFill>
  );
};

// ─── GRID ─────────────────────────────────────────────────────────────────────
const Grid: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [8, 28], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp * 0.05;
  return (
    <AbsoluteFill style={{ opacity: op }}>
      {Array.from({ length: 9 }, (_, i) => (
        <div key={`v${i}`} style={{
          position: "absolute", left: `${(i / 8) * 100}%`, top: 0, bottom: 0, width: 1,
          background: `linear-gradient(to bottom, transparent, ${color}44, transparent)`,
        }} />
      ))}
      {Array.from({ length: 6 }, (_, i) => (
        <div key={`h${i}`} style={{
          position: "absolute", top: `${(i / 5) * 100}%`, left: 0, right: 0, height: 1,
          background: "linear-gradient(to right, transparent, #FFFFFF10, transparent)",
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── SCAN LINE ────────────────────────────────────────────────────────────────
const ScanLine: React.FC<{ frame: number; color: string }> = ({ frame, color }) => {
  const y  = interpolate(frame, [0, 20], [0, 110], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });
  const op = interpolate(frame, [0, 4, 16, 24], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0, height: 2,
      background: `linear-gradient(to right, transparent, ${color}CC, transparent)`,
      boxShadow: `0 0 22px ${color}88`, opacity: op,
    }} />
  );
};

// ─── EXIT SCAN ────────────────────────────────────────────────────────────────
const ExitScan: React.FC<{ frame: number; total: number; color: string }> = ({ frame, total, color }) => {
  const start = total - EXIT_DUR + 10;
  const y  = interpolate(frame, [start, start + 18], [110, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const op = interpolate(frame, [start, start + 3, start + 14, start + 22], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0, height: 2,
      background: `linear-gradient(to right, transparent, ${color}88, transparent)`,
      boxShadow: `0 0 16px ${color}55`, opacity: op,
    }} />
  );
};

// ─── RING EXPAND ──────────────────────────────────────────────────────────────
const RingExpand: React.FC<{ frame: number; color: string; trigger: number }> = ({ frame, color, trigger }) => {
  const rings = [0, 7, 14].map((offset, i) => {
    const age = frame - trigger - offset;
    if (age < 0) return null;
    const t  = Math.min(age / 45, 1);
    const r  = interpolate(t, [0, 1], [50, 380], { easing: Easing.out(Easing.cubic) });
    const op = interpolate(t, [0, 0.08, 0.5, 1], [0, 0.5, 0.2, 0]);
    return { r, op, i };
  }).filter(Boolean);

  return (
    <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
      {rings.map(ring => ring && (
        <div key={ring.i} style={{
          position: "absolute", left: "50%", top: "50%",
          transform: "translate(-50%, -50%)",
          width: ring.r * 2, height: ring.r * 2,
          borderRadius: "50%",
          border: `2px solid ${color}`,
          opacity: ring.op,
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── IMPACT FLASH ─────────────────────────────────────────────────────────────
const ImpactFlash: React.FC<{ frame: number; color: string; trigger: number }> = ({ frame, color, trigger }) => {
  const age = frame - trigger;
  const opacity = interpolate(age, [0, 2, 6, 14], [0, 0.22, 0.08, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return <AbsoluteFill style={{ background: color, opacity, pointerEvents: "none" }} />;
};

// ─── PARTICLES ────────────────────────────────────────────────────────────────
const Particles: React.FC<{ frame: number; color: string; trigger: number }> = ({ frame, color, trigger }) => {
  const age = frame - trigger;
  if (age < 0 || age > 50) return null;

  const particles = Array.from({ length: 16 }, (_, i) => {
    const angle  = (i / 16) * Math.PI * 2;
    const speed  = 0.8 + (i % 4) * 0.3;
    const life   = 35 + (i % 5) * 5;
    const startAge = (i % 3) * 2;
    const pAge   = age - startAge;
    if (pAge < 0 || pAge > life) return null;

    const t   = pAge / life;
    const r   = speed * 280 * t * (1 - t * 0.4);
    const x   = Math.cos(angle) * r;
    const y   = Math.sin(angle) * r * 0.7;
    const op  = interpolate(t, [0, 0.15, 0.7, 1], [0, 0.9, 0.5, 0]);
    const sz  = interpolate(t, [0, 0.12, 1], [0, 4, 2]);

    return { x, y, op, sz, i };
  }).filter(Boolean);

  return (
    <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
      {particles.map(p => p && (
        <div key={p.i} style={{
          position: "absolute",
          left: `calc(50% + ${p.x}px)`, top: `calc(50% + ${p.y}px)`,
          width: p.sz, height: p.sz,
          borderRadius: "50%",
          background: color,
          boxShadow: `0 0 ${p.sz * 4}px ${color}`,
          opacity: p.op,
          transform: "translate(-50%, -50%)",
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const Countdown: React.FC<CountdownProps> = ({
  from            = 3,
  label,
  duration_s      = 6,
  bg_color        = "#020218",
  seed            = 0,
  accent_color_str = "#FF3CAC",
}) => {
  const accent_color = accent_color_str;
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_DUR;

  const rand = seededRand(seed);
  const _u = rand();

  const fadeIn    = interpolate(frame, [0, 8], [0, 1], { extrapolateRight: "clamp" });
  const finalFade = interpolate(frame, [totalFrames - 6, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Per-digit timing — divide totalFrames evenly among `from` digits
  const countFrames = exitStart - 10; // frames available for count
  const framesPerDigit = Math.floor(countFrames / from);

  // Current digit info
  const digitIndex = Math.min(Math.floor(frame / framesPerDigit), from - 1);
  const digitValue = from - digitIndex;
  const digitStart = digitIndex * framesPerDigit;
  const digitAge   = frame - digitStart;

  // Spring crash from above
  const { fps: _fps } = useVideoConfig();
  const digitSpr  = spring({ frame: digitAge, fps, config: { damping: 8, stiffness: 400, mass: 1.0 } });
  const digitY    = interpolate(digitSpr, [0, 1], [-280, 0]);
  const digitOp   = interpolate(digitAge, [0, 8], [0, 1], { extrapolateRight: "clamp" });

  // Scale + fade out before next digit
  const holdFrames  = Math.floor(framesPerDigit * 0.7);
  const exitAge     = digitAge - holdFrames;
  const digitExitSc = exitAge > 0 ? interpolate(exitAge, [0, framesPerDigit * 0.3], [1, 1.35], {
    extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  }) : 1;
  const digitExitOp = exitAge > 0 ? interpolate(exitAge, [0, framesPerDigit * 0.3], [1, 0], {
    extrapolateRight: "clamp",
  }) : 1;

  // Combined
  const finalDigitOp = digitOp * digitExitOp;
  const glow         = 0.6 + Math.sin(frame * 0.1) * 0.4;

  // Trigger frame for effects — when digit crashes in
  const triggerFrame = digitStart + 4;

  // Intensity ramps up as countdown progresses
  const intensity = digitIndex / Math.max(from - 1, 1);

  // Label — appears after all digits
  const labelStart = from * framesPerDigit + 4;
  const labelSpr   = spring({ frame: frame - labelStart, fps, config: { damping: 14, stiffness: 300 } });
  const labelY     = interpolate(labelSpr, [0, 1], [40, 0]);
  const labelOp    = interpolate(frame, [labelStart, labelStart + 12], [0, 1], { extrapolateRight: "clamp" }) *
                     interpolate(frame, [exitStart + 2, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} intensity={intensity} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* Rings and flashes per digit */}
        {Array.from({ length: from }, (_, i) => {
          const trig = i * framesPerDigit + 4;
          return (
            <React.Fragment key={i}>
              <RingExpand frame={frame} color={accent_color} trigger={trig} />
              <ImpactFlash frame={frame} color={accent_color} trigger={trig} />
              <Particles frame={frame} color={accent_color} trigger={trig} />
            </React.Fragment>
          );
        })}

        {/* SFX per digit */}
        {Array.from({ length: from }, (_, i) => {
          const trig = i * framesPerDigit + 4;
          return (
            <React.Fragment key={i}>
              <Sequence from={trig} durationInFrames={22}>
                <Audio src={staticFile("sfx/impact.wav")} volume={0.22 + i * 0.04} />
              </Sequence>
              <Sequence from={trig + 2} durationInFrames={24}>
                <Audio src={staticFile("sfx/ping.wav")} volume={0.16 + i * 0.03} />
              </Sequence>
            </React.Fragment>
          );
        })}
        {label && (
          <Sequence from={labelStart} durationInFrames={30}>
            <Audio src={staticFile("sfx/stinger.wav")} volume={0.24} />
          </Sequence>
        )}
        <Sequence from={exitStart + 8} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        {/* Digit display */}
        <AbsoluteFill style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
          <div style={{
            opacity: finalDigitOp,
            transform: `translateY(${digitY}px) scale(${digitExitSc})`,
            fontFamily: SYNE,
            fontSize: 380,
            fontWeight: "800",
            letterSpacing: "-0.06em",
            lineHeight: 1,
            color: accent_color,
            textShadow: `
              0 0 ${120 * glow}px ${accent_color}DD,
              0 0 ${240 * glow}px ${accent_color}66,
              0 0 ${400 * glow}px ${accent_color}22
            `,
            userSelect: "none",
          }}>
            {digitValue}
          </div>

          {/* Label */}
          {label && frame >= labelStart && (
            <div style={{
              opacity: labelOp,
              transform: `translateY(${labelY}px)`,
              fontFamily: SYNE,
              fontSize: 48,
              fontWeight: "800",
              letterSpacing: "0.14em",
              textTransform: "uppercase",
              color: "#FFFFFF",
              textAlign: "center",
              marginTop: -20,
              textShadow: `0 0 40px ${accent_color}88`,
            }}>
              {label}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
