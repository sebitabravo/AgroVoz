import { readFileSync } from "node:fs";
import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { describe, expect, test } from "vitest";
import Footer from "../src/components/Footer.astro";
import PrivacyPage from "../src/pages/privacidad.astro";

describe("privacidad pública", () => {
  test("el footer enlaza a la ruta pública y no al repositorio", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(Footer);

    expect(result).toContain('href="/privacidad/"');
    expect(result).not.toMatch(/href="https?:\/\//);
  });

  test("la ruta pública renderiza un resumen técnico y sus fuentes", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(PrivacyPage);

    expect(result).toContain("Información de privacidad");
    expect(result).toContain("Resumen técnico público");
    expect(result).toContain("brechas materiales pendientes");
    expect(result).toContain("Ley 21.719");
    expect(result).not.toContain("no aprobado para publicación");
    expect(result).not.toContain("bloqueado para publicación");
    expect(result).toContain('id="fuentes"');
    expect(result).toContain("www.bcn.cl/leychile");
    expect(result).not.toContain("/blob/");
  });

  test("centraliza el drawer mobile en Header sin duplicarlo en index", () => {
    const headerSource = readFileSync(
      new URL("../src/components/Header.astro", import.meta.url),
      "utf8",
    );
    const indexSource = readFileSync(
      new URL("../src/pages/index.astro", import.meta.url),
      "utf8",
    );

    expect(headerSource.match(/function setNav/g)).toHaveLength(1);
    expect(headerSource).toContain("navToggle");
    expect(indexSource).not.toContain("function setNav");
    expect(indexSource).not.toContain("navToggle");
  });
});
