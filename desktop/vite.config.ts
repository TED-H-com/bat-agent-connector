import { defineConfig } from "vite";
import {fileURLToPath} from "node:url";
import {frontendDigest} from "./scripts/frontend-build.mjs";

export default defineConfig(({ mode }) => ({
  base: "./",
  // No Git SHA or BATC_BUILD_COMMIT: committed browser assets must reproduce.
  define: {__BATC_FRONTEND_DIGEST__: JSON.stringify(frontendDigest(fileURLToPath(new URL('.', import.meta.url))))},
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
