import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcI18nPath = path.resolve(__dirname, "../../src/i18n.js");
const protoI18nPath = path.resolve(__dirname, "./proto-i18n.js");

export default defineConfig({
  root: __dirname,
  base: "./",
  resolve: {
    alias: [
      {
        find: srcI18nPath,
        replacement: protoI18nPath,
      },
      {
        find: "../i18n.js",
        replacement: protoI18nPath,
      },
    ],
  },
  build: {
    outDir: path.resolve(__dirname, "../../dist-prototype/v1.3"),
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: false,
    rollupOptions: {
      input: path.resolve(__dirname, "index.html"),
      output: {
        entryFileNames: "app.js",
        chunkFileNames: "[name].js",
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith(".css")) {
            return "app.css";
          }
          return "[name][extname]";
        },
      },
    },
  },
});
