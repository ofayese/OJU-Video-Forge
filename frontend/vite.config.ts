import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Local versioned API proxy; VITE_API_BASE_URL can override it.
      "/v1": "http://127.0.0.1:8000",
    },
  },
});
