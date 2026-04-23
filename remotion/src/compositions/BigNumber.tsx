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

export interface BigNumberProps {
  value: string;
  unit: string;
  description: string;
  context?: string;
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const EXIT_DUR = 44;

// ─── AURA ─────────────────────────────────────────────────────────────────────
const Aura: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;

  const s1 = 1.0 + Math.sin(frame * 0.040) * 0.09;
  const s2 = 1.0 + Math.sin(frame * 0.060 + 1) * 0.07;
  const s3 = 1.0 + Math.sin(frame * 0.030 + 2) * 0.11;
  const nx  = noise2D("bnx", frame * 0.002, 0) * 4;
  const ny  = noise2D("bny", 0, frame * 0.002) * 3;

  return (
    <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%, -50%) scale(${s3})`,
        width: 900, height: 900, borderRadius: "50%",
        background: color, filter: "blur(180px)",
        opacity: op * (0.09 + Math.sin(frame * 0.05) * 0.03),
      }} />
      <div style={{
        position: "absolute",
        left: `${50 - nx * 0.5}%`, top: `${50 - ny * 0.5}%`,
        transform: `translate(-50%, -50%) scale(${s1})`,
        width: 560, height: 560, borderRadius: "50%",
        background: color, filter: "blur(110px)",
        opacity: op * (0.14 + Math.sin(frame * 0.04) * 0.04),
      }} />
      <div style={{
        position: "absolute",
        left: "50%", top: "50%",
        transform: `translate(-50%, -50%) scale(${s2})`,
        width: 260, height: 260, borderRadius: "50%",
        background: color, filter: "blur(60px)",
        opacity: op * (0.18 + Math.sin(frame * 0.05) * 0.06),
      }} />
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 90% 85% at 50% 50%, transparent 20%, #000000DD 100%)",
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
  const op = inOp * outOp * 0.06;
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

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const BigNumber: React.FC<BigNumberProps> = ({
  value,
  unit,
  description,
  context,
  accent_color = "#00C8FF",
  duration_s   = 8,
  bg_color     = "#020218",
  seed         = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_DUR;

  const rand = seededRand(seed);
  const glowMult = 0.8 + rand() * 0.4;

  const fadeIn    = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  const finalFade = interpolate(frame, [totalFrames - 6, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Value + unit crash in from above together
  const crashEnter = 6;
  const crashSpr   = spring({ frame: frame - crashEnter, fps, config: { damping: 7, stiffness: 500, mass: 1.2 } });
  const crashPrev  = spring({ frame: frame - crashEnter - 1, fps, config: { damping: 7, stiffness: 500, mass: 1.2 } });
  const velocity   = Math.abs((crashSpr - crashPrev) * 300);
  const blurPx     = Math.min(velocity * 0.4, 28);
  const crashY     = interpolate(crashSpr, [0, 1], [-300, 0]);
  const crashOp    = interpolate(frame, [crashEnter, crashEnter + 10], [0, 1], { extrapolateRight: "clamp" });

  const glow = (0.5 + Math.sin(frame * 0.06) * 0.5) * glowMult;
  const breathe = 1 + Math.sin(frame * 0.045) * 0.012;

  // Crash exit
  const crashExitOp = interpolate(frame, [exitStart + 4, exitStart + 26], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Description slides up
  const descEnter = crashEnter + 26;
  const descSpr   = spring({ frame: frame - descEnter, fps, config: { damping: 22, stiffness: 240 } });
  const descY     = interpolate(descSpr, [0, 1], [24, 0]);
  const descOp    = interpolate(frame, [descEnter, descEnter + 14], [0, 1], { extrapolateRight: "clamp" }) *
                    interpolate(frame, [exitStart + 2, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Context fades in last
  const ctxEnter = descEnter + 18;
  const ctxOp    = interpolate(frame, [ctxEnter, ctxEnter + 14], [0, 1], { extrapolateRight: "clamp" }) *
                   interpolate(frame, [exitStart, exitStart + 18], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const valLen   = value.length;
  const valSize  = valLen > 8 ? 150 : valLen > 5 ? 180 : valLen > 3 ? 210 : 220;

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Aura frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={crashEnter + 2} durationInFrames={26}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.15} />
        </Sequence>
        <Sequence from={crashEnter + 8} durationInFrames={26}>
          <Audio src={staticFile("sfx/impact.wav")} volume={0.28} />
        </Sequence>
        <Sequence from={crashEnter + 10} durationInFrames={28}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={descEnter} durationInFrames={24}>
          <Audio src={staticFile("sfx/stinger.wav")} volume={0.14} />
        </Sequence>
        <Sequence from={exitStart + 8} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 0, padding: "0 100px",
        }}>
          {/* Value + Unit row */}
          <div style={{
            opacity: crashOp * (frame >= exitStart + 4 ? crashExitOp : 1),
            transform: `translateY(${crashY}px) scale(${breathe})`,
            filter: blurPx > 0.8 ? `blur(${blurPx}px)` : undefined,
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "center",
            gap: 12,
            lineHeight: 1,
          }}>
            {/* Value */}
            <span style={{
              fontFamily: SYNE,
              fontSize: valSize,
              fontWeight: "800",
              letterSpacing: "-0.04em",
              color: accent_color,
              textShadow: `
                0 0 ${80 * glow}px ${accent_color}CC,
                0 0 ${160 * glow}px ${accent_color}55,
                0 0 ${260 * glow}px ${accent_color}22
              `,
            }}>
              {value}
            </span>
            {/* Unit */}
            <span style={{
              fontFamily: MANROPE,
              fontSize: 60,
              fontWeight: "600",
              color: "#FFFFFF",
              letterSpacing: "-0.02em",
              paddingBottom: 12,
            }}>
              {unit}
            </span>
          </div>

          {/* Description */}
          <div style={{
            opacity: descOp,
            transform: `translateY(${descY}px)`,
            fontFamily: SYNE,
            fontSize: 22,
            fontWeight: "700",
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "#FFFFFFCC",
            textAlign: "center",
            marginTop: 8,
          }}>
            {description}
          </div>

          {/* Context */}
          {context && (
            <div style={{
              opacity: ctxOp,
              fontFamily: MANROPE,
              fontSize: 16,
              fontWeight: "400",
              color: "#FFFFFF55",
              textAlign: "center",
              letterSpacing: "0.04em",
              lineHeight: 1.5,
              marginTop: 14,
              maxWidth: 540,
            }}>
              {context}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
