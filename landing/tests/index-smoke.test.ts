import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";
import IndexPage from "../src/pages/index.astro";

const PAGES = [
  "src/pages/index.astro",
  "src/pages/privacidad.astro",
  "src/pages/demo.astro",
  "src/components/Header.astro",
  "src/components/Footer.astro",
  "src/components/Hero.astro",
  "src/components/PlanCard.astro",
  "src/components/ContactForm.astro",
  "src/components/DemoPhone.astro",
];

describe("landing principal (index)", () => {
  test("la página comunica la propuesta, los costos y las cifras sin fechas falsas", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(IndexPage);
    const source = readFileSync(resolve(process.cwd(), "src/pages/index.astro"), "utf8");

    // Las 10 secciones de la página y sus anclas de navegación.
    for (const id of [
      "top",
      "problema",
      "como",
      "demo",
      "ecosistema",
      "stack",
      "planes",
      "impacto",
      "equipo",
      "contacto",
    ]) {
      expect(result, `falta la sección id="${id}"`).toContain(`id="${id}"`);
    }
    expect(result).toContain('id="demo-track"');

    // El hero declara la propuesta y el objetivo de latencia; "<15 s" se
    // escapa como "&lt;15 s" en el render, así que se aserta "(objetivo)".
    expect(result).toContain("Probar la demo");
    expect(result).toContain("Precios ODEPA");
    expect(result).toContain("(objetivo)");
    expect(result).toContain("WhatsApp en piloto");
    expect(result).toContain('href="/demo"');

    // Costos explícitos y población objetivo acotada a una estimación interna.
    expect(source).toContain("CLP 14.364/mes");
    expect(source).toContain("costo base de operación");
    expect(source).toContain("count: 265000");
    expect(result).toContain("265.000");
    expect(source).toContain("Cruce INDAP–SII, abril 2026 · estimación interna por validar");
    expect(source).toContain("Censo Agropecuario 2021 · cifra por validar con fuente primaria versionada");
    expect(source).not.toContain("97%");
    expect(source).not.toContain("CLP 0");

    // CTA de la sección demo hacia la página interactiva.
    expect(source).toContain('data-i18n="demo.cta"');
    expect(source).toContain("Probar la demo interactiva");

    // Ninguna página ni componente de la landing debe cargar una fecha
    // hardcodeada "dd/mm/2026": la actualidad la aporta el backend.
    for (const file of PAGES) {
      const content = readFileSync(resolve(process.cwd(), file), "utf8");
      expect(content, `${file} contiene una fecha hardcodeada`).not.toMatch(/\d{2}\/\d{2}\/2026/);
    }
  });
});
