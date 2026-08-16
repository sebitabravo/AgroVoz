import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";
import DemoPage from "../src/pages/demo.astro";

const PROMPTS = [
  "¿A cuánto está la papa?",
  "¿Va a llover en Traiguén mañana?",
  "¿Cuál es el precio del tomate?",
  "¿Cuál es el calendario agrícola del tomate?",
  "¿Qué hago si mis papas tienen manchas en las hojas?",
  "¿Cuándo se siembra el trigo en Traiguén?",
];

describe("precisión del copy de la demo", () => {
  test("los accesos rápidos, la burbuja de bienvenida y el teléfono dicen lo mismo", async () => {
    const source = readFileSync(resolve(process.cwd(), "src/pages/demo.astro"), "utf8");
    const phoneSource = readFileSync(
      resolve(process.cwd(), "src/components/DemoPhone.astro"),
      "utf8",
    );
    const container = await AstroContainer.create();
    const result = await container.renderToString(DemoPage);

    // Los seis accesos rápidos (botones) coinciden con la burbuja de bienvenida.
    const buttonPrompts = [...source.matchAll(/data-demo-prompt="([^"]+)"/g)].map((m) => m[1]);
    expect(buttonPrompts).toEqual(PROMPTS);
    const liTexts = [...source.matchAll(/<li[^>]*>([^<]+)<\/li>/g)].map((m) => m[1].trim());
    expect([...liTexts].sort()).toEqual([...PROMPTS].sort());

    // El lede describe la demo sin prometer más de lo que hace.
    expect(source).toContain("Como una conversación de WhatsApp, pero con datos agrícolas oficiales.");
    expect(source).toContain("Escribe una pregunta y mira cómo responde.");

    // La caption separa la vista simulada del flujo conectado con datos reales
    // (asertada también por demo-page.test.ts; acá se replica como contrato).
    expect(source).toContain("Vista simulada · La demo conectada usa datos reales");
    expect(result).toContain("Vista simulada · La demo conectada usa datos reales");

    // El teléfono del hero no miente: el estado inicia en "conectando…" y se
    // resuelve contra la API real; el precio usa la fecha real del dato ODEPA.
    expect(phoneSource).toContain("conectando…");
    expect(phoneSource).toContain('fetch(apiBase() + "/api/v1/health")');
    expect(phoneSource).toContain('fetch(apiBase() + "/api/v1/prices/papa")');
    expect(phoneSource).toContain("Lo Valledor");
    expect(phoneSource).toContain('.includes("lo valledor")');
    expect(phoneSource).toContain("priceEl.dataset.es = precio.texto");
    expect(phoneSource).toContain("sin conexión");
    expect(phoneSource).toContain("Papa según ODEPA, con el precio más reciente disponible.");

    // La demo interactiva renderiza el estado inicial del chat y su límite.
    expect(result).toContain('id="chat-status"');
    expect(result).toContain("en línea");
    expect(result).toContain("Escribe tu pregunta...");
    expect(result).toContain('maxlength="500"');
  });
});
