import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  Sequence,
  Audio,
  staticFile,
  Easing,
} from "remotion";

// ─── DESIGN TOKENS ────────────────────────────────────────────────────────────
const COLORS = {
  bg:          "#05051A",
  sunCore:     "#FF8C00",
  sunMid:      "#FF6B00",
  sunEdge:     "#CC3300",
  corona:      "#E0F0FF",
  coronaGlow:  "#9ECFFF",
  textPrimary: "#FFFFFF",
  textDim:     "#8899BB",
  tempSurface: "#FF9A3C",
  tempCorona:  "#A8D8FF",
  accent:      "#FF4444",
  gold:        "#FFD700",
};

// ─── FONT ─────────────────────────────────────────────────────────────────────
const FONT_MONO  = "'Courier New', monospace";
const FONT_SANS  = "'Arial', 'Helvetica Neue', sans-serif";

// ─── ANIMATED NUMBER COUNTER ──────────────────────────────────────────────────
const Counter: React.FC<{
  value: number;
  frame: number;
  startFrame: number;
  endFrame: number;
  suffix?: string;
  color?: string;
  fontSize?: number;
}> = ({ value, frame, startFrame, endFrame, suffix = "", color = COLORS.textPrimary, fontSize = 72 }) => {
  const progress = interpolate(frame, [startFrame, endFrame], [0, 1], {
    extrapolateLeft:  "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });
  const current = Math.round(progress * value);
  const formatted = current.toLocaleString("de-DE");

  return (
    <span style={{
      fontFamily:  FONT_MONO,
      fontSize,
      fontWeight:  "bold",
      color,
      letterSpacing: "-2px",
      textShadow:  `0 0 30px ${color}88, 0 0 60px ${color}44`,
    }}>
      {formatted}{suffix}
    </span>
  );
};

// ─── SUN SPHERE ───────────────────────────────────────────────────────────────
const Sun: React.FC<{ frame: number; phase: "surface" | "corona" | "both" }> = ({ frame, phase }) => {
  const { fps } = useVideoConfig();

  // Scale in spring
  const scaleProgress = spring({ frame, fps, config: { damping: 14, stiffness: 80, mass: 1 } });
  const scale = interpolate(scaleProgress, [0, 1], [0.3, 1]);

  // Pulse animation (always active)
  const pulse = Math.sin(frame * 0.08) * 0.03 + 1.0;

  // Corona expansion
  const coronaScale = phase === "corona" || phase === "both"
    ? interpolate(frame, [0, 30], [1, 1.6], { extrapolateRight: "clamp", easing: Easing.out(Easing.quad) })
    : 0;

  const coronaOpacity = phase === "corona" || phase === "both"
    ? interpolate(frame, [0, 20], [0, 1], { extrapolateRight: "clamp" })
    : 0;

  const SIZE = 220;

  return (
    <div style={{ position: "relative", width: SIZE, height: SIZE }}>
      {/* Outer corona glow layers */}
      {(phase === "corona" || phase === "both") && [3, 2, 1].map((i) => (
        <div key={i} style={{
          position:    "absolute",
          inset:       `-${i * 30 * coronaScale}px`,
          borderRadius: "50%",
          background:  `radial-gradient(circle, transparent 40%, ${COLORS.coronaGlow}${Math.round(8 / i).toString(16).padStart(2, '0')} 70%, transparent 100%)`,
          opacity:     coronaOpacity * (0.4 / i),
          transform:   `scale(${coronaScale})`,
        }} />
      ))}

      {/* Corona ring */}
      {(phase === "corona" || phase === "both") && (
        <div style={{
          position:     "absolute",
          inset:        `-${50 * coronaScale}px`,
          borderRadius: "50%",
          border:       `${3 / coronaScale}px solid ${COLORS.corona}`,
          opacity:      coronaOpacity * 0.6,
          transform:    `scale(${coronaScale})`,
          boxShadow:    `0 0 40px ${COLORS.corona}44, inset 0 0 40px ${COLORS.corona}22`,
        }} />
      )}

      {/* Sun body */}
      <div style={{
        width:        SIZE,
        height:       SIZE,
        borderRadius: "50%",
        background:   `radial-gradient(circle at 38% 38%,
          #FFEE88 0%,
          ${COLORS.sunCore} 20%,
          ${COLORS.sunMid} 50%,
          ${COLORS.sunEdge} 80%,
          #660000 100%)`,
        boxShadow:    `
          0 0 60px ${COLORS.sunCore}99,
          0 0 120px ${COLORS.sunCore}44,
          0 0 200px ${COLORS.sunMid}22
        `,
        transform:    `scale(${scale * pulse})`,
        transition:   "none",
      }} />

      {/* Surface shimmer */}
      <div style={{
        position:     "absolute",
        inset:        0,
        borderRadius: "50%",
        background:   `radial-gradient(circle at 30% 25%,
          rgba(255,255,200,0.4) 0%,
          transparent 50%)`,
        transform:    `scale(${scale * pulse})`,
      }} />
    </div>
  );
};

// ─── TEMPERATURE BADGE ────────────────────────────────────────────────────────
const TempBadge: React.FC<{
  label: string;
  value: number;
  suffix: string;
  color: string;
  frame: number;
  startFrame: number;
  position: "left" | "right";
}> = ({ label, value, suffix, color, frame, startFrame, position }) => {
  const { fps } = useVideoConfig();

  const slideProgress = spring({
    frame: frame - startFrame,
    fps,
    config: { damping: 18, stiffness: 120, mass: 0.8 },
  });

  const x = interpolate(slideProgress, [0, 1], [position === "left" ? -120 : 120, 0]);
  const opacity = interpolate(frame, [startFrame, startFrame + 10], [0, 1], { extrapolateRight: "clamp" });

  return (
    <div style={{
      transform:  `translateX(${x}px)`,
      opacity,
      textAlign:  position === "left" ? "right" : "left",
      padding:    "0 20px",
    }}>
      <div style={{
        fontFamily:    FONT_SANS,
        fontSize:      13,
        fontWeight:    "600",
        letterSpacing: "3px",
        textTransform: "uppercase",
        color:         COLORS.textDim,
        marginBottom:  6,
      }}>
        {label}
      </div>
      <Counter
        value={value}
        frame={frame}
        startFrame={startFrame}
        endFrame={startFrame + 60}
        suffix={suffix}
        color={color}
        fontSize={position === "left" ? 52 : 68}
      />
    </div>
  );
};

// ─── PHYSICS LAW TEXT ─────────────────────────────────────────────────────────
const PhysicsLaw: React.FC<{ frame: number }> = ({ frame }) => {
  const { fps } = useVideoConfig();

  const slideIn = spring({ frame, fps, config: { damping: 20, stiffness: 100 } });
  const opacity = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: "clamp" });

  // Strikethrough draws left to right
  const strikeWidth = interpolate(frame, [25, 55], [0, 100], {
    extrapolateLeft:  "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

  // X mark
  const xOpacity = interpolate(frame, [50, 65], [0, 1], {
    extrapolateLeft:  "clamp",
    extrapolateRight: "clamp",
  });
  const xScale = spring({
    frame: frame - 50,
    fps,
    config: { damping: 8, stiffness: 300, mass: 0.5 },
  });

  const y = interpolate(slideIn, [0, 1], [30, 0]);

  return (
    <div style={{
      opacity,
      transform: `translateY(${y}px)`,
      textAlign: "center",
    }}>
      {/* Law text */}
      <div style={{ position: "relative", display: "inline-block" }}>
        <div style={{
          fontFamily:    FONT_SANS,
          fontSize:      22,
          fontWeight:    "300",
          letterSpacing: "1px",
          color:         COLORS.textDim,
          fontStyle:     "italic",
        }}>
          „Wärme fließt immer von{" "}
          <span style={{ color: COLORS.tempSurface, fontWeight: "600" }}>heiß</span>
          {" → "}
          <span style={{ color: COLORS.tempCorona, fontWeight: "600" }}>kalt</span>
          ."
        </div>

        {/* Strikethrough line */}
        <div style={{
          position:   "absolute",
          top:        "50%",
          left:       0,
          height:     "3px",
          width:      `${strikeWidth}%`,
          background: `linear-gradient(to right, ${COLORS.accent}, #FF8888)`,
          boxShadow:  `0 0 12px ${COLORS.accent}`,
          marginTop:  "-1px",
        }} />
      </div>

      {/* X mark badge */}
      <div style={{
        marginTop:  20,
        opacity:    xOpacity,
        transform:  `scale(${xScale})`,
        display:    "inline-flex",
        alignItems: "center",
        gap:        10,
        background: `${COLORS.accent}22`,
        border:     `1px solid ${COLORS.accent}88`,
        borderRadius: 8,
        padding:    "8px 20px",
      }}>
        <span style={{ fontSize: 22, color: COLORS.accent }}>✕</span>
        <span style={{
          fontFamily:    FONT_SANS,
          fontSize:      16,
          fontWeight:    "700",
          letterSpacing: "2px",
          textTransform: "uppercase",
          color:         COLORS.accent,
        }}>
          Physikalisch unmöglich
        </span>
      </div>
    </div>
  );
};

// ─── MULTIPLIER BADGE ─────────────────────────────────────────────────────────
const MultiplierBadge: React.FC<{ frame: number; startFrame: number }> = ({ frame, startFrame }) => {
  const { fps } = useVideoConfig();
  const elapsed = frame - startFrame;

  const pop = spring({
    frame: elapsed,
    fps,
    config: { damping: 7, stiffness: 400, mass: 0.5 },
  });

  const opacity = interpolate(elapsed, [0, 8], [0, 1], { extrapolateRight: "clamp" });
  const rotate = interpolate(elapsed, [0, 20], [-8, 0], { extrapolateRight: "clamp" });

  return (
    <div style={{
      opacity,
      transform: `scale(${pop}) rotate(${rotate}deg)`,
      background: `linear-gradient(135deg, ${COLORS.gold}33, ${COLORS.gold}55)`,
      border:     `2px solid ${COLORS.gold}99`,
      borderRadius: 12,
      padding:    "10px 18px",
    }}>
      <div style={{
        fontFamily:    FONT_MONO,
        fontSize:      40,
        fontWeight:    "bold",
        color:         COLORS.gold,
        textShadow:    `0 0 20px ${COLORS.gold}88`,
        textAlign:     "center",
      }}>
        ×350
      </div>
      <div style={{
        fontFamily:    FONT_SANS,
        fontSize:      11,
        letterSpacing: "2px",
        textTransform: "uppercase",
        color:         `${COLORS.gold}AA`,
        textAlign:     "center",
        marginTop:     4,
      }}>
        heißer
      </div>
    </div>
  );
};

// ─── TITLE TEXT ───────────────────────────────────────────────────────────────
const Title: React.FC<{ frame: number; startFrame: number; text: string; sub?: string }> = ({
  frame, startFrame, text, sub
}) => {
  const { fps } = useVideoConfig();
  const elapsed = frame - startFrame;
  const slideIn = spring({ frame: elapsed, fps, config: { damping: 20, stiffness: 80 } });
  const opacity = interpolate(elapsed, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const y = interpolate(slideIn, [0, 1], [20, 0]);

  return (
    <div style={{ opacity, transform: `translateY(${y}px)`, textAlign: "center" }}>
      <div style={{
        fontFamily:    FONT_SANS,
        fontSize:      15,
        fontWeight:    "700",
        letterSpacing: "5px",
        textTransform: "uppercase",
        color:         COLORS.textDim,
        marginBottom:  8,
      }}>
        {text}
      </div>
      {sub && (
        <div style={{
          fontFamily: FONT_SANS,
          fontSize:   12,
          color:      `${COLORS.textDim}88`,
          letterSpacing: "2px",
        }}>
          {sub}
        </div>
      )}
    </div>
  );
};

// ─── MAIN COMPOSITION ─────────────────────────────────────────────────────────
// 3 сегмента из сценария "Solarproblem DE":
// Seg 1 (0–3s):   "Die Oberfläche der Sonne ist etwa 5.500 Grad Celsius heiß."
// Seg 2 (3–7s):   "Die Korona ist zwei Millionen Grad heiß. Das ist 350× heißer."
// Seg 3 (7–10s):  "Das Problem ist die Physik: Wärme fließt immer von heiß nach kalt."
export const SolarParadox: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Phase timings (30fps)
  const PHASE1_END   = fps * 3;   // f=90:  surface temp
  const PHASE2_START = fps * 3;   // f=90:  corona reveals
  const PHASE2_END   = fps * 7;   // f=210: corona established
  const PHASE3_START = fps * 7;   // f=210: physics law
  const PHASE3_END   = fps * 10;  // f=300: end

  // Overall fade in / out
  const fadeIn  = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const fadeOut = interpolate(frame, [PHASE3_END - 20, PHASE3_END], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const globalOpacity = fadeIn * fadeOut;

  // Background pulse (subtle) during phase 2
  const bgIntensity = interpolate(
    frame,
    [PHASE2_START, PHASE2_START + 30, PHASE2_END],
    [0, 0.08, 0.04],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  // Determine sun phase
  const sunPhase: "surface" | "corona" | "both" =
    frame < PHASE2_START ? "surface" :
    frame < PHASE3_START ? "corona"  : "both";

  // Sun local frame (always starts at 0 for spring)
  const sunFrame = frame;

  // Horizontal separator line
  const lineWidth = interpolate(frame, [10, 50], [0, 100], {
    extrapolateLeft:  "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.quad),
  });

  return (
    <AbsoluteFill style={{
      background: `radial-gradient(ellipse at 50% 40%,
        ${COLORS.bg.replace('#', '#')}CC 0%,
        #03030F 100%)`,
      fontFamily: FONT_SANS,
      color:      COLORS.textPrimary,
      overflow:   "hidden",
    }}>
      {/* Background corona haze (phase 2+) */}
      <AbsoluteFill style={{
        background: `radial-gradient(ellipse at 50% 50%,
          ${COLORS.coronaGlow}${Math.round(bgIntensity * 255).toString(16).padStart(2,'0')} 0%,
          transparent 60%)`,
        opacity: globalOpacity,
      }} />

      {/* Subtle star dots */}
      <AbsoluteFill style={{ opacity: globalOpacity * 0.4 }}>
        {[...Array(40)].map((_, i) => {
          const x = ((i * 137.5) % 100);
          const y = ((i * 97.3) % 100);
          const size = (i % 3) + 1;
          const twinkle = Math.sin(frame * 0.05 + i) * 0.3 + 0.7;
          return (
            <div key={i} style={{
              position:     "absolute",
              left:         `${x}%`,
              top:          `${y}%`,
              width:        size,
              height:       size,
              borderRadius: "50%",
              background:   COLORS.textPrimary,
              opacity:      twinkle * 0.6,
            }} />
          );
        })}
      </AbsoluteFill>

      {/* Main content */}
      <AbsoluteFill style={{
        opacity:        globalOpacity,
        display:        "flex",
        flexDirection:  "column",
        alignItems:     "center",
        justifyContent: "center",
        gap:            0,
      }}>

        {/* ── PHASE 1 & 2: Sun + Temperatures ── */}
        {frame < PHASE3_START && (
          <div style={{
            display:       "flex",
            flexDirection: "column",
            alignItems:    "center",
            gap:           40,
          }}>
            {/* Title */}
            <Title
              frame={frame}
              startFrame={0}
              text="Das Korona-Problem"
              sub="Astrophysik · Ungelöstes Paradox"
            />

            {/* Temp badges + Sun */}
            <div style={{
              display:     "flex",
              alignItems:  "center",
              gap:         50,
            }}>
              {/* Surface temp */}
              <TempBadge
                label="Oberfläche"
                value={5500}
                suffix="°C"
                color={COLORS.tempSurface}
                frame={frame}
                startFrame={8}
                position="left"
              />

              {/* Sun */}
              <Sun frame={sunFrame} phase={sunPhase} />

              {/* Corona temp */}
              {frame >= PHASE2_START && (
                <TempBadge
                  label="Korona"
                  value={2000000}
                  suffix="°C"
                  color={COLORS.tempCorona}
                  frame={frame}
                  startFrame={PHASE2_START + 5}
                  position="right"
                />
              )}
            </div>

            {/* Horizontal divider */}
            <div style={{
              width:      `${lineWidth}%`,
              height:     1,
              background: `linear-gradient(to right,
                transparent,
                ${COLORS.textDim}44 30%,
                ${COLORS.textDim}44 70%,
                transparent)`,
            }} />

            {/* ×350 badge — appears in phase 2 */}
            {frame >= PHASE2_START + 60 && (
              <MultiplierBadge
                frame={frame}
                startFrame={PHASE2_START + 60}
              />
            )}
          </div>
        )}

        {/* ── PHASE 3: Physics law paradox ── */}
        {frame >= PHASE3_START && (
          <div style={{
            display:       "flex",
            flexDirection: "column",
            alignItems:    "center",
            gap:           50,
          }}>
            {/* Smaller sun */}
            <div style={{ transform: "scale(0.6)", opacity: 0.7 }}>
              <Sun frame={sunFrame} phase="both" />
            </div>

            <PhysicsLaw frame={frame - PHASE3_START} />
          </div>
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
