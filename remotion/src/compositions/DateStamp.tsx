import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne    } from "@remotion/google-fonts/Syne";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadSpaceMono } from "@remotion/google-fonts/SpaceMono";
import { noise2D } from "@remotion/noise";
import { seededRand } from "../utils/seeded";

const { fontFamily: SYNE      } = loadSyne();
const { fontFamily: MONTSERRAT   } = loadMontserrat();
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
  const s1 = 1 + Math.sin(frame * 0.020) * 0.07;
  const nx  = noise2D("dsbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("dsby", 0, frame * 0.0015) * 4;

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

  // Adaptive date font size
  const dateFontSize = date.length > 16 ? 90 : date.length > 10 ? 120 : date.length > 6 ? 160 : 200;

  // Badge: slides from top
  const badgeSpr = spring({ frame: frame - 4, fps, config: { damping: 20, stiffness: 260 } });
  const badgeY   = interpolate(badgeSpr, [0, 1], [-120, 0]);
  const badgeOp  = interpolate(frame, [4, 18], [0, 1], { extrapolateRight: "clamp" })
                 * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Divider line
  const lineW = interpolate(frame, [18, 40], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  const lineOp = interpolate(frame, [exitStart, exitStart + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Event text
  const eventSpr = spring({ frame: frame - 24, fps, config: { damping: 26, stiffness: 240 } });
  const eventY   = interpolate(eventSpr, [0, 1], [30, 0]);
  const eventOp  = interpolate(frame, [24, 38], [0, 1], { extrapolateRight: "clamp" })
                 * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Sub text
  const subSpr = spring({ frame: frame - 34, fps, config: { damping: 26, stiffness: 220 } });
  const subY   = interpolate(subSpr, [0, 1], [20, 0]);
  const subOp  = interpolate(frame, [34, 48], [0, 1], { extrapolateRight: "clamp" })
               * interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

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
        <Sequence from={4} durationInFrames={28}>
          <Audio src={staticFile("sfx/impact.wav")} volume={0.24} />
        </Sequence>
        <Sequence from={24} durationInFrames={20}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.12} />
        </Sequence>
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "60px 120px",
          gap: 32,
        }}>
          {/* Date badge */}
          <div style={{
            opacity: badgeOp,
            transform: `translateY(${badgeY}px)`,
            border: `1px solid ${accent_color}`,
            boxShadow: `0 0 30px ${accent_color}44, inset 0 0 20px ${accent_color}11`,
            borderRadius: 8,
            padding: "16px 48px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}>
            <div style={{
              fontFamily: SPACEMONO,
              fontSize: dateFontSize,
              fontWeight: "700",
              color: accent_color,
              textShadow: `0 0 40px ${accent_color}88`,
              letterSpacing: "0.04em",
              lineHeight: 1,
            }}>
              {date}
            </div>
          </div>

          {/* Thin divider line */}
          <div style={{
            width: `${lineW}%`,
            height: 1,
            background: `linear-gradient(to right, transparent, ${accent_color}66, transparent)`,
            opacity: lineOp,
          }} />

          {/* Event */}
          <div style={{
            opacity: eventOp,
            transform: `translateY(${eventY}px)`,
            fontFamily: SYNE,
            fontSize: 32,
            fontWeight: "800",
            color: "#FFFFFF",
            letterSpacing: "0.1em",
            textAlign: "center",
            lineHeight: 1.2,
          }}>
            {event}
          </div>

          {/* Sub */}
          {sub && (
            <div style={{
              opacity: subOp,
              transform: `translateY(${subY}px)`,
              fontFamily: MONTSERRAT,
              fontSize: 16,
              fontWeight: "600",
              color: "#FFFFFF77",
              letterSpacing: "0.02em",
              textAlign: "center",
              lineHeight: 1.5,
            }}>
              {sub}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
