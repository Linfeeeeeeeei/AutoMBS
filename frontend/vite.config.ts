// vite.config.ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const TUNNEL_HOST = process.env.VITE_TUNNEL_HOST || 'roses-mongolia-phi-competent.trycloudflare.com';

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,          // bind 0.0.0.0
    port: 5173,
    allowedHosts: [
      'localhost',
      '127.0.0.1',
      TUNNEL_HOST,       // <-- allow your trycloudflare host
    ],
    // HMR over HTTPS tunnel (optional but recommended)
    hmr: {
      host: TUNNEL_HOST, // websocket host should match the public URL
      protocol: 'wss',   // tunnel is HTTPS, so use secure WS
      clientPort: 443,   // Cloudflare terminates TLS on 443
    },
  },
});
