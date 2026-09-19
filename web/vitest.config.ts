import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
  test: {
    environment: 'jsdom',
    exclude: ['tests/e2e/**', 'node_modules/**', 'dist/**'],
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    server: {
      deps: {
        inline: ['vuetify'],
      },
    },
  },
})
