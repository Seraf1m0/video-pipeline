import React from "react";
import { Composition } from "remotion";
import { SolarParadox } from "./animations/SolarParadox";
import { DataCard, DataCardProps }       from "./compositions/DataCard";
import { Statement, StatementProps, StatementStyle }     from "./compositions/Statement";
import { Timeline, TimelineProps }       from "./compositions/Timeline";
import { Comparison, ComparisonProps }   from "./compositions/Comparison";
import { Highlight, HighlightProps }     from "./compositions/Highlight";
import { List, ListProps }               from "./compositions/List";
import { BarChart, BarChartProps }       from "./compositions/BarChart";
import { RadialChart, RadialChartProps } from "./compositions/RadialChart";
import { LineChart, LineChartProps }     from "./compositions/LineChart";

const FPS = 25;

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/*
        SolarParadox — topic-specific: solar corona paradox (DE)
        10s, 30fps, 300 frames, 1920×1080
      */}
      <Composition
        id="SolarParadox"
        component={SolarParadox}
        durationInFrames={300}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{}}
      />

      {/*
        DataCard — generic data display: 1-3 items with labels + animated values
        Props: { title, items: [{label, value, color?, numeric?, suffix?}], duration_s? }
        Default: 8s. Duration driven by calculateMetadata → duration_s prop.
      */}
      <Composition
        id="DataCard"
        component={DataCard}
        durationInFrames={240}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Fakten",
          items: [{ label: "Wert", value: "42", color: "#00D4FF" }],
          duration_s: 8,
        } satisfies DataCardProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 8) * FPS),
        })}
      />

      {/*
        Statement — cinematic text reveal for key thesis / push moments
        Props: { text, highlight?, sub?, accent_color?, duration_s? }
        Default: 6s. Duration driven by calculateMetadata → duration_s prop.
      */}
      <Composition
        id="Statement"
        component={Statement}
        durationInFrames={180}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          text: "Das ist der entscheidende Moment.",
          duration_s: 6,
        } satisfies StatementProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 6) * FPS),
        })}
      />

      {/*
        Timeline — numbered steps with connector lines + descriptions
        Props: { title, steps: [{label, desc?, color?}], duration_s? }
        Default: 10s. Duration driven by calculateMetadata → duration_s prop.
      */}
      <Composition
        id="Timeline"
        component={Timeline}
        durationInFrames={300}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Der Ablauf",
          steps: [
            { label: "Schritt 1", desc: "Erster Schritt" },
            { label: "Schritt 2", desc: "Zweiter Schritt" },
            { label: "Schritt 3", desc: "Dritter Schritt" },
          ],
          duration_s: 10,
        } satisfies TimelineProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 10) * FPS),
        })}
      />

      {/*
        Comparison — VS layout: two values crash in from opposite sides
        Props: { title?, left, right, left_color?, right_color?, duration_s? }
        Default: 8s. Duration driven by calculateMetadata → duration_s prop.
      */}
      <Composition
        id="Comparison"
        component={Comparison}
        durationInFrames={240}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Vergleich",
          left:  { label: "Damals", value: "10×", sub: "früher" },
          right: { label: "Heute",  value: "1×",  sub: "jetzt"  },
          duration_s: 8,
        } satisfies ComparisonProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 8) * FPS),
        })}
      />

      {/*
        Highlight — single massive fact / number, most dramatic of all
        Props: { value, label, sub?, accent_color?, duration_s? }
        Default: 7s. Duration driven by calculateMetadata → duration_s prop.
      */}
      <Composition
        id="Highlight"
        component={Highlight}
        durationInFrames={210}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          value: "15 Mio.",
          label: "Grad Celsius",
          sub: "Temperatur im Kern der Sonne",
          accent_color: "#FF6B35",
          duration_s: 7,
        } satisfies HighlightProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 7) * FPS),
        })}
      />
      {/*
        List — animated bullet list, 2-5 items sliding in one by one
        Props: { title, items: [{text, sub?}], accent_color?, duration_s? }
        Default: 10s.
      */}
      <Composition
        id="List"
        component={List}
        durationInFrames={300}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Fakten",
          items: [
            { text: "Erster Fakt", sub: "Details hier" },
            { text: "Zweiter Fakt" },
            { text: "Dritter Fakt" },
          ],
          accent_color: "#00C8FF",
          duration_s: 10,
        } satisfies ListProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 10) * FPS),
        })}
      />
      {/*
        BarChart — vertical bar histogram, up to 8 bars
        Props: { title, bars: [{label, value, color?}], unit?, accent_color?, duration_s? }
        Default: 10s.
      */}
      <Composition
        id="BarChart"
        component={BarChart}
        durationInFrames={300}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Vergleich",
          bars: [
            { label: "2000", value: 340 },
            { label: "2010", value: 520 },
            { label: "2020", value: 890 },
            { label: "2024", value: 1200 },
          ],
          unit: "",
          accent_color: "#00C8FF",
          duration_s: 10,
        } satisfies BarChartProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 10) * FPS),
        })}
      />

      {/*
        RadialChart — animated donut/ring chart with legend
        Props: { title, segments: [{label, value, color?}], center_text?, accent_color?, duration_s? }
        Default: 10s. Values are normalised to percentages automatically.
      */}
      <Composition
        id="RadialChart"
        component={RadialChart}
        durationInFrames={300}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Zusammensetzung",
          segments: [
            { label: "Wasserstoff", value: 73 },
            { label: "Helium",      value: 25 },
            { label: "Andere",      value:  2 },
          ],
          center_text: "73%",
          accent_color: "#FF9F00",
          duration_s: 10,
        } satisfies RadialChartProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 10) * FPS),
        })}
      />
      {/*
        LineChart — animated line with traveling ball, ups & downs
        Props: { title, points: [{label, value}], unit?, accent_color?, duration_s? }
        Default: 12s.
      */}
      <Composition
        id="LineChart"
        component={LineChart}
        durationInFrames={360}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Динамика",
          points: [
            { label: "2000", value: 340 },
            { label: "2005", value: 520 },
            { label: "2010", value: 410 },
            { label: "2015", value: 780 },
            { label: "2020", value: 650 },
            { label: "2024", value: 1200 },
          ],
          unit: "",
          accent_color: "#00C8FF",
          duration_s: 12,
        } satisfies LineChartProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 12) * FPS),
        })}
      />
      {/* ── Statement style variants (test) ────────────────────────── */}
      <Composition id="StatementGlitch" component={Statement} durationInFrames={175} fps={FPS} width={1920} height={1080}
        defaultProps={{ text: "Das Zentrum der Sonne ist 15 Millionen Grad heiß.", accent_color: "#00FFD0", duration_s: 7, style: "glitch" } satisfies StatementProps}
        calculateMetadata={({ props }) => ({ durationInFrames: Math.round((props.duration_s ?? 7) * FPS) })}
      />
      <Composition id="StatementCharReveal" component={Statement} durationInFrames={175} fps={FPS} width={1920} height={1080}
        defaultProps={{ text: "Das Zentrum der Sonne ist 15 Millionen Grad heiß.", accent_color: "#FF6B35", duration_s: 7, style: "char_reveal" } satisfies StatementProps}
        calculateMetadata={({ props }) => ({ durationInFrames: Math.round((props.duration_s ?? 7) * FPS) })}
      />
      <Composition id="StatementTypewriter" component={Statement} durationInFrames={175} fps={FPS} width={1920} height={1080}
        defaultProps={{ text: "Das Zentrum der Sonne ist 15 Millionen Grad heiß.", sub: "Temperatur im Sonnenkern", accent_color: "#A855F7", duration_s: 7, style: "typewriter" } satisfies StatementProps}
        calculateMetadata={({ props }) => ({ durationInFrames: Math.round((props.duration_s ?? 7) * FPS) })}
      />
    </>
  );
};
