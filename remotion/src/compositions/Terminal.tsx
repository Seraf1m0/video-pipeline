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

export interface TerminalProps {
  lines: string[];
  title?: string;
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
  const s1 = 1 + Math.sin(frame * 0.018) * 0.06;
  const nx  = noise2D("tmbx", frame * 0.0014, 0) * 4;
  const ny  = noise2D("tmby", 0, frame * 0.0014) * 3;

  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 900, height: 600, borderRadius: "50%",
        background: `radial-gradient(ellipse, ${color} 0%, transparent 65%)`,
        filter: "blur(150px)", opacity: op * 0.08,
      }} />
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 90% 85% at 50% 50%, transparent 20%, #000000E5 100%)",
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
  const op = inOp * outOp * 0.04;
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
          background: "linear-gradient(to right, transparent, #FFFFFF08, transparent)",
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

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const Terminal: React.FC<TerminalProps> = ({
  lines,
  title,
  accent_color = "#00FF88",
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

  // Panel enters with a spring slide from below
  const panelEnter = 8;
  const panelSpr   = spring({ frame: frame - panelEnter, fps, config: { damping: 22, stiffness: 220 } });
  const panelY     = interpolate(panelSpr, [0, 1], [60, 0]);
  const panelOp    = interpolate(frame, [panelEnter, panelEnter + 14], [0, 1], { extrapolateRight: "clamp" });
  const panelExitOp = interpolate(frame, [exitStart + 4, exitStart + 28], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const panelExitY  = interpolate(frame, [exitStart + 4, exitStart + 28], [0, 40], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // Determine typing schedule
  // Available frames for typing: panelEnter + 14 to exitStart - 6
  const typeStart = panelEnter + 20;
  const typeEnd   = exitStart - 8;
  const typeDur   = typeEnd - typeStart;

  // Each line gets equal time
  const framesPerLine = Math.floor(typeDur / Math.max(lines.length, 1));

  // For each line, compute how many chars are visible
  const lineData = lines.map((line, i) => {
    const lineStart = typeStart + i * framesPerLine;
    const lineEnd   = lineStart + Math.floor(framesPerLine * 0.85);
    const charsVisible = Math.floor(
      interpolate(frame, [lineStart, lineEnd], [0, line.length], {
        extrapolateLeft: "clamp", extrapolateRight: "clamp",
      })
    );
    const lineOp = interpolate(frame, [lineStart - 2, lineStart + 6], [0, 1], { extrapolateRight: "clamp" });
    return { line, charsVisible, lineOp, lineStart };
  });

  // Blinking cursor: shows after last typed char of the last active line
  const lastActiveLine = lineData.reduce((acc, d, i) => frame >= d.lineStart ? i : acc, -1);
  const cursorVisible  = lastActiveLine >= 0
    && lineData[lastActiveLine].charsVisible < lineData[lastActiveLine].line.length
    && frame < exitStart;
  const cursorBlink    = Math.floor(frame / 6) % 2 === 0;

  // Typing SFX: keyboard sound while any line is being typed
  const anyTyping = lineData.some(d => d.charsVisible > 0 && d.charsVisible < d.line.length);

  // Font size based on line count and max line length
  const maxLineLen = Math.max(...lines.map(l => l.length + 4)); // +4 for prompt
  const fontSize   = maxLineLen > 60 ? 18 : maxLineLen > 45 ? 20 : maxLineLen > 30 ? 22 : 24;

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.14} />
        </Sequence>
        {anyTyping && (
          <Sequence from={typeStart} durationInFrames={typeDur}>
            <Audio src={staticFile("sfx/keyboard.wav")} volume={0.22} />
          </Sequence>
        )}
        <Sequence from={exitStart + 6} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.16} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          padding: "0 120px",
        }}>
          {/* Terminal window */}
          <div style={{
            opacity: panelOp * (frame >= exitStart + 4 ? panelExitOp : 1),
            transform: `translateY(${panelY + panelExitY}px)`,
            width: "100%",
            maxWidth: 1100,
            background: "rgba(0, 0, 0, 0.85)",
            border: `1px solid ${accent_color}33`,
            borderRadius: 12,
            overflow: "hidden",
            boxShadow: `0 0 60px ${accent_color}18, 0 0 120px ${accent_color}08, inset 0 1px 0 ${accent_color}22`,
          }}>
            {/* Title bar */}
            <div style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "12px 18px",
              background: "rgba(255,255,255,0.04)",
              borderBottom: `1px solid ${accent_color}22`,
            }}>
              {/* Traffic light dots */}
              {["#FF5F56", "#FFBD2E", "#27C93F"].map((dot, i) => (
                <div key={i} style={{
                  width: 12, height: 12, borderRadius: "50%",
                  background: dot,
                  boxShadow: `0 0 6px ${dot}88`,
                }} />
              ))}
              {title && (
                <div style={{
                  flex: 1,
                  textAlign: "center",
                  fontFamily: "monospace",
                  fontSize: 13,
                  color: "#FFFFFF55",
                  letterSpacing: "0.06em",
                  marginRight: 44,
                }}>
                  {title}
                </div>
              )}
            </div>

            {/* Terminal body */}
            <div style={{
              padding: "24px 28px",
              display: "flex",
              flexDirection: "column",
              gap: 6,
              minHeight: 220,
            }}>
              {lineData.map((d, i) => {
                if (frame < d.lineStart - 2) return null;
                const isLastActive = i === lastActiveLine;
                const showCursor   = isLastActive && cursorVisible && cursorBlink;

                return (
                  <div key={i} style={{
                    opacity: d.lineOp,
                    fontFamily: "monospace",
                    fontSize,
                    lineHeight: 1.6,
                    display: "flex",
                    alignItems: "center",
                  }}>
                    {/* Prompt */}
                    <span style={{
                      color: accent_color,
                      marginRight: 8,
                      textShadow: `0 0 12px ${accent_color}88`,
                      flexShrink: 0,
                    }}>
                      {">"}
                    </span>
                    {/* Typed content */}
                    <span style={{ color: "#FFFFFF", letterSpacing: "0.02em" }}>
                      {d.line.slice(0, d.charsVisible)}
                    </span>
                    {/* Cursor */}
                    {showCursor && (
                      <span style={{
                        color: accent_color,
                        marginLeft: 2,
                        textShadow: `0 0 10px ${accent_color}`,
                      }}>▌</span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
