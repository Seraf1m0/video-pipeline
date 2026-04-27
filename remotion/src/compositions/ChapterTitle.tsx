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

export interface ChapterTitleProps {
  chapter: string;
  title: string;
  sub?: string;
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
  const nx  = noise2D("ctbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("ctby", 0, frame * 0.0015) * 4;

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
export const ChapterTitle: React.FC<ChapterTitleProps> = ({
  chapter,
  title,
  sub,
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

  // Adaptive title size
  const titleFontSize = title.length > 40 ? 80 : title.length > 25 ? 100 : 120;

  // Left accent bar — spring height reveal
  const barH = interpolate(
    spring({ frame: frame - 4, fps, config: { damping: 24, stiffness: 180 } }),
    [0, 1], [0, 60]
  );
  const barOp = interpolate(frame, [exitStart, exitStart + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Staggered entries — chapter label, title, sub
  const makeEntry = (startF: number) => {
    const spr = spring({ frame: frame - startF, fps, config: { damping: 26, stiffness: 240 } });
    const x   = interpolate(spr, [0, 1], [-80, 0]);
    const op  = interpolate(frame, [startF, startF + 14], [0, 1], { extrapolateRight: "clamp" })
              * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    return { x, op };
  };

  const chapterEntry = makeEntry(6);
  const titleEntry   = makeEntry(16);
  const subEntry     = makeEntry(26);

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
        <Sequence from={6} durationInFrames={24}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.14} />
        </Sequence>
        <Sequence from={16} durationInFrames={28}>
          <Audio src={staticFile("sfx/impact.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        {/* Left accent bar */}
        <div style={{
          position: "absolute",
          left: 72,
          top: "20%",
          width: 4,
          height: `${barH}%`,
          background: accent_color,
          boxShadow: `0 0 20px ${accent_color}88`,
          opacity: barOp,
          borderRadius: 2,
        }} />

        <AbsoluteFill style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "0 120px",
          gap: 12,
        }}>
          {/* Chapter label */}
          <div style={{
            opacity: chapterEntry.op,
            transform: `translateX(${chapterEntry.x}px)`,
            fontFamily: SYNE,
            fontSize: 16,
            fontWeight: "800",
            letterSpacing: "0.35em",
            textTransform: "uppercase",
            color: accent_color,
          }}>
            {chapter}
          </div>

          {/* Main title */}
          <div style={{
            opacity: titleEntry.op,
            transform: `translateX(${titleEntry.x}px)`,
            fontFamily: SYNE,
            fontSize: titleFontSize,
            fontWeight: "800",
            color: "#FFFFFF",
            letterSpacing: "-0.02em",
            lineHeight: 1.0,
          }}>
            {title}
          </div>

          {/* Sub */}
          {sub && (
            <div style={{
              opacity: subEntry.op,
              transform: `translateX(${subEntry.x}px)`,
              fontFamily: MONTSERRAT,
              fontSize: 24,
              fontWeight: "600",
              color: "#FFFFFFCC",
              letterSpacing: "0.01em",
              lineHeight: 1.5,
              marginTop: 8,
            }}>
              {sub}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
