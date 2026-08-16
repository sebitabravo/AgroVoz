import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";

const vercelConfig = JSON.parse(
  readFileSync(resolve(import.meta.dirname, "../vercel.json"), "utf8"),
) as {
  rewrites?: Array<{ source?: string; destination?: string }>;
};
const demoSource = readFileSync(
  resolve(import.meta.dirname, "../src/pages/demo.astro"),
  "utf8",
);

describe("proxy de demo en Vercel", () => {
  test("reenvia solo los endpoints que la landing usa, sin catch-all", () => {
    // El catch-all exponia todo el namespace /api/v1 del backend (webhook,
    // admin, panel). La landing solo consume estos patrones; si se
    // reintroduce el catch-all, la suite falla.
    const sources = (vercelConfig.rewrites ?? []).map((r) => r.source);
    expect(sources).not.toContain("/api/v1/:path*");
    expect(sources).toEqual(
      expect.arrayContaining([
        "/api/v1/health",
        "/api/v1/products",
        "/api/v1/data/sources",
        "/api/v1/demo/preguntar",
        "/api/v1/prices/:path*",
        "/api/v1/weather/:path*",
        "/api/v1/weather",
      ]),
    );
    expect(vercelConfig.rewrites?.find((r) => r.source === "/api/v1/weather")?.destination).toBe(
      "https://agrovoz.sbravo.app/api/v1/weather",
    );
  });

  test("usa el origen actual en previews de Vercel", () => {
    expect(demoSource).toContain('window.location.hostname.endsWith(".vercel.app")');
    expect(demoSource).toContain("return window.location.origin;");
  });
});
