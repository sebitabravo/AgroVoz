import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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

  test("representa la demo como un teléfono con accesos rápidos", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(DemoPage);

    expect(result).toContain("demo-device");
    expect(result).toContain("chat-messages");
    expect(result).toContain('data-demo-prompt="¿A cuánto está la papa?"');
    expect(result).toContain("Vista simulada · La demo conectada usa datos reales");
  });

  test("mantiene estilos globales para burbujas creadas por JavaScript", () => {
    const source = readFileSync(resolve(process.cwd(), "src/pages/demo.astro"), "utf8");

    expect(source).toContain("<style is:global>");
  });

  test("expone voz y conexiones públicas del backend", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(DemoPage);
    const source = readFileSync(resolve(process.cwd(), "src/pages/demo.astro"), "utf8");

    expect(result).toContain('id="chat-mic"');
    expect(result).toContain('data-live-action="price"');
    expect(result).toContain('data-live-action="weather"');
    expect(result).toContain('data-live-action="sources"');
    expect(source).toContain("audio_base64");
    expect(source).toContain("MediaRecorder");
  });

  test("limita el historial al contrato del backend y evita errores object", () => {
    const source = readFileSync(resolve(process.cwd(), "src/pages/demo.astro"), "utf8");

    expect(source).toContain('texto: texto.slice(0, 500)');
    expect(source).toContain("function apiErrorMessage(detail: unknown): string");
    expect(source).toContain("errorMessage = apiErrorMessage(errData.detail);");
    expect(source).toContain('typeof data.texto !== "string"');
    expect(source).toContain("const responseAudioBase64 =");
    expect(source).not.toContain("const audioBase64 =");
  });

  test("muestra una burbuja WhatsApp animada mientras espera la respuesta", () => {
    const source = readFileSync(resolve(process.cwd(), "src/pages/demo.astro"), "utf8");

    expect(source).toContain("let typingBubble: HTMLDivElement | null = null;");
    expect(source).toContain("function addTypingBubble()");
    expect(source).toContain('typingBubble.className = "chat-bubble chat-bubble--received chat-bubble--typing"');
    expect(source).toContain('dots.className = "chat-typing-dots"');
    expect(source).toContain('aria-label", "AgroVoz está escribiendo"');
    expect(source).toContain("function removeTypingBubble()");
    expect(source).toContain(".chat-typing-dots span:nth-child(2)");
    expect(source).toContain("@keyframes typingDot");
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
