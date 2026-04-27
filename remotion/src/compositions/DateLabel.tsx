import React from "react";
import {
  AbsoluteFill, interpolate, spring,
  useCurrentFrame, useVideoConfig, Easing,
} from "remotion";
import { loadFont as loadSyne      } from "@remotion/google-fonts/Syne";
import { loadFont as loadSpaceMono } from "@remotion/google-fonts/SpaceMono";

const { fontFamily: SYNE      } = loadSyne();
const { fontFamily: SPACEMONO } = loadSpaceMono();

export interface DateLabelProps {
  date: string;
  label?: string;
  accent_color?: string;
  duration_s?: number;
  side?: "left" | "right";
}

const EXIT_HOLD = 30; // frames before end to start exit

export const DateLabel: React.FC<DateLabelProps> = ({
  date,
  label,
  accent_color = "#00C8FF",
  duration_s   = 6,
  side         = "left",
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_HOLD;

  const dir = side === "left" ? -1 : 1;

  // ── Enter: spring from side ──
  const enterSpr = spring({
    frame: frame - 4,
    fps,
    config: { damping: 20, stiffness: 220 },
  });
  const enterX = interpolate(enterSpr, [0, 1], [dir * 600, 0]);

  // ── Exit: ease back to same side ──
  const exitX = interpolate(
    frame,
    [exitStart, exitStart + EXIT_HOLD - 4],
    [0, dir * 600],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.in(Easing.cubic) },
  );

  const x = enterX + exitX;

  // Opacity: quick fade in, no fade out (badge just slides off)
  const op = interpolate(frame, [4, 14], [0, 1], { extrapolateRight: "clamp" });

  // Accent strip pulse
  const glow = 0.6 + Math.sin(frame * 0.07) * 0.3;

  // Date font size
  const dateFontSize = date.length > 14 ? 36 : date.length > 9 ? 44 : 52;

  return (
    <AbsoluteFill style={{ background: "transparent", overflow: "hidden" }}>
      {/* Centered vertically, anchored to chosen side */}
      <div style={{
        position: "absolute",
        top: "50%",
        left: side === "left" ? 80 : undefined,
        right: side === "right" ? 80 : undefined,
        transform: `translateY(-50%) translateX(${x}px)`,
        opacity: op,
        display: "inline-flex",
        alignItems: "stretch",
        borderRadius: 14,
        border: `1px solid ${accent_color}55`,
        boxShadow: `0 0 40px ${accent_color}22, 0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 ${accent_color}22`,
        background: "rgba(4, 4, 28, 0.82)",
        backdropFilter: "blur(16px)",
        WebkitBackdropFilter: "blur(16px)",
      }}>
        {/* Left / Right accent strip */}
        {side === "left" && (
          <div style={{
            width: 5,
            background: `linear-gradient(to bottom, ${accent_color}CC, ${accent_color})`,
            boxShadow: `0 0 ${16 * glow}px ${accent_color}`,
            flexShrink: 0,
          }} />
        )}

        {/* Content */}
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: side === "left" ? "flex-start" : "flex-end",
          justifyContent: "center",
          padding: "18px 28px",
          gap: 4,
        }}>
          {/* Label */}
          {label && (
            <div style={{
              fontFamily: SYNE,
              fontSize: 11,
              fontWeight: "800",
              letterSpacing: "0.35em",
              textTransform: "uppercase",
              color: `${accent_color}CC`,
              lineHeight: 1,
              whiteSpace: "nowrap",
            }}>
              {label}
            </div>
          )}

          {/* Date */}
          <div style={{
            fontFamily: SPACEMONO,
            fontSize: dateFontSize,
            fontWeight: "700",
            color: "#FFFFFF",
            letterSpacing: "0.02em",
            lineHeight: 1.1,
            textShadow: `0 0 30px ${accent_color}44`,
            whiteSpace: "nowrap",
          }}>
            {date}
          </div>
        </div>

        {/* Right accent strip */}
        {side === "right" && (
          <div style={{
            width: 5,
            background: `linear-gradient(to bottom, ${accent_color}CC, ${accent_color})`,
            boxShadow: `0 0 ${16 * glow}px ${accent_color}`,
            flexShrink: 0,
          }} />
        )}
      </div>
    </AbsoluteFill>
  );
};
