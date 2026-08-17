import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { inspectAttr } from "kimi-plugin-inspect-react"

// https://vite.dev/config/
export default defineConfig({
  base: "./",
  plugins: [inspectAttr(), react()],
  server: {
    port: 5173,
    proxy: {
      // ANCHORUM endpoints are served by the ANCHORUM plain-HTTP site
      // (anchorum_http) on :8080. Match before the generic /api proxy.
      "/api/v1/anchorum/fs": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/api/v1/anchorum/imap": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/api/v1/anchorum/tools": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/api/v1/anchorum/cases": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/api": {
        target: "http://localhost:3001",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:3000",
        ws: true,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "dist",
  },
})
