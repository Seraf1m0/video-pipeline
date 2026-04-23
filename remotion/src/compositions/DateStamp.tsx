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
  const s1 = 1 + Math.sin(frame * 0.021) * 0.07;
  const s2 = 1 + Math.sin(frame * 0.017 + 1.7) * 0.05;
  const nx  = noise2D("dsx", frame * 0.0016, 0) * 5;
  const ny  = noise2D("dsy", 0, frame * 0.0016) * 4;

  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s2})`,
        width: 1000, height: 700, borderRadius: "50%",
        background: `radial-gradient(ellipse, ${color} 0%, transparent 65%)`,
        filter: "blur(130px)", opacity: op * 0.11,
      }} />
      <div style={{
        position: "absolute",
        left: `${50 - nx * 0.5}%`, top: `${50 - ny * 0.5}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 480, height: 480, borderRadius: "50%",
        background: color, filter: "blur(80px)", opacity: op * 0.16,
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

// ─── RING EXPAND ──────────────────────────────────────────────────────────────
const RingExpand: React.FC<{ frame: number; color: string; startFrame: number }> = ({ frame, color, startFrame }) => {
  const rings = [0, 8, 16].map((offset, i) => {
    const age = frame - startFrame - offset;
    if (age < 0) return null;
    const t  = Math.min(age / 55, 1);
    const r  = interpolate(t, [0, 1], [40, 360], { easing: Easing.out(Easing.cubic) });
    const op = interpolate(t, [0, 0.1, 0.6, 1], [0, 0.35, 0.12, 0]);
    return { r, op, i };
  }).filter(Boolean);

  return (
    <AbsoluteFill style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
      {rings.map(ring => ring && (
        <div key={ring.i} style={{
          position: "absolute",
          left: "50%", top: "50%",
          transform: "translate(-50%, -50%)",
          width: ring.r * 2, height: ring.r * 2,
          borderRadius: "50%",
          border: `1.5px solid ${color}`,
          opacity: ring.op,
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── CORNER DOTS ──────────────────────────────────────────────────────────────
const CornerDots: React.FC<{ frame: number; color: string; enterFrame: number; exitStart: number }> = ({
  frame, color, enterFrame, exitStart,
}) => {
  const op = interpolate(frame, [enterFrame, enterFrame + 12], [0, 1], { extrapolateRight: "clamp" }) *
             interpolate(frame, [exitStart, exitStart + 16], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const pulse = 0.6 + Math.sin(frame * 0.07) * 0.4;

  const positions = [
    { left: "calc(50% - 240px)", top: "calc(50% - 90px)" },
    { left: "calc(50% + 240px)", top: "calc(50% - 90px)" },
    { left: "calc(50% - 240px)", top: "calc(50% + 90px)" },
    { left: "calc(50% + 240px)", top: "calc(50% + 90px)" },
  ];

  return (
    <AbsoluteFill>
      {positions.map((pos, i) => (
        <div key={i} style={{
          position: "absolute",
          left: pos.left, top: pos.top,
          width: 6, height: 6,
          borderRadius: "50%",
          background: color,
          boxShadow: `0 0 ${10 * pulse}px ${color}CC`,
          opacity: op,
          transform: "translate(-50%, -50%)",
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const DateStamp: React.FC<DateStampProps> = ({
  date,
  event,
  sub,
  accent_color = "#FFD700",
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

  // Top line — grows from center
  const topLineEnter = 8;
  const topLineW     = interpolate(frame, [topLineEnter, topLineEnter + 28], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  const topLineOp    = interpolate(frame, [exitStart, exitStart + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Date — crashes in from above (massive)
  const dateEnter = topLineEnter + 6;
  const dateSpr   = spring({ frame: frame - dateEnter, fps, config: { damping: 7, stiffness: 500, mass: 1.2 } });
  const dateY     = interpolate(dateSpr, [0, 1], [-280, 0]);
  const dateOp    = interpolate(frame, [dateEnter, dateEnter + 10], [0, 1], { extrapolateRight: "clamp" });

  // Date exit fade
  const dateFade  = interpolate(frame, [exitStart + 4, exitStart + 26], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const glow = (0.5 + Math.sin(frame * 0.06) * 0.5) * glowMult;
  const dateLen  = date.length;
  const dateSize = dateLen > 16 ? 110 : dateLen > 10 ? 140 : dateLen > 6 ? 180 : 220;

  // Event slides up below date
  const eventEnter = dateEnter + 22;
  const eventSpr   = spring({ frame: frame - eventEnter, fps, config: { damping: 22, stiffness: 240 } });
  const eventY     = interpolate(eventSpr, [0, 1], [30, 0]);
  const eventOp    = interpolate(frame, [eventEnter, eventEnter + 14], [0, 1], { extrapolateRight: "clamp" });
  const eventFade  = interpolate(frame, [exitStart + 2, exitStart + 22], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Sub
  const subEnter = eventEnter + 16;
  const subOp    = interpolate(frame, [subEnter, subEnter + 14], [0, 1], { extrapolateRight: "clamp" }) *
                   interpolate(frame, [exitStart, exitStart + 14], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Bottom line
  const btmLineEnter = eventEnter + 8;
  const btmLineW     = interpolate(frame, [btmLineEnter, btmLineEnter + 28], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  const btmLineOp    = interpolate(frame, [exitStart, exitStart + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />
        <RingExpand frame={frame} color={accent_color} startFrame={dateEnter + 4} />
        <CornerDots frame={frame} color={accent_color} enterFrame={dateEnter + 10} exitStart={exitStart} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={dateEnter + 2} durationInFrames={26}>
          <Audio src={staticFile("sfx/impact.wav")} volume={0.28} />
        </Sequence>
        <Sequence from={dateEnter + 4} durationInFrames={28}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.20} />
        </Sequence>
        <Sequence from={eventEnter} durationInFrames={24}>
          <Audio src={staticFile("sfx/stinger.wav")} volume={0.14} />
        </Sequence>
        <Sequence from={exitStart + 8} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 0, padding: "0 120px",
        }}>
          {/* Top line */}
          <div style={{
            width: `${topLineW}%`, maxWidth: 560, height: 1.5,
            background: `linear-gradient(to right, transparent, ${accent_color}AA, transparent)`,
            opacity: frame >= exitStart ? topLineOp : 1,
            marginBottom: 28,
          }} />

          {/* Date — huge */}
          <div style={{
            opacity: dateOp * (frame >= exitStart + 4 ? dateFade : 1),
            transform: `translateY(${dateY}px)`,
            fontFamily: SYNE,
            fontSize: dateSize,
            fontWeight: "800",
            letterSpacing: "-0.04em",
            lineHeight: 1,
            color: accent_color,
            textAlign: "center",
            textShadow: `
              0 0 ${80 * glow}px ${accent_color}CC,
              0 0 ${160 * glow}px ${accent_color}55,
              0 0 ${260 * glow}px ${accent_color}22
            `,
          }}>
            {date}
          </div>

          {/* Bottom line */}
          <div style={{
            width: `${btmLineW}%`, maxWidth: 560, height: 1.5,
            background: `linear-gradient(to right, transparent, ${accent_color}AA, transparent)`,
            opacity: frame >= exitStart ? btmLineOp : 1,
            marginTop: 20, marginBottom: 24,
          }} />

          {/* Event */}
          <div style={{
            opacity: eventOp * (frame >= exitStart + 2 ? eventFade : 1),
            transform: `translateY(${eventY}px)`,
            fontFamily: SYNE,
            fontSize: 28,
            fontWeight: "700",
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: "#FFFFFF",
            textAlign: "center",
          }}>
            {event}
          </div>

          {/* Sub */}
          {sub && (
            <div style={{
              opacity: subOp,
              fontFamily: MANROPE,
              fontSize: 17,
              fontWeight: "400",
              color: "#FFFFFF55",
              textAlign: "center",
              letterSpacing: "0.04em",
              lineHeight: 1.5,
              marginTop: 14,
              maxWidth: 520,
            }}>
              {sub}
            </div>
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
