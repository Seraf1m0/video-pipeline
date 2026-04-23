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

export interface QuoteProps {
  text: string;
  author?: string;
  source?: string;
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
  const s2 = 1 + Math.sin(frame * 0.016 + 2.0) * 0.05;
  const nx  = noise2D("qbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("qby", 0, frame * 0.0015) * 4;

  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s2})`,
        width: 1000, height: 700, borderRadius: "50%",
        background: `radial-gradient(ellipse, ${color} 0%, transparent 65%)`,
        filter: "blur(120px)", opacity: op * 0.10,
      }} />
      <div style={{
        position: "absolute",
        left: `${50 - nx * 0.5}%`, top: `${50 - ny * 0.5}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 480, height: 480, borderRadius: "50%",
        background: color, filter: "blur(85px)", opacity: op * 0.13,
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
export const Quote: React.FC<QuoteProps> = ({
  text,
  author,
  source,
  accent_color = "#A855F7",
  duration_s   = 9,
  bg_color     = "#020218",
  seed         = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_DUR;

  const rand = seededRand(seed);
  const _unused = rand();

  const fadeIn    = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  const finalFade = interpolate(frame, [totalFrames - 6, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Big decorative quote mark fades in early
  const quoteMrkEnter = 4;
  const quoteMrkOp    = interpolate(frame, [quoteMrkEnter, quoteMrkEnter + 20], [0, 1], { extrapolateRight: "clamp" }) *
                        interpolate(frame, [exitStart + 6, exitStart + 28], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const quotePulse    = 0.85 + Math.sin(frame * 0.04) * 0.15;

  // Text slides up from below
  const textEnter = 14;
  const textSpr   = spring({ frame: frame - textEnter, fps, config: { damping: 14, stiffness: 280, mass: 0.9 } });
  const textY     = interpolate(textSpr, [0, 1], [80, 0]);
  const textOp    = interpolate(frame, [textEnter, textEnter + 14], [0, 1], { extrapolateRight: "clamp" });

  // Text exit: fades + slides left
  const textExitOp = interpolate(frame, [exitStart + 4, exitStart + 26], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const textExitX  = interpolate(frame, [exitStart + 4, exitStart + 26], [0, -180], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  // After text settles — accent line appears
  const lineEnter = textEnter + 30;
  const lineW     = interpolate(frame, [lineEnter, lineEnter + 28], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  const lineOp    = interpolate(frame, [exitStart, exitStart + 14], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Author slides in
  const authorEnter = lineEnter + 10;
  const authorSpr   = spring({ frame: frame - authorEnter, fps, config: { damping: 24, stiffness: 220 } });
  const authorY     = interpolate(authorSpr, [0, 1], [18, 0]);
  const authorOp    = interpolate(frame, [authorEnter, authorEnter + 14], [0, 1], { extrapolateRight: "clamp" }) *
                      interpolate(frame, [exitStart + 2, exitStart + 20], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Source fades in last
  const sourceEnter = authorEnter + 12;
  const sourceOp    = interpolate(frame, [sourceEnter, sourceEnter + 14], [0, 1], { extrapolateRight: "clamp" }) *
                      interpolate(frame, [exitStart, exitStart + 16], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const textLen  = text.length;
  const textSize = textLen > 120 ? 48 : textLen > 80 ? 58 : textLen > 50 ? 66 : 72;

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.16} />
        </Sequence>
        <Sequence from={textEnter} durationInFrames={22}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.14} />
        </Sequence>
        <Sequence from={authorEnter} durationInFrames={26}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.14} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        {/* Decorative quotation mark */}
        <div style={{
          position: "absolute",
          left: 100, top: "50%",
          transform: "translateY(-50%)",
          fontFamily: MANROPE,
          fontSize: 300,
          fontWeight: "800",
          color: accent_color,
          opacity: quoteMrkOp * 0.08 * quotePulse,
          lineHeight: 1,
          userSelect: "none",
          pointerEvents: "none",
        }}>
          "
        </div>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 0, padding: "0 180px",
        }}>
          {/* Quote text */}
          <div style={{
            opacity: textOp * (frame >= exitStart + 4 ? textExitOp : 1),
            transform: `translate(${textExitX}px, ${textY}px)`,
            fontFamily: MANROPE,
            fontSize: textSize,
            fontWeight: "600",
            fontStyle: "italic",
            color: "#FFFFFF",
            textAlign: "center",
            lineHeight: 1.35,
            letterSpacing: "0.01em",
          }}>
            {text}
          </div>

          {/* Accent line */}
          {(author || source) && (
            <div style={{
              width: `${lineW}%`, maxWidth: 200, height: 1.5,
              background: `linear-gradient(to right, transparent, ${accent_color}88, transparent)`,
              opacity: frame >= exitStart ? lineOp : 1,
              marginTop: 32, marginBottom: 20,
            }} />
          )}

          {/* Author */}
          {author && (
            <div style={{
              opacity: authorOp,
              transform: `translateY(${authorY}px)`,
              fontFamily: SYNE,
              fontSize: 15,
              fontWeight: "700",
              letterSpacing: "0.32em",
              textTransform: "uppercase",
              fontVariant: "small-caps",
              color: `${accent_color}CC`,
              textAlign: "center",
            }}>
              {author}
            </div>
          )}

          {/* Source */}
          {source && (
            <div style={{
              opacity: sourceOp,
              fontFamily: MANROPE,
              fontSize: 14,
              fontWeight: "400",
              color: "#FFFFFF44",
              textAlign: "center",
              letterSpacing: "0.06em",
              marginTop: 8,
            }}>
              {source}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
