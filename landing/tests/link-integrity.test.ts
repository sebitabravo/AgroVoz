import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";
import DemoPage from "../src/pages/demo.astro";
import IndexPage from "../src/pages/index.astro";
import PrivacyPage from "../src/pages/privacidad.astro";

// Archivo fuente -> páginas donde se renderiza. Las anclas "#x" se validan
// contra al menos uno de esos renders; las rutas "/pagina#x" contra la página
// destino. Header/Footer viven en las tres páginas públicas.
const SOURCE_FILES: Record<string, string[]> = {
  "src/pages/index.astro": ["index"],
  "src/pages/privacidad.astro": ["privacidad"],
  "src/pages/demo.astro": ["demo"],
  "src/components/Header.astro": ["index", "privacidad", "demo"],
  "src/components/Footer.astro": ["index", "privacidad", "demo"],
  "src/components/Hero.astro": ["index"],
  "src/components/PlanCard.astro": ["index"],
  "src/components/ContactForm.astro": ["index"],
};

// Resuelve un href interno a (archivo destino, ancla). Los hrefs externos
// (mailto/http), dinámicos ("{...}") y las anclas puras "#x" salen sin archivo.
function resolveHref(href: string): { file?: string; anchor?: string } | null {
  if (href.startsWith("mailto:") || href.startsWith("http") || href.startsWith("{")) {
    return null;
  }
  const [pathPart, anchor] = href.split("#");
  if (pathPart === "") {
    return { anchor }; // "#contacto": ancla de la misma página
  }
  const path = pathPart.replace(/\/+$/, "") || "/index"; // "/" -> index
  return { file: `src/pages${path}.astro`, anchor: anchor || undefined };
}

describe("integridad de enlaces de la landing", () => {
  test("todo enlace interno y CTA resuelve a páginas e ids existentes", async () => {
    const container = await AstroContainer.create();
    const renders: Record<string, string> = {
      index: await container.renderToString(IndexPage),
      privacidad: await container.renderToString(PrivacyPage),
      demo: await container.renderToString(DemoPage),
    };

    for (const [file, pages] of Object.entries(SOURCE_FILES)) {
      const source = readFileSync(resolve(process.cwd(), file), "utf8");
      const hrefs = [...source.matchAll(/href="([^"]+)"/g)].map((m) => m[1]);

      for (const href of hrefs) {
        const resolved = resolveHref(href);
        if (!resolved) continue;

        if (resolved.file) {
          // Ruta absoluta: la página destino debe existir...
          expect(
            existsSync(resolve(process.cwd(), resolved.file)),
            `${file}: href="${href}" apunta a ${resolved.file}, que no existe`,
          ).toBe(true);
          // ...y el id ancla debe estar en su render.
          if (resolved.anchor) {
            const target = resolved.file.replace(/^src\/pages\//, "").replace(/\.astro$/, "");
            expect(
              renders[target].includes(`id="${resolved.anchor}"`),
              `${file}: href="${href}" -> id="${resolved.anchor}" no existe en ${target}`,
            ).toBe(true);
          }
        } else if (resolved.anchor) {
          // Ancla pura: el id debe existir en al menos una página del archivo.
          const ok = pages.some((p) => renders[p].includes(`id="${resolved.anchor}"`));
          expect(
            ok,
            `${file}: href="${href}" -> id="${resolved.anchor}" no existe en ninguna página`,
          ).toBe(true);
        }
      }
    }

    // CTA del hero y contacto institucional: destinos reales.
    const indexSource = readFileSync(resolve(process.cwd(), "src/pages/index.astro"), "utf8");
    expect(indexSource).toContain('ctaLink="/demo"');
    expect(existsSync(resolve(process.cwd(), "src/pages/demo.astro"))).toBe(true);
    expect(indexSource).toContain('ctaSecondaryLink="#contacto"');
    expect(renders.index).toContain('id="contacto"');
    expect(indexSource).toContain('fetch(form.action');
  });
});
