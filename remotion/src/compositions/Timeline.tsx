import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne    } from "@remotion/google-fonts/Syne";
import { loadFont as loadManrope } from "@remotion/google-fonts/Manrope";
import { noise2D } from "@remotion/noise";
import { seededShuffle } from "../utils/seeded";

const { fontFamily: SYNE    } = loadSyne();
const { fontFamily: MANROPE } = loadManrope();

export interface TimelineStep {
  label: string;
  desc?: string;
  color?: string;
}
export interface TimelineProps {
  title: string;
  steps: TimelineStep[];
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const STEP_COLORS = ["#00C8FF","#FF3CAC","#4DFFB4","#FFD700","#A855F7","#FF6B35"];

const PALETTE: [string, string][] = [
  ["#00C8FF", "#0071E3"],
  ["#FF3CAC", "#FF6B35"],
  ["#4DFFB4", "#00C8FF"],
  ["#FFD700", "#FF9F00"],
  ["#A855F7", "#EC4899"],
];

// ─── CONNECTOR LINE between two circles ───────────────────────────────────────
const Connector: React.FC<{
  frame: number; startFrame: number; color: string;
}> = ({ frame, startFrame, color }) => {
  const w = interpolate(frame, [startFrame, startFrame + 22], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  return (
    <div style={{
      flex: 1, height: 1.5, alignSelf: "center",
      background: `linear-gradient(to right, ${color}88, ${color}22)`,
      clipPath: `inset(0 ${100 - w}% 0 0)`,
    }} />
  );
};

// ─── CIRCLE (numbered step) ───────────────────────────────────────────────────
const Circle: React.FC<{
  num: number; colorA: string; colorB: string;
  frame: number; enterFrame: number;
}> = ({ num, colorA, colorB, frame, enterFrame }) => {
  const { fps } = useVideoConfig();
  const pop = spring({
    frame: frame - enterFrame, fps,
    config: { damping: 7, stiffness: 500, mass: 0.5 },
  });
  const scale   = interpolate(pop, [0, 1], [0, 1]);
  const opacity = interpolate(frame, [enterFrame, enterFrame + 6], [0, 1], {
    extrapolateRight: "clamp",
  });
  const glow = 0.6 + Math.sin(frame * 0.06 + num) * 0.4;

  return (
    <div style={{
      width: 56, height: 56, borderRadius: "50%",
      background: `linear-gradient(135deg, ${colorA}, ${colorB})`,
      display: "flex", alignItems: "center", justifyContent: "center",
      transform: `scale(${scale})`, opacity, flexShrink: 0,
      boxShadow: `0 0 ${24 * glow}px ${colorA}88, 0 0 ${48 * glow}px ${colorA}33`,
    }}>
      <span style={{
        fontFamily: SYNE, fontSize: 20, fontWeight: "800",
        color: "#fff",
      }}>{num}</span>
    </div>
  );
};

// ─── STEP LABEL + DESC ────────────────────────────────────────────────────────
const StepText: React.FC<{
  label: string; desc?: string; colorA: string;
  frame: number; enterFrame: number;
  labelFontSize: number; descFontSize: number;
}> = ({ label, desc, colorA, frame, enterFrame, labelFontSize, descFontSize }) => {
  const { fps } = useVideoConfig();
  const labelEnter = enterFrame + 8;
  const descEnter  = enterFrame + 18;

  const labelSpring = spring({ frame: frame - labelEnter, fps, config: { damping: 20, stiffness: 240 } });
  const labelY  = interpolate(labelSpring, [0, 1], [16, 0]);
  const labelOp = interpolate(frame, [labelEnter, labelEnter + 10], [0, 1], { extrapolateRight: "clamp" });

  const descOp = interpolate(frame, [descEnter, descEnter + 12], [0, 1], { extrapolateRight: "clamp" });
  const descY  = interpolate(frame, [descEnter, descEnter + 12], [8, 0], {
    extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });

  return (
    <div style={{ textAlign: "center", display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{
        opacity: labelOp, transform: `translateY(${labelY}px)`,
        fontFamily: SYNE, fontSize: labelFontSize, fontWeight: "700",
        letterSpacing: "0.18em", textTransform: "uppercase",
        color: colorA,
      }}>
        {label}
      </div>
      {desc && (
        <div style={{
          opacity: descOp, transform: `translateY(${descY}px)`,
          fontFamily: MANROPE, fontSize: descFontSize, fontWeight: "400",
          color: "#FFFFFF66", letterSpacing: "0.04em", lineHeight: 1.4,
          maxWidth: 160,
        }}>
          {desc}
        </div>
      )}
    </div>
  );
};

// ─── SINGLE STEP COLUMN ───────────────────────────────────────────────────────
const Step: React.FC<{
  step: TimelineStep; index: number;
  frame: number; total: number; stepColor?: string;
  labelFontSize: number; descFontSize: number;
}> = ({ step, index, frame, total, stepColor, labelFontSize, descFontSize }) => {
  const [palA, palB] = PALETTE[index % PALETTE.length];
  const colorA = step.color || stepColor || palA;
  const colorB = palB;
  const enterFrame = 14 + index * 20;

  return (
    <div style={{
      display: "flex", flexDirection: "column",
      alignItems: "center", gap: 16,
      flex: 1, minWidth: 0,
    }}>
      <Circle num={index + 1} colorA={colorA} colorB={colorB} frame={frame} enterFrame={enterFrame} />
      <StepText label={step.label} desc={step.desc} colorA={colorA} frame={frame} enterFrame={enterFrame} labelFontSize={labelFontSize} descFontSize={descFontSize} />
    </div>
  );
};

// ─── TITLE ────────────────────────────────────────────────────────────────────
const Title: React.FC<{ text: string; frame: number }> = ({ text, frame }) => {
  const { fps } = useVideoConfig();
  const prog  = spring({ frame, fps, config: { damping: 26, stiffness: 220 } });
  const y     = interpolate(prog, [0, 1], [-20, 0]);
  const op    = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });
  const lineW = interpolate(frame, [4, 28], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.exp),
  });
  return (
    <div style={{ opacity: op, transform: `translateY(${y}px)`, textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
      <div style={{ fontFamily: SYNE, fontSize: 11, fontWeight: "800", letterSpacing: "0.40em", textTransform: "uppercase", color: "#FFFFFF44" }}>
        {text}
      </div>
      <div style={{ width: `${lineW}%`, maxWidth: 140, height: 1, background: "linear-gradient(to right, transparent, #FFFFFF33, transparent)" }} />
    </div>
  );
};

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; steps: TimelineStep[] }> = ({ frame, steps }) => (
  <AbsoluteFill>
    {steps.map((_, i) => {
      const [colorA] = PALETTE[i % PALETTE.length];
      const xBase = steps.length === 1 ? 50 : 10 + (i / (steps.length - 1)) * 80;
      const nx = noise2D(`tx${i}`, frame * 0.003, 0) * 10;
      const ny = noise2D(`ty${i}`, 0, frame * 0.003) * 8;
      const op = 0.05 + noise2D(`to${i}`, frame * 0.005, 0) * 0.02;
      return (
        <div key={i} style={{
          position: "absolute", left: `${xBase + nx}%`, top: `${48 + ny}%`,
          transform: "translate(-50%, -50%)",
          width: 360, height: 360, borderRadius: "50%",
          background: colorA, filter: "blur(100px)", opacity: op,
        }} />
      );
    })}
  </AbsoluteFill>
);

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const Timeline: React.FC<TimelineProps> = ({ title, steps, duration_s = 10, bg_color = "#020218", seed = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const STEP_COLORS_S = seededShuffle(STEP_COLORS, seed);
  const totalFrames = Math.round(duration_s * fps);

  const maxLabelLen = Math.max(...steps.map(s => s.label.length));
  const n = steps.length;
  const labelFontSize =
    n >= 5 ? (maxLabelLen > 20 ? 11 : 13) :
    n >= 4 ? (maxLabelLen > 20 ? 12 : 14) : 14;
  const descFontSize = Math.max(11, labelFontSize - 1);

  const fadeIn  = interpolate(frame, [0, 8],  [0, 1], { extrapolateRight: "clamp" });
  const fadeOut = interpolate(frame, [totalFrames - 10, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * fadeOut, width: "100%", height: "100%" }}>
        <Bg frame={frame} steps={steps} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={2} durationInFrames={30}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.12} />
        </Sequence>
        {steps.map((_, i) => (
          <Sequence key={i} from={14 + i * 20} durationInFrames={15}>
            <Audio src={staticFile("sfx/impact.wav")} volume={0.12 - i * 0.01} />
          </Sequence>
        ))}

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 52, padding: "0 80px",
        }}>
          <Title text={title} frame={frame} />

          {/* Steps row with connectors */}
          <div style={{
            display: "flex", alignItems: "flex-start",
            justifyContent: "center", width: "100%",
          }}>
            {steps.map((step, i) => (
              <React.Fragment key={i}>
                {i > 0 && (
                  <Connector
                    frame={frame}
                    startFrame={14 + (i - 1) * 20 + 12}
                    color={PALETTE[(i - 1) % PALETTE.length][0]}
                  />
                )}
                <Step step={step} index={i} frame={frame} total={steps.length} stepColor={STEP_COLORS_S[i % STEP_COLORS_S.length]} labelFontSize={labelFontSize} descFontSize={descFontSize} />
              </React.Fragment>
            ))}
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
