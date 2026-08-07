import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { describe, expect, test } from "vitest";
import StatCard from "../src/components/StatCard.astro";

describe("StatCard", () => {
  test("formatea count con separador de miles es-CL y sin fuente no la renderiza", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(StatCard, {
      props: { count: 1234, label: "Agricultores" },
    });

    expect(result).toContain("1.234");
    expect(result).toContain("Agricultores");
    expect(result).not.toContain("stat-source");
  });

  test("usa texto estatico cuando no hay count y muestra la fuente citada", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(StatCard, {
      props: { text: "40–60%", label: "Latencia", source: "Medición interna 2026" },
    });

    expect(result).toContain("40–60%");
    expect(result).toContain("Medición interna 2026");
    expect(result).toContain('class="stat-source"');
  });

  test("aplica decimals, prefix, suffix, revealDelay, accent, variant impact e i18n", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(StatCard, {
      props: {
        count: 12.5,
        decimals: 1,
        prefix: "$",
        suffix: "/kg",
        label: "Precio promedio",
        variant: "impact",
        accent: true,
        revealDelay: 300,
        i18nLabel: "precio.promedio",
        i18nSource: "precio.fuente",
        source: "ODEPA",
      },
    });

    expect(result).toContain("data-reveal-delay=\"300ms\"");
    expect(result).toContain("impact-number");
    expect(result).toContain("impact-card");
    expect(result).toContain('data-i18n="precio.promedio"');
    expect(result).toContain('data-i18n="precio.fuente"');
    expect(result).toContain("$12,5/kg");
  });
});
