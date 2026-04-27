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

const EXIT_DUR = 44;

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;
  const s1 = 1 + Math.sin(frame * 0.020) * 0.07;
  const nx  = noise2D("scbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("scby", 0, frame * 0.0015) * 4;

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
  frame: number;
  enterFrame: number;
  exitStart: number;
  accentColor: string;
  fps: number;
}> = ({ metric, frame, enterFrame, exitStart, accentColor, fps }) => {
  const color = metric.color ?? accentColor;

  const spr  = spring({ frame: frame - enterFrame, fps, config: { damping: 24, stiffness: 260 } });
  const yIn  = interpolate(spr, [0, 1], [40, 0]);
  const opIn = interpolate(frame, [enterFrame, enterFrame + 14], [0, 1], { extrapolateRight: "clamp" });

  const exitOp = interpolate(frame, [exitStart + 4, exitStart + 24], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const exitY  = interpolate(frame, [exitStart + 4, exitStart + 24], [0, 40], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  const op = opIn * (frame >= exitStart + 4 ? exitOp : 1);
  const y  = yIn  + (frame >= exitStart + 4 ? exitY : 0);

  return (
    <div style={{
      opacity: op,
      transform: `translateY(${y}px)`,
      background: "rgba(255,255,255,0.05)",
      border: `1px solid ${accentColor}33`,
      borderRadius: 12,
      padding: "28px 32px",
      display: "flex",
      flexDirection: "column",
      justifyContent: "center",
      alignItems: "flex-start",
      gap: 6,
      flex: 1,
      minWidth: 0,
      boxShadow: `inset 0 1px 0 ${accentColor}11, 0 0 30px ${color}0A`,
    }}>
      {/* Label */}
      <div style={{
        fontFamily: MONTSERRAT,
        fontSize: 22,
        fontWeight: "700",
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        color: "#FFFFFF",
      }}>
        {metric.label}
      </div>

      {/* Value */}
      <div style={{
        fontFamily: MONTSERRAT,
        fontSize: 120,
        fontWeight: "900",
        letterSpacing: "-0.02em",
        color: color,
        lineHeight: 1.0,
        textShadow: `0 0 60px ${color}77`,
        paddingTop: 4,
      }}>
        {metric.value}
      </div>

      {/* Unit */}
      {metric.unit && (
        <div style={{
          fontFamily: MONTSERRAT,
          fontSize: 24,
          fontWeight: "700",
          color: color,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
        }}>
          {metric.unit}
        </div>
      )}
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

  const titleEnter = 6;
  const cardsBase  = titleEnter + 18;
  const CARD_STEP  = 12;

  // Title
  const titleSpr = spring({ frame: frame - titleEnter, fps, config: { damping: 28, stiffness: 240 } });
  const titleY   = interpolate(titleSpr, [0, 1], [-16, 0]);
  const titleOp  = interpolate(frame, [titleEnter, titleEnter + 14], [0, 1], { extrapolateRight: "clamp" })
                 * interpolate(frame, [exitStart + 4, exitStart + 20], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Divide metrics into rows of 2 (or all in one row if ≤ 2)
  const cols = metrics.length <= 2 ? metrics.length : 2;
  const rows: ScorecardMetric[][] = [];
  for (let i = 0; i < metrics.length; i += cols) {
    rows.push(metrics.slice(i, i + cols));
  }

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
        <Sequence from={titleEnter} durationInFrames={24}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.12} />
        </Sequence>
        {metrics.map((_, i) => (
          <Sequence key={i} from={cardsBase + i * CARD_STEP + 4} durationInFrames={20}>
            <Audio src={staticFile("sfx/ping.wav")} volume={0.10 - i * 0.003} />
          </Sequence>
        ))}
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "60px 100px",
          gap: 24,
        }}>
          {/* Title */}
          <div style={{
            opacity: titleOp,
            transform: `translateY(${titleY}px)`,
            fontFamily: MONTSERRAT,
            fontSize: 20,
            fontWeight: "800",
            letterSpacing: "0.28em",
            textTransform: "uppercase",
            color: "#FFFFFF",
          }}>
            {title}
          </div>

          {/* Metric grid rows */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {rows.map((row, rowIdx) => (
              <div key={rowIdx} style={{ display: "flex", gap: 16, minHeight: 200 }}>
                {row.map((metric, colIdx) => {
                  const globalIdx = rowIdx * cols + colIdx;
                  return (
                    <MetricCard
                      key={globalIdx}
                      metric={metric}
                      frame={frame}
                      enterFrame={cardsBase + globalIdx * CARD_STEP}
                      exitStart={exitStart}
                      accentColor={accent_color}
                      fps={fps}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
