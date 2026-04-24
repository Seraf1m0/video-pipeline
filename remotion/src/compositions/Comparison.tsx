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

export interface ComparisonSide {
  label: string;
  value: string;
  sub?: string;
}
export interface ComparisonProps {
  title?: string;
  left: ComparisonSide;
  right: ComparisonSide;
  left_color?: string;
  right_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const LEFT_DEFAULT  = "#FF6B35";
const RIGHT_DEFAULT = "#00C8FF";

// Exit starts this many frames before the end
const EXIT_DUR = 55;

// ─── GRID ─────────────────────────────────────────────────────────────────────
const Grid: React.FC<{ frame: number; lc: string; rc: string; total: number }> = ({
  frame, lc, rc, total,
}) => {
  const inOp  = interpolate(frame, [8, 32], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp * 0.11;
  const cols = 10; const rows = 6;
  return (
    <AbsoluteFill style={{ opacity: op }}>
      {Array.from({ length: cols + 1 }, (_, i) => (
        <div key={`v${i}`} style={{
          position: "absolute", left: `${(i / cols) * 100}%`, top: 0, bottom: 0, width: 1,
          background: i <= cols / 2
            ? `linear-gradient(to bottom, transparent, ${lc}88, transparent)`
            : `linear-gradient(to bottom, transparent, ${rc}88, transparent)`,
        }} />
      ))}
      {Array.from({ length: rows + 1 }, (_, i) => (
        <div key={`h${i}`} style={{
          position: "absolute", top: `${(i / rows) * 100}%`, left: 0, right: 0, height: 1,
          background: "linear-gradient(to right, transparent, #FFFFFF18, transparent)",
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; lc: string; rc: string; total: number }> = ({
  frame, lc, rc, total,
}) => {
  const inOp  = interpolate(frame, [0, 40], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 30], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const blobOp = inOp * outOp;

  const l1x = 14 + noise2D("l1x", frame * 0.0015, 0) * 5;
  const l1y = 46 + noise2D("l1y", 0,              frame * 0.0015) * 4;
  const r1x = 86 + noise2D("r1x", frame * 0.0015, 2) * 5;
  const r1y = 46 + noise2D("r1y", 2,              frame * 0.0015) * 4;
  const breatheL = 1 + Math.sin(frame * 0.022) * 0.06;
  const breatheR = 1 + Math.sin(frame * 0.025 + 1.2) * 0.06;

  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute", inset: 0,
        background: `linear-gradient(105deg,
          ${lc}12 0%, ${lc}05 32%,
          transparent 45%, transparent 55%,
          ${rc}05 68%, ${rc}12 100%)`,
      }} />
      <div style={{
        position: "absolute", opacity: blobOp * 0.22,
        left: `${l1x}%`, top: `${l1y}%`,
        transform: `translate(-50%,-50%) scale(${breatheL})`,
        width: 720, height: 720, borderRadius: "50%",
        background: `radial-gradient(circle, ${lc} 0%, transparent 70%)`,
        filter: "blur(55px)",
      }} />
      <div style={{
        position: "absolute", opacity: blobOp * 0.22,
        left: `${r1x}%`, top: `${r1y}%`,
        transform: `translate(-50%,-50%) scale(${breatheR})`,
        width: 720, height: 720, borderRadius: "50%",
        background: `radial-gradient(circle, ${rc} 0%, transparent 70%)`,
        filter: "blur(55px)",
      }} />
      {/* Vignette */}
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 85% 85% at 50% 50%, transparent 35%, #000000CC 100%)",
      }} />
    </AbsoluteFill>
  );
};

// ─── CENTER DIVIDER ────────────────────────────────────────────────────────────
const CenterDivider: React.FC<{ frame: number; lc: string; rc: string; total: number }> = ({
  frame, lc, rc, total,
}) => {
  const lineH = interpolate(frame, [14, 46], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  // exit: collapse back to center
  const exitP = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 30], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.in(Easing.cubic),
  });
  const collapseH = interpolate(exitP, [0, 1], [lineH, 0]);
  const h = exitP > 0 ? collapseH : lineH;

  const glow = 0.55 + Math.sin(frame * 0.05) * 0.45;
  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute", left: "50%", top: `${(100 - h) / 2}%`,
        transform: "translateX(-50%)",
        width: 1.5, height: `${h}%`,
        background: `linear-gradient(to bottom, transparent, ${lc}99 22%, #FFFFFF77 50%, ${rc}99 78%, transparent)`,
        boxShadow: `0 0 ${10 * glow}px #FFFFFF33`,
      }} />
    </AbsoluteFill>
  );
};

// ─── SCAN LINE ────────────────────────────────────────────────────────────────
const ScanLine: React.FC<{ frame: number }> = ({ frame }) => {
  const y  = interpolate(frame, [0, 24], [0, 110], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const op = interpolate(frame, [0, 4, 20, 28], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0, height: 2,
      background: "linear-gradient(to right, transparent, #FFFFFF99, transparent)",
      boxShadow: "0 0 20px #FFFFFF55",
      opacity: op,
    }} />
  );
};

// ─── EXIT SCAN LINE (bottom → top on exit) ────────────────────────────────────
const ExitScan: React.FC<{ frame: number; total: number }> = ({ frame, total }) => {
  const start = total - EXIT_DUR + 10;
  const y  = interpolate(frame, [start, start + 22], [110, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.in(Easing.cubic),
  });
  const op = interpolate(frame, [start, start + 4, start + 18, start + 26], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0, height: 2,
      background: "linear-gradient(to right, transparent, #FFFFFF77, transparent)",
      boxShadow: "0 0 18px #FFFFFF44",
      opacity: op,
    }} />
  );
};

// ─── FLOATING PARTICLES ───────────────────────────────────────────────────────
const Particles: React.FC<{ frame: number; lc: string; rc: string; total: number }> = ({
  frame, lc, rc, total,
}) => {
  const inOp  = interpolate(frame, [22, 44], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR + 4, total - EXIT_DUR + 24], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const globalOp = inOp * outOp;

  return (
    <AbsoluteFill>
      {Array.from({ length: 18 }, (_, i) => {
        const side  = i % 2 === 0 ? "left" : "right";
        const color = side === "left" ? lc : rc;
        const baseX = side === "left"
          ? 4  + (Math.floor(i / 2) % 5) * 9
          : 53 + (Math.floor(i / 2) % 5) * 9;
        const baseY = 15 + (i * 41.7) % 68;
        const drift = noise2D(`px${i}`, frame * (0.01 + i * 0.003), i * 0.4) * 3;
        const floatY = ((baseY - frame * (0.035 + i * 0.004)) % 85 + 85) % 85 + 5;
        const alpha = (0.25 + (i % 3) * 0.12) * globalOp;
        const sz = 1.5 + (i % 3) * 0.9;
        return (
          <div key={i} style={{
            position: "absolute",
            left: `${baseX + drift}%`, top: `${floatY}%`,
            width: sz, height: sz, borderRadius: "50%",
            background: color,
            boxShadow: `0 0 ${sz * 3}px ${color}`,
            opacity: alpha,
            transform: "translate(-50%,-50%)",
          }} />
        );
      })}
    </AbsoluteFill>
  );
};

// ─── VALUE PANEL ──────────────────────────────────────────────────────────────
const Panel: React.FC<{
  side: ComparisonSide;
  color: string;
  from: "left" | "right";
  frame: number;
  enterFrame: number;
  total: number;
}> = ({ side, color, from, frame, enterFrame, total }) => {
  const { fps } = useVideoConfig();
  const dir = from === "left" ? -1 : 1;

  // entrance
  const slideP = interpolate(frame, [enterFrame, enterFrame + 44], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.exp),
  });
  const xIn  = interpolate(slideP, [0, 1], [dir * 300, 0]);
  const opIn = interpolate(frame, [enterFrame, enterFrame + 20], [0, 1], { extrapolateRight: "clamp" });

  // exit: slide back out the same direction
  const exitStart = total - EXIT_DUR + 6;
  const exitP = interpolate(frame, [exitStart, exitStart + 32], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.in(Easing.cubic),
  });
  const xOut  = interpolate(exitP, [0, 1], [0, dir * 340]);
  const opOut = interpolate(frame, [exitStart + 14, exitStart + 32], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const x  = xIn + xOut;
  const op = opIn * (exitP > 0 ? opOut : 1);

  // label
  const lblF   = enterFrame + 50;
  const lblSpr = spring({ frame: frame - lblF, fps, config: { damping: 32, stiffness: 190 } });
  const lblY   = interpolate(lblSpr, [0, 1], [20, 0]);
  const lblOpIn  = interpolate(frame, [lblF, lblF + 14], [0, 1], { extrapolateRight: "clamp" });
  const lblOpOut = interpolate(frame, [exitStart, exitStart + 18], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const lblOp = lblOpIn * (exitP > 0 ? lblOpOut : 1);

  const lineW = interpolate(frame, [lblF + 8, lblF + 40], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.exp),
  });
  const subOp = interpolate(frame, [lblF + 22, lblF + 38], [0, 1], { extrapolateRight: "clamp" })
    * (exitP > 0 ? interpolate(frame, [exitStart, exitStart + 14], [1, 0], {
        extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : 1);

  const settled = Math.max(0, frame - (enterFrame + 48));
  const breathe = 1 + Math.sin(settled * 0.036 + (from === "left" ? 0 : Math.PI)) * 0.007;
  const glow    = 0.45 + Math.sin(settled * 0.048 + (from === "left" ? 0 : 1.5)) * 0.45;

  const len  = side.value.length;
  const size = len > 18 ? 56 : len > 14 ? 72 : len > 10 ? 96 : len > 6 ? 124 : len > 3 ? 158 : 196;

  return (
    <div style={{
      width: "44%",
      display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 26,
      overflow: "hidden",
    }}>
      <div style={{
        opacity: op,
        transform: `translateX(${x}px) scale(${breathe})`,
        fontFamily: SYNE,
        fontSize: size, fontWeight: "800",
        letterSpacing: "-0.035em", lineHeight: 1.1,
        color: "#FFFFFF",
        textShadow: `
          0 0 ${38 * glow}px ${color}EE,
          0 0 ${76 * glow}px ${color}77,
          0 0 ${130 * glow}px ${color}33,
          0 3px 8px rgba(0,0,0,0.95)
        `,
        textAlign: "center", wordBreak: "break-word",
        maxWidth: "100%",
      }}>
        {side.value}
      </div>

      <div style={{
        opacity: lblOp, transform: `translateY(${lblY}px)`,
        display: "flex", flexDirection: "column", alignItems: "center", gap: 10,
      }}>
        <div style={{
          fontFamily: SYNE, fontSize: 13, fontWeight: "700",
          letterSpacing: "0.30em", textTransform: "uppercase",
          color: "#FFFFFF", textAlign: "center",
        }}>
          {side.label}
        </div>
        <div style={{
          width: `${lineW}%`, maxWidth: 120, height: 1.5,
          background: `linear-gradient(to right, transparent, ${color}CC, transparent)`,
          borderRadius: 1,
        }} />
        {side.sub && (
          <div style={{
            opacity: subOp,
            fontFamily: MONTSERRAT, fontSize: 14, fontWeight: "600",
            color: "#FFFFFF66", textAlign: "center",
            letterSpacing: "0.04em", lineHeight: 1.5, maxWidth: 280,
          }}>
            {side.sub}
          </div>
        )}
      </div>
    </div>
  );
};

// ─── VS BADGE ─────────────────────────────────────────────────────────────────
const Vs: React.FC<{ frame: number; lc: string; rc: string; total: number }> = ({
  frame, lc, rc, total,
}) => {
  const { fps } = useVideoConfig();
  const ENTER = 72;
  const spr   = spring({ frame: frame - ENTER, fps, config: { damping: 10, stiffness: 420, mass: 0.5 } });
  const scale = interpolate(spr, [0, 1], [0, 1]);
  const opIn  = interpolate(frame, [ENTER, ENTER + 10], [0, 1], { extrapolateRight: "clamp" });

  const exitStart = total - EXIT_DUR + 14;
  const exitSpr   = spring({ frame: frame - exitStart, fps, config: { damping: 14, stiffness: 380, mass: 0.6 } });
  const scaleOut  = exitStart <= frame ? interpolate(exitSpr, [0, 1], [1, 0]) : 1;
  const opOut     = interpolate(frame, [exitStart, exitStart + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const settled = Math.max(0, frame - ENTER + 14);
  const pulse   = 1 + Math.sin(settled * 0.048) * 0.022;
  const glowCyc = 0.5 + Math.sin(settled * 0.055) * 0.5;

  return (
    <div style={{ width: 90, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{
        opacity: opIn * (exitStart <= frame ? opOut : 1),
        transform: `scale(${scale * (exitStart <= frame ? scaleOut : pulse)})`,
        width: 74, height: 74, borderRadius: "50%",
        background: `radial-gradient(circle at 38% 34%, ${lc}28 0%, ${rc}14 60%, transparent 100%)`,
        border: "2px solid #FFFFFF44",
        display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: `
          0 0 ${22 * glowCyc}px ${lc}55,
          0 0 ${22 * glowCyc}px ${rc}55,
          inset 0 0 14px #FFFFFF0A
        `,
      }}>
        <span style={{
          fontFamily: SYNE, fontSize: 18, fontWeight: "900",
          color: "#FFFFFF", letterSpacing: "0.04em",
          textShadow: "0 0 12px #FFFFFF88, 0 1px 3px rgba(0,0,0,0.9)",
        }}>VS</span>
      </div>
    </div>
  );
};

// ─── TITLE ────────────────────────────────────────────────────────────────────
const Title: React.FC<{ text: string; frame: number; total: number }> = ({ text, frame, total }) => {
  const opIn  = interpolate(frame, [6, 24], [0, 1], { extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const yIn   = interpolate(frame, [6, 24], [-14, 0], { extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const opOut = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const lw = interpolate(frame, [14, 40], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  return (
    <div style={{ opacity: opIn * opOut, transform: `translateY(${yIn}px)`,
      display: "flex", flexDirection: "column", alignItems: "center", gap: 10 }}>
      <div style={{
        fontFamily: SYNE, fontSize: 11, fontWeight: "800",
        letterSpacing: "0.46em", textTransform: "uppercase", color: "#FFFFFF55",
      }}>{text}</div>
      <div style={{
        width: `${lw}%`, maxWidth: 150, height: 1,
        background: "linear-gradient(to right, transparent, #FFFFFF44, transparent)",
      }} />
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const Comparison: React.FC<ComparisonProps> = ({
  title, left, right,
  left_color  = LEFT_DEFAULT,
  right_color = RIGHT_DEFAULT,
  duration_s  = 12,
  bg_color    = "#020218",
  seed        = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const total = Math.round(duration_s * fps);

  const rand = seededRand(seed);
  const COMP_COLORS = ["#00C8FF","#FF3CAC","#4DFFB4","#FFD700","#A855F7","#FF6B35"];
  const leftColor  = left_color  || COMP_COLORS[Math.floor(rand() * COMP_COLORS.length)];
  const rightColor = right_color || COMP_COLORS[Math.floor(rand() * COMP_COLORS.length)];

  // Only final hard fade — everything else handles its own exit
  const finalFade = interpolate(frame, [total - 8, total], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const fadeIn = interpolate(frame, [0, 18], [0, 1], {
    extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} lc={leftColor} rc={rightColor} total={total} />
        <Grid frame={frame} lc={leftColor} rc={rightColor} total={total} />
        <Particles frame={frame} lc={leftColor} rc={rightColor} total={total} />
        <CenterDivider frame={frame} lc={leftColor} rc={rightColor} total={total} />
        <ScanLine frame={frame} />
        <ExitScan frame={frame} total={total} />

        {/* SFX — entrance */}
        <Sequence from={0}  durationInFrames={22}><Audio src={staticFile("sfx/rise.wav")}    volume={0.18} /></Sequence>
        <Sequence from={4}  durationInFrames={32}><Audio src={staticFile("sfx/ping.wav")}    volume={0.12} /></Sequence>
        <Sequence from={20} durationInFrames={22}><Audio src={staticFile("sfx/impact.wav")}  volume={0.20} /></Sequence>
        <Sequence from={34} durationInFrames={22}><Audio src={staticFile("sfx/impact.wav")}  volume={0.16} /></Sequence>
        <Sequence from={72} durationInFrames={28}><Audio src={staticFile("sfx/stinger.wav")} volume={0.14} /></Sequence>
        {/* SFX — exit (library whoosh) */}
        <Sequence from={total - EXIT_DUR + 4} durationInFrames={30}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.24} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 60, padding: "0 40px",
        }}>
          {title && <Title text={title} frame={frame} total={total} />}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: "100%" }}>
            <Panel side={left}  color={leftColor}  from="left"  frame={frame} enterFrame={20} total={total} />
            <Vs frame={frame} lc={leftColor} rc={rightColor} total={total} />
            <Panel side={right} color={rightColor} from="right" frame={frame} enterFrame={34} total={total} />
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
