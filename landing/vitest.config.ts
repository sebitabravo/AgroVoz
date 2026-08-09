/// <reference types="vitest/config" />
import { getViteConfig } from "astro/config";

// getViteConfig() carga el plugin de Astro (parsea .astro) dentro de Vitest.
// Astro v6+ exige entorno 'node' para renderizar componentes con la Container API:
// https://docs.astro.build/en/guides/upgrade-to/v6/
export default getViteConfig({
  test: {
    environment: "node",
  },
});
