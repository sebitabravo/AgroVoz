import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";

const SRC = resolve(process.cwd(), "src");

// Todos los archivos .astro del proyecto (páginas, layouts y componentes).
function astroFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = resolve(dir, entry.name);
    if (entry.isDirectory()) return astroFiles(full);
    return entry.name.endsWith(".astro") ? [full] : [];
  });
}

// Extrae las claves del bloque "const <nombre>: Record<string, string> = {...};"
// por marcador, sin depender del número de línea.
function dictKeys(source: string, dictName: string): string[] {
  const start = source.indexOf(`const ${dictName}: Record<string, string> = {`);
  expect(start, `no se encontró "const ${dictName}" en index.astro`).toBeGreaterThanOrEqual(0);
  const end = source.indexOf("};", start);
  const block = source.slice(start, end);
  return [...block.matchAll(/"([^"]+)":\s*"/g)].map((m) => m[1]);
}

// Extrae las claves i18n usadas: props "i18nXxx: '...'" del frontmatter,
// atributos data-i18n / data-i18n-ph y arrays i18nFeatures={[...]}.
function usedKeys(source: string): string[] {
  const keys = new Set<string>();
  const propKeys = /(?<![\w-])i18n\w*[:=]\s*"([^"]+)"/g;
  for (const m of source.matchAll(propKeys)) keys.add(m[1]);
  const attrKeys = /data-i18n(?:-ph)?="([^"]+)"/g;
  for (const m of source.matchAll(attrKeys)) keys.add(m[1]);
  const arrayKeys = /i18nFeatures[=:]\s*\[([^\]]+)\]/g;
  for (const m of source.matchAll(arrayKeys)) {
    for (const inner of m[1].matchAll(/"([^"]+)"/g)) keys.add(inner[1]);
  }
  return [...keys];
}

describe("integridad i18n de la landing", () => {
  test("toda clave usada tiene traducción y los placeholders están cubiertos", () => {
    const indexSource = readFileSync(resolve(SRC, "pages/index.astro"), "utf8");

    // El dict EN es la fuente de verdad; ENPH traduce solo los placeholders.
    const en = dictKeys(indexSource, "EN");
    const enph = dictKeys(indexSource, "ENPH");
    expect(en.length, "el dict EN quedó vacío o truncado").toBeGreaterThan(50);
    expect(enph.sort()).toEqual(["form.nameph", "form.emailph", "form.msgph"].sort());

    const dictionary = new Set([...en, ...enph]);

    // Cada uso de i18n en cualquier archivo .astro debe tener traducción.
    for (const file of astroFiles(SRC)) {
      const source = readFileSync(file, "utf8");
      for (const key of usedKeys(source)) {
        expect(
          dictionary.has(key),
          `${file}: la clave "${key}" no tiene traducción en EN ni ENPH`,
        ).toBe(true);
      }
    }

    // Los placeholders se usan en el formulario y "demo.status" no existe:
    // el estado del backend se resuelve por fetch, no prometido en el markup.
    const contactSource = readFileSync(resolve(SRC, "components/ContactForm.astro"), "utf8");
    expect(usedKeys(contactSource)).toContain("form.nameph");
    expect(usedKeys(contactSource)).toContain("form.emailph");
    expect(usedKeys(contactSource)).toContain("form.msgph");
    expect(en).not.toContain("demo.status");
    for (const file of astroFiles(SRC)) {
      expect(usedKeys(readFileSync(file, "utf8")), file).not.toContain("demo.status");
    }
  });
});
