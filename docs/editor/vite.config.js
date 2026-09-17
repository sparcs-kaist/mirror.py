import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  base: './',
  build: {
    outDir: '../_static/config-editor',
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: fileURLToPath(new URL('./src/editor.js', import.meta.url)),
      output: {
        entryFileNames: 'editor.js',
        chunkFileNames: 'assets/[name]-[hash].js',
        assetFileNames: (asset) => asset.names?.some((name) => name.endsWith('.css'))
          ? 'editor.css' : 'assets/[name]-[hash][extname]',
      },
    },
  },
  worker: { format: 'es' },
});
