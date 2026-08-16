type ContactPayload = {
  name?: unknown;
  email?: unknown;
  message?: unknown;
  plan?: unknown;
  website?: unknown;
};

type RateEntry = { count: number; windowStartedAt: number };

const attempts = new Map<string, RateEntry>();
const RATE_LIMIT = 5;
const RATE_WINDOW_MS = 60_000;
const MAX_NAME_LENGTH = 120;
const MAX_EMAIL_LENGTH = 254;
const MAX_MESSAGE_LENGTH = 4000;

function json(body: Record<string, unknown>, status: number): Response {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function env(name: string): string {
  const processLike = (globalThis as typeof globalThis & {
    process?: { env?: Record<string, string | undefined> };
  }).process;
  return processLike?.env?.[name]?.trim() ?? "";
}

function text(value: unknown, maxLength: number): string {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : "";
}

function singleLine(value: string): string {
  return value.replace(/[\x00-\x1f\x7f]/g, " ").replace(/\s+/g, " ").trim();
}

function escapeHtml(value: string): string {
  return value.replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ] ?? character,
  );
}

function clientIp(request: Request): string {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "unknown";
}

function rateLimited(request: Request): boolean {
  const now = Date.now();
  const key = clientIp(request);
  const previous = attempts.get(key);
  if (!previous || now - previous.windowStartedAt >= RATE_WINDOW_MS) {
    attempts.set(key, { count: 1, windowStartedAt: now });
    return false;
  }
  previous.count += 1;
  return previous.count > RATE_LIMIT;
}

function validEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

export default {
  async fetch(request: Request): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: { "Cache-Control": "no-store" },
      });
    }
    if (request.method !== "POST") return json({ detail: "Método no permitido" }, 405);
    if (rateLimited(request)) return json({ detail: "Demasiados envíos" }, 429);

    let payload: ContactPayload;
    try {
      payload = (await request.json()) as ContactPayload;
    } catch {
      return json({ detail: "Solicitud inválida" }, 400);
    }

    // Honeypot: responde como éxito para no enseñar a los bots si fueron detectados.
    if (text(payload.website, 120)) return json({ ok: true }, 202);

    const name = text(payload.name, MAX_NAME_LENGTH);
    const email = text(payload.email, MAX_EMAIL_LENGTH).toLowerCase();
    const message = text(payload.message, MAX_MESSAGE_LENGTH);
    const plan = text(payload.plan, 40) || "general";
    if (!name || !validEmail(email) || !message) {
      return json({ detail: "Completa nombre, email y mensaje" }, 400);
    }

    const apiKey = env("RESEND_API_KEY");
    const recipient = env("CONTACT_RECIPIENT");
    if (!apiKey || !recipient || !validEmail(recipient)) {
      return json({ detail: "Servicio de contacto no configurado" }, 503);
    }

    // El asunto se envía a un proveedor de correo: nunca debe conservar
    // saltos de línea ni caracteres de control provenientes del formulario.
    const subject = `AgroVoz — ${singleLine(plan)} — ${singleLine(name)}`;
    const plainText = [
      `Plan de interés: ${plan}`,
      `Nombre: ${name}`,
      `Email: ${email}`,
      "",
      message,
    ].join("\n");
    const html = `<p><strong>Plan de interés:</strong> ${escapeHtml(plan)}</p><p><strong>Nombre:</strong> ${escapeHtml(name)}</p><p><strong>Email:</strong> ${escapeHtml(email)}</p><p>${escapeHtml(message).replace(/\n/g, "<br>")}</p>`;

    try {
      const response = await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          from: env("CONTACT_FROM") || "AgroVoz <onboarding@resend.dev>",
          to: [recipient],
          reply_to: email,
          subject,
          text: plainText,
          html,
        }),
      });
      if (!response.ok) return json({ detail: "No se pudo enviar el mensaje" }, 502);
    } catch {
      return json({ detail: "No se pudo enviar el mensaje" }, 502);
    }

    return json({ ok: true }, 200);
  },
};
