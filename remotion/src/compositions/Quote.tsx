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
  const nx  = noise2D("qtbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("qtby", 0, frame * 0.0015) * 4;

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
export const Quote: React.FC<QuoteProps> = ({
  text,
  author,
  source,
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

  // Adaptive text size
  const textFontSize = text.length > 120 ? 18 : text.length > 80 ? 21 : text.length > 50 ? 24 : 28;

  // Giant quote mark — pulsing opacity
  const quoteBaseOp = interpolate(frame, [4, 18], [0, 1], { extrapolateRight: "clamp" })
                    * interpolate(frame, [exitStart + 4, exitStart + 20], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const quotePulse  = 0.15 + Math.sin(frame * 0.06) * 0.10;
  const quoteOp     = quoteBaseOp * (quotePulse / 0.25); // normalize to 0.15-0.35 range

  // Text slides up
  const textSpr = spring({ frame: frame - 10, fps, config: { damping: 24, stiffness: 220 } });
  const textY   = interpolate(textSpr, [0, 1], [30, 0]);
  const textOp  = interpolate(frame, [10, 24], [0, 1], { extrapolateRight: "clamp" })
                * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Author fades in after text
  const authorOp = interpolate(frame, [26, 40], [0, 1], { extrapolateRight: "clamp" })
                 * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Accent line between text and author
  const lineW = interpolate(frame, [28, 42], [0, 60], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  const lineOp = interpolate(frame, [exitStart, exitStart + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

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
        <Sequence from={10} durationInFrames={24}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.12} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        {/* Giant decorative quote mark */}
        <div style={{
          position: "absolute",
          left: 80,
          top: "50%",
          transform: "translateY(-50%)",
          fontFamily: BEBAS,
          fontSize: 280,
          color: accent_color,
          opacity: Math.max(0, Math.min(0.35, quoteOp)),
          lineHeight: 1,
          textShadow: `0 0 60px ${accent_color}`,
          userSelect: "none",
          pointerEvents: "none",
        }}>
          "
        </div>

        <AbsoluteFill style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px 120px 80px 180px",
          gap: 0,
        }}>
          {/* Left border on text block */}
          <div style={{
            position: "absolute",
            left: 120,
            top: "20%",
            bottom: "20%",
            width: 3,
            background: accent_color,
            opacity: textOp * 0.8,
            borderRadius: 2,
          }} />

          {/* Quote text */}
          <div style={{
            opacity: textOp,
            transform: `translateY(${textY}px)`,
            fontFamily: MONTSERRAT,
            fontSize: textFontSize,
            fontWeight: "700",
            fontStyle: "italic",
            color: "#FFFFFF",
            lineHeight: 1.55,
            letterSpacing: "0.01em",
          }}>
            {text}
          </div>

          {/* Accent line */}
          {(author || source) && (
            <div style={{
              width: lineW,
              height: 2,
              background: accent_color,
              marginTop: 24,
              marginBottom: 16,
              opacity: lineOp,
            }} />
          )}

          {/* Author */}
          {author && (
            <div style={{
              opacity: authorOp,
              fontFamily: SYNE,
              fontSize: 15,
              fontWeight: "700",
              fontVariant: "small-caps",
              letterSpacing: "0.08em",
              color: accent_color,
              textTransform: "lowercase",
            }}>
              — {author}
            </div>
          )}

          {/* Source */}
          {source && (
            <div style={{
              opacity: authorOp,
              fontFamily: MONTSERRAT,
              fontSize: 13,
              fontWeight: "600",
              color: "#FFFFFF55",
              letterSpacing: "0.02em",
              marginTop: 4,
            }}>
              {source}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
