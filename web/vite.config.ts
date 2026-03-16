import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  base: '/static/',
  plugins: [react()],
  server: {
    port: 8888,
    proxy: {
      '/api': {
        target: 'http://localhost:8300',
        changeOrigin: true,
      },
      '/api/session-token': {
        target: 'http://localhost:8300',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8300',
        ws: true,
        changeOrigin: true,
      },
      '/uploads': {
        target: 'http://localhost:8300',
        changeOrigin: true,
      },
    }
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
  }
})
