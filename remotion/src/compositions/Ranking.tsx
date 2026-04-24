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

export interface RankingItem {
  rank?: number;
  label: string;
  value?: string;
  color?: string;
}

export interface RankingProps {
  title: string;
  items: RankingItem[];
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const EXIT_DUR  = 44;
const ROW_STEP  = 10;

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;
  const s1 = 1 + Math.sin(frame * 0.020) * 0.07;
  const nx  = noise2D("rkbx", frame * 0.0015, 0) * 5;
  const ny  = noise2D("rkby", 0, frame * 0.0015) * 4;

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

// ─── RANKING ROW ─────────────────────────────────────────────────────────────
const RankRow: React.FC<{
  item: RankingItem;
  displayRank: number;
  enterFrame: number;
  frame: number;
  exitStart: number;
  accentColor: string;
  isFirst: boolean;
  fontSize: number;
}> = ({ item, displayRank, enterFrame, frame, exitStart, accentColor, isFirst, fontSize }) => {
  const { fps } = useVideoConfig();
  const color = item.color ?? accentColor;
  const rowColor = isFirst ? "#FFD700" : color;

  // Slide in from right
  const spr  = spring({ frame: frame - enterFrame, fps, config: { damping: 20, stiffness: 240 } });
  const xIn  = interpolate(spr, [0, 1], [200, 0]);
  const opIn = interpolate(frame, [enterFrame, enterFrame + 14], [0, 1], { extrapolateRight: "clamp" });

  // Exit: slides right simultaneously
  const opOut = interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const xOut  = interpolate(frame, [exitStart + 4, exitStart + 22], [0, 300], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  const op = opIn * (frame >= exitStart + 4 ? opOut : 1);
  const x  = xIn + xOut;

  // Panel highlight on entry — fades in
  const panelOp = interpolate(frame, [enterFrame, enterFrame + 16], [0, isFirst ? 0.12 : 0.06], {
    extrapolateRight: "clamp",
  });

  // Rank number size
  const rankSize = isFirst ? fontSize * 1.5 : fontSize;
  const glow     = isFirst ? 0.6 + Math.sin(frame * 0.07) * 0.4 : 0.3;

  return (
    <div style={{
      opacity: op,
      transform: `translateX(${x}px)`,
      display: "flex",
      alignItems: "center",
      gap: 24,
      padding: "14px 20px",
      borderRadius: 10,
      background: `rgba(255,255,255,${panelOp})`,
      border: isFirst ? `1px solid ${rowColor}33` : `1px solid rgba(255,255,255,0.04)`,
      boxShadow: isFirst ? `0 0 40px ${rowColor}18` : "none",
    }}>
      {/* Rank number */}
      <div style={{
        fontFamily: SYNE,
        fontSize: rankSize,
        fontWeight: "800",
        letterSpacing: isFirst ? "-0.05em" : "-0.03em",
        color: rowColor,
        textShadow: `0 0 ${40 * glow}px ${rowColor}AA`,
        minWidth: isFirst ? 80 : 60,
        textAlign: "center",
        flexShrink: 0,
        lineHeight: 1,
      }}>
        {item.rank ?? displayRank}
      </div>

      {/* Label */}
      <div style={{
        fontFamily: SYNE,
        fontSize: isFirst ? fontSize + 2 : fontSize,
        fontWeight: isFirst ? "800" : "700",
        color: isFirst ? "#FFFFFF" : "#FFFFFFCC",
        letterSpacing: "-0.01em",
        flex: 1,
        lineHeight: 1.2,
      }}>
        {item.label}
      </div>

      {/* Value (right-aligned) */}
      {item.value && (
        <div style={{
          fontFamily: MONTSERRAT,
          fontSize: isFirst ? fontSize - 2 : fontSize - 4,
          fontWeight: "600",
          color: `${rowColor}CC`,
          letterSpacing: "0.02em",
          textAlign: "right",
          flexShrink: 0,
        }}>
          {item.value}
        </div>
      )}
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const Ranking: React.FC<RankingProps> = ({
  title,
  items,
  accent_color = "#00C8FF",
  duration_s   = 12,
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

  const titleEnter = 8;
  const rowBase    = titleEnter + 22;

  // Title
  const titleSpr = spring({ frame: frame - titleEnter, fps, config: { damping: 28, stiffness: 240 } });
  const titleY   = interpolate(titleSpr, [0, 1], [-18, 0]);
  const titleOp  = interpolate(frame, [titleEnter, titleEnter + 14], [0, 1], { extrapolateRight: "clamp" }) *
                   interpolate(frame, [exitStart + 4, exitStart + 20], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const titleLineW = interpolate(frame, [titleEnter + 6, titleEnter + 32], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });

  // Font size based on item count and max label length
  const maxLabelLen = Math.max(...items.map(i => i.label.length));
  const fontSize    = items.length > 7 ? (maxLabelLen > 30 ? 18 : 20) :
                      items.length > 5 ? (maxLabelLen > 30 ? 20 : 24) :
                                          (maxLabelLen > 30 ? 22 : 26);

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
          <Sequence key={i} from={rowBase + i * ROW_STEP + 4} durationInFrames={20}>
            <Audio src={staticFile("sfx/ping.wav")} volume={i === 0 ? 0.18 : 0.10 - i * 0.004} />
          </Sequence>
        ))}
        {items.length > 0 && (
          <Sequence from={rowBase + 6} durationInFrames={28}>
            <Audio src={staticFile("sfx/impact.wav")} volume={0.16} />
          </Sequence>
        )}
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          justifyContent: "center",
          gap: 28, padding: "60px 100px",
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

          {/* Rows */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {items.map((item, i) => (
              <RankRow
                key={i}
                item={item}
                displayRank={i + 1}
                enterFrame={rowBase + i * ROW_STEP}
                frame={frame}
                exitStart={exitStart}
                accentColor={accent_color}
                isFirst={i === 0}
                fontSize={fontSize}
              />
            ))}
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
