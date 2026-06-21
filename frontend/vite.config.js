import { defineConfig } from 'vite'

export default defineConfig({
  base: '/voyx-main/',  // ← ИСПРАВЛЕНО: '/' вместо '/voyx-main/'
  server: {
    port: 3000,
    host: '0.0.0.0',
    watch: {
      usePolling: true
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})