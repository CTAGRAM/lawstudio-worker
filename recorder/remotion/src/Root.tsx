import { Composition } from "remotion";
import { GoLegalDemo, calculateGoLegalDemoMetadata } from "./GoLegalDemo";

// Single composition: the polished centre-frame explainer. Per-run assets are
// written into public/run/ by the pipeline; brand cards live in public/brand/.
export const RemotionRoot: React.FC = () => (
  <Composition
    id="GoLegalDemo"
    component={GoLegalDemo}
    durationInFrames={30 * 60}
    fps={30}
    width={1920}
    height={1080}
    calculateMetadata={calculateGoLegalDemoMetadata}
    defaultProps={{
      videoSrc: "run/body.mp4",
      voSrc: "run/vo.wav",
      wordsSrc: "run/words.json",
      focusSrc: "run/focus.json",
      logoSrc: "brand/logo.png",
      introVideoSrc: "brand/intro.mp4",
      outroVideoSrc: "brand/outro.mp4",
      tagline: "Ask, draft & review legal docs in minutes",
      cta: "Try Go Legal AI Free",
      accent: "#6C5CE7",
      accent2: "#B98CFF",
      words: [],
      focus: [],
      introSeconds: 3.0,
      outroSeconds: 3.0,
    }}
  />
);
