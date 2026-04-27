import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne       } from "@remotion/google-fonts/Syne";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { noise2D } from "@remotion/noise";
import { seededRand } from "../utils/seeded";

const { fontFamily: SYNE       } = loadSyne();
const { fontFamily: MONTSERRAT } = loadMontserrat();

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

const Bg: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const op  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" })
            * interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const s1  = 1 + Math.sin(frame * 0.018) * 0.06;
  const nx  = noise2D("qtbx", frame * 0.0014, 0) * 6;
  const ny  = noise2D("qtby", 0, frame * 0.0014) * 4;
  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 900, height: 700, borderRadius: "50%",
        background: `radial-gradient(ellipse, ${color} 0%, transparent 65%)`,
        filter: "blur(130px)", opacity: op * 0.13,
      }} />
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 90% 85% at 50% 50%, transparent 25%, #000000E0 100%)",
      }} />
    </AbsoluteFill>
  );
};

const Grid: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const op = interpolate(frame, [8, 28], [0, 1], { extrapolateRight: "clamp" })
           * interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 16], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })
           * 0.05;
  return (
    <AbsoluteFill style={{ opacity: op }}>
      {Array.from({ length: 9 }, (_, i) => (
        <div key={i} style={{ position: "absolute", left: `${(i / 8) * 100}%`, top: 0, bottom: 0, width: 1,
          background: `linear-gradient(to bottom, transparent, ${color}44, transparent)` }} />
      ))}
    </AbsoluteFill>
  );
};

const ScanLine: React.FC<{ frame: number; color: string }> = ({ frame, color }) => {
  const y  = interpolate(frame, [0, 20], [0, 110], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const op = interpolate(frame, [0, 4, 16, 24], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <div style={{ position: "absolute", top: `${y}%`, left: 0, right: 0, height: 2,
    background: `linear-gradient(to right, transparent, ${color}CC, transparent)`,
    boxShadow: `0 0 22px ${color}88`, opacity: op }} />;
};

const ExitScan: React.FC<{ frame: number; total: number; color: string }> = ({ frame, total, color }) => {
  const start = total - EXIT_DUR + 10;
  const y  = interpolate(frame, [start, start + 18], [110, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic) });
  const op = interpolate(frame, [start, start + 3, start + 14, start + 22], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <div style={{ position: "absolute", top: `${y}%`, left: 0, right: 0, height: 2,
    background: `linear-gradient(to right, transparent, ${color}88, transparent)`,
    boxShadow: `0 0 16px ${color}55`, opacity: op }} />;
};

export const Quote: React.FC<QuoteProps> = ({
  text,
  author,
  source,
  accent_color = "#A855F7",
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
  const finalFade = interpolate(frame, [totalFrames - 6, totalFrames], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Font size — generous, readable
  const textFontSize = text.length > 100 ? 34 : text.length > 70 ? 40 : text.length > 40 ? 48 : 56;

  // Left accent bar grows top→bottom
  const barProgress = interpolate(frame, [4, 28], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp) });
  const barOp = interpolate(frame, [exitStart, exitStart + 18], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Text slides up + fades in
  const textSpr = spring({ frame: frame - 14, fps, config: { damping: 26, stiffness: 220 } });
  const textY   = interpolate(textSpr, [0, 1], [28, 0]);
  const textOp  = interpolate(frame, [14, 28], [0, 1], { extrapolateRight: "clamp" })
                * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Author fades in after text
  const authorSpr = spring({ frame: frame - 34, fps, config: { damping: 28, stiffness: 200 } });
  const authorY   = interpolate(authorSpr, [0, 1], [16, 0]);
  const authorOp  = interpolate(frame, [34, 48], [0, 1], { extrapolateRight: "clamp" })
                  * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Separator line
  const lineW = interpolate(frame, [36, 52], [0, 80], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp) });

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={14} durationInFrames={24}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.12} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        {/* Left accent bar */}
        <div style={{
          position: "absolute",
          left: 100,
          top: "18%",
          width: 5,
          height: `${barProgress * 64}%`,
          background: `linear-gradient(to bottom, ${accent_color}, ${accent_color}AA)`,
          borderRadius: 3,
          opacity: frame >= exitStart ? barOp : 1,
          boxShadow: `0 0 20px ${accent_color}88`,
        }} />

        {/* Content */}
        <AbsoluteFill style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px 160px 80px 180px",
          gap: 0,
        }}>
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

          {/* Separator + author */}
          {(author || source) && (
            <div style={{
              opacity: authorOp,
              transform: `translateY(${authorY}px)`,
              marginTop: 36,
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}>
              {/* Line */}
              <div style={{
                width: lineW,
                height: 2,
                background: accent_color,
                boxShadow: `0 0 12px ${accent_color}88`,
              }} />

              {/* Author name */}
              {author && (
                <div style={{
                  fontFamily: SYNE,
                  fontSize: 22,
                  fontWeight: "700",
                  letterSpacing: "0.14em",
                  textTransform: "uppercase",
                  color: accent_color,
                  marginTop: 4,
                }}>
                  — {author}
                </div>
              )}

              {/* Source */}
              {source && (
                <div style={{
                  fontFamily: MONTSERRAT,
                  fontSize: 18,
                  fontWeight: "600",
                  color: "#FFFFFFAA",
                  letterSpacing: "0.03em",
                }}>
                  {source}
                </div>
              )}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
