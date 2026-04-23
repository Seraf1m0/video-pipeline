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

  const s1 = 1 + Math.sin(frame * 0.022) * 0.08;
  const s2 = 1 + Math.sin(frame * 0.016 + 2.1) * 0.06;
  const nx  = noise2D("ctx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("cty", 0, frame * 0.0015) * 4;

  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${48 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s2})`,
        width: 1100, height: 600, borderRadius: "50%",
        background: `radial-gradient(ellipse, ${color} 0%, transparent 65%)`,
        filter: "blur(120px)",
        opacity: op * 0.10,
      }} />
      <div style={{
        position: "absolute",
        left: `${50 - nx * 0.5}%`, top: `${50 - ny * 0.5}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 500, height: 500, borderRadius: "50%",
        background: color, filter: "blur(90px)",
        opacity: op * 0.13,
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
      boxShadow: `0 0 22px ${color}88`,
      opacity: op,
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
      boxShadow: `0 0 16px ${color}55`,
      opacity: op,
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
  const glowMult = 0.8 + rand() * 0.4;

  const fadeIn    = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  const finalFade = interpolate(frame, [totalFrames - 6, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Chapter label — slides in from left at frame 8
  const chapterEnter = 8;
  const chapterSpr   = spring({ frame: frame - chapterEnter, fps, config: { damping: 24, stiffness: 220 } });
  const chapterX     = interpolate(chapterSpr, [0, 1], [-120, 0]);
  const chapterOp    = interpolate(frame, [chapterEnter, chapterEnter + 14], [0, 1], { extrapolateRight: "clamp" });

  // Chapter exit — slides left
  const chapterExitOp = interpolate(frame, [exitStart + 4, exitStart + 24], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const chapterExitX  = interpolate(frame, [exitStart + 4, exitStart + 24], [0, -200], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  // Horizontal rule between chapter and title — grows from left
  const ruleEnter = chapterEnter + 12;
  const ruleW     = interpolate(frame, [ruleEnter, ruleEnter + 28], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  const ruleOp    = interpolate(frame, [exitStart, exitStart + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Title crashes in from below (heavy spring)
  const titleEnter = ruleEnter + 8;
  const titleSpr   = spring({ frame: frame - titleEnter, fps, config: { damping: 8, stiffness: 400, mass: 1.1 } });
  const titleY     = interpolate(titleSpr, [0, 1], [200, 0]);
  const titleOp    = interpolate(frame, [titleEnter, titleEnter + 10], [0, 1], { extrapolateRight: "clamp" });

  // Title exit — slides right
  const titleExitOp = interpolate(frame, [exitStart + 4, exitStart + 26], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const titleExitX  = interpolate(frame, [exitStart + 4, exitStart + 26], [0, 300], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  // Sub fades in after title settles
  const subEnter = titleEnter + 28;
  const subOp    = interpolate(frame, [subEnter, subEnter + 16], [0, 1], { extrapolateRight: "clamp" });
  const subExitOp = interpolate(frame, [exitStart, exitStart + 14], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Decorative vertical lines — fade in
  const vLineOp = interpolate(frame, [titleEnter, titleEnter + 20], [0, 1], { extrapolateRight: "clamp" });
  const vLineExitOp = interpolate(frame, [exitStart, exitStart + 18], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const titleLen  = title.length;
  const titleSize = titleLen > 40 ? 72 : titleLen > 25 ? 92 : titleLen > 15 ? 112 : 130;
  const glow      = (0.5 + Math.sin(frame * 0.06) * 0.5) * glowMult;

  const chapterFinalOp = chapterOp * (frame >= exitStart + 4 ? chapterExitOp : 1);
  const chapterFinalX  = chapterX + chapterExitX;
  const titleFinalOp   = titleOp * (frame >= exitStart + 4 ? titleExitOp : 1);
  const titleFinalX    = titleExitX;

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
        <Sequence from={chapterEnter} durationInFrames={22}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.15} />
        </Sequence>
        <Sequence from={titleEnter + 4} durationInFrames={26}>
          <Audio src={staticFile("sfx/impact.wav")} volume={0.22} />
        </Sequence>
        <Sequence from={titleEnter + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.16} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        {/* Decorative left vertical line */}
        <div style={{
          position: "absolute",
          left: 100, top: "25%", bottom: "25%",
          width: 1.5,
          background: `linear-gradient(to bottom, transparent, ${accent_color}66, transparent)`,
          opacity: vLineOp * (frame >= exitStart ? vLineExitOp : 1),
        }} />
        {/* Decorative right vertical line */}
        <div style={{
          position: "absolute",
          right: 100, top: "25%", bottom: "25%",
          width: 1.5,
          background: `linear-gradient(to bottom, transparent, ${accent_color}66, transparent)`,
          opacity: vLineOp * (frame >= exitStart ? vLineExitOp : 1),
        }} />

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 0, padding: "0 140px",
        }}>
          {/* Chapter label */}
          <div style={{
            opacity: chapterFinalOp,
            transform: `translateX(${chapterFinalX}px)`,
            fontFamily: SYNE,
            fontSize: 13,
            fontWeight: "800",
            letterSpacing: "0.42em",
            textTransform: "uppercase",
            fontVariant: "small-caps",
            color: accent_color,
            textShadow: `0 0 30px ${accent_color}88`,
            marginBottom: 20,
            textAlign: "center",
          }}>
            {chapter}
          </div>

          {/* Horizontal rule */}
          <div style={{
            width: `${ruleW}%`, maxWidth: 480, height: 1,
            background: `linear-gradient(to right, transparent, ${accent_color}88, transparent)`,
            opacity: frame >= exitStart ? ruleOp : 1,
            marginBottom: 28,
          }} />

          {/* Main title */}
          <div style={{
            opacity: titleFinalOp,
            transform: `translate(${titleFinalX}px, ${titleY}px)`,
            fontFamily: SYNE,
            fontSize: titleSize,
            fontWeight: "800",
            letterSpacing: "-0.03em",
            lineHeight: 1.05,
            color: "#FFFFFF",
            textAlign: "center",
            textShadow: `
              0 0 ${60 * glow}px ${accent_color}66,
              0 0 ${120 * glow}px ${accent_color}22
            `,
          }}>
            {title}
          </div>

          {/* Sub */}
          {sub && (
            <div style={{
              opacity: subOp * (frame >= exitStart ? subExitOp : 1),
              fontFamily: MANROPE,
              fontSize: 18,
              fontWeight: "400",
              color: "#FFFFFF55",
              textAlign: "center",
              letterSpacing: "0.05em",
              lineHeight: 1.5,
              marginTop: 28,
              maxWidth: 560,
            }}>
              {sub}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
