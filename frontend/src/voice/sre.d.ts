// Speech Rule Engine's browser bundle (a UMD module), which ships no types of its own.
declare module "speech-rule-engine/lib/sre.js" {
  const SRE: {
    setupEngine(feature: Record<string, unknown>): Promise<void>;
    engineReady(): Promise<unknown>;
    toSpeech(mathml: string): string;
  };
  export default SRE;
}
