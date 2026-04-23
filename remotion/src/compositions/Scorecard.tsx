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

export interface ScorecardMetric {
  label: string;
  value: string;
  unit?: string;
  color?: string;
}

export interface ScorecardProps {
  title: string;
  metrics: ScorecardMetric[];
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const EXIT_DUR  = 44;
const CARD_STEP = 8;

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;
  const s1 = 1 + Math.sin(frame * 0.020) * 0.07;
  const nx  = noise2D("scbx", frame * 0.0014, 0) * 5;
  const ny  = noise2D("scby", 0, frame * 0.0014) * 4;

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

// ─── METRIC CARD ─────────────────────────────────────────────────────────────
const MetricCard: React.FC<{
  metric: ScorecardMetric;
  enterFrame: number;
  frame: number;
  exitStart: number;
  accentColor: string;
}> = ({ metric, enterFrame, frame, exitStart, accentColor }) => {
  const { fps } = useVideoConfig();
  const color = metric.color ?? accentColor;

  // Slide up spring
  const spr  = spring({ frame: frame - enterFrame, fps, config: { damping: 22, stiffness: 280 } });
  const yIn  = interpolate(spr, [0, 1], [40, 0]);
  const opIn = interpolate(frame, [enterFrame, enterFrame + 12], [0, 1], { extrapolateRight: "clamp" });

  // Exit fade
  const opOut = interpolate(frame, [exitStart + 4, exitStart + 24], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const yOut  = interpolate(frame, [exitStart + 4, exitStart + 24], [0, 20], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const op = opIn * (frame >= exitStart + 4 ? opOut : 1);
  const y  = yIn + yOut;

  // Panel highlight on entry
  const panelGlow = interpolate(frame, [enterFrame, enterFrame + 20], [0.04, 0.08], {
    extrapolateRight: "clamp",
  });

  const valLen  = metric.value.length;
  const valSize = valLen > 8 ? 52 : valLen > 5 ? 64 : 80;

  return (
    <div style={{
      opacity: op,
      transform: `translateY(${y}px)`,
      background: `rgba(255,255,255,${panelGlow})`,
      border: `1px solid ${color}22`,
      borderRadius: 12,
      padding: "28px 32px",
      display: "flex",
      flexDirection: "column",
      gap: 8,
      boxShadow: `0 0 30px ${color}11, inset 0 1px 0 ${color}18`,
    }}>
      {/* Value + unit */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 6, lineHeight: 1 }}>
        <span style={{
          fontFamily: SYNE,
          fontSize: valSize,
          fontWeight: "800",
          letterSpacing: "-0.03em",
          color: color,
          textShadow: `0 0 40px ${color}88`,
        }}>
          {metric.value}
        </span>
        {metric.unit && (
          <span style={{
            fontFamily: MANROPE,
            fontSize: 22,
            fontWeight: "600",
            color: "#FFFFFFAA",
            paddingBottom: 6,
            letterSpacing: "-0.01em",
          }}>
            {metric.unit}
          </span>
        )}
      </div>

      {/* Label */}
      <div style={{
        fontFamily: SYNE,
        fontSize: 11,
        fontWeight: "700",
        letterSpacing: "0.28em",
        textTransform: "uppercase",
        color: "#FFFFFF55",
      }}>
        {metric.label}
      </div>
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const Scorecard: React.FC<ScorecardProps> = ({
  title,
  metrics,
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

  // Clamp metrics 3-6
  const items = metrics.slice(0, 6);
  const cols  = items.length <= 4 ? 2 : 3;

  // Title enters
  const titleEnter = 8;
  const titleSpr   = spring({ frame: frame - titleEnter, fps, config: { damping: 28, stiffness: 240 } });
  const titleY     = interpolate(titleSpr, [0, 1], [-18, 0]);
  const titleOp    = interpolate(frame, [titleEnter, titleEnter + 14], [0, 1], { extrapolateRight: "clamp" }) *
                     interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  const titleLineW = interpolate(frame, [titleEnter + 4, titleEnter + 32], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });

  // Card enter frames: staggered
  const cardBase = titleEnter + 20;

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
        {items.map((_, i) => (
          <Sequence key={i} from={cardBase + i * CARD_STEP + 4} durationInFrames={20}>
            <Audio src={staticFile("sfx/ping.wav")} volume={0.12 - i * 0.005} />
          </Sequence>
        ))}
        <Sequence from={exitStart + 8} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          justifyContent: "center",
          gap: 36, padding: "60px 100px",
        }}>
          {/* Title */}
          <div style={{
            opacity: titleOp,
            transform: `translateY(${titleY}px)`,
            display: "flex", flexDirection: "column", gap: 10,
          }}>
            <div style={{
              fontFamily: SYNE,
              fontSize: 13,
              fontWeight: "800",
              letterSpacing: "0.40em",
              textTransform: "uppercase",
              color: "#FFFFFF55",
            }}>
              {title}
            </div>
            <div style={{
              width: `${titleLineW}%`, maxWidth: 200, height: 1,
              background: `linear-gradient(to right, ${accent_color}88, transparent)`,
            }} />
          </div>

          {/* Grid of cards */}
          <div style={{
            display: "grid",
            gridTemplateColumns: `repeat(${cols}, 1fr)`,
            gap: 20,
          }}>
            {items.map((metric, i) => (
              <MetricCard
                key={i}
                metric={metric}
                enterFrame={cardBase + i * CARD_STEP}
                frame={frame}
                exitStart={exitStart}
                accentColor={accent_color}
              />
            ))}
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
