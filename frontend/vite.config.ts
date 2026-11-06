import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Local API proxy is convenient in development; VITE_API_BASE_URL can override it.
      "/auth": "http://127.0.0.1:8000",
      "/projects": "http://127.0.0.1:8000",
      "/v1": "http://127.0.0.1:8000",
    },
  },
});
