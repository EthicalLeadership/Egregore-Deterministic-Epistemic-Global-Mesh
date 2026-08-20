import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  base: "./",
  plugins: [react()],
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
      // Live projection plane — cells, factory, nodes health come from the
      // bootstrap HTTPS (8443) with self-signed certs.
      "/api/v1/ombudsman": {
        target: "https://localhost:8443",
        changeOrigin: true,
        secure: false,
      },
      "/api/v1/factory": {
        target: "https://localhost:8443",
        changeOrigin: true,
        secure: false,
      },
      "/health": {
        target: "https://localhost:8443",
        changeOrigin: true,
        secure: false,
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
