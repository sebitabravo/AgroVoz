// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';

// Configuracion AgroVoz landing: SSG estatico, dominio agrovoz.cl.
// Tailwind 4 via plugin de Vite (no usa @astrojs/tailwind, que es v3).
// https://astro.build/config
export default defineConfig({
  site: 'https://agrovoz.cl',
  output: 'static',
  vite: {
    plugins: [tailwindcss()],
  },
});
