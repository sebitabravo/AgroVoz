// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';

// Configuracion AgroVoz landing: SSG estatico, dominio agrovoz.cl.
// Tailwind 4 via plugin de Vite (no usa @astrojs/tailwind, que es v3).
// https://astro.build/config
export default defineConfig({
  // Astro 7 cambia el default de compressHTML a 'jsx' (quita whitespace entre inline
  // elements, estilo React). Para el landing institucional preservamos el comportamiento de
  // v5/v6 (HTML-aware) y evitar regresiones de spacing en titulos y botones.
  compressHTML: true,
  site: 'https://agrovoz.cl',
  output: 'static',
  vite: {
    plugins: [tailwindcss()],
  },
});
