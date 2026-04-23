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

export interface WordRevealProps {
  words: string[];
  accent_words?: string[];
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
  const nx  = noise2D("wrbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("wrby", 0, frame * 0.0015) * 4;

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
export const WordReveal: React.FC<WordRevealProps> = ({
  words,
  accent_words = [],
  accent_color = "#00C8FF",
  duration_s   = 10,
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

  const accentSet = new Set(accent_words.map(w => w.toLowerCase()));
  const n         = words.length;
  const stagger   = Math.max(Math.floor((exitStart - 10) / Math.max(n, 1)), 6);

  // Font size based on word count and length
  const maxLen = Math.max(...words.map(w => w.length));
  const fontSize =
    n > 8 ? (maxLen > 10 ? 60 : 72) :
    n > 5 ? (maxLen > 10 ? 72 : 88) :
    n > 3 ? (maxLen > 10 ? 88 : 104) :
            (maxLen > 10 ? 96 : 116);

  // Exit fade
  const exitOp = interpolate(frame, [exitStart + 4, exitStart + 28], [1, 0], {
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
          <Audio src={staticFile("sfx/rise.wav")} volume={0.16} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex",
          flexWrap: "wrap",
          alignContent: "center",
          justifyContent: "center",
          alignItems: "center",
          padding: "0 120px",
          gap: "0.25em 0.35em",
          opacity: frame >= exitStart + 4 ? exitOp : 1,
        }}>
          {words.map((word, i) => {
            const isAccent  = accentSet.has(word.toLowerCase());
            const isLast    = i === n - 1;
            const enterFrame = 10 + i * stagger;
            const age        = frame - enterFrame;

            // spring scale + opacity
            const spr    = spring({ frame: age, fps, config: { damping: 10, stiffness: 300 }, from: 0, to: 1 });
            const scaleIn = interpolate(spr, [0, 1], [0.6, 1]);
            const opIn    = interpolate(age, [0, 10], [0, 1], { extrapolateRight: "clamp" });

            // Last word bounce: 1.0 → 1.08 → 1.0
            let extraScale = 1;
            if (isLast && age > 0) {
              const bounceAge = age - 6;
              const bounceSpr = spring({ frame: bounceAge, fps, config: { damping: 6, stiffness: 600 }, from: 0, to: 1 });
              extraScale = 1 + interpolate(bounceSpr, [0, 0.5, 1], [0, 0.08, 0]) ;
            }

            const glow = isAccent ? 0.5 + Math.sin(frame * 0.07 + i) * 0.5 : 0;

            return (
              <span key={i} style={{
                display: "inline-block",
                fontFamily: isAccent ? SYNE : MANROPE,
                fontSize,
                fontWeight: "800",
                color: isAccent ? accent_color : "#FFFFFF",
                opacity: opIn,
                transform: `scale(${scaleIn * extraScale})`,
                letterSpacing: isAccent ? "0.02em" : "-0.01em",
                lineHeight: 1.15,
                textShadow: isAccent
                  ? `0 0 ${50 * glow}px ${accent_color}CC, 0 0 ${100 * glow}px ${accent_color}44`
                  : "none",
                transformOrigin: "center center",
              }}>
                {word}
              </span>
            );
          })}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
