import {
  AbsoluteFill,
  Audio,
  CalculateMetadataFunction,
  Img,
  OffthreadVideo,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { getVideoMetadata } from "@remotion/media-utils";
import { POPPINS } from "./localFonts";

// Go Legal AI "polished demo": a raw uploaded screen-recording, dressed as a
// premium product film in the BRAND's light + pink-gradient world. The recording
// sits at NATIVE resolution inside a centred, rounded browser window (so it stays
// crisp — no full-bleed upscaling), floating over an animated blush-pink / lavender
// background, with a separate AI voiceover and kinetic captions.

export interface Word { text: string; startMs: number; endMs: number }
export type Focus = [number, number, number];   // [zoom, focusXfrac, focusYfrac]
export interface GoLegalDemoProps {
  videoSrc: string;
  voSrc: string;
  wordsSrc?: string;
  focusSrc?: string;
  logoSrc: string;
  introVideoSrc?: string;   // branded intro card (the approved sample); falls back to TitleCard
  outroVideoSrc?: string;
  tagline: string;
  cta: string;
  accent: string;   // violet
  accent2: string;  // pink
  words?: Word[];
  focus?: Focus[];
  videoSeconds?: number;
  videoAspect?: number;
  hollow?: boolean;   // render the overlay only (magenta key for the recording)
  introSeconds?: number;
  outroSeconds?: number;
}

const HEAD = POPPINS;
const CAP_FONT = POPPINS;          // subtitles use the original brand font
const NAVY = "#171744";

function resolveAsset(src: string): string {
  if (src.startsWith("http") || src.startsWith("data:")) return src;
  const clean = src.replace(/^file:\/\/\/?/, "");
  if (clean.startsWith("/") || /^[A-Za-z]:[\\/]/.test(clean)) return `file:///${clean.replace(/\\/g, "/")}`;
  return staticFile(clean);
}

// ---- brand background: soft pink + lavender glows on white. Layered CSS
// radial-gradients (no blur filter / no per-frame animation) so it renders fast. ----
const Background: React.FC<{ accent: string; accent2: string }> = () => (
  <AbsoluteFill style={{
    background: [
      "radial-gradient(58% 52% at 22% 32%, rgba(247,199,221,0.60), transparent 70%)",
      "radial-gradient(64% 56% at 80% 63%, rgba(213,199,246,0.60), transparent 70%)",
      "radial-gradient(48% 44% at 62% 12%, rgba(251,216,230,0.55), transparent 70%)",
      "radial-gradient(50% 48% at 14% 84%, rgba(108,92,231,0.16), transparent 70%)",
      "radial-gradient(circle at 50% 42%, rgba(255,255,255,0.55), transparent 62%)",
      "linear-gradient(135deg,#FBF6FB 0%,#F2ECFB 52%,#FCEEF4 100%)",
    ].join(","),
  }} />
);

// ---- the recording, native-res, in a centred rounded browser window, with a
// Screen-Studio-style zoom that punches in on the action (from the focus track).
// In `hollow` mode the recording is replaced by a magenta key + the window is
// static — the pipeline composites the (ffmpeg-zoomed) recording behind it. That
// keeps this render fast (no per-frame video decode). ----
const KEY = "#FF00FF";   // chroma key for the hollow composite
const ScreenPanel: React.FC<{ videoSrc: string; focus: Focus[]; hollow?: boolean; videoAspect?: number }> = ({ videoSrc, focus, hollow, videoAspect }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const intro = hollow ? 1 : spring({ frame, fps, config: { damping: 24, stiffness: 85, mass: 1.1 } });
  const scale = interpolate(intro, [0, 1], [0.9, 1]);
  const float = hollow ? 0 : Math.sin(frame / 52) * 5;
  const rise = interpolate(intro, [0, 1], [46, 0]);
  const [z, fx, fy] = (focus.length ? focus[Math.min(Math.max(frame, 0), focus.length - 1)] : [1, 0.5, 0.5]) as Focus;
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{
        width: "76%", borderRadius: 18, overflow: "hidden", background: "#fff",
        transform: `translateY(${float + rise - 26}px) scale(${scale})`,
        boxShadow: "0 44px 110px rgba(94,74,180,0.30), 0 0 0 1px rgba(120,90,200,0.08)",
      }}>
        {/* browser chrome */}
        <div style={{ height: 46, background: "#F4F1FA", display: "flex", alignItems: "center", padding: "0 20px", gap: 10, borderBottom: "1px solid #EAE3F5" }}>
          <div style={{ width: 13, height: 13, borderRadius: 99, background: "#FF5F57" }} />
          <div style={{ width: 13, height: 13, borderRadius: 99, background: "#FEBC2E" }} />
          <div style={{ width: 13, height: 13, borderRadius: 99, background: "#28C840" }} />
          <div style={{ flex: 1, display: "flex", justifyContent: "center" }}>
            <div style={{ background: "#fff", border: "1px solid #E7DFF4", borderRadius: 99, padding: "6px 30px", fontFamily: HEAD, fontSize: 18, fontWeight: 600, color: "#8A7CB0" }}>go-legal.ai</div>
          </div>
        </div>
        {/* video viewport */}
        <div style={{ overflow: "hidden", lineHeight: 0 }}>
          {hollow ? (
            <div style={{ width: "100%", aspectRatio: String(videoAspect || 1800 / 1080), background: KEY }} />
          ) : (
            <OffthreadVideo src={resolveAsset(videoSrc)} style={{
              width: "100%", display: "block",
              transform: `scale(${z})`, transformOrigin: `${fx * 100}% ${fy * 100}%`,
            }} />
          )}
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ---- kinetic captions: navy pill, spoken word lights up pink ----
const Captions: React.FC<{ words: Word[]; accent2: string }> = ({ words, accent2 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const ms = (frame / fps) * 1000;
  const pages: Word[][] = [];
  let cur: Word[] = [];
  for (const w of words) {
    if (cur.length) {
      const gap = w.startMs - cur[cur.length - 1].endMs;
      const dur = w.endMs - cur[0].startMs;
      if (cur.length >= 4 || dur > 2200 || gap > 550) { pages.push(cur); cur = []; }
    }
    cur.push(w);
  }
  if (cur.length) pages.push(cur);
  const page = pages.find((p) => ms >= p[0].startMs - 120 && ms <= p[p.length - 1].endMs + 260);
  if (!page) return null;
  const inT = interpolate(ms, [page[0].startMs - 120, page[0].startMs + 60], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const scale = interpolate(inT, [0, 1], [0.86, 1]);
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: 66, textAlign: "center", opacity: inT, transform: `scale(${scale})` }}>
      <span style={{
        display: "inline-block", background: "#171744", borderRadius: 18, padding: "14px 32px",
        fontFamily: CAP_FONT, fontSize: 46, fontWeight: 800, lineHeight: 1.14, letterSpacing: -0.4,
        boxShadow: "0 16px 46px rgba(23,23,68,0.28)",
      }}>
        {page.map((w, i) => {
          const on = ms >= w.startMs && ms <= w.endMs + 40;
          return (
            <span key={i} style={{ color: on ? accent2 : "rgba(255,255,255,0.74)", margin: "0 7px", display: "inline-block", transform: on ? "translateY(-2px)" : "none" }}>
              {w.text.trim().toUpperCase()}
            </span>
          );
        })}
      </span>
    </div>
  );
};

// ---- intro / outro cards (light, brand logo) ----
const TitleCard: React.FC<{ logoSrc: string; tagline?: string; cta?: string; accent: string; out?: boolean }> = ({ logoSrc, tagline, cta, accent, out }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const inn = spring({ frame, fps, config: { damping: 20, stiffness: 90 } });
  const fade = interpolate(frame, [durationInFrames - 16, durationInFrames - 2], [1, 0], { extrapolateLeft: "clamp" });
  const y = interpolate(inn, [0, 1], [40, 0]);
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", opacity: Math.min(inn, fade) }}>
      <div style={{ transform: `translateY(${y}px)`, textAlign: "center" }}>
        <Img src={resolveAsset(logoSrc)} style={{ width: 640, display: "block", margin: "0 auto 40px" }} />
        {out ? (
          <>
            <div style={{ fontFamily: HEAD, fontSize: 96, fontWeight: 800, color: NAVY, letterSpacing: -2, lineHeight: 1 }}>{cta}</div>
            <div style={{ fontFamily: HEAD, fontSize: 42, fontWeight: 600, color: accent, marginTop: 20 }}>Sign up at go-legal.ai</div>
          </>
        ) : (
          <div style={{ fontFamily: HEAD, fontSize: 48, fontWeight: 600, color: "#5A5580" }}>{tagline}</div>
        )}
        <div style={{ width: 120, height: 6, borderRadius: 99, background: accent, margin: "34px auto 0", boxShadow: `0 0 22px ${accent}66` }} />
      </div>
    </AbsoluteFill>
  );
};

const FullVideo: React.FC<{ src: string }> = ({ src }) => (
  <AbsoluteFill style={{ backgroundColor: "#FBF6FB" }}>
    <OffthreadVideo src={resolveAsset(src)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
  </AbsoluteFill>
);

export const GoLegalDemo: React.FC<GoLegalDemoProps> = ({
  videoSrc, voSrc, logoSrc, introVideoSrc, outroVideoSrc, tagline, cta, accent, accent2, words = [], focus = [], videoSeconds, videoAspect, hollow, introSeconds = 2.6, outroSeconds = 3.2,
}) => {
  const { fps } = useVideoConfig();
  const introF = Math.round(introSeconds * fps);
  const outroF = Math.round(outroSeconds * fps);
  const bodyF = Math.round((videoSeconds || 30) * fps);
  return (
    <AbsoluteFill style={{ backgroundColor: "#FBF6FB" }}>
      <Background accent={accent} accent2={accent2} />
      <Sequence durationInFrames={introF}>
        {introVideoSrc ? <FullVideo src={introVideoSrc} /> : <TitleCard logoSrc={logoSrc} tagline={tagline} accent={accent} />}
      </Sequence>
      <Sequence from={introF} durationInFrames={bodyF}>
        <ScreenPanel videoSrc={videoSrc} focus={focus} hollow={hollow} videoAspect={videoAspect} />
        {!hollow && <Audio src={resolveAsset(voSrc)} />}
        <Captions words={words} accent2={accent2} />
      </Sequence>
      <Sequence from={introF + bodyF} durationInFrames={outroF}>
        {outroVideoSrc ? <FullVideo src={outroVideoSrc} /> : <TitleCard logoSrc={logoSrc} cta={cta} accent={accent} out />}
      </Sequence>
    </AbsoluteFill>
  );
};

export const calculateGoLegalDemoMetadata: CalculateMetadataFunction<GoLegalDemoProps> = async ({ props }) => {
  const fps = 30;
  const meta = await getVideoMetadata(resolveAsset(props.videoSrc));
  let words: Word[] = props.words || [];
  if (!words.length && props.wordsSrc) {
    try { words = (await (await fetch(resolveAsset(props.wordsSrc))).json()) as Word[]; } catch { words = []; }
  }
  let focus: Focus[] = props.focus || [];
  if (!focus.length && props.focusSrc) {
    try { focus = (await (await fetch(resolveAsset(props.focusSrc))).json()) as Focus[]; } catch { focus = []; }
  }
  const intro = props.introSeconds ?? 2.6;
  const outro = props.outroSeconds ?? 3.2;
  const durationInFrames = Math.round((intro + meta.durationInSeconds + outro) * fps);
  return { durationInFrames, fps, width: 1920, height: 1080, props: { ...props, words, focus, videoSeconds: meta.durationInSeconds, videoAspect: meta.width / meta.height } };
};
