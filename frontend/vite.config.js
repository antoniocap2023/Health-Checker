import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Standard Vite + React setup. `npm run dev` serves the app on http://localhost:5173.
//
// The `server.proxy` block makes Vite's dev server act as a reverse proxy during
// local development: any request the app makes to a path starting with /api is
// forwarded to the backend at http://localhost:8000. This is the local-dev twin of
// what nginx does inside the frontend container (see frontend/nginx.conf), so the
// app's relative API URL ("/api/chat") works identically in both places.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
