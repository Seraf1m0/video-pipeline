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

export type StatementStyle = "default" | "glitch" | "char_reveal" | "typewriter";

export interface StatementProps {
  text: string;
  highlight?: string;
  sub?: string;
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
  style?: StatementStyle;
}

const EXIT_DUR = 44;

// ─── BACKGROUND ───────────────────────────────────────────────────────────────
const Bg: React.FC<{ frame: number; color: string; total: number }> = ({ frame, color, total }) => {
  const inOp  = interpolate(frame, [0, 30], [0, 1], { extrapolateRight: "clamp" });
  const outOp = interpolate(frame, [total - EXIT_DUR, total - EXIT_DUR + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const op = inOp * outOp;

  const s1 = 1 + Math.sin(frame * 0.022) * 0.08;
  const s2 = 1 + Math.sin(frame * 0.018 + 1.4) * 0.06;
  const nx  = noise2D("bx", frame * 0.0018, 0) * 4;
  const ny  = noise2D("by", 0, frame * 0.0018) * 3;

  return (
    <AbsoluteFill>
      {/* Outer atmospheric halo */}
      <div style={{
        position: "absolute",
        left: `${50 + nx}%`, top: `${50 + ny}%`,
        transform: `translate(-50%,-50%) scale(${s2})`,
        width: 1000, height: 700, borderRadius: "50%",
        background: `radial-gradient(ellipse, ${color} 0%, transparent 65%)`,
        filter: "blur(110px)",
        opacity: op * 0.11,
      }} />
      {/* Core glow */}
      <div style={{
        position: "absolute",
        left: `${50 - nx * 0.5}%`, top: `${50 - ny * 0.5}%`,
        transform: `translate(-50%,-50%) scale(${s1})`,
        width: 520, height: 520, borderRadius: "50%",
        background: color, filter: "blur(90px)",
        opacity: op * 0.14,
      }} />
      {/* Vignette */}
      <div style={{
        position: "absolute", inset: 0,
        background: "radial-gradient(ellipse 90% 85% at 50% 50%, transparent 25%, #000000DD 100%)",
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
  const op = inOp * outOp * 0.07;
  const cols = 8; const rows = 5;
  return (
    <AbsoluteFill style={{ opacity: op }}>
      {Array.from({ length: cols + 1 }, (_, i) => (
        <div key={`v${i}`} style={{
          position: "absolute", left: `${(i / cols) * 100}%`, top: 0, bottom: 0, width: 1,
          background: `linear-gradient(to bottom, transparent, ${color}55, transparent)`,
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
      boxShadow: `0 0 22px ${color}88`,
      opacity: op,
    }} />
  );
};

// ─── EXIT SCAN LINE (bottom → top) ────────────────────────────────────────────
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
      boxShadow: `0 0 16px ${color}55`,
      opacity: op,
    }} />
  );
};

// ─── IMPACT FLASH ─────────────────────────────────────────────────────────────
const ImpactFlash: React.FC<{ frame: number; color: string }> = ({ frame, color }) => {
  const opacity = interpolate(frame, [30, 32, 36, 44], [0, 0.16, 0.06, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ background: color, opacity, pointerEvents: "none" }} />
  );
};

// ─── TEXT BLOCK ───────────────────────────────────────────────────────────────
const TextBlock: React.FC<{
  text: string; highlight?: string; accentColor: string;
  frame: number; exitStart: number; glowMult?: number;
}> = ({ text, highlight, accentColor, frame, exitStart, glowMult = 1 }) => {
  const { fps } = useVideoConfig();

  // entrance: block crashes up from below
  const blockSpring = spring({ frame: frame - 14, fps, config: { damping: 10, stiffness: 320, mass: 0.9 } });
  const blockYIn    = interpolate(blockSpring, [0, 1], [120, 0]);
  const blockOpIn   = interpolate(frame, [14, 22], [0, 1], { extrapolateRight: "clamp" });

  // exit: block slides left
  const blockExitP  = interpolate(frame, [exitStart, exitStart + 30], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic),
  });
  const blockXOut   = interpolate(blockExitP, [0, 1], [0, -400]);
  const blockOpOut  = interpolate(frame, [exitStart + 6, exitStart + 28], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const blockY  = blockYIn;
  const blockX  = blockXOut;
  const blockOp = blockOpIn * (frame >= exitStart ? blockOpOut : 1);

  // highlight word entrance
  const hlSpring  = spring({ frame: frame - 30, fps, config: { damping: 6, stiffness: 480, mass: 0.6 } });
  const hlScaleIn = interpolate(hlSpring, [0, 1], [1.8, 1]);
  const hlOpIn    = interpolate(frame, [30, 36], [0, 1], { extrapolateRight: "clamp" });

  const glowPulse = (0.5 + Math.sin(frame * 0.07) * 0.5) * glowMult;

  const hlLower = highlight?.toLowerCase().trim() ?? "";
  const words   = text.split(/\s+/).filter(Boolean);
  const hlIdx   = hlLower
    ? words.findIndex(w => w.toLowerCase().replace(/[.,!?:;«»"'()[\]-]/g, "").includes(hlLower))
    : -1;

  const fontSize = text.length > 80 ? 58 : text.length > 55 ? 70 : text.length > 35 ? 84 : 100;

  return (
    <div style={{
      transform: `translate(${blockX}px, ${blockY}px)`,
      opacity: blockOp,
      textAlign: "center",
      display: "flex", flexWrap: "wrap",
      justifyContent: "center",
      rowGap: "0.18em", lineHeight: 1.15,
    }}>
      {words.map((word, i) => {
        const isHl = hlIdx !== -1 && i === hlIdx;
        const isLast = i === words.length - 1;
        if (isHl) {
          return (
            <span key={i} style={{
              display: "inline-block",
              fontFamily: SYNE, fontSize, fontWeight: "800",
              color: accentColor,
              opacity: hlOpIn,
              transform: `scale(${hlScaleIn})`,
              textShadow: `
                0 0 ${55 * glowPulse}px ${accentColor}BB,
                0 0 ${110 * glowPulse}px ${accentColor}44
              `,
              letterSpacing: "0em",
              marginRight: isLast ? 0 : "0.26em",
            }}>{word}</span>
          );
        }
        return (
          <span key={i} style={{
            display: "inline-block",
            fontFamily: MANROPE, fontSize, fontWeight: "600",
            color: "#FFFFFF", letterSpacing: "0em",
            marginRight: isLast ? 0 : "0.26em",
          }}>{word}</span>
        );
      })}
    </div>
  );
};

// ─── GLITCH TEXT BLOCK ────────────────────────────────────────────────────────
const TextBlockGlitch: React.FC<{
  text: string; accentColor: string; frame: number; exitStart: number;
}> = ({ text, accentColor, frame, exitStart }) => {
  const entryNoise = noise2D("ge", frame * 0.8, 0);
  const glitchOn   = frame < 20 ? (entryNoise > 0.2 ? 1 : 0) : 0;
  const pulse      = glitchOn + Math.sin(frame * 0.4) * 0.3;
  const rgbX = pulse * 6;
  const rgbY = Math.sin(frame * 0.7) * pulse * 3;

  const flickerNoise = noise2D("gf", frame * 1.2, 0);
  const flickerOp    = frame < 25 ? (flickerNoise > 0 ? 0.14 : 0) : 0;
  const flickerY     = (frame * 37) % 100;

  const exitOp = interpolate(frame, [exitStart + 6, exitStart + 30], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const fontSize = text.length > 55 ? 68 : text.length > 35 ? 84 : 100;

  return (
    <>
      {/* Scanline flicker on entry */}
      <div style={{
        position: "absolute", top: `${flickerY}%`, left: 0, right: 0,
        height: 3, background: accentColor, opacity: flickerOp, pointerEvents: "none",
      }} />
      <div style={{ opacity: exitOp, position: "relative", textAlign: "center", width: "100%" }}>
        <div style={{ position: "absolute", inset: 0, fontFamily: SYNE, fontSize, fontWeight: "800",
          color: "cyan", mixBlendMode: "screen", fontFeatureSettings: '"ss01" 0',
          transform: `translate(${rgbX}px,${rgbY}px)`, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {text}
        </div>
        <div style={{ position: "absolute", inset: 0, fontFamily: SYNE, fontSize, fontWeight: "800",
          color: "magenta", mixBlendMode: "screen", fontFeatureSettings: '"ss01" 0',
          transform: `translate(${-rgbX}px,${-rgbY}px)`, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {text}
        </div>
        <div style={{ fontFamily: SYNE, fontSize, fontWeight: "800",
          color: "#FFFFFF", opacity: 0.92, whiteSpace: "pre-wrap", wordBreak: "break-word",
          fontFeatureSettings: '"ss01" 0' }}>
          {text}
        </div>
      </div>
    </>
  );
};

// ─── CHAR REVEAL TEXT BLOCK ───────────────────────────────────────────────────
const TextBlockCharReveal: React.FC<{
  text: string; accentColor: string; frame: number; exitStart: number;
}> = ({ text, accentColor, frame, exitStart }) => {
  const { fps } = useVideoConfig();
  const STAGGER  = 3;
  const fontSize = text.length > 55 ? 68 : text.length > 35 ? 84 : 100;

  const exitOp = interpolate(frame, [exitStart + 6, exitStart + 30], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  const chars = text.split("");
  return (
    <div style={{
      opacity: exitOp, textAlign: "center", padding: "0 140px",
      display: "flex", flexWrap: "wrap", justifyContent: "center",
    }}>
      {chars.map((char, i) => {
        const delay = i * STAGGER;
        const op  = spring({ frame: frame - delay, fps, from: 0, to: 1, config: { damping: 10, stiffness: 200 } });
        const y   = spring({ frame: frame - delay, fps, from: -55, to: 0, config: { damping: 12, stiffness: 280 } });
        const rot = spring({ frame: frame - delay, fps, from: -20, to: 0, config: { damping: 14, stiffness: 320 } });
        const isWordStart = i === 0 || chars[i - 1] === " ";
        return (
          <span key={i} style={{
            display: "inline-block",
            fontFamily: isWordStart ? SYNE : MANROPE,
            fontSize, fontWeight: "800",
            color: isWordStart ? accentColor : "#FFFFFF",
            opacity: op,
            transform: `translateY(${y}px) rotate(${rot}deg)`,
            marginRight: char === " " ? "0.3em" : "0.02em",
          }}>
            {char === " " ? "\u00A0" : char}
          </span>
        );
      })}
    </div>
  );
};

// ─── TYPEWRITER TEXT BLOCK ────────────────────────────────────────────────────
const TextBlockTypewriter: React.FC<{
  text: string; sub?: string; accentColor: string;
  frame: number; exitStart: number; totalFrames: number;
}> = ({ text, sub, accentColor, frame, exitStart, totalFrames }) => {
  const typeEnd     = Math.round(totalFrames * 0.52);
  const visibleChars = Math.floor(
    interpolate(frame, [8, typeEnd], [0, text.length], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp",
    })
  );

  const cursorOp  = frame < exitStart && visibleChars < text.length ? (frame % 10 < 5 ? 1 : 0) : 0;
  const exitOp    = interpolate(frame, [exitStart, exitStart + 20], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const subOp = interpolate(frame, [typeEnd, typeEnd + 18], [0, 1], {
    extrapolateRight: "clamp", extrapolateLeft: "clamp",
  }) * exitOp;
  const lineW = interpolate(frame, [typeEnd, typeEnd + 28], [0, 240], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });

  const fontSize = text.length > 55 ? 64 : text.length > 35 ? 80 : 96;

  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center",
      gap: 28, padding: "0 140px", opacity: exitOp,
    }}>
      <div style={{ textAlign: "center" }}>
        <span style={{ fontFamily: MANROPE, fontSize, fontWeight: "800", color: "#FFFFFF" }}>
          {text.slice(0, visibleChars)}
        </span>
        <span style={{
          fontFamily: MANROPE, fontSize, fontWeight: "300",
          color: accentColor, opacity: cursorOp, marginLeft: 4,
        }}>▌</span>
      </div>

      <div style={{
        width: lineW, height: 2,
        background: `linear-gradient(to right, transparent, ${accentColor}88, transparent)`,
      }} />

      {sub && (
        <div style={{
          fontFamily: MANROPE, fontSize: 22, fontWeight: "600",
          letterSpacing: "0.14em", textTransform: "uppercase",
          color: `${accentColor}AA`, opacity: subOp, textAlign: "center",
        }}>{sub}</div>
      )}
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const Statement: React.FC<StatementProps> = ({
  text, highlight, sub,
  accent_color = "#0071E3",
  duration_s   = 7,
  bg_color     = "#020218",
  seed         = 0,
  style        = "default",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_DUR;

  const rand = seededRand(seed);
  const glowMult = 0.8 + rand() * 0.4;

  const fadeIn    = interpolate(frame, [0, 10], [0, 1], {
    extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });
  const finalFade = interpolate(frame, [totalFrames - 6, totalFrames], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  // sub — appears after highlight, exits before block
  const subOpIn  = interpolate(frame, [42, 54], [0, 1], { extrapolateRight: "clamp" });
  const subOpOut = interpolate(frame, [exitStart, exitStart + 16], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const subOp = subOpIn * (frame >= exitStart ? subOpOut : 1);
  const subY  = interpolate(frame, [42, 54], [12, 0], {
    extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
  });

  // bottom rule
  const ruleWIn  = interpolate(frame, [50, 72], [0, 100], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.exp),
  });
  const ruleOpOut = interpolate(frame, [exitStart, exitStart + 14], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill style={{ background: bg_color, overflow: "hidden" }}>
      <div style={{ opacity: fadeIn * finalFade, width: "100%", height: "100%" }}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <Grid frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />
        <ImpactFlash frame={frame} color={accent_color} />

        {/* SFX — entrance */}
        <Sequence from={0} durationInFrames={16}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        <Sequence from={6} durationInFrames={22}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.20} />
        </Sequence>
        {highlight && (
          <Sequence from={30} durationInFrames={26}>
            <Audio src={staticFile("sfx/stinger.wav")} volume={0.22} />
          </Sequence>
        )}
        {/* SFX — exit */}
        <Sequence from={exitStart + 8} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>

        <AbsoluteFill style={{
          display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center",
          gap: 44, padding: "0 140px",
        }}>
          {style === "default" && (
            <>
              {sub && (
                <div style={{
                  fontFamily: SYNE, fontSize: 12, fontWeight: "700",
                  letterSpacing: "0.35em", textTransform: "uppercase",
                  color: `${accent_color}88`,
                  opacity: subOp, transform: `translateY(${subY}px)`,
                  textAlign: "center",
                }}>
                  {sub}
                </div>
              )}
              <TextBlock
                text={text} highlight={highlight}
                accentColor={accent_color}
                frame={frame} exitStart={exitStart}
                glowMult={glowMult}
              />
              <div style={{
                width: `${ruleWIn}%`, maxWidth: 240, height: 1.5,
                background: `linear-gradient(to right, transparent, ${accent_color}66, transparent)`,
                borderRadius: 1,
                opacity: frame >= exitStart ? ruleOpOut : 1,
              }} />
            </>
          )}

          {style === "glitch" && (
            <TextBlockGlitch
              text={text} accentColor={accent_color}
              frame={frame} exitStart={exitStart}
            />
          )}

          {style === "char_reveal" && (
            <TextBlockCharReveal
              text={text} accentColor={accent_color}
              frame={frame} exitStart={exitStart}
            />
          )}

          {style === "typewriter" && (
            <TextBlockTypewriter
              text={text} sub={sub} accentColor={accent_color}
              frame={frame} exitStart={exitStart} totalFrames={totalFrames}
            />
          )}
        </AbsoluteFill>
      </div>
    </AbsoluteFill>
  );
};
