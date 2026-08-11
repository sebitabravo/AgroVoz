import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { describe, expect, test } from "vitest";
import DemoPage from "../src/pages/demo.astro";
import LandingPage from "../src/pages/index.astro";

describe("demo pública", () => {
  test("usa copy chileno/neutral y no voseo rioplatense", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(DemoPage);

    expect(result).toContain("Prueba AgroVoz");
    expect(result).toContain("Escribe tu pregunta");
    expect(result).toContain("Pregúntame");
    expect(result).not.toContain("Probá");
    expect(result).not.toContain("Escribí");
    expect(result).not.toContain("Preguntáme");
  });

  test("la landing pública tampoco expone voseo rioplatense", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(LandingPage);

    expect(result).toContain("Probar la demo interactiva");
    expect(result).not.toContain("Probá");
    expect(result).not.toContain("Escribí");
    expect(result).not.toContain("Preguntáme");
  });
});
