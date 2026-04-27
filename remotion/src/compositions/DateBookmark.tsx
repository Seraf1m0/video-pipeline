import React from "react";
import {
  AbsoluteFill, interpolate, interpolateColors, spring,
  useCurrentFrame, useVideoConfig, Easing,
} from "remotion";
import { loadFont as loadOrbitron } from "@remotion/google-fonts/Orbitron";
import { loadFont as loadSyne     } from "@remotion/google-fonts/Syne";

const { fontFamily: ORBITRON } = loadOrbitron();
const { fontFamily: SYNE     } = loadSyne();

export interface DateBookmarkProps {
  date: string;
  label?: string;
  color_start?: string;
  color_end?: string;
  duration_s?: number;
}

const NOTCH = 30;   // notch depth px
const ENTER = 4;
const EXIT_HOLD = 28;

export const DateBookmark: React.FC<DateBookmarkProps> = ({
  date,
  label,
  color_start = "#00C8FF",
  color_end   = "#A855F7",
  duration_s  = 6,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_HOLD;

  // ── Color transition over full hold ──
  const colorProgress = interpolate(frame, [ENTER, exitStart], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const accent = interpolateColors(colorProgress, [0, 1], [color_start, color_end]);

  // ── Entry: spring from left ──
  const enterSpr = spring({
    frame: frame - ENTER,
    fps,
    config: { damping: 16, stiffness: 170, mass: 0.9 },
  });
  const enterX = interpolate(enterSpr, [0, 1], [-700, 0]);

  // ── Exit: clean slide off to left, no bounce ──
  const exitX = frame < exitStart ? 0 : interpolate(
    frame,
    [exitStart, exitStart + EXIT_HOLD],
    [0, -700],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic) },
  );

  const op = interpolate(frame, [ENTER, ENTER + 8], [0, 1], { extrapolateRight: "clamp" });
  const glow = 0.5 + Math.sin(frame * 0.055) * 0.25;

  const dateFontSize = date.length > 13 ? 30 : date.length > 8 ? 38 : 46;

  return (
    <AbsoluteFill style={{ background: "transparent", overflow: "hidden" }}>
      <div style={{
        position: "absolute",
        bottom: 80,
        left: 0,
        opacity: op,
        transform: `translateX(${enterX + exitX}px)`,
      }}>
        {/* Glow shadow behind */}
        <div style={{
          position: "absolute",
          inset: -6,
          background: accent,
          filter: `blur(${20 * glow}px)`,
          opacity: 0.22,
          clipPath: `polygon(0 0, 100% 0, calc(100% - ${NOTCH}px) 50%, 100% 100%, 0 100%)`,
          borderRadius: "14px 0 0 14px",
        }} />

        {/* Badge body — inline-flex so it auto-sizes to content */}
        <div style={{
          display: "inline-flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          position: "relative",
          background: "rgba(4, 4, 22, 0.93)",
          backdropFilter: "blur(18px)",
          WebkitBackdropFilter: "blur(18px)",
          border: `1.5px solid ${accent}55`,
          borderRight: "none",
          borderRadius: "12px 0 0 12px",
          clipPath: `polygon(0 0, 100% 0, calc(100% - ${NOTCH}px) 50%, 100% 100%, 0 100%)`,
          // paddingRight = paddingLeft + NOTCH → text center == visual center of clipped shape
          padding: `18px ${20 + NOTCH}px 18px 20px`,
          gap: 5,
        }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 5, transform: "translateX(3px) translateY(-6px)" }}>
            {/* Label */}
            {label && (
              <div style={{
                fontFamily: SYNE,
                fontSize: 10,
                fontWeight: "800",
                letterSpacing: "0.35em",
                textTransform: "uppercase",
                color: `${accent}CC`,
                textAlign: "center",
                lineHeight: 1,
              }}>
                {label}
              </div>
            )}

            {/* Date — Orbitron Bold, uppercase */}
            <div style={{
              fontFamily: ORBITRON,
              fontSize: dateFontSize,
              fontWeight: "900",
              color: "#FFFFFF",
              letterSpacing: "0.06em",
              lineHeight: 1.05,
              textAlign: "center",
              textTransform: "uppercase",
              textShadow: `0 0 22px ${accent}55`,
              whiteSpace: "nowrap",
            }}>
              {date}
            </div>
          </div>{/* /inner centering wrapper */}

          {/* Left accent strip — absolute, outside inner wrapper */}
          <div style={{
            position: "absolute",
            left: 0, top: 0, bottom: 0,
            width: 4,
            borderRadius: "12px 0 0 12px",
            background: accent,
            boxShadow: `0 0 ${14 * glow}px ${accent}`,
          }} />
        </div>
      </div>
    </AbsoluteFill>
  );
};
