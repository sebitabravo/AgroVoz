import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";
import IndexPage from "../src/pages/index.astro";

describe("tarjetas de plan", () => {
  test("los planes institucional y convenios exponen precios, beneficios y selector", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(IndexPage);
    const source = readFileSync(resolve(process.cwd(), "src/pages/index.astro"), "utf8");

    // El plan institucional es el destacado: badge, precio de referencia y
    // los cuatro beneficios declarados en el frontmatter.
    expect(result).toContain("plan-card--featured");
    expect(result).toContain("Recomendado");
    expect(result).toContain("CLP 500–1.000");
    expect(result).toContain("por agricultor · mes · precio de referencia");
    for (const feature of [
      "Gratis para el agricultor (subsidiado)",
      "Consultas de precios y clima (sujetas a límites operativos)",
      "Panel de cobertura y uso (en desarrollo)",
      "Onboarding en terreno",
    ]) {
      expect(result, `falta el beneficio "${feature}"`).toContain(feature);
    }
    expect(result).toContain("Hablar con ventas");

    // El plan de convenios es a conversar, sin precio fijo.
    expect(result).toContain("Convenios privados");
    expect(result).toContain("A convenir");
    expect(result).toContain("según programa");
    expect(result).toContain("Conversemos");

    // Exactamente dos planes, ambos con data-plan para el selector del form.
    const planAttrs = result.match(/data-plan="[^"]+"/g) ?? [];
    expect(planAttrs).toHaveLength(2);
    expect(result).toContain('data-plan="institucional"');
    expect(result).toContain('data-plan="convenios"');
    expect(result).toContain('href="#contacto"');

    // El script cliente preselecciona el plan con aria-pressed al hacer click.
    expect(source).toContain("[data-plan]");
    expect(source).toContain("aria-pressed");

    // Las traducciones referencian claves que existen en el dict EN.
    for (const key of ["plan1.f1", "plan1.f2", "plan1.f3", "plan1.f4", "plan3.f1", "plan3.f2"]) {
      expect(source, `falta la clave i18n "${key}"`).toContain(`"${key}"`);
    }
  });
});
