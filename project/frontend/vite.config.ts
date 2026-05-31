import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Minimal Vite config for the first React MVP. Static SPA. The
// React app fetches /fixtures/k6_mtf_ranking.json as a static
// asset; the publish step that would point at a real served
// artifact URL is deferred per the React Migration Declaration's
// "publish step is deferred" wording. base "/" is the simplest
// dev / preview default; revisit at publish-step time.
export default defineConfig({
  plugins: [react()],
  base: "/",
});
