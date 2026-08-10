import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { describe, expect, test } from "vitest";
import Footer from "../src/components/Footer.astro";
import PrivacyPage from "../src/pages/privacidad.astro";

describe("privacidad pública", () => {
  test("el footer enlaza a la ruta pública y no al repositorio", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(Footer);

    expect(result).toContain('href="/privacidad/"');
    expect(result).not.toContain("github.com/sebitabravo/AgroVoz/blob/main/docs/legal/politica-privacidad.md");
  });

  test("la ruta pública renderiza el aviso legal y sus fuentes", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(PrivacyPage);

    expect(result).toContain("Información de privacidad");
    expect(result).toContain("Ley 21.719");
    expect(result).toContain("borrador técnico, no aprobado");
    expect(result).toContain('id="fuentes"');
    expect(result).toContain("www.bcn.cl/leychile");
  });
});
