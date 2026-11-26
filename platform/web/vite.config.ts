import { defineConfig } from "vitest/config";
// @ts-ignore Node's built-in type declarations are not part of the browser package.
import { Agent } from "node:http";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "PLATFORM_");
  const webHost = env.PLATFORM_WEB_HOST ?? "127.0.0.1";
  const webPort = Number(env.PLATFORM_WEB_PORT ?? 5173);
  const apiTarget = env.PLATFORM_API_PROXY_TARGET ?? "http://127.0.0.1:8000";
  // Reuse a small bounded set of upstream connections.  Disabling keep-alive
  // creates a new TCP socket for every polling request; with several browser
  // tabs that quickly fills Windows' TIME_WAIT table and produces EADDRINUSE.
  const apiProxyAgent = new Agent({
    keepAlive: true,
    maxSockets: 32,
    maxFreeSockets: 8,
    scheduling: "lifo",
    // Keep the proxy's client-side sockets on a separate loopback address so
    // stale 127.0.0.1 TIME_WAIT entries cannot starve the API connection pool.
    localAddress: "127.0.0.2",
  });
  const apiProxy = {
    "/api": {
      target: apiTarget,
      changeOrigin: false,
      agent: apiProxyAgent,
      timeout: 5_000,
      proxyTimeout: 10_000,
    },
  };

  return {
    plugins: [react()],
    server: {
      host: webHost,
      port: webPort,
      strictPort: true,
      proxy: apiProxy,
    },
    preview: {
      host: webHost,
      port: webPort,
      strictPort: true,
      proxy: apiProxy,
    },
  build: {
    chunkSizeWarningLimit: 950,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("echarts") || id.includes("zrender")) return "charts";
          if (id.includes("react") || id.includes("scheduler")) return "react";
          if (id.includes("lucide-react")) return "icons";
          return undefined;
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        url: "http://localhost/",
      },
    },
    setupFiles: "./src/test/setup.ts",
  },
  };
});
