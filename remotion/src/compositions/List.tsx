import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne    } from "@remotion/google-fonts/Syne";
import { loadFont as loadMontserrat } from "@remotion/google-fonts/Montserrat";
import { noise2D } from "@remotion/noise";
import { seededShuffle } from "../utils/seeded";

const { fontFamily: SYNE    } = loadSyne();
const { fontFamily: MONTSERRAT } = loadMontserrat();

export interface ListItem {
  text: string;
  sub?: string;
}
export interface ListProps {
  title: string;
  items: ListItem[];
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const EXIT_DUR  = 52;
const ITEM_STEP = 22; // frames between each item entrance

const ACCENT_DEFAULT = "#00C8FF";

// Palette for item dots — cycles
const DOT_COLORS = ["#00C8FF", "#FF3CAC", "#4DFFB4", "#FFD700", "#A855F7"];

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [0, 32], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 22], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;

  const s1  = 1 + Math.sin(frame * 0.020) * 0.07;
  const s2  = 1 + Math.sin(frame * 0.016 + 1.8) * 0.05;
  const nx  = noise2D("bx", frame * 0.0016, 0) * 5;
  const ny  = noise2D("by", 0, frame * 0.0016) * 4;

  return (
    <AbsoluteFill>
      {/* Left-side ambient orb */}
      <div style={{
        position: "absolute",
        left: `${28 + nx}%`, top: `${52 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 680, height: 680, borderRadius: "50%",
        background: `radial-gradient(circle, ${color} 0%, transparent 68%)`,
        filter: "blur(100px)",
        opacity: op * 0.13,
      }} />
      {/* Right subtle counter-orb */}
      <div style={{
        position: "absolute",
        left: `${78 - nx * 0.4}%`, top: `${48 - ny * 0.5}%`,
        transform: `translate(-50%,-50%) scale(${s2})`,
        width: 400, height: 400, borderRadius: "50%",
        background: color, filter: "blur(110px)",
        opacity: op * 0.07,
      }} />
      {/* Vignette */}
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 90% 85% at 50% 50%, transparent 20%, #000000D0 100%)",
      }} />
    </AbsoluteFill>
  );
};

// ─── GRID ─────────────────────────────────────────────────────────────────────
const Grid: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [6, 26], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp * 0.07;
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
          background: "linear-gradient(to right, transparent, #FFFFFF12, transparent)",
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
  const op = interpolate(frame, [0, 3, 16, 24], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0, height: 2,
      background: `linear-gradient(to right, transparent, ${color}AA, transparent)`,
      boxShadow: `0 0 20px ${color}66`,
      opacity: op,
    }} />
  );
};

// ─── EXIT SCAN ────────────────────────────────────────────────────────────────
const ExitScan: React.FC<{ frame: number; total: number; color: string }> = ({ frame, total, color }) => {
  const start = total - EXIT_DUR + 12;
  const y  = interpolate(frame, [start, start + 18], [110, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const op = interpolate(frame, [start, start + 3, start + 14, start + 22], [0, 1, 1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <div style={{
      position: "absolute", top: `${y}%`, left: 0, right: 0, height: 2,
      background: `linear-gradient(to right, transparent, ${color}77, transparent)`,
      boxShadow: `0 0 16px ${color}44`,
      opacity: op,
    }} />
  );
};

// ─── TITLE ────────────────────────────────────────────────────────────────────
const Title: React.FC<{ text: string; frame: number; color: string; exitStart: number }> = ({
  text, frame, color, exitStart,
}) => {
  const { fps } = useVideoConfig();
  const spr  = spring({ frame, fps, config: { damping: 28, stiffness: 240 } });
  const yIn  = interpolate(spr, [0, 1], [-20, 0]);
  const opIn = interpolate(frame, [0, 10], [0, 1], { extrapolateRight: "clamp" });

  const opOut = interpolate(frame, [exitStart + 4, exitStart + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const yOut  = interpolate(frame, [exitStart + 4, exitStart + 20], [0, -16], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });

  const op = opIn * (frame >= exitStart + 4 ? opOut : 1);
  const y  = yIn + yOut;

  const lineW = interpolate(frame, [4, 28], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });

  return (
    <div style={{ opacity: op, transform: `translateY(${y}px)`,
      display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 10 }}>
      <div style={{
        fontFamily: SYNE, fontSize: 11, fontWeight: "800",
        letterSpacing: "0.44em", textTransform: "uppercase", color: "#FFFFFF55",
      }}>
        {text}
      </div>
      <div style={{
        width: `${lineW}%`, maxWidth: 200, height: 1,
        background: `linear-gradient(to right, ${color}88, transparent)`,
      }} />
    </div>
  );
};

// ─── LIST ITEM ────────────────────────────────────────────────────────────────
const Item: React.FC<{
  item: ListItem;
  index: number;
  enterFrame: number;
  frame: number;
  exitStart: number;
  dotColor: string;
  fontSize: number;
}> = ({ item, index, enterFrame, frame, exitStart, dotColor, fontSize }) => {
  const { fps } = useVideoConfig();

  // entrance: slides in from left
  const slideP = interpolate(frame, [enterFrame, enterFrame + 32], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.out(Easing.exp),
  });
  const xIn  = interpolate(slideP, [0, 1], [-80, 0]);
  const opIn = interpolate(frame, [enterFrame, enterFrame + 16], [0, 1], { extrapolateRight: "clamp" });

  // exit: slides out to right, staggered (last item exits first)
  const exitDelay  = (3 - Math.min(index, 3)) * 6; // reverse order
  const exitFrame  = exitStart + exitDelay;
  const exitP      = interpolate(frame, [exitFrame, exitFrame + 26], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.in(Easing.cubic),
  });
  const xOut  = interpolate(exitP, [0, 1], [0, -300]);
  const opOut = interpolate(frame, [exitFrame + 8, exitFrame + 26], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const x  = xIn + xOut;
  const op = opIn * (frame >= exitFrame ? opOut : 1);

  // dot pop
  const dotSpr   = spring({ frame: frame - enterFrame, fps, config: { damping: 8, stiffness: 500, mass: 0.4 } });
  const dotScale = interpolate(dotSpr, [0, 1], [0, 1]);
  const dotGlow  = 0.5 + Math.sin(Math.max(0, frame - enterFrame - 10) * 0.06 + index) * 0.5;

  // sub text
  const subOp = interpolate(frame, [enterFrame + 20, enterFrame + 34], [0, 1], { extrapolateRight: "clamp" });

  return (
    <div style={{
      opacity: op,
      transform: `translateX(${x}px)`,
      display: "flex", alignItems: "flex-start", gap: 20,
    }}>
      {/* Dot */}
      <div style={{
        flexShrink: 0,
        width: 10, height: 10, borderRadius: "50%",
        background: dotColor,
        boxShadow: `0 0 ${12 * dotGlow}px ${dotColor}CC, 0 0 ${24 * dotGlow}px ${dotColor}55`,
        transform: `scale(${dotScale})`,
        marginTop: 8,
      }} />

      {/* Text */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{
          fontFamily: SYNE, fontSize, fontWeight: "700",
          color: "#FFFFFF", letterSpacing: "-0.01em", lineHeight: 1.2,
        }}>
          {item.text}
        </div>
        {item.sub && (
          <div style={{
            opacity: subOp,
            fontFamily: MONTSERRAT, fontSize: 14, fontWeight: "600",
            color: "#FFFFFF55", letterSpacing: "0.02em", lineHeight: 1.4,
          }}>
            {item.sub}
          </div>
        )}
      </div>
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const List: React.FC<ListProps> = ({
  title, items,
  accent_color = ACCENT_DEFAULT,
  duration_s   = 10,
  bg_color     = "#020218",
  seed         = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const DOT_COLORS_S = seededShuffle(DOT_COLORS, seed);
  const totalFrames = Math.round(duration_s * fps);

  const maxTextLen = Math.max(...items.map(it => it.text.length));
  const n = items.length;
  const itemFontSize =
    n >= 5 ? (maxTextLen > 35 ? 18 : maxTextLen > 20 ? 20 : 22) :
    n >= 4 ? (maxTextLen > 35 ? 20 : maxTextLen > 20 ? 22 : 24) :
             (maxTextLen > 35 ? 22 : maxTextLen > 20 ? 24 : 26);
  const exitStart   = totalFrames - EXIT_DUR;

  const fadeIn    = interpolate(frame, [0, 12], [0, 1], {
    extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });
  const finalFade = interpolate(frame, [totalFrames - 7, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // item enter frames — after title settles
  const itemEnterBase = 18;

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* SFX — entrance */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={4} durationInFrames={28}>
          <Audio src={staticFile("sfx/ping.wav")} volume={0.12} />
        </Sequence>
        {items.map((_, i) => (
          // +4 frames: dot pops in at enterFrame, ping peaks ~4 frames later
          <Sequence key={i} from={itemEnterBase + i * ITEM_STEP + 4} durationInFrames={22}>
            <Audio src={staticFile("sfx/ping.wav")} volume={0.10 - i * 0.006} />
          </Sequence>
        ))}
        {/* SFX — exit */}
        <Sequence from={exitStart + 10} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          justifyContent: "center",
          gap: 52, padding: "0 100px 0 120px",
        }}>
          <Title text={title} frame={frame} color={accent_color} exitStart={exitStart} />

          <div style={{ display: "flex", flexDirection: "column", gap: 32 }}>
            {items.map((item, i) => (
              <Item
                key={i}
                item={item}
                index={i}
                enterFrame={itemEnterBase + i * ITEM_STEP}
                frame={frame}
                exitStart={exitStart}
                dotColor={DOT_COLORS_S[i % DOT_COLORS_S.length]}
                fontSize={itemFontSize}
              />
            ))}
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
