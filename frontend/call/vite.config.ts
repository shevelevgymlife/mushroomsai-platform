import path from "path";
import { fileURLToPath } from "url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  base: "/static/call/",
  build: {
    outDir: path.resolve(__dirname, "../../static/call"),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: path.resolve(__dirname, "index.html"),
      output: {
        entryFileNames: "assets/call.js",
        chunkFileNames: "assets/chunk-[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
