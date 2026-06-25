import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Toutes les routes de l'API FastAPI sont proxifiées vers le backend
// en développement (le frontend tourne sur :5173, l'API sur :8000).
const cibleAPI = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 800,
  },
  server: {
    proxy: {
      "/auth": { target: cibleAPI, changeOrigin: true },
      "/chat": { target: cibleAPI, changeOrigin: true },
      "/documents": { target: cibleAPI, changeOrigin: true },
      "/history": { target: cibleAPI, changeOrigin: true },
      "/admin": { target: cibleAPI, changeOrigin: true },
      "/health": { target: cibleAPI, changeOrigin: true },
    },
  },
});