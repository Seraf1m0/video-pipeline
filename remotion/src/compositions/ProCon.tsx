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

export interface ProConProps {
  title?: string;
  pros: string[];
  cons: string[];
  pro_color?: string;
  con_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
  accent_color?: string;
}

const EXIT_DUR  = 44;
const ITEM_STEP = 12;

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; proColor: string; conColor: string; total: number }> = ({
  frame, proColor, conColor, total,
}) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;
  const s1 = 1 + Math.sin(frame * 0.018) * 0.06;
  const nx  = noise2D("pcbx", frame * 0.0014, 0) * 5;
  const ny  = noise2D("pcby", 0, frame * 0.0014) * 4;

  return (
    <AbsoluteFill>
      {/* Left pro glow */}
      <div style={{
        position: "absolute",
        left: `${28 + nx}%`, top: `${52 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 600, height: 600, borderRadius: "50%",
        background: proColor, filter: "blur(130px)",
        opacity: op * 0.09,
      }} />
      {/* Right con glow */}
      <div style={{
        position: "absolute",
        left: `${72 - nx * 0.6}%`, top: `${48 - ny * 0.4}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 600, height: 600, borderRadius: "50%",
        background: conColor, filter: "blur(130px)",
        opacity: op * 0.09,
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
  const op = inOp * outOp * 0.05;
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

// ─── COLUMN HEADER ────────────────────────────────────────────────────────────
const ColumnHeader: React.FC<{
  label: string;
  color: string;
  frame: number;
  fromLeft: boolean;
  enterFrame: number;
  exitStart: number;
}> = ({ label, color, frame, fromLeft, enterFrame, exitStart }) => {
  const { fps } = useVideoConfig();
  const spr  = spring({ frame: frame - enterFrame, fps, config: { damping: 18, stiffness: 260 } });
  const xIn  = interpolate(spr, [0, 1], [fromLeft ? -200 : 200, 0]);
  const opIn = interpolate(frame, [enterFrame, enterFrame + 14], [0, 1], { extrapolateRight: "clamp" });
  const opOut = interpolate(frame, [exitStart + 4, exitStart + 22], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = opIn * (frame >= exitStart + 4 ? opOut : 1);

  return (
    <div style={{
      opacity: op,
      transform: `translateX(${xIn}px)`,
      fontFamily: SYNE,
      fontSize: 20,
      fontWeight: "800",
      letterSpacing: "0.22em",
      textTransform: "uppercase",
      color: color,
      textShadow: `0 0 24px ${color}88`,
      display: "flex",
      alignItems: "center",
      gap: 10,
    }}>
      {!fromLeft && <span style={{ fontSize: 22 }}>✗</span>}
      {label}
      {fromLeft && <span style={{ fontSize: 22 }}>✓</span>}
    </div>
  );
};

// ─── LIST ITEM ────────────────────────────────────────────────────────────────
const ListItem: React.FC<{
  text: string;
  color: string;
  isPro: boolean;
  enterFrame: number;
  frame: number;
  exitStart: number;
  fontSize: number;
}> = ({ text, color, isPro, enterFrame, frame, exitStart, fontSize }) => {
  const { fps } = useVideoConfig();
  const spr  = spring({ frame: frame - enterFrame, fps, config: { damping: 22, stiffness: 260 } });
  const yIn  = interpolate(spr, [0, 1], [24, 0]);
  const opIn = interpolate(frame, [enterFrame, enterFrame + 12], [0, 1], { extrapolateRight: "clamp" });
  const opOut = interpolate(frame, [exitStart + 4, exitStart + 24], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = opIn * (frame >= exitStart + 4 ? opOut : 1);

  return (
    <div style={{
      opacity: op,
      transform: `translateY(${yIn}px)`,
      display: "flex",
      alignItems: "flex-start",
      gap: 10,
    }}>
      <span style={{
        color: color,
        fontSize: fontSize + 2,
        fontWeight: "700",
        flexShrink: 0,
        marginTop: 1,
        textShadow: `0 0 12px ${color}88`,
      }}>
        {isPro ? "✓" : "✗"}
      </span>
      <span style={{
        fontFamily: MANROPE,
        fontSize,
        fontWeight: "500",
        color: "#FFFFFFCC",
        lineHeight: 1.4,
        letterSpacing: "0.01em",
      }}>
        {text}
      </span>
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const ProCon: React.FC<ProConProps> = ({
  title,
  pros,
  cons,
  pro_color    = "#00FF88",
  con_color    = "#FF4444",
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

  const headerEnter = 8;
  const itemsBase   = headerEnter + 20;
  const maxItems    = Math.max(pros.length, cons.length);

  // Items alternate pro/con appearance: pro0, con0, pro1, con1...
  const getEnterFrame = (side: "pro" | "con", index: number) => {
    const pairIndex = index;
    const base      = itemsBase + pairIndex * ITEM_STEP * 2;
    return side === "pro" ? base : base + ITEM_STEP;
  };

  const maxLen = Math.max(
    ...pros.map(p => p.length),
    ...cons.map(c => c.length),
  );
  const fontSize = maxLen > 60 ? 16 : maxLen > 40 ? 18 : maxLen > 25 ? 20 : 22;

  // Title
  const { fps: _fps } = useVideoConfig();
  const titleSpr  = spring({ frame, fps, config: { damping: 28, stiffness: 240 } });
  const titleY    = interpolate(titleSpr, [0, 1], [-16, 0]);
  const titleOp   = interpolate(frame, [0, 14], [0, 1], { extrapolateRight: "clamp" }) *
                    interpolate(frame, [exitStart + 4, exitStart + 20], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // Divider line
  const divOp = interpolate(frame, [headerEnter + 8, headerEnter + 24], [0, 1], { extrapolateRight: "clamp" }) *
                interpolate(frame, [exitStart, exitStart + 18], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} proColor={pro_color} conColor={con_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={headerEnter} durationInFrames={24}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.14} />
        </Sequence>
        {Array.from({ length: maxItems }, (_, i) => (
          <React.Fragment key={i}>
            <Sequence from={getEnterFrame("pro", i) + 4} durationInFrames={18}>
              <Audio src={staticFile("sfx/ping.wav")} volume={0.10} />
            </Sequence>
          </React.Fragment>
        ))}
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          justifyContent: "center",
          gap: 32, padding: "60px 100px",
        }}>
          {/* Title */}
          {title && (
            <div style={{
              opacity: titleOp,
              transform: `translateY(${titleY}px)`,
              fontFamily: SYNE,
              fontSize: 13,
              fontWeight: "800",
              letterSpacing: "0.40em",
              textTransform: "uppercase",
              color: "#FFFFFF55",
              textAlign: "center",
            }}>
              {title}
            </div>
          )}

          {/* Two columns */}
          <div style={{ position: "relative", display: "flex", gap: 0 }}>
            {/* Left column — PRO */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, paddingRight: 48 }}>
              <ColumnHeader
                label="Pro" color={pro_color} frame={frame}
                fromLeft={true} enterFrame={headerEnter} exitStart={exitStart}
              />
              <div style={{ height: 1, background: `linear-gradient(to right, ${pro_color}44, transparent)` }} />
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {pros.map((text, i) => (
                  <ListItem
                    key={i}
                    text={text}
                    color={pro_color}
                    isPro={true}
                    enterFrame={getEnterFrame("pro", i)}
                    frame={frame}
                    exitStart={exitStart}
                    fontSize={fontSize}
                  />
                ))}
              </div>
            </div>

            {/* Vertical divider */}
            <div style={{
              position: "absolute",
              left: "50%", top: 0, bottom: 0,
              width: 1,
              background: `linear-gradient(to bottom, transparent, #FFFFFF22, transparent)`,
              opacity: divOp,
            }} />

            {/* Right column — CON */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, paddingLeft: 48 }}>
              <ColumnHeader
                label="Con" color={con_color} frame={frame}
                fromLeft={false} enterFrame={headerEnter} exitStart={exitStart}
              />
              <div style={{ height: 1, background: `linear-gradient(to left, ${con_color}44, transparent)` }} />
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {cons.map((text, i) => (
                  <ListItem
                    key={i}
                    text={text}
                    color={con_color}
                    isPro={false}
                    enterFrame={getEnterFrame("con", i)}
                    frame={frame}
                    exitStart={exitStart}
                    fontSize={fontSize}
                  />
                ))}
              </div>
            </div>
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
