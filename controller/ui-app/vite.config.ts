import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  root: "ui-app",
  plugins: [react()],
  build: { outDir: "../ui", emptyOutDir: true },
});
