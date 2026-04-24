import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne    } from "@remotion/google-fonts/Syne";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadBebasNeue } from "@remotion/google-fonts/BebasNeue";
import { noise2D } from "@remotion/noise";
import { seededRand } from "../utils/seeded";

const { fontFamily: SYNE    } = loadSyne();
const { fontFamily: MONTSERRAT } = loadMontserrat();
const { fontFamily: BEBAS   } = loadBebasNeue();

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

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;
  const s1 = 1 + Math.sin(frame * 0.020) * 0.07;
  const nx  = noise2D("bnbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("bnby", 0, frame * 0.0015) * 4;

  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 1000, height: 700, borderRadius: "50%",
        background: `radial-gradient(ellipse, ${color} 0%, transparent 65%)`,
        filter: "blur(130px)", opacity: op * 0.10,
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
  const _u = rand();

  const fadeIn    = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  const finalFade = interpolate(frame, [totalFrames - 6, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Left panel: slides from left
  const leftSpr = spring({ frame: frame - 6, fps, config: { damping: 22, stiffness: 220 } });
  const leftX   = interpolate(leftSpr, [0, 1], [-300, 0]);
  const leftOp  = interpolate(frame, [6, 20], [0, 1], { extrapolateRight: "clamp" });

  // Right panel: slides from right
  const rightSpr = spring({ frame: frame - 6, fps, config: { damping: 22, stiffness: 220 } });
  const rightX   = interpolate(rightSpr, [0, 1], [300, 0]);
  const rightOp  = interpolate(frame, [6, 20], [0, 1], { extrapolateRight: "clamp" });

  // Exit — both slide off
  const exitOp = interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const leftExitX  = interpolate(frame, [exitStart + 4, exitStart + 22], [0, -300], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const rightExitX = interpolate(frame, [exitStart + 4, exitStart + 22], [0, 300], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  // Vertical line reveal
  const lineH = interpolate(frame, [14, 36], [0, 60], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  const lineOp = interpolate(frame, [exitStart, exitStart + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const combinedLeftOp  = leftOp  * (frame >= exitStart + 4 ? exitOp : 1);
  const combinedRightOp = rightOp * (frame >= exitStart + 4 ? exitOp : 1);
  const combinedLeftX   = leftX   + leftExitX;
  const combinedRightX  = rightX  + rightExitX;

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={8} durationInFrames={28}>
          <Audio src={staticFile("sfx/impact.wav")} volume={0.22} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          padding: "60px 100px",
        }}>
          {/* Left 55% — huge value */}
          <div style={{
            width: "55%",
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            justifyContent: "center",
            paddingRight: 60,
            opacity: combinedLeftOp,
            transform: `translateX(${combinedLeftX}px)`,
          }}>
            {/* Unit label above */}
            <div style={{
              fontFamily: SYNE,
              fontSize: 13,
              fontWeight: "800",
              letterSpacing: "0.4em",
              textTransform: "uppercase",
              color: accent_color,
              marginBottom: 8,
            }}>
              {unit}
            </div>

            {/* Main value */}
            <div style={{
              fontFamily: BEBAS,
              fontSize: 260,
              letterSpacing: "0.02em",
              color: "#FFFFFF",
              lineHeight: 0.88,
              textShadow: `0 0 80px ${accent_color}44`,
            }}>
              {value}
            </div>
          </div>

          {/* Vertical accent line */}
          <div style={{
            width: 1,
            height: `${lineH}%`,
            background: `linear-gradient(to bottom, transparent, ${accent_color}88, transparent)`,
            flexShrink: 0,
            opacity: lineOp,
          }} />

          {/* Right 45% — description + context */}
          <div style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            justifyContent: "center",
            paddingLeft: 60,
            gap: 20,
            opacity: combinedRightOp,
            transform: `translateX(${combinedRightX}px)`,
          }}>
            <div style={{
              fontFamily: SYNE,
              fontSize: 28,
              fontWeight: "800",
              color: "#FFFFFF",
              letterSpacing: "-0.01em",
              lineHeight: 1.25,
            }}>
              {description}
            </div>
            {context && (
              <div style={{
                fontFamily: MONTSERRAT,
                fontSize: 16,
                fontWeight: "600",
                color: "#FFFFFF88",
                lineHeight: 1.5,
                letterSpacing: "0.01em",
              }}>
                {context}
              </div>
            )}
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
