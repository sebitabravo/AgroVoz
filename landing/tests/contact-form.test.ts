import { experimental_AstroContainer as AstroContainer } from "astro/container";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";
import ContactForm from "../src/components/ContactForm.astro";
import IndexPage from "../src/pages/index.astro";

describe("formulario de contacto", () => {
  test("envía al endpoint server-side sin exponer el destinatario", async () => {
    const container = await AstroContainer.create();
    const result = await container.renderToString(ContactForm);
    const source = readFileSync(resolve(process.cwd(), "src/components/ContactForm.astro"), "utf8");

    // El destinatario se resuelve solo en Vercel Environment Variables.
    expect(result).toContain('id="contact-form"');
    expect(result).toContain('action="/api/contact"');
    expect(result).toContain('method="post"');
    expect(result).toContain('id="contact-status"');
    expect(result).toContain('name="website"');
    expect(source).toContain('formAction = "/api/contact"');
    expect(source).not.toContain("hola@agrovoz.cl");

    // Los tres chips de plan inician deseleccionados y son accesibles.
    for (const plan of ["institucional", "convenios", "general"]) {
      expect(result, `falta el chip data-plan-chip="${plan}"`).toContain(
        `data-plan-chip="${plan}"`,
      );
    }
    expect(result).toContain('aria-pressed="false"');
    expect(result).toContain("Institucional");
    expect(result).toContain("Convenios");
    expect(result).toContain("Consulta general");

    // Nombre, email y mensaje son obligatorios, con placeholders claros.
    expect(result).toContain('id="cf-name"');
    expect(result).toContain('name="name"');
    expect(result).toContain("placeholder=\"Tu nombre\"");
    expect(result).toContain('id="cf-email"');
    expect(result).toContain('name="email"');
    expect(result).toContain('type="email"');
    expect(result).toContain("placeholder=\"tu@correo.cl\"");
    expect(result).toContain('id="cf-msg"');
    expect(result).toContain('name="message"');
    expect(result).toContain("placeholder=\"¿Cómo podemos colaborar?\"");
    expect(result).toContain("required");

    // El envío pide una demo y las claves i18n apuntan al dict real.
    expect(result).toContain('data-i18n="form.submit"');
    expect(result).toContain("Solicitar demo");
    expect(result).toContain('data-i18n-ph="form.nameph"');
    expect(result).toContain('data-i18n-ph="form.emailph"');
    expect(result).toContain('data-i18n-ph="form.msgph"');

    // La landing integra el formulario con el endpoint server-side.
    const indexRender = await container.renderToString(IndexPage);
    expect(indexRender).toContain('id="contact-form"');
    expect(indexRender).toContain('action="/api/contact"');
    expect(indexRender).not.toContain("hola@agrovoz.cl");
  });
});
