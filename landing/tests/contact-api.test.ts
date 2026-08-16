import { afterEach, describe, expect, test, vi } from "vitest";
import contact from "../api/contact";

const handler = contact.fetch;
type FetchMock = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

function request(body: Record<string, string>, ip: string): Request {
  return new Request("https://landing.example/api/contact", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-forwarded-for": ip,
    },
    body: JSON.stringify(body),
  });
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("Function de contacto", () => {
  test("envía al destinatario server-side y usa el email del visitante como reply-to", async () => {
    vi.stubEnv("RESEND_API_KEY", "re_test_key");
    vi.stubEnv("CONTACT_RECIPIENT", "destino-interno@example.com");
    const resend = vi.fn<FetchMock>(async () => new Response(JSON.stringify({ id: "email_123" }), { status: 200 }));
    vi.stubGlobal("fetch", resend);

    const response = await handler(request({
      name: "Ana",
      email: "ana@example.com",
      message: "Quiero conocer la demo",
      plan: "institucional",
      website: "",
    }, "192.0.2.1"));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
    expect(resend).toHaveBeenCalledOnce();
    const options = resend.mock.calls[0]?.[1] as RequestInit;
    const payload = JSON.parse(String(options.body)) as Record<string, unknown>;
    expect(payload.to).toEqual(["destino-interno@example.com"]);
    expect(payload.reply_to).toBe("ana@example.com");
    expect(payload.subject).toBe("AgroVoz — institucional — Ana");
    expect(payload.text).toContain("Quiero conocer la demo");
  });

  test("limpia caracteres de control del asunto", async () => {
    vi.stubEnv("RESEND_API_KEY", "re_test_key");
    vi.stubEnv("CONTACT_RECIPIENT", "destino-interno@example.com");
    const resend = vi.fn<FetchMock>(async () => new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", resend);

    await handler(request({
      name: "Ana\r\nBcc: atacante@example.com",
      email: "ana@example.com",
      message: "Hola",
      plan: "general\nX-Header: injected",
      website: "",
    }, "192.0.2.5"));

    const options = resend.mock.calls[0]?.[1] as RequestInit;
    const payload = JSON.parse(String(options.body)) as Record<string, unknown>;
    expect(payload.subject).toBe("AgroVoz — general X-Header: injected — Ana Bcc: atacante@example.com");
    expect(String(payload.subject)).not.toMatch(/[\r\n]/);
  });

  test("falla cerrado si falta la configuración del proveedor", async () => {
    vi.stubEnv("CONTACT_RECIPIENT", "destino-interno@example.com");
    const resend = vi.fn<FetchMock>(async () => new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", resend);

    const response = await handler(request({
      name: "Ana",
      email: "ana@example.com",
      message: "Hola",
      plan: "general",
      website: "",
    }, "192.0.2.2"));

    expect(response.status).toBe(503);
    expect(resend).not.toHaveBeenCalled();
  });

  test("rechaza payload incompleto y silencia el honeypot", async () => {
    const resend = vi.fn<FetchMock>(async () => new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", resend);

    const invalid = await handler(request({ name: "Ana", email: "no-es-email", message: "" }, "192.0.2.3"));
    expect(invalid.status).toBe(400);

    const bot = await handler(request({
      name: "Bot",
      email: "bot@example.com",
      message: "spam",
      website: "https://spam.example",
    }, "192.0.2.4"));
    expect(bot.status).toBe(202);
    expect(resend).not.toHaveBeenCalled();
  });
});
