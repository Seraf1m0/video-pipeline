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

export interface BarItem {
  label: string;
  value: number;
  color?: string;
}
export interface BarChartProps {
  title: string;
  bars: BarItem[];
  unit?: string;
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const EXIT_DUR  = 50;
const BAR_STEP  = 16;
const MAX_BAR_H = 430;
const CHART_BOT = 800;   // y of baseline
const CHART_L   = 160;
const CHART_R   = 1760;
const COLORS = ["#00C8FF","#FF3CAC","#4DFFB4","#FFD700","#A855F7","#FF6B35"];

// ─── BG ──────────────────────────────────────────────────────────────────────
const Bg: React.FC<{frame:number;color:string;total:number}> = ({frame,color,total}) => {
  const inOp  = interpolate(frame,[0,30],[0,1],{extrapolateRight:"clamp"});
  const outOp = interpolate(frame,[total-EXIT_DUR,total-EXIT_DUR+20],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const op    = inOp * outOp;
  const s1    = 1 + Math.sin(frame*0.020)*0.07;
  const nx    = noise2D("bx",frame*0.0016,0)*5;
  const ny    = noise2D("by",0,frame*0.0016)*4;
  return (
    <AbsoluteFill>
      <div style={{position:"absolute",left:`${28+nx}%`,top:`${65+ny}%`,transform:`translate(-50%,-50%) scale(${s1})`,width:700,height:500,borderRadius:"50%",background:`radial-gradient(circle, ${color} 0%, transparent 65%)`,filter:"blur(110px)",opacity:op*0.12}} />
      <div style={{position:"absolute",inset:0,background:"radial-gradient(ellipse 90% 85% at 50% 50%, transparent 20%, #000000D0 100%)"}} />
    </AbsoluteFill>
  );
};

// ─── GRID ─────────────────────────────────────────────────────────────────────
const GridOverlay: React.FC<{frame:number;color:string;total:number}> = ({frame,color,total}) => {
  const inOp  = interpolate(frame,[6,26],[0,1],{extrapolateRight:"clamp"});
  const outOp = interpolate(frame,[total-EXIT_DUR,total-EXIT_DUR+16],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const op    = inOp * outOp * 0.05;
  return (
    <AbsoluteFill style={{opacity:op}}>
      {Array.from({length:9},(_,i)=>(
        <div key={`v${i}`} style={{position:"absolute",left:`${(i/8)*100}%`,top:0,bottom:0,width:1,background:`linear-gradient(to bottom, transparent, ${color}44, transparent)`}} />
      ))}
    </AbsoluteFill>
  );
};

// ─── SCAN LINE ────────────────────────────────────────────────────────────────
const ScanLine: React.FC<{frame:number;color:string}> = ({frame,color}) => {
  const y  = interpolate(frame,[0,20],[0,110],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.out(Easing.cubic)});
  const op = interpolate(frame,[0,3,16,24],[0,1,1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  return <div style={{position:"absolute",top:`${y}%`,left:0,right:0,height:2,background:`linear-gradient(to right, transparent, ${color}AA, transparent)`,boxShadow:`0 0 20px ${color}66`,opacity:op}} />;
};

// ─── EXIT SCAN ────────────────────────────────────────────────────────────────
const ExitScan: React.FC<{frame:number;total:number;color:string}> = ({frame,total,color}) => {
  const start = total - EXIT_DUR + 12;
  const y     = interpolate(frame,[start,start+18],[110,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.in(Easing.cubic)});
  const op    = interpolate(frame,[start,start+3,start+14,start+22],[0,1,1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  return <div style={{position:"absolute",top:`${y}%`,left:0,right:0,height:2,background:`linear-gradient(to right, transparent, ${color}77, transparent)`,boxShadow:`0 0 16px ${color}44`,opacity:op}} />;
};

// ─── CHART GRID LINES ─────────────────────────────────────────────────────────
const ChartGridLines: React.FC<{frame:number;exitStart:number;accent:string}> = ({frame,exitStart,accent}) => {
  const inOp  = interpolate(frame,[12,28],[0,1],{extrapolateRight:"clamp"});
  const outOp = interpolate(frame,[exitStart,exitStart+16],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const op    = inOp * (frame>=exitStart?outOp:1);
  return (
    <>{[0.25,0.5,0.75,1.0].map((frac,i)=>(
      <div key={i} style={{position:"absolute",top:CHART_BOT-MAX_BAR_H*frac,left:CHART_L,right:1920-CHART_R,height:1,background:"#FFFFFF15",opacity:op}} />
    ))}</>
  );
};

// ─── INDIVIDUAL BAR ───────────────────────────────────────────────────────────
const Bar: React.FC<{
  bar:BarItem; index:number; enterFrame:number; frame:number;
  exitStart:number; maxVal:number; accent:string; unit:string;
  barW:number; barLeft:number; colors:string[];
  valueFontSize:number; labelFontSize:number;
}> = ({bar,index,enterFrame,frame,exitStart,maxVal,accent,unit,barW,barLeft,colors,valueFontSize,labelFontSize}) => {
  const color   = bar.color || colors[index % colors.length];
  const targetH = (bar.value / maxVal) * MAX_BAR_H;

  const growP = interpolate(frame,[enterFrame,enterFrame+38],[0,1],{
    extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.out(Easing.exp),
  });
  const exitDelay = index * 5;
  const exitP = interpolate(frame,[exitStart+exitDelay,exitStart+exitDelay+26],[0,1],{
    extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.in(Easing.cubic),
  });
  const currentH = growP * targetH * (1 - exitP);
  const valueOp  = interpolate(frame,[enterFrame+30,enterFrame+44],[0,1],{extrapolateRight:"clamp"}) * (1-exitP);
  const labelOp  = interpolate(frame,[enterFrame+8, enterFrame+20],[0,1],{extrapolateRight:"clamp"}) * (1-exitP);
  const padX = barW * 0.12;

  return (
    <>
      {/* Bar body */}
      <div style={{
        position:"absolute",
        bottom:1080-CHART_BOT,
        left:barLeft+padX,
        width:barW-padX*2,
        height:currentH,
        background:`linear-gradient(to top, ${color}EE 0%, ${color}66 100%)`,
        borderRadius:"5px 5px 0 0",
        boxShadow:`0 0 18px ${color}66, 0 0 40px ${color}22`,
      }} />
      {/* Value label */}
      <div style={{
        position:"absolute",
        bottom:1080-CHART_BOT+currentH+22,
        left:barLeft,
        width:barW,
        textAlign:"center",
        opacity:valueOp,
        fontFamily:SYNE,fontSize:valueFontSize,fontWeight:"700",
        color:"#FFFFFF",letterSpacing:"-0.02em",
      }}>
        {bar.value}{unit}
      </div>
      {/* Label */}
      <div style={{
        position:"absolute",
        top:CHART_BOT+18,
        left:barLeft,
        width:barW,
        textAlign:"center",
        opacity:labelOp,
        fontFamily:MONTSERRAT,fontSize:labelFontSize,fontWeight:"600",
        color:"#FFFFFF55",letterSpacing:"0.02em",lineHeight:1.3,
      }}>
        {bar.label}
      </div>
    </>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const BarChart: React.FC<BarChartProps> = ({
  title, bars,
  unit          = "",
  accent_color  = "#00C8FF",
  duration_s    = 10,
  bg_color      = "#020218",
  seed          = 0,
}) => {
  const COLORS_S = seededShuffle(COLORS, seed);
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_DUR;

  const fadeIn    = interpolate(frame,[0,12],[0,1],{extrapolateRight:"clamp",easing:Easing.out(Easing.cubic)});
  const finalFade = interpolate(frame,[totalFrames-7,totalFrames],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  const maxVal = Math.max(...bars.map(b => b.value));
  const barW   = (CHART_R - CHART_L) / bars.length;
  const enterBase = 16;

  const maxLabelLen = Math.max(...bars.map(b => b.label.length));
  const nb = bars.length;
  const labelFontSize =
    nb >= 7 ? (maxLabelLen > 7 ? 9  : 11) :
    nb >= 5 ? (maxLabelLen > 7 ? 10 : 12) : 13;
  const valueFontSize = nb >= 7 ? 18 : nb >= 5 ? 21 : 24;

  // Title
  const spr     = spring({frame, fps, config:{damping:28,stiffness:220}});
  const titleY  = interpolate(spr,[0,1],[-18,0]);
  const titleOp = interpolate(frame,[0,10],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart+4,exitStart+20],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const lineW   = interpolate(frame,[4,26],[0,100],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.out(Easing.exp)});

  // Baseline opacity
  const baseOp = interpolate(frame,[8,24],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart,exitStart+14],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  return (
    <AbsoluteFill style={{background:bg_color,overflow:"hidden"}}>
      <div style={{opacity:fadeIn*finalFade,width:"100%",height:"100%"}}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <GridOverlay frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />
        <ChartGridLines frame={frame} exitStart={exitStart} accent={accent_color} />

        {/* Baseline */}
        <div style={{position:"absolute",top:CHART_BOT,left:CHART_L,right:1920-CHART_R,height:1.5,background:`linear-gradient(to right, transparent, ${accent_color}66, transparent)`,opacity:baseOp}} />

        {/* Title */}
        <div style={{position:"absolute",top:72,left:CHART_L,opacity:titleOp,transform:`translateY(${titleY}px)`,display:"flex",flexDirection:"column",gap:10}}>
          <div style={{fontFamily:SYNE,fontSize:11,fontWeight:"800",letterSpacing:"0.44em",textTransform:"uppercase",color:"#FFFFFF55"}}>{title}</div>
          <div style={{width:`${lineW}%`,maxWidth:180,height:1,background:`linear-gradient(to right, ${accent_color}88, transparent)`}} />
        </div>

        {/* Bars */}
        {bars.map((bar,i) => (
          <Bar key={i} bar={bar} index={i}
            enterFrame={enterBase + i*BAR_STEP}
            frame={frame} exitStart={exitStart}
            maxVal={maxVal} accent={accent_color} unit={unit}
            barW={barW} barLeft={CHART_L + i*barW}
            colors={COLORS_S}
            valueFontSize={valueFontSize} labelFontSize={labelFontSize}
          />
        ))}

        {/* SFX */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.18} />
        </Sequence>
        {bars.map((_,i) => (
          <Sequence key={i} from={enterBase + i*BAR_STEP + 36} durationInFrames={20}>
            <Audio src={staticFile("sfx/ping.wav")} volume={Math.max(0.04, 0.10 - i*0.008)} />
          </Sequence>
        ))}
        <Sequence from={exitStart+10} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.18} />
        </Sequence>
      </div>
    </AbsoluteFill>
  );
};
