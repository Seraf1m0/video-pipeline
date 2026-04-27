import React from "react";
import { Composition } from "remotion";
import { SolarParadox } from "./animations/SolarParadox";
import { DataCard, DataCardProps }             from "./compositions/DataCard";
import { Statement, StatementProps, StatementStyle } from "./compositions/Statement";
import { Timeline, TimelineProps }             from "./compositions/Timeline";
import { Comparison, ComparisonProps }         from "./compositions/Comparison";
import { Highlight, HighlightProps }           from "./compositions/Highlight";
import { List, ListProps }                     from "./compositions/List";
import { BarChart, BarChartProps }             from "./compositions/BarChart";
import { RadialChart, RadialChartProps }       from "./compositions/RadialChart";
import { LineChart, LineChartProps }           from "./compositions/LineChart";
import { ChapterTitle, ChapterTitleProps }     from "./compositions/ChapterTitle";
import { DateStamp, DateStampProps }           from "./compositions/DateStamp";
import { Quote, QuoteProps }                   from "./compositions/Quote";
import { BigNumber, BigNumberProps }           from "./compositions/BigNumber";
import { Scorecard, ScorecardProps }           from "./compositions/Scorecard";
import { Terminal, TerminalProps }             from "./compositions/Terminal";
import { ProCon, ProConProps }                 from "./compositions/ProCon";
import { Ranking, RankingProps }               from "./compositions/Ranking";
import { DateLabel, DateLabelProps }           from "./compositions/DateLabel";
import { DateBookmark, DateBookmarkProps }     from "./compositions/DateBookmark";
import { HBarChart, HBarChartProps }           from "./compositions/HBarChart";
import { MultiRing, MultiRingProps }           from "./compositions/MultiRing";

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

      {/*
        ChapterTitle — topic/chapter transition with chapter label, bold title, optional sub
        Props: { chapter, title, sub?, accent_color?, duration_s? = 8, bg_color?, seed? }
      */}
      <Composition
        id="ChapterTitle"
        component={ChapterTitle}
        durationInFrames={200}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          chapter: "Chapter I",
          title: "The Beginning",
          sub: "How it all started",
          accent_color: "#00C8FF",
          duration_s: 8,
        } satisfies ChapterTitleProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 8) * FPS),
        })}
      />

      {/*
        DateStamp — historical date badge with huge date, event text, newspaper lines
        Props: { date, event, sub?, accent_color?, duration_s? = 8, bg_color?, seed? }
      */}
      <Composition
        id="DateStamp"
        component={DateStamp}
        durationInFrames={200}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          date: "July 20, 1969",
          event: "First Moon Landing",
          sub: "Apollo 11 mission",
          accent_color: "#FFD700",
          duration_s: 8,
        } satisfies DateStampProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 8) * FPS),
        })}
      />

      {/*
        Quote — citation with decorative quote mark, author, source
        Props: { text, author?, source?, accent_color?, duration_s? = 9, bg_color?, seed? }
      */}
      <Composition
        id="Quote"
        component={Quote}
        durationInFrames={225}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          text: "That's one small step for man, one giant leap for mankind.",
          author: "Neil Armstrong",
          source: "Apollo 11, 1969",
          accent_color: "#A855F7",
          duration_s: 9,
        } satisfies QuoteProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 9) * FPS),
        })}
      />

      {/*
        BigNumber — stat with value, unit, description, context — different from Highlight
        Props: { value, unit, description, context?, accent_color?, duration_s? = 8, bg_color?, seed? }
      */}
      <Composition
        id="BigNumber"
        component={BigNumber}
        durationInFrames={200}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          value: "384",
          unit: "Mio. km",
          description: "Entfernung zum Mond",
          context: "Gemessen vom Erdmittelpunkt",
          accent_color: "#00C8FF",
          duration_s: 8,
        } satisfies BigNumberProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 8) * FPS),
        })}
      />

      {/*
        Scorecard — grid of 3-6 metric cards with staggered appearance
        Props: { title, metrics: [{label, value, unit?, color?}], accent_color?, duration_s? = 10, bg_color?, seed? }
      */}
      <Composition
        id="Scorecard"
        component={Scorecard}
        durationInFrames={250}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Mission Stats",
          metrics: [
            { label: "Distanz", value: "384", unit: "Mio. km", color: "#00C8FF" },
            { label: "Dauer", value: "8", unit: "Tage", color: "#FF6B35" },
            { label: "Besatzung", value: "3", unit: "Mann", color: "#4DFFB4" },
            { label: "Geschwindigkeit", value: "39.000", unit: "km/h", color: "#A855F7" },
          ],
          accent_color: "#00C8FF",
          duration_s: 10,
        } satisfies ScorecardProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 10) * FPS),
        })}
      />

      {/*
        Terminal — console-style text reveal with typing animation
        Props: { lines, title?, accent_color? = "#00FF88", duration_s? = 10, bg_color?, seed? }
      */}
      <Composition
        id="Terminal"
        component={Terminal}
        durationInFrames={250}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "mission-log.sh",
          lines: [
            "Initiating launch sequence...",
            "All systems nominal",
            "T-minus 10 seconds",
            "Main engine start",
            "We have liftoff.",
          ],
          accent_color: "#00FF88",
          duration_s: 10,
        } satisfies TerminalProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 10) * FPS),
        })}
      />

      {/*
        ProCon — two-column pros/cons with staggered item appearance
        Props: { title?, pros, cons, pro_color?, con_color?, duration_s? = 10, bg_color?, seed? }
      */}
      <Composition
        id="ProCon"
        component={ProCon}
        durationInFrames={250}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Raumfahrt — Eine Abwägung",
          pros: ["Wissenschaftlicher Fortschritt", "Technologische Innovation", "Internationaler Ruhm"],
          cons: ["Extreme Kosten", "Lebensrisiko", "Ressourcenaufwand"],
          pro_color: "#00FF88",
          con_color: "#FF4444",
          duration_s: 10,
        } satisfies ProConProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 10) * FPS),
        })}
      />

      {/*
        Ranking — animated top-N list, rank #1 gets gold treatment
        Props: { title, items: [{rank?, label, value?, color?}], accent_color?, duration_s? = 12, bg_color?, seed? }
      */}
      <Composition
        id="Ranking"
        component={Ranking}
        durationInFrames={300}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Größte Weltraummissionen",
          items: [
            { label: "Apollo 11", value: "1969" },
            { label: "Voyager 1", value: "1977" },
            { label: "Hubble Teleskop", value: "1990" },
            { label: "Mars Curiosity", value: "2012" },
            { label: "James Webb", value: "2021" },
          ],
          accent_color: "#00C8FF",
          duration_s: 12,
        } satisfies RankingProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 12) * FPS),
        })}
      />
      {/*
        DateLabel — small transparent overlay badge with date, flies in from side
        Props: { date, label?, accent_color?, duration_s? = 6, side? = "left" }
        Transparent bg — meant to overlay on video clips.
      */}
      <Composition
        id="HBarChart"
        component={HBarChart}
        durationInFrames={250}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Mission Comparison",
          bars: [
            { label: "Apollo 11",    value: 87,  color: "#FFD700", unit: "pts" },
            { label: "Voyager 1",    value: 72,  color: "#00C8FF", unit: "pts" },
            { label: "Mars Rover",   value: 65,  color: "#FF6B35", unit: "pts" },
            { label: "Hubble",       value: 91,  color: "#A855F7", unit: "pts" },
          ],
          accent_color: "#00C8FF",
          duration_s: 10,
        } satisfies HBarChartProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 10) * FPS),
        })}
      />

      <Composition
        id="MultiRing"
        component={MultiRing}
        durationInFrames={300}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          title: "Composition",
          rings: [
            { label: "Hydrogen",  value: 73, suffix: "%", color: "#00C8FF" },
            { label: "Helium",    value: 25, suffix: "%", color: "#A855F7" },
            { label: "Oxygen",    value: 1,  suffix: "%", color: "#FF6B35" },
            { label: "Carbon",    value: 0.5,suffix: "%", color: "#4DFFB4" },
          ],
          accent_color: "#A855F7",
          duration_s: 12,
        } satisfies MultiRingProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 12) * FPS),
        })}
      />

      <Composition
        id="DateLabel"
        component={DateLabel}
        durationInFrames={150}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          date: "July 20, 1969",
          label: "Moon Landing",
          accent_color: "#FFD700",
          duration_s: 6,
          side: "left",
        } satisfies DateLabelProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 6) * FPS),
        })}
      />
      {/*
        DateBookmark — small transparent bookmark-shaped overlay, flies from bottom-left
        Props: { date, label?, accent_color?, duration_s? = 6 }
        Transparent bg — overlays on video clips.
      */}
      <Composition
        id="DateBookmark"
        component={DateBookmark}
        durationInFrames={150}
        fps={FPS}
        width={1920}
        height={1080}
        defaultProps={{
          date: "October 2025",
          label: "Live Event",
          color_start: "#00C8FF",
          color_end:   "#A855F7",
          duration_s: 6,
        } satisfies DateBookmarkProps}
        calculateMetadata={({ props }) => ({
          durationInFrames: Math.round((props.duration_s ?? 6) * FPS),
        })}
      />
    </>
  );
};
