import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  base: "./",
  server: { port: 1420, strictPort: true },
  build: {
    outDir: mode === "browser" ? "../src/bat_agent_connector/dashboard" : "dist",
    emptyOutDir: true,
    minify: false,
    cssMinify: false,
    modulePreload: false,
    rollupOptions: {
      output: {
        codeSplitting: false,
        comments: { legal: true, annotation: false, jsdoc: false },
        entryFileNames: "app.js",
        assetFileNames: "app[extname]",
        banner: "// Generated from desktop/src. Run: cd desktop && npm ci && npm run build:browser. Do not edit."
      }
    }
  }
}));
