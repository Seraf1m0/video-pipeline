import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne       } from "@remotion/google-fonts/Syne";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadSpaceMono  } from "@remotion/google-fonts/SpaceMono";
import { noise2D } from "@remotion/noise";
import { seededRand } from "../utils/seeded";

const { fontFamily: SYNE      } = loadSyne();
const { fontFamily: MONTSERRAT } = loadMontserrat();
const { fontFamily: SPACEMONO } = loadSpaceMono();

export interface DateStampProps {
  date: string;
  event: string;
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
  const s1 = 1 + Math.sin(frame * 0.018) * 0.06;
  const nx  = noise2D("dsbx", frame * 0.0014, 0) * 5;
  const ny  = noise2D("dsby", 0, frame * 0.0014) * 4;

  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute",
        left: `${42 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 900, height: 600, borderRadius: "50%",
        background: `radial-gradient(ellipse, ${color} 0%, transparent 65%)`,
        filter: "blur(150px)", opacity: op * 0.09,
      }} />
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 90% 85% at 50% 50%, transparent 20%, #000000E5 100%)",
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
  const op = inOp * outOp * 0.04;
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
          background: "linear-gradient(to right, transparent, #FFFFFF08, transparent)",
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
export const DateStamp: React.FC<DateStampProps> = ({
  date,
  event,
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

  // Date font size
  const dateFontSize = date.length > 16 ? 72 : date.length > 10 ? 96 : date.length > 6 ? 130 : 160;

  // ── Badge: flies in from RIGHT ──
  const badgeSpr = spring({ frame: frame - 6, fps, config: { damping: 18, stiffness: 200 } });
  const badgeX   = interpolate(badgeSpr, [0, 1], [1400, 0]);
  const badgeOp  = interpolate(frame, [6, 22], [0, 1], { extrapolateRight: "clamp" })
                 * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], {
                     extrapolateLeft: "clamp", extrapolateRight: "clamp",
                   });
  // Exit: flies back to right
  const badgeExitX = interpolate(frame, [exitStart + 4, exitStart + 24], [0, 1400], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  // ── Event label: fades in from below after badge settles ──
  const eventSpr = spring({ frame: frame - 28, fps, config: { damping: 26, stiffness: 220 } });
  const eventY   = interpolate(eventSpr, [0, 1], [20, 0]);
  const eventOp  = interpolate(frame, [28, 42], [0, 1], { extrapolateRight: "clamp" })
                 * interpolate(frame, [exitStart + 2, exitStart + 18], [1, 0], {
                     extrapolateLeft: "clamp", extrapolateRight: "clamp",
                   });

  // ── Sub: fades in last ──
  const subSpr = spring({ frame: frame - 40, fps, config: { damping: 28, stiffness: 220 } });
  const subY   = interpolate(subSpr, [0, 1], [14, 0]);
  const subOp  = interpolate(frame, [40, 54], [0, 1], { extrapolateRight: "clamp" })
               * interpolate(frame, [exitStart + 2, exitStart + 16], [1, 0], {
                   extrapolateLeft: "clamp", extrapolateRight: "clamp",
                 });

  // Accent strip pulse
  const stripGlow = 0.5 + Math.sin(frame * 0.06) * 0.3;

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.14} />
        </Sequence>
        <Sequence from={6} durationInFrames={28}>
          <Audio src={staticFile("sfx/impact.wav")} volume={0.22} />
        </Sequence>
        <Sequence from={28} durationInFrames={18}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.10} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        {/* Centered layout */}
        <AbsoluteFill style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "80px 140px",
          gap: 28,
        }}>

          {/* ── BEAUTIFUL BADGE ── */}
          <div style={{
            opacity: badgeOp,
            transform: `translateX(${badgeX + badgeExitX}px)`,
            display: "flex",
            alignItems: "stretch",
            borderRadius: 16,
            overflow: "hidden",
            border: `1px solid ${accent_color}55`,
            boxShadow: `0 0 60px ${accent_color}22, 0 0 120px ${accent_color}0A, inset 0 1px 0 ${accent_color}33`,
            background: "rgba(255,255,255,0.04)",
            backdropFilter: "blur(12px)",
          }}>
            {/* Left accent strip */}
            <div style={{
              width: 8,
              background: accent_color,
              flexShrink: 0,
              boxShadow: `0 0 ${24 * stripGlow}px ${accent_color}CC`,
            }} />

            {/* Badge content */}
            <div style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              justifyContent: "center",
              padding: "28px 52px 28px 40px",
              gap: 4,
            }}>
              {/* Label row */}
              <div style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 6,
              }}>
                {/* Dot */}
                <div style={{
                  width: 8, height: 8, borderRadius: "50%",
                  background: accent_color,
                  boxShadow: `0 0 10px ${accent_color}`,
                  flexShrink: 0,
                }} />
                <div style={{
                  fontFamily: SYNE,
                  fontSize: 14,
                  fontWeight: "800",
                  letterSpacing: "0.35em",
                  textTransform: "uppercase",
                  color: `${accent_color}CC`,
                }}>
                  {event}
                </div>
              </div>

              {/* Date — big */}
              <div style={{
                fontFamily: SPACEMONO,
                fontSize: dateFontSize,
                fontWeight: "700",
                color: "#FFFFFF",
                letterSpacing: "0.03em",
                lineHeight: 1.0,
                textShadow: `0 0 50px ${accent_color}55`,
              }}>
                {date}
              </div>

              {/* Sub */}
              {sub && (
                <div style={{
                  fontFamily: MONTSERRAT,
                  fontSize: 18,
                  fontWeight: "600",
                  color: "#FFFFFFAA",
                  letterSpacing: "0.04em",
                  marginTop: 8,
                  lineHeight: 1.4,
                }}>
                  {sub}
                </div>
              )}
            </div>
          </div>

          {/* Decorative bottom line */}
          <div style={{
            opacity: eventOp,
            transform: `translateY(${eventY}px)`,
            display: "flex",
            alignItems: "center",
            gap: 16,
          }}>
            <div style={{
              width: 48, height: 1,
              background: `${accent_color}66`,
            }} />
            <div style={{
              fontFamily: MONTSERRAT,
              fontSize: 16,
              fontWeight: "600",
              letterSpacing: "0.20em",
              textTransform: "uppercase",
              color: "#FFFFFF55",
            }}>
              {sub ? sub : event}
            </div>
            <div style={{
              width: 48, height: 1,
              background: `${accent_color}66`,
            }} />
          </div>

        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
