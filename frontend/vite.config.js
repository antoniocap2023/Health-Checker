import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Standard Vite + React setup. `npm run dev` serves the app on http://localhost:5173
export default defineConfig({
  plugins: [react()],
});
