import React from "react";
import {
  AbsoluteFill, Audio, interpolate, spring,
  staticFile, useCurrentFrame, useVideoConfig,
  Easing, Sequence,
} from "remotion";
import { loadFont as loadSyne    } from "@remotion/google-fonts/Syne";
import { loadFont as loadManrope } from "@remotion/google-fonts/Manrope";
import { noise2D } from "@remotion/noise";
import { seededShuffle } from "../utils/seeded";

const { fontFamily: SYNE    } = loadSyne();
const { fontFamily: MANROPE } = loadManrope();

export interface RadialSegment {
  label: string;
  value: number;
  color?: string;
}
export interface RadialChartProps {
  title: string;
  segments: RadialSegment[];
  center_text?: string;
  accent_color?: string;
  duration_s?: number;
  bg_color?: string;
  seed?: number;
}

const EXIT_DUR = 52;
const SEG_STEP = 22;

const COLORS = ["#00E5FF","#FF0099","#00FF88","#FFD600","#CC00FF","#FF4500"];

// SVG donut geometry
const SVG_SIZE = 960;
const CX = 480, CY = 480;
const R   = 250;
const SW  = 56;
const CIRC = 2 * Math.PI * R;
const LABEL_R = R + SW / 2 + 68;   // distance from center to label anchor
const LINE_R  = R + SW / 2 + 8;    // connector start (just outside ring)
const LINE_R2 = R + SW / 2 + 52;   // connector end (just before label)

// Screen position of SVG center
const SCR_CX = 1920 / 2;  // 960
const SCR_CY = 1080 / 2;  // 540
const SVG_L  = SCR_CX - CX;  // 960 - 480 = 480
const SVG_T  = SCR_CY - CY;  // 540 - 480 = 60

// ─── BG ──────────────────────────────────────────────────────────────────────
const Bg: React.FC<{frame:number;color:string;total:number}> = ({frame,color,total}) => {
  const inOp  = interpolate(frame,[0,32],[0,1],{extrapolateRight:"clamp"});
  const outOp = interpolate(frame,[total-EXIT_DUR,total-EXIT_DUR+22],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const op    = inOp * outOp;
  const s1 = 1 + Math.sin(frame*0.016)*0.09;
  const s2 = 1 + Math.sin(frame*0.021+1.5)*0.06;
  const nx = noise2D("rx",frame*0.0014,0)*5;
  const ny = noise2D("ry",0,frame*0.0014)*4;
  return (
    <AbsoluteFill>
      <div style={{position:"absolute",left:`${50+nx}%`,top:`${50+ny}%`,transform:`translate(-50%,-50%) scale(${s1})`,width:900,height:700,borderRadius:"50%",background:`radial-gradient(ellipse, ${color} 0%, transparent 62%)`,filter:"blur(120px)",opacity:op*0.14}} />
      <div style={{position:"absolute",left:`${50-nx*0.5}%`,top:`${50-ny*0.5}%`,transform:`translate(-50%,-50%) scale(${s2})`,width:450,height:450,borderRadius:"50%",background:color,filter:"blur(90px)",opacity:op*0.07}} />
      <div style={{position:"absolute",inset:0,background:"radial-gradient(ellipse 90% 85% at 50% 50%, transparent 22%, #000000D8 100%)"}} />
    </AbsoluteFill>
  );
};

// ─── GRID ─────────────────────────────────────────────────────────────────────
const GridOverlay: React.FC<{frame:number;color:string;total:number}> = ({frame,color,total}) => {
  const op = interpolate(frame,[8,28],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[total-EXIT_DUR,total-EXIT_DUR+16],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"}) * 0.05;
  return (
    <AbsoluteFill style={{opacity:op}}>
      {Array.from({length:9},(_,i)=>(
        <div key={i} style={{position:"absolute",left:`${(i/8)*100}%`,top:0,bottom:0,width:1,background:`linear-gradient(to bottom, transparent, ${color}44, transparent)`}} />
      ))}
    </AbsoluteFill>
  );
};

// ─── SCAN / EXIT SCAN ─────────────────────────────────────────────────────────
const ScanLine: React.FC<{frame:number;color:string}> = ({frame,color}) => {
  const y  = interpolate(frame,[0,20],[0,110],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.out(Easing.cubic)});
  const op = interpolate(frame,[0,3,16,24],[0,1,1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  return <div style={{position:"absolute",top:`${y}%`,left:0,right:0,height:2,background:`linear-gradient(to right, transparent, ${color}CC, transparent)`,boxShadow:`0 0 22px ${color}88`,opacity:op}} />;
};
const ExitScan: React.FC<{frame:number;total:number;color:string}> = ({frame,total,color}) => {
  const s   = total - EXIT_DUR + 12;
  const y   = interpolate(frame,[s,s+18],[110,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.in(Easing.cubic)});
  const op  = interpolate(frame,[s,s+3,s+14,s+22],[0,1,1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  return <div style={{position:"absolute",top:`${y}%`,left:0,right:0,height:2,background:`linear-gradient(to right, transparent, ${color}77, transparent)`,boxShadow:`0 0 16px ${color}44`,opacity:op}} />;
};

// ─── SEGMENT (arc + tip glow) ─────────────────────────────────────────────────
const Segment: React.FC<{
  pct:number; rotation:number; color:string;
  index:number; enterFrame:number; frame:number; exitStart:number;
}> = ({pct,rotation,color,index,enterFrame,frame,exitStart}) => {
  const finalArc = pct * CIRC;

  const growP = interpolate(frame,[enterFrame,enterFrame+60],[0,1],{
    extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.bezier(0.16,1,0.3,1),
  });
  const exitP = interpolate(frame,[exitStart,exitStart+30],[0,1],{
    extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.in(Easing.cubic),
  });

  const arcLen   = growP * finalArc * (1-exitP);
  if (arcLen <= 0.5) return null;

  // Tip glow dot position
  const tipAngleDeg = rotation + growP * pct * 360 * (1-exitP);
  const tipRad      = (tipAngleDeg * Math.PI) / 180;
  const tipX = CX + R * Math.cos(tipRad);
  const tipY = CY + R * Math.sin(tipRad);

  const tipGlow = interpolate(frame,[enterFrame,enterFrame+12],[0,1],{extrapolateRight:"clamp"}) * (1-exitP);

  return (
    <g>
      {/* Main arc */}
      <circle
        cx={CX} cy={CY} r={R}
        fill="none" stroke={color} strokeWidth={SW}
        strokeDasharray={`${arcLen} ${CIRC - arcLen}`}
        strokeLinecap="butt"
        transform={`rotate(${rotation}, ${CX}, ${CY})`}
      />
      {/* Bright tip dot — multiple concentric circles for glow, no CSS filter */}
      <circle cx={tipX} cy={tipY} r={22} fill={color} opacity={tipGlow * 0.08} />
      <circle cx={tipX} cy={tipY} r={14} fill={color} opacity={tipGlow * 0.18} />
      <circle cx={tipX} cy={tipY} r={8}  fill={color} opacity={tipGlow * 0.55} />
      <circle cx={tipX} cy={tipY} r={4}  fill="#FFFFFF" opacity={tipGlow * 0.90} />
    </g>
  );
};

// ─── EXPANSION RING (fires when all segments done) ────────────────────────────
const ExpansionRing: React.FC<{frame:number;fireFrame:number;color:string;delay:number}> = ({frame,fireFrame,color,delay}) => {
  const f = frame - fireFrame - delay;
  if (f < 0) return null;
  const r  = interpolate(f,[0,40],[R,R+120],{extrapolateRight:"clamp",easing:Easing.out(Easing.cubic)});
  const op = interpolate(f,[0,5,30,42],[0,0.6,0.3,0],{extrapolateRight:"clamp"});
  return (
    <circle cx={CX} cy={CY} r={r} fill="none"
      stroke={color} strokeWidth={2} opacity={op}
    />
  );
};

// ─── CONNECTOR LINE ───────────────────────────────────────────────────────────
const ConnectorLine: React.FC<{
  midAngleDeg:number; color:string;
  enterFrame:number; frame:number; exitStart:number;
}> = ({midAngleDeg,color,enterFrame,frame,exitStart}) => {
  const midRad = (midAngleDeg * Math.PI) / 180;
  const x1 = CX + LINE_R  * Math.cos(midRad);
  const y1 = CY + LINE_R  * Math.sin(midRad);
  const x2 = CX + LINE_R2 * Math.cos(midRad);
  const y2 = CY + LINE_R2 * Math.sin(midRad);

  const op = interpolate(frame,[enterFrame+30,enterFrame+44],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart,exitStart+18],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  return <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={1.5} opacity={op*0.7} />;
};

// ─── LABEL (HTML, positioned around donut) ────────────────────────────────────
const Label: React.FC<{
  label:string; pct:number; color:string; midAngleDeg:number;
  index:number; enterFrame:number; frame:number; exitStart:number;
}> = ({label,pct,color,midAngleDeg,index,enterFrame,frame,exitStart}) => {
  const midRad = (midAngleDeg * Math.PI) / 180;
  const lx = CX + LABEL_R * Math.cos(midRad);  // position in SVG space
  const ly = CY + LABEL_R * Math.sin(midRad);
  const screenX = SVG_L + lx;
  const screenY = SVG_T + ly;

  const isLeft = Math.cos(midRad) < -0.1;
  const align  = isLeft ? "right" : "left";
  const xOff   = isLeft ? -8 : 8;

  const slideP = interpolate(frame,[enterFrame+26,enterFrame+44],[0,1],{
    extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.out(Easing.exp),
  });
  const slideX = interpolate(slideP,[0,1],[isLeft ? 20 : -20, 0]);
  const op = interpolate(frame,[enterFrame+26,enterFrame+40],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart,exitStart+18],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  return (
    <div style={{
      position:"absolute",
      left:screenX + xOff,
      top:screenY,
      transform:`translate(${isLeft?"-100%":"0"}, -50%) translateX(${slideX}px)`,
      opacity:op,
      display:"flex",flexDirection:"column",gap:4,
      textAlign:align,
    }}>
      <div style={{fontFamily:SYNE,fontSize:26,fontWeight:"800",color:"#FFFFFF",letterSpacing:"-0.02em",lineHeight:1}}>
        {Math.round(pct*100)}<span style={{fontSize:18,color:color}}>%</span>
      </div>
      <div style={{fontFamily:MANROPE,fontSize:13,fontWeight:"500",color:"#FFFFFF55",letterSpacing:"0.02em"}}>
        {label}
      </div>
    </div>
  );
};

// ─── CENTER TEXT ──────────────────────────────────────────────────────────────
const CenterText: React.FC<{text:string;color:string;fireFrame:number;frame:number;exitStart:number}> = ({text,color,fireFrame,frame,exitStart}) => {
  const {fps} = useVideoConfig();
  const spr = spring({frame:frame-fireFrame, fps, config:{damping:7,stiffness:400,mass:1.1}});
  const scl = interpolate(spr,[0,1],[2.0,1]);
  const op  = interpolate(frame,[fireFrame,fireFrame+10],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart,exitStart+20],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const glow = 0.5 + Math.sin(frame*0.05)*0.5;
  return (
    <div style={{
      position:"absolute",
      left:SVG_L+CX, top:SVG_T+CY,
      transform:`translate(-50%,-50%) scale(${scl})`,
      opacity:op,
      textAlign:"center",
      display:"flex",flexDirection:"column",alignItems:"center",gap:6,
    }}>
      <div style={{
        fontFamily:SYNE,fontSize:64,fontWeight:"900",color:"#FFFFFF",
        letterSpacing:"-0.05em",lineHeight:1,
        textShadow:`0 0 ${40*glow}px ${color}88`,
      }}>
        {text}
      </div>
    </div>
  );
};

// ─── MAIN ─────────────────────────────────────────────────────────────────────
export const RadialChart: React.FC<RadialChartProps> = ({
  title, segments,
  center_text,
  accent_color  = "#00C8FF",
  duration_s    = 12,
  bg_color      = "#020218",
  seed          = 0,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const totalFrames = Math.round(duration_s * fps);
  const exitStart   = totalFrames - EXIT_DUR;

  const COLORS_S = seededShuffle(COLORS, seed);

  const fadeIn    = interpolate(frame,[0,12],[0,1],{extrapolateRight:"clamp",easing:Easing.out(Easing.cubic)});
  const finalFade = interpolate(frame,[totalFrames-7,totalFrames],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  const total = segments.reduce((s,seg)=>s+seg.value, 0);
  const segs  = segments.map((seg,i) => ({
    ...seg,
    pct:   seg.value / total,
    color: seg.color || COLORS_S[i % COLORS_S.length],
  }));

  let cumAngle = 0;
  const segWithRot = segs.map(seg => {
    const rotation  = -90 + cumAngle * 360;
    const midAngle  = rotation + seg.pct * 180;  // mid-angle of this segment
    cumAngle += seg.pct;
    return {...seg, rotation, midAngle};
  });

  const enterBase     = 20;
  const allDoneFrame  = enterBase + segs.length * SEG_STEP + 48;
  const centerFrame   = allDoneFrame + 4;

  // Title
  const titleSpr = spring({frame, fps, config:{damping:28,stiffness:220}});
  const titleY   = interpolate(titleSpr,[0,1],[-18,0]);
  const titleOp  = interpolate(frame,[0,10],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart+4,exitStart+20],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});
  const lineW    = interpolate(frame,[4,26],[0,100],{extrapolateLeft:"clamp",extrapolateRight:"clamp",easing:Easing.out(Easing.exp)});

  // Track ring
  const trackOp = interpolate(frame,[12,30],[0,1],{extrapolateRight:"clamp"})
    * interpolate(frame,[exitStart,exitStart+16],[1,0],{extrapolateLeft:"clamp",extrapolateRight:"clamp"});

  return (
    <AbsoluteFill style={{background:bg_color,overflow:"hidden"}}>
      <div style={{opacity:fadeIn*finalFade,width:"100%",height:"100%"}}>
        <Bg frame={frame} color={accent_color} total={totalFrames} />
        <GridOverlay frame={frame} color={accent_color} total={totalFrames} />
        <ScanLine frame={frame} color={accent_color} />
        <ExitScan frame={frame} total={totalFrames} color={accent_color} />

        {/* Title */}
        <div style={{position:"absolute",top:28,left:140,opacity:titleOp,transform:`translateY(${titleY}px)`,display:"flex",flexDirection:"column",gap:10}}>
          <div style={{fontFamily:SYNE,fontSize:11,fontWeight:"800",letterSpacing:"0.44em",textTransform:"uppercase",color:"#FFFFFF55"}}>{title}</div>
          <div style={{width:`${lineW}%`,maxWidth:180,height:1,background:`linear-gradient(to right, ${accent_color}88, transparent)`}} />
        </div>

        {/* SVG Donut */}
        <div style={{position:"absolute",left:SVG_L,top:SVG_T,width:SVG_SIZE,height:SVG_SIZE}}>
          <svg width={SVG_SIZE} height={SVG_SIZE} viewBox={`0 0 ${SVG_SIZE} ${SVG_SIZE}`}>
            {/* Track ring */}
            <circle cx={CX} cy={CY} r={R} fill="none" stroke="#FFFFFF06" strokeWidth={SW} opacity={trackOp} />

            {/* Segments + tip glows */}
            {segWithRot.map((seg,i) => (
              <Segment
                key={i} pct={seg.pct} rotation={seg.rotation} color={seg.color}
                index={i} enterFrame={enterBase + i*SEG_STEP}
                frame={frame} exitStart={exitStart}
              />
            ))}

            {/* Connector lines */}
            {segWithRot.map((seg,i) => (
              <ConnectorLine
                key={i} midAngleDeg={seg.midAngle} color={seg.color}
                enterFrame={enterBase + i*SEG_STEP} frame={frame} exitStart={exitStart}
              />
            ))}

            {/* Expansion rings on completion */}
            {[0,1,2].map(d => (
              <ExpansionRing key={d} frame={frame} fireFrame={allDoneFrame} color={accent_color} delay={d*8} />
            ))}
          </svg>
        </div>

        {/* Labels (HTML, positioned around donut) */}
        {segWithRot.map((seg,i) => (
          <Label
            key={i} label={seg.label} pct={seg.pct}
            color={seg.color} midAngleDeg={seg.midAngle}
            index={i} enterFrame={enterBase + i*SEG_STEP}
            frame={frame} exitStart={exitStart}
          />
        ))}

        {/* Center text */}
        {center_text && (
          <CenterText
            text={center_text} color={accent_color}
            fireFrame={centerFrame} frame={frame} exitStart={exitStart}
          />
        )}

        {/* SFX — clean: rise + whoosh_in + stinger at completion + whoosh_out */}
        <Sequence from={0} durationInFrames={20}>
          <Audio src={staticFile("sfx/rise.wav")} volume={0.20} />
        </Sequence>
        <Sequence from={8} durationInFrames={24}>
          <Audio src={staticFile("sfx/whoosh_in.wav")} volume={0.14} />
        </Sequence>
        {segs.map((_,i) => (
          <Sequence key={i} from={enterBase + i*SEG_STEP + 30} durationInFrames={20}>
            <Audio src={staticFile("sfx/ping.wav")} volume={0.07} />
          </Sequence>
        ))}
        <Sequence from={allDoneFrame} durationInFrames={28}>
          <Audio src={staticFile("sfx/stinger.wav")} volume={0.22} />
        </Sequence>
        <Sequence from={exitStart+10} durationInFrames={28}>
          <Audio src={staticFile("sfx/whoosh_out_3.wav")} volume={0.20} />
        </Sequence>
      </div>
    </AbsoluteFill>
  );
};
