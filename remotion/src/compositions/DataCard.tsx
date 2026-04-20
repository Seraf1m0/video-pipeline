import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne } from "@remotion/google-fonts/Syne";
import { loadFont as loadMono } from "@remotion/google-fonts/IBMPlexMono";
import { noise2D } from "@remotion/noise";
import { seededShuffle } from "../utils/seeded";

const { fontFamily: SYNE } = loadSyne();
const { fontFamily: MONO } = loadMono();

export interface DataItem {
  label: string;
  value: string;
  color?: string;
  numeric?: number;
  suffix?: string;
}
export interface DataCardProps {
  title: string;
  items: DataItem[];
  lang?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const GRADIENTS: [string, string][] = [
  ["#FF3CAC", "#FF6B35"],
  ["#00C8FF", "#00FFB2"],
  ["#FFD700", "#FF9F00"],
  ["#A855F7", "#EC4899"],
  ["#22D3EE", "#6366F1"],
];

const ITEM_COLORS = ["#00C8FF","#FF3CAC","#4DFFB4","#FFD700","#A855F7","#FF6B35"];

const EXIT_DUR = 50;

function gradientStyle(index: number): React.CSSProperties {
  const [a, b] = GRADIENTS[index % GRADIENTS.length];
  return {
    background: `linear-gradient(135deg, ${a} 0%, ${b} 100%)`,
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
    backgroundClip: "text",
  };
}

function calcFontSize(items: DataItem[]): number {
  const maxLen = Math.max(...items.map(it =>
    it.numeric !== undefined
      ? it.numeric.toLocaleString("de-DE").length + (it.suffix?.length ?? 0)
      : it.value.length
  ));
  const n = items.length;
  if (n === 1) return maxLen > 9 ? 120 : 150;
  if (n === 2) return maxLen > 9 ? 96 : 116;
  if (maxLen > 10) return 68;
  if (maxLen > 7) return 80;
  return 92;
}

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; items: DataItem[]; total: number }> = ({ frame, items, total }) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 22], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const blobOp = inOp * outOp;

  return (
    <AbsoluteFill>
      {/* Per-item orbs */}
      {items.map((_, i) => {
        const [colorA] = GRADIENTS[i % GRADIENTS.length];
        const xBase = items.length === 1 ? 50 : 10 + (i / (items.length - 1)) * 80;
        const nx = noise2D(`tx${i}`, frame * 0.002, 0) * 8;
        const ny = noise2D(`ty${i}`, 0, frame * 0.002) * 6;
        const breathe = 1 + Math.sin(frame * 0.02 + i * 1.2) * 0.07;
        return (
          <div key={i} style={{
            position: "absolute",
            left: `${xBase + nx}%`, top: `${48 + ny}%`,
            transform: `translate(-50%, -50%) scale(${breathe})`,
            width: 440, height: 440, borderRadius: "50%",
            background: `radial-gradient(circle, ${colorA} 0%, transparent 70%)`,
            filter: "blur(80px)",
            opacity: blobOp * 0.14,
          }} />
        );
      })}
      {/* Vignette */}
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 85% 80% at 50% 50%, transparent 30%, #000000CC 100%)",
      }} />
    </AbsoluteFill>
  );
};

// ─── GRID ─────────────────────────────────────────────────────────────────────
const Grid: React.FC<{ frame: number; items: DataItem[]; total: number }> = ({ frame, items, total }) => {
  const inOp  = interpolate(frame, [6, 28], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 18], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp * 0.08;
  const cols = 8; const rows = 5;
  const [c0] = GRADIENTS[0 % GRADIENTS.length];
  return (
    <AbsoluteFill style={{ opacity: op }}>
      {Array.from({ length: cols + 1 }, (_, i) => (
        <div key={`v${i}`} style={{
          position: "absolute", left: `${(i / cols) * 100}%`, top: 0, bottom: 0, width: 1,
          background: `linear-gradient(to bottom, transparent, ${c0}66, transparent)`,
        }} />
      ))}
      {Array.from({ length: rows + 1 }, (_, i) => (
        <div key={`h${i}`} style={{
          position: "absolute", top: `${(i / rows) * 100}%`, left: 0, right: 0, height: 1,
          background: "linear-gradient(to right, transparent, #FFFFFF14, transparent)",
        }} />
      ))}
    </AbsoluteFill>
  );
};

// ─── SCAN LINE (entrance) ─────────────────────────────────────────────────────
const ScanLine: React.FC<{ frame: number }> = ({ frame }) => {
  const y  = interpolate(frame, [0, 18], [0, 110], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });
  const op = interpolate(frame, [0, 3, 14, 22], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0, height: 1.5,
      background: "linear-gradient(to right, transparent, #FFFFFF88, transparent)",
      boxShadow: "0 0 20px #FFFFFF55",
      opacity: op,
    }} />
  );
};

// ─── EXIT SCAN LINE (bottom → top) ────────────────────────────────────────────
const ExitScan: React.FC<{ frame: number; total: number }> = ({ frame, total }) => {
  const start = total - EXIT_DUR + 8;
  const y  = interpolate(frame, [start, start + 20], [110, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const op = interpolate(frame, [start, start + 4, start + 16, start + 24], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0, height: 1.5,
      background: "linear-gradient(to right, transparent, #FFFFFF66, transparent)",
      boxShadow: "0 0 16px #FFFFFF44",
      opacity: op,
    }} />
  );
};

// ─── ANIMATED NUMBER ──────────────────────────────────────────────────────────
const AnimNum: React.FC<{
  item: DataItem; idx: number; frame: number;
  enterFrame: number; totalFrames: number; fontSize: number; exitStart: number;
}> = ({ item, idx, frame, enterFrame, totalFrames, fontSize, exitStart }) => {
  const { fps } = useVideoConfig();

  // entrance: fall from above
  const fallSpring = spring({ frame: frame - enterFrame, fps, config: { damping: 8, stiffness: 420, mass: 1.0 } });
  const yIn = interpolate(fallSpring, [0, 1], [-200, 0]);
  const opIn = interpolate(frame, [enterFrame, enterFrame + 8], [0, 1], { extrapolateRight: "clamp" });

  const fallPrev = spring({ frame: frame - enterFrame - 1, fps, config: { damping: 8, stiffness: 420, mass: 1.0 } });
  const velocity = Math.abs((fallSpring - fallPrev) * 200);
  const blurPx   = Math.min(velocity * 0.4, 18);

  // exit: fly left
  const exitP = interpolate(frame, [exitStart, exitStart + 28], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const xOut  = interpolate(exitP, [0, 1], [0, -320]);
  const opOut = interpolate(frame, [exitStart + 10, exitStart + 28], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const y  = yIn;
  const x  = xOut;
  const op = opIn * (exitP > 0 ? opOut : 1);

  const breathe = 1 + Math.sin(frame * 0.055 + idx) * 0.007;

  let display: string;
  if (item.numeric !== undefined) {
    const countEnd = Math.round(totalFrames * 0.78);
    const progress = interpolate(frame, [enterFrame + 6, countEnd], [0, 1], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
    });
    display = Math.round(progress * item.numeric).toLocaleString("de-DE") + (item.suffix ?? "");
  } else {
    display = item.value;
  }

  return (
    <div style={{
      opacity: op,
      transform: `translate(${x}px, ${y}px) scale(${breathe})`,
      filter: blurPx > 0.5 ? `blur(${blurPx}px)` : undefined,
      fontFamily: MONO, fontSize, fontWeight: "700",
      letterSpacing: "-0.04em", lineHeight: 1,
      textAlign: "center", whiteSpace: "nowrap",
      ...gradientStyle(idx),
    }}>
      {display}
    </div>
  );
};

// ─── PROGRESS BAR ─────────────────────────────────────────────────────────────
const Bar: React.FC<{
  frame: number; enterFrame: number; totalFrames: number; idx: number; exitStart: number;
}> = ({ frame, enterFrame, totalFrames, idx, exitStart }) => {
  const [colorA, colorB] = GRADIENTS[idx % GRADIENTS.length];
  const w = interpolate(frame, [enterFrame + 6, Math.round(totalFrames * 0.78)], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });
  // exit: bar collapses
  const wExit = interpolate(frame, [exitStart, exitStart + 22], [w, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const barW = frame >= exitStart ? wExit : w;
  const opExit = interpolate(frame, [exitStart + 10, exitStart + 22], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <div style={{
      width: "100%", height: 2, background: "#FFFFFF0D", borderRadius: 2, marginTop: 18,
      opacity: frame >= exitStart ? opExit : 1,
    }}>
      <div style={{
        height: "100%", width: `${barW}%`,
        background: `linear-gradient(to right, ${colorA}, ${colorB})`,
        boxShadow: `0 0 10px ${colorA}CC`,
        borderRadius: 2,
      }} />
    </div>
  );
};

// ─── CELL ─────────────────────────────────────────────────────────────────────
const Cell: React.FC<{
  item: DataItem; idx: number; total: number;
  frame: number; totalFrames: number; fontSize: number; exitStart: number;
  itemColor?: string;
}> = ({ item, idx, frame, totalFrames, fontSize, exitStart, itemColor }) => {
  const { fps } = useVideoConfig();
  const [colorA] = GRADIENTS[idx % GRADIENTS.length];
  const labelColor = item.color || itemColor || colorA;
  const enterFrame = 10 + idx * 20;

  const lblEnter = enterFrame + 28;
  const lblProg  = spring({ frame: frame - lblEnter, fps, config: { damping: 22, stiffness: 280 } });
  const lblYIn   = interpolate(lblProg, [0, 1], [20, 0]);
  const lblOpIn  = interpolate(frame, [lblEnter, lblEnter + 10], [0, 1], { extrapolateRight: "clamp" });

  // exit: label slides left
  const lblExitP = interpolate(frame, [exitStart + 4, exitStart + 24], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const lblXOut  = interpolate(lblExitP, [0, 1], [0, -280]);
  const lblOpOut = interpolate(frame, [exitStart + 4, exitStart + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const lblY  = lblYIn;
  const lblX  = lblXOut;
  const lblOp = lblOpIn * (frame >= exitStart + 4 ? lblOpOut : 1);

  const lineW = interpolate(frame, [lblEnter, lblEnter + 22], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });

  return (
    <div style={{
      flex: 1, minWidth: 0,
      display: "flex", flexDirection: "column", alignItems: "center",
      gap: 12, padding: "0 20px",
    }}>
      <AnimNum
        item={item} idx={idx} frame={frame}
        enterFrame={enterFrame} totalFrames={totalFrames}
        fontSize={fontSize} exitStart={exitStart}
      />

      {item.numeric !== undefined && (
        <Bar frame={frame} enterFrame={enterFrame} totalFrames={totalFrames} idx={idx} exitStart={exitStart} />
      )}

      <div style={{ opacity: lblOp, transform: `translate(${lblX}px, ${lblY}px)`, textAlign: "center" }}>
        <div style={{
          fontFamily: SYNE, fontSize: 11, fontWeight: "700",
          letterSpacing: "0.32em", textTransform: "uppercase",
          color: `${labelColor}BB`, marginBottom: 6,
        }}>
          {item.label}
        </div>
        <div style={{
          margin: "0 auto", width: `${lineW}%`, maxWidth: 80, height: 1,
          background: `linear-gradient(to right, transparent, ${labelColor}66, transparent)`,
        }} />
      </div>
    </div>
  );
};

// ─── SEPARATOR ────────────────────────────────────────────────────────────────
const Sep: React.FC<{ frame: number; delay: number; exitStart: number }> = ({ frame, delay, exitStart }) => {
  const h = interpolate(frame, [delay, delay + 28], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });
  const centerPct = (100 - h) / 2;
  const opExit = interpolate(frame, [exitStart, exitStart + 18], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      flexShrink: 0, width: 1, height: 220, alignSelf: "center",
      opacity: frame >= exitStart ? opExit : 1,
      background: `linear-gradient(to bottom,
        transparent ${centerPct}%,
        #FFFFFF22 ${centerPct + h * 0.3}%,
        #FFFFFF22 ${100 - centerPct - h * 0.3}%,
        transparent ${100 - centerPct}%)`,
    }} />
  );
};

// ─── TITLE ────────────────────────────────────────────────────────────────────
const TitleBar: React.FC<{ text: string; frame: number; exitStart: number }> = ({ text, frame, exitStart }) => {
  const { fps } = useVideoConfig();
  const prog  = spring({ frame, fps, config: { damping: 28, stiffness: 260 } });
  const yIn   = interpolate(prog, [0, 1], [-22, 0]);
  const opIn  = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });

  // exit: slide up + fade
  const xOut  = interpolate(frame, [exitStart + 6, exitStart + 26], [0, -260], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const opOut = interpolate(frame, [exitStart + 6, exitStart + 22], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const y  = yIn;
  const x  = xOut;
  const op = opIn * (frame >= exitStart + 6 ? opOut : 1);

  const lineW = interpolate(frame, [4, 28], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });

  return (
    <div style={{
      opacity: op, transform: `translate(${x}px, ${y}px)`,
      display: "flex", flexDirection: "column", alignItems: "center", gap: 10,
    }}>
      <div style={{
        fontFamily: SYNE, fontSize: 11, fontWeight: "800",
        letterSpacing: "0.40em", textTransform: "uppercase", color: "#FFFFFF55",
      }}>
        {text}
      </div>
      <div style={{
        width: `${lineW}%`, maxWidth: 140, height: 1,
        background: "linear-gradient(to right, transparent, #FFFFFF33, transparent)",
      }} />
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const DataCard: React.FC<DataCardProps> = ({ title, items, duration_s = 8, bg_color = "#020218", seed = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ITEM_COLORS_S = seededShuffle(ITEM_COLORS, seed);
  const totalFrames = Math.round(duration_s * fps);
  const exitStart = totalFrames - EXIT_DUR;
  const fontSize  = calcFontSize(items);

  const fadeIn  = interpolate(frame, [0, 8], [0, 1], {
    extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });
  const finalFade = interpolate(frame, [totalFrames - 8, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} items={items} total={totalFrames} />
        <Grid frame={frame} items={items} total={totalFrames} />
        <ScanLine frame={frame} />
        <ExitScan frame={frame} total={totalFrames} />

        {/* SFX — entrance */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.22} />
        </Sequence>
        <Sequence from={4} durationInFrames={30}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.14} />
        </Sequence>
        {items.map((_, i) => (
          <Sequence key={i} from={10 + i * 20 + 10} durationInFrames={20}>
            <Audio src={staticFile("sfx/impact.wav")} volume={0.18 - i * 0.02} />
          </Sequence>
        ))}
        {/* SFX — exit */}
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.22} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 48, padding: "0 60px",
        }}>
          <TitleBar text={title} frame={frame} exitStart={exitStart} />

          <div style={{
            display: "flex", alignItems: "center",
            justifyContent: "center", width: "100%",
          }}>
            {items.map((item, i) => (
              <React.Fragment key={i}>
                {i > 0 && <Sep frame={frame} delay={14 + i * 14} exitStart={exitStart} />}
                <Cell
                  item={item} idx={i} total={items.length}
                  frame={frame} totalFrames={totalFrames}
                  fontSize={fontSize} exitStart={exitStart}
                  itemColor={ITEM_COLORS_S[i % ITEM_COLORS_S.length]}
                />
              </React.Fragment>
            ))}
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
