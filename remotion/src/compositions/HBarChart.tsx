import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne        } from "@remotion/google-fonts/Syne";
import { loadFont as loadMontserrat  } from "@remotion/google-fonts/Montserrat";
import { loadFont as loadBebasNeue   } from "@remotion/google-fonts/BebasNeue";
import { noise2D } from "@remotion/noise";
import { seededRand } from "../utils/seeded";

const { fontFamily: SYNE       } = loadSyne();
const { fontFamily: MONTSERRAT } = loadMontserrat();
const { fontFamily: BEBAS      } = loadBebasNeue();

export interface HBarChartProps {
  title: string;
  bars: Array<{
    label: string;
    value: number;
    color?: string;
    unit?: string;
  }>;
  max_value?: number;
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const EXIT_DUR = 44;

// ─── BACKGROUND ──────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;
  const s1 = 1 + Math.sin(frame * 0.020) * 0.07;
  const nx  = noise2D("hbbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("hbby", 0, frame * 0.0015) * 4;

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

// ─── GRID ────────────────────────────────────────────────────────────────────
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

// ─── SCAN LINE ───────────────────────────────────────────────────────────────
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

// ─── EXIT SCAN ───────────────────────────────────────────────────────────────
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

// ─── BAR ROW ─────────────────────────────────────────────────────────────────
const BarRow: React.FC<{
  bar: HBarChartProps["bars"][number];
  frame: number;
  enterFrame: number;
  exitStart: number;
  accentColor: string;
  maxVal: number;
  fps: number;
  compact: boolean;
  index: number;
  total: number;
}> = ({ bar, frame, enterFrame, exitStart, accentColor, maxVal, fps, compact, index, total }) => {
  const color = bar.color ?? accentColor;
  const barH  = compact ? 48 : 60;
  const labelSize = compact ? 14 : 16;
  const valueSize = compact ? 30 : 38;

  // Fill spring
  const fillSpr = spring({
    frame: frame - enterFrame,
    fps,
    config: { damping: 28, stiffness: 140 },
  });
  const fillPct = `${(bar.value / maxVal) * 100 * Math.min(fillSpr, 1)}%`;

  // Entry opacity
  const opIn = interpolate(frame, [enterFrame, enterFrame + 14], [0, 1], {
    extrapolateRight: "clamp",
  });

  // Exit: slide right + fade, staggered reverse
  const exitDelay = (total - 1 - index) * 6;
  const opOut = interpolate(frame, [exitStart + exitDelay, exitStart + exitDelay + 18], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const xOut  = interpolate(frame, [exitStart + exitDelay, exitStart + exitDelay + 18], [0, 320], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  const op = opIn * (frame >= exitStart ? opOut : 1);
  const x  = frame >= exitStart ? xOut : 0;

  // Value appears when fill > 70%
  const valOp = interpolate(fillSpr, [0.70, 0.85], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 20,
      opacity: op,
      transform: `translateX(${x}px)`,
    }}>
      {/* Label — fixed width, right-aligned */}
      <div style={{
        fontFamily: MONTSERRAT,
        fontWeight: "700",
        fontSize: labelSize,
        width: 240,
        flexShrink: 0,
        textAlign: "right",
        color: "#FFFFFFCC",
        letterSpacing: "0.03em",
        lineHeight: 1.2,
      }}>
        {bar.label}
      </div>

      {/* Bar track + fill — fixed width, never flex */}
      <div style={{
        width: 820,
        flexShrink: 0,
        height: barH,
        borderRadius: 8,
        background: "#FFFFFF0A",
        position: "relative",
        overflow: "hidden",
      }}>
        <div style={{
          position: "absolute",
          top: 0, left: 0, bottom: 0,
          width: fillPct,
          borderRadius: 8,
          background: `linear-gradient(to right, ${color}99, ${color})`,
          boxShadow: `0 0 20px ${color}44`,
        }} />
      </div>

      {/* Value — always to the right of bar */}
      <div style={{
        display: "flex",
        alignItems: "baseline",
        gap: 5,
        opacity: valOp,
        width: 120,
        flexShrink: 0,
      }}>
        <span style={{
          fontFamily: BEBAS,
          fontSize: valueSize,
          color: color,
          lineHeight: 1,
          textShadow: `0 0 20px ${color}88`,
        }}>
          {bar.value}
        </span>
        {bar.unit && (
          <span style={{
            fontFamily: MONTSERRAT,
            fontWeight: "700",
            fontSize: 11,
            color: `${color}CC`,
            letterSpacing: "0.1em",
          }}>
            {bar.unit}
          </span>
        )}
      </div>
    </div>
  );
};

// ─── MAIN ────────────────────────────────────────────────────────────────────
export const HBarChart: React.FC<HBarChartProps> = ({
  title,
  bars,
  max_value,
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

  const maxVal = max_value ?? Math.max(...bars.map(b => b.value));
  const compact = bars.length > 5;

  const TITLE_ENTER = 8;
  const BARS_BASE   = 20;
  const BAR_STEP    = 14;

  // Title slide in
  const titleSpr = spring({ frame: frame - TITLE_ENTER, fps, config: { damping: 28, stiffness: 240 } });
  const titleX   = interpolate(titleSpr, [0, 1], [-60, 0]);
  const titleOp  = interpolate(frame, [TITLE_ENTER, TITLE_ENTER + 14], [0, 1], { extrapolateRight: "clamp" }) *
                   interpolate(frame, [exitStart + 4, exitStart + 20], [1, 0], {
                     extrapolateLeft: "clamp", extrapolateRight: "clamp",
                   });

  // Accent line under title
  const lineW = interpolate(frame, [TITLE_ENTER + 6, TITLE_ENTER + 32], [0, 200], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });

  // Determine bar gap/padding based on count
  const barsGap = compact ? 14 : 20;

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
        <Sequence from={TITLE_ENTER} durationInFrames={24}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.12} />
        </Sequence>
        {bars.map((_, i) => (
          <Sequence key={i} from={BARS_BASE + i * BAR_STEP + 4} durationInFrames={20}>
            <Audio src={staticFile("sfx/ping.wav")} volume={Math.max(0.04, 0.14 - i * 0.02)} />
          </Sequence>
        ))}
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        {/* Центрированный контейнер: label(240) + gap(20) + bar(820) + gap(20) + value(120) = 1220px */}
        <AbsoluteFill style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          padding: "80px 0",
        }}>
          <div style={{
            width: 1220,
            display: "flex",
            flexDirection: "column",
            gap: 24,
          }}>
            {/* Title */}
            <div style={{
              opacity: titleOp,
              transform: `translateX(${titleX}px)`,
              display: "flex",
              flexDirection: "column",
              gap: 10,
              paddingLeft: 260, // выравнивание по левому краю баров (240 label + 20 gap)
            }}>
              <div style={{
                fontFamily: SYNE,
                fontSize: 13,
                fontWeight: 800,
                letterSpacing: "0.35em",
                textTransform: "uppercase",
                color: "#FFFFFF",
              }}>
                {title}
              </div>
              <div style={{
                width: lineW,
                height: 1,
                background: `linear-gradient(to right, ${accent_color}88, transparent)`,
              }} />
            </div>

            {/* Bars */}
            <div style={{
              display: "flex",
              flexDirection: "column",
              gap: barsGap,
            }}>
              {bars.map((bar, i) => (
                <BarRow
                  key={i}
                  bar={bar}
                  frame={frame}
                  enterFrame={BARS_BASE + i * BAR_STEP}
                  exitStart={exitStart}
                  accentColor={accent_color}
                  maxVal={maxVal}
                  fps={fps}
                  compact={compact}
                  index={i}
                  total={bars.length}
                />
              ))}
            </div>
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
