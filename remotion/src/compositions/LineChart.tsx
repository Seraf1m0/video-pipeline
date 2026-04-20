import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne    } from "@remotion/google-fonts/Syne";
import { loadFont as loadManrope } from "@remotion/google-fonts/Manrope";
import { loadFont as loadBebas   } from "@remotion/google-fonts/BebasNeue";
import { noise2D } from "@remotion/noise";
import { seededRand } from "../utils/seeded";

const { fontFamily: SYNE    } = loadSyne();
const { fontFamily: MANROPE } = loadManrope();
const { fontFamily: BEBAS   } = loadBebas();

export interface LinePoint { label: string; value: number; }
export interface LineChartProps {
  title:         string;
  points:        LinePoint[];
  unit?:         string;
  accent_color?: string;
  duration_s?:   number;
  bg_color?:     string;
  seed?:         number;
}

const EXIT_DUR  = 55;
const CHART_L   = 200;
const CHART_R   = 1720;
const CHART_BOT = 840;
const CHART_TOP = 230;
const CHART_H   = CHART_BOT - CHART_TOP;   // 610
const CHART_W   = CHART_R   - CHART_L;      // 1520

// ─── Catmull-Rom helpers ───────────────────────────────────────────────────────
type Pt = { x: number; y: number };

function catmullCP(pts: Pt[], i: number): [Pt, Pt] {
  const p0 = pts[Math.max(0, i - 1)];
  const p1 = pts[i];
  const p2 = pts[i + 1];
  const p3 = pts[Math.min(pts.length - 1, i + 2)];
  return [
    { x: p1.x + (p2.x - p0.x) / 6, y: p1.y + (p2.y - p0.y) / 6 },
    { x: p2.x - (p3.x - p1.x) / 6, y: p2.y - (p3.y - p1.y) / 6 },
  ];
}

function buildLinePath(pts: Pt[]): string {
  if (pts.length < 2) return `M ${pts[0].x},${pts[0].y}`;
  let d = `M ${pts[0].x},${pts[0].y}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const [c1, c2] = catmullCP(pts, i);
    d += ` C ${c1.x.toFixed(1)},${c1.y.toFixed(1)} ${c2.x.toFixed(1)},${c2.y.toFixed(1)} ${pts[i+1].x},${pts[i+1].y}`;
  }
  return d;
}

function buildAreaPath(pts: Pt[], bot: number): string {
  if (pts.length < 2) return "";
  let d = `M ${pts[0].x},${bot} L ${pts[0].x},${pts[0].y}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const [c1, c2] = catmullCP(pts, i);
    d += ` C ${c1.x.toFixed(1)},${c1.y.toFixed(1)} ${c2.x.toFixed(1)},${c2.y.toFixed(1)} ${pts[i+1].x},${pts[i+1].y}`;
  }
  d += ` L ${pts[pts.length - 1].x},${bot} Z`;
  return d;
}

// Ball position on cubic bezier segment i at parameter t∈[0,1]
function bezierPt(pts: Pt[], i: number, t: number): Pt {
  const [c1, c2] = catmullCP(pts, i);
  const p1 = pts[i];
  const p2 = pts[i + 1];
  const u = 1 - t;
  return {
    x: u*u*u*p1.x + 3*u*u*t*c1.x + 3*u*t*t*c2.x + t*t*t*p2.x,
    y: u*u*u*p1.y + 3*u*u*t*c1.y + 3*u*t*t*c2.y + t*t*t*p2.y,
  };
}

// ─── BG ───────────────────────────────────────────────────────────────────────
const Bg: React.FC<{frame:number;color:string;total:number}> = ({frame,color,total}) => {
  const inOp  = interpolate(frame,[0,30],[0,1],{extrapolateRight:"clamp"});
  const outOp = interpolate(frame,[total-EXIT_DUR,total],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const op    = inOp * outOp;
  const nx = noise2D("lcx",frame*0.0014,0)*6;
  const ny = noise2D("lcy",0,frame*0.0014)*4;
  return (
    <AbsoluteFill>
      <div style={{position:"absolute",left:`${32+nx}%`,top:`${60+ny}%`,
        transform:"translate(-50%,-50%)",width:850,height:580,borderRadius:"50%",
        background:`radial-gradient(circle, ${color} 0%, transparent 65%)`,
        filter:"blur(130px)",opacity:op*0.14}} />
      <div style={{position:"absolute",inset:0,
        background:"radial-gradient(ellipse 90% 85% at 50% 50%, transparent 20%, #000000D4 100%)"}} />
    </AbsoluteFill>
  );
};

// ─── SCAN / EXIT SCAN ─────────────────────────────────────────────────────────
const ScanLine: React.FC<{frame:number;color:string}> = ({frame,color}) => {
  const y  = interpolate(frame,[0,22],[0,110],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.out(Easing.cubic)});
  const op = interpolate(frame,[0,3,16,24],[0,1,1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  return <div style={{position:"absolute",top:`${y}%`,left:0,right:0,height:2,
    background:`linear-gradient(to right, transparent, ${color}BB, transparent)`,
    boxShadow:`0 0 24px ${color}88`,opacity:op}} />;
};
const ExitScan: React.FC<{frame:number;total:number;color:string}> = ({frame,total,color}) => {
  const s  = total - EXIT_DUR + 6;
  const y  = interpolate(frame,[s,s+20],[110,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.in(Easing.cubic)});
  const op = interpolate(frame,[s,s+3,s+16,s+24],[0,1,1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  return <div style={{position:"absolute",top:`${y}%`,left:0,right:0,height:2,
    background:`linear-gradient(to right, transparent, ${color}88, transparent)`,
    boxShadow:`0 0 18px ${color}55`,opacity:op}} />;
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const LineChart: React.FC<LineChartProps> = ({
  title, points,
  unit         = "",
  accent_color = "#4DFFB4",
  duration_s   = 12,
  bg_color     = "#020218",
  seed         = 0,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const rand = seededRand(seed);
  const lineOpacity = 0.80 + rand() * 0.08;
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_DUR;

  const fadeIn    = interpolate(frame,[0,12],[0,1],{extrapolateRight:"clamp",easing:Easing.out(Easing.cubic)});
  const finalFade = interpolate(frame,[totalFrames-5,totalFrames],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  const n      = points.length;
  const maxVal = Math.max(...points.map(p => p.value));
  const minVal = Math.min(...points.map(p => p.value));
  const range  = Math.max(maxVal - minVal, 1);
  const yMin   = Math.max(0, minVal - range * 0.18);
  const yMax   = maxVal + range * 0.20;

  const yFor = (v: number) => CHART_BOT - ((v - yMin) / (yMax - yMin)) * CHART_H;
  const xFor = (i: number) => n <= 1 ? CHART_L + CHART_W / 2 : CHART_L + (i / (n - 1)) * CHART_W;

  const ptCoords: Pt[] = points.map((p, i) => ({ x: xFor(i), y: yFor(p.value) }));

  // ── Draw progress — LINEAR easing → ping frames are exact ──
  const drawStart = 26;
  const drawDur   = Math.max(60, (n - 1) * 30);

  const drawP = interpolate(frame, [drawStart, drawStart + drawDur], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.linear,
  });

  // ── Exit: line erases left→right (retractX advances) ──
  const retractP = interpolate(frame, [exitStart + 6, exitStart + EXIT_DUR - 10], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.bezier(0.55, 0, 1, 0.45),
  });
  const retractX  = CHART_L + retractP * CHART_W;
  const drawClipX = CHART_L + drawP * CHART_W;
  const clipLeft  = retractX;
  const clipWidth = Math.max(0, drawClipX - clipLeft);

  // ── Ball position on smooth bezier ──
  const clampP  = Math.min(drawP, 0.9999);
  const segF    = clampP * Math.max(n - 1, 1);
  const segIdx  = Math.min(Math.floor(segF), n - 2);
  const segT    = n <= 1 ? 0 : segF - Math.floor(segF);
  const ballPt  = n <= 1 ? ptCoords[0] : bezierPt(ptCoords, segIdx, segT);

  // ── Ball exit: shoots right, then fades ──
  const lastPt     = ptCoords[n - 1];
  const ballExitX  = interpolate(frame, [exitStart, exitStart + 28], [lastPt.x, lastPt.x + 380], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
    easing: Easing.in(Easing.cubic),
  });
  const isDoneDrawing = drawP >= 1.0;
  const finalBallX = isDoneDrawing && frame >= exitStart ? ballExitX : ballPt.x;
  const finalBallY = isDoneDrawing && frame >= exitStart ? lastPt.y : ballPt.y;

  const ballOp = interpolate(frame,[drawStart,drawStart+14],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart+12,exitStart+34],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  // ── Ping frames — exact because easing is linear ──
  const ptPassFrame = points.map((_, i) =>
    drawStart + (i / Math.max(n - 1, 1)) * drawDur
  );

  // ── Opacity helpers ──
  const gridOp  = interpolate(frame,[10,28],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart,exitStart+16],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const axisOp  = interpolate(frame,[8,22],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart,exitStart+14],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const labGlob = interpolate(frame,[exitStart,exitStart+22],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  // ── Title ──
  const spr     = spring({frame, fps, config:{damping:28,stiffness:220}});
  const titleY  = interpolate(spr,[0,1],[-18,0]);
  const titleOp = interpolate(frame,[0,10],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart+4,exitStart+20],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const lineW   = interpolate(frame,[4,26],[0,100],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.out(Easing.exp)});

  // ── Y ticks ──
  const yTicks = [0.25, 0.5, 0.75, 1.0];

  // ── SVG paths ──
  const linePath = buildLinePath(ptCoords);
  const areaPath = buildAreaPath(ptCoords, CHART_BOT);

  return (
    <AbsoluteFill style={{background:bg_color,overflow:"hidden"}}>
      <div style={{opacity:fadeIn*finalFade,width:"100%",height:"100%"}}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />

        {/* ── Chart SVG ── */}
        <svg style={{position:"absolute",left:0,top:0,width:1920,height:1080}}
          viewBox="0 0 1920 1080">
          <defs>
            {/* Clip: visible between retractX and drawClipX */}
            <clipPath id="lc-clip">
              <rect x={clipLeft} y={0} width={clipWidth} height={1080} />
            </clipPath>
            <linearGradient id="lc-area-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor={accent_color} stopOpacity="0.32" />
              <stop offset="100%" stopColor={accent_color} stopOpacity="0.02" />
            </linearGradient>
          </defs>

          {/* Grid lines */}
          {yTicks.map((t, i) => (
            <line key={i}
              x1={CHART_L} y1={CHART_BOT - t*CHART_H}
              x2={CHART_R} y2={CHART_BOT - t*CHART_H}
              stroke="#FFFFFF0D" strokeWidth={1} opacity={gridOp}
            />
          ))}

          {/* Baseline */}
          <line x1={CHART_L} y1={CHART_BOT} x2={CHART_R} y2={CHART_BOT}
            stroke={`${accent_color}55`} strokeWidth={1.5} opacity={axisOp} />

          {/* Left axis */}
          <line x1={CHART_L} y1={CHART_TOP - 20} x2={CHART_L} y2={CHART_BOT}
            stroke="#FFFFFF12" strokeWidth={1} opacity={axisOp} />

          {/* Area fill — clipped */}
          <path d={areaPath} fill="url(#lc-area-grad)" clipPath="url(#lc-clip)" />

          {/* Outer glow — wide soft */}
          <path d={linePath} fill="none"
            stroke={accent_color} strokeWidth={20}
            strokeLinecap="round" strokeLinejoin="round"
            opacity={0.12} clipPath="url(#lc-clip)" />

          {/* Mid glow */}
          <path d={linePath} fill="none"
            stroke={accent_color} strokeWidth={8}
            strokeLinecap="round" strokeLinejoin="round"
            opacity={0.45} clipPath="url(#lc-clip)" />

          {/* Crisp white core */}
          <path d={linePath} fill="none"
            stroke="#FFFFFF" strokeWidth={2.5}
            strokeLinecap="round" strokeLinejoin="round"
            opacity={lineOpacity} clipPath="url(#lc-clip)" />

          {/* Data point dots */}
          {ptCoords.map((pt, i) => {
            const dotOp = interpolate(frame,[ptPassFrame[i],ptPassFrame[i]+10],[0,1],{extrapolateRight:"clamp"})
              * labGlob;
            return (
              <g key={i} opacity={dotOp}>
                <circle cx={pt.x} cy={pt.y} r={18} fill={accent_color} opacity={0.10} />
                <circle cx={pt.x} cy={pt.y} r={8}  fill={accent_color} />
                <circle cx={pt.x} cy={pt.y} r={4}  fill="#FFFFFF" opacity={0.92} />
              </g>
            );
          })}

          {/* Ball — leading glow */}
          <g opacity={ballOp}>
            <circle cx={finalBallX} cy={finalBallY} r={44} fill={accent_color} opacity={0.05} />
            <circle cx={finalBallX} cy={finalBallY} r={28} fill={accent_color} opacity={0.13} />
            <circle cx={finalBallX} cy={finalBallY} r={15} fill={accent_color} opacity={0.42} />
            <circle cx={finalBallX} cy={finalBallY} r={8}  fill={accent_color} opacity={0.90} />
            <circle cx={finalBallX} cy={finalBallY} r={4}  fill="#FFFFFF"      opacity={1.0} />
          </g>
        </svg>

        {/* ── Y-axis labels ── */}
        {yTicks.map((t, i) => {
          const gy  = CHART_BOT - t * CHART_H;
          const val = Math.round(yMin + t * (yMax - yMin));
          return (
            <div key={i} style={{
              position:"absolute", left:CHART_L - 14, top:gy,
              transform:"translate(-100%,-50%)",
              opacity:gridOp,
              fontFamily:MANROPE, fontSize:13, fontWeight:"500",
              color:"#FFFFFF30", letterSpacing:"0.01em", textAlign:"right",
            }}>
              {val}{unit}
            </div>
          );
        })}

        {/* ── Per-point labels ── */}
        {points.map((pt, i) => {
          const px    = xFor(i);
          const py    = yFor(pt.value);
          // Always above dot, but if too close to top edge → flip below
          const aboveTop = py - 68;
          const belowDot = py + 36;
          const valTop   = aboveTop < CHART_TOP + 4 ? belowDot : aboveTop;

          const labOp = interpolate(frame,[ptPassFrame[i]+10,ptPassFrame[i]+24],[0,1],{extrapolateRight:"clamp"})
            * labGlob;
          const fadeY = interpolate(frame,[ptPassFrame[i]+10,ptPassFrame[i]+24],[-8, 0],
            {extrapolateLeft:"clamp",extrapolateRight:"clamp"});
          return (
            <React.Fragment key={i}>
              {/* X label — below baseline */}
              <div style={{
                position:"absolute", left:px, top:CHART_BOT + 22,
                transform:"translateX(-50%)",
                opacity:labOp,
                fontFamily:MANROPE, fontSize:14, fontWeight:"500",
                color:"#FFFFFF38", letterSpacing:"0.04em",
                whiteSpace:"nowrap",
              }}>
                {pt.label}
              </div>
              {/* Value chip — always above dot (or below if near top) */}
              <div style={{
                position:"absolute",
                left:px,
                top: valTop,
                transform:`translateX(-50%) translateY(${fadeY}px)`,
                opacity:labOp,
                display:"flex", flexDirection:"column", alignItems:"center", gap:0,
              }}>
                <div style={{
                  fontFamily:BEBAS, fontSize:38,
                  color:"#FFFFFF", letterSpacing:"0.04em", lineHeight:1,
                  textShadow:`0 0 22px ${accent_color}99`,
                }}>
                  {pt.value}{unit}
                </div>
              </div>
            </React.Fragment>
          );
        })}

        {/* ── Title ── */}
        <div style={{
          position:"absolute", top:72, left:CHART_L,
          opacity:titleOp, transform:`translateY(${titleY}px)`,
          display:"flex", flexDirection:"column", gap:10,
        }}>
          <div style={{fontFamily:SYNE,fontSize:11,fontWeight:"800",
            letterSpacing:"0.44em",textTransform:"uppercase",color:"#FFFFFF55"}}>
            {title}
          </div>
          <div style={{width:`${lineW}%`,maxWidth:180,height:1,
            background:`linear-gradient(to right, ${accent_color}88, transparent)`}} />
        </div>

        {/* ── SFX ── */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        {points.map((_, i) => (
          <Sequence key={i} from={Math.round(ptPassFrame[i])} durationInFrames={18}>
            <Audio src={staticFile("sfx/ping.wav")} volume={Math.max(0.04, 0.12 - i * 0.007)} />
          </Sequence>
        ))}
        <Sequence from={exitStart + 4} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>
      </div>
    </AbsoluteFill>
  );
};
