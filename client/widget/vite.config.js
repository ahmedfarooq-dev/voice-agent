import { defineConfig } from "vite";

// Builds one self-contained file, dist/voice-widget.js, that a website includes with a
// single <script> tag. IIFE format so it needs no module loader on the host page.
export default defineConfig({
  build: {
    lib: {
      entry: "src/widget.js",
      name: "VoiceWidget",
      formats: ["iife"],
      fileName: () => "voice-widget.js",
    },
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
