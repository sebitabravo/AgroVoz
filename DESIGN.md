# AgroVoz — Sistema de Diseño

> Referencia visual para landing page, admin dashboard y assets de marca.
> Stack: Astro + Tailwind CSS 4.x. Sin Google Fonts, sin dependencias externas de íconos.

---

## Identidad de marca

**Nombre**: AgroVoz
**Tagline**: "Tu voz tiene el precio justo"
**Personalidad**: Cercano, técnico pero simple, confiable. Startup agrícola con base científica.
**Audiencia**: Funcionarios INDAP/PRODESAL, jurados Crea INACAP, potenciales aliados institucionales.

---

## Tipografía

**Stack**: system-ui, sin fuentes externas.

```css
font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
             Roboto, "Helvetica Neue", Arial, sans-serif;
```

| Elemento | Weight | Size | Line-height |
|---|---|---|---|
| H1 (hero) | 800 | `text-5xl` (~3rem) | 1.1 |
| H2 (secciones) | 700 | `text-3xl` (~1.875rem) | 1.2 |
| H3 (tarjetas) | 600 | `text-xl` (~1.25rem) | 1.3 |
| Body | 400 | `text-base` (1rem) | 1.6 |
| Caption | 400 | `text-sm` (0.875rem) | 1.5 |

---

## Paleta de colores

### Modo claro (default)

| Token | Hex | Tailwind | Uso |
|---|---|---|---|
| Green 700 | `#15803d` | `green-700` | Botones CTA, links, acentos primarios |
| Green 600 | `#16a34a` | `green-600` | Hover de botones |
| Green 50 | `#f0fdf4` | `green-50` | Fondos de secciones alternas |
| Amber 500 | `#f59e0b` | `amber-500` | Acentos secundarios (íconos de trigo/sol) |
| Earth 700 | `#78350f` | `amber-900` | Texto sobre fondos green-50 |
| Slate 900 | `#0f172a` | `slate-900` | Texto principal |
| Slate 600 | `#475569` | `slate-600` | Texto secundario |
| White | `#ffffff` | `white` | Fondos principales |

### Modo oscuro

- No implementado en MVP. La landing es clara por defecto.
- Si se agrega post-MVP: invertir a fondo slate-900, texto white, mantener green-600 como acento.

---

## Layout

### Landing page

```
┌──────────────────────────────────────────┐
│ Header (sticky, backdrop-blur)           │
├──────────────────────────────────────────┤
│ Hero (full viewport height, centered)    │
│   "Tu voz tiene el precio justo"          │
│   Subheadline + CTA "Cómo funciona"      │
├──────────────────────────────────────────┤
│ Problema (bg green-50)                   │
│   "205.000 agricultores pierden..."      │
│   StatCards: 40-60%, 205K, 80%           │
├──────────────────────────────────────────┤
│ Solución (bg white)                      │
│   3 pasos: Audio → Consulta → Respuesta  │
│   FeatureCards con íconos                │
├──────────────────────────────────────────┤
│ Stack abierto (bg green-50)              │
│   WhatsApp + Whisper + ODEPA + Piper     │
├──────────────────────────────────────────┤
│ Impacto (bg white)                       │
│   ODS 2, 8, 10, 13 + cifras             │
├──────────────────────────────────────────┤
│ Equipo (bg green-50)                     │
│   3 TeamCards con nombre, rol, fortaleza │
├──────────────────────────────────────────┤
│ Contacto (bg white)                      │
│   Formulario simple (nombre, email, msg) │
├──────────────────────────────────────────┤
│ Footer (bg slate-900, text white)        │
│   © 2026 AgroVoz — INACAP Temuco        │
└──────────────────────────────────────────┘
```

### Breakpoints (Tailwind defaults)

| Breakpoint | Width | Layout |
|---|---|---|
| Default | <640px | Single column, stack vertical |
| `sm` | ≥640px | Single column, wider padding |
| `md` | ≥768px | Two columns para tarjetas |
| `lg` | ≥1024px | Full layout, max-width 1200px |

### Contenedor

```css
max-width: 1200px;
margin: 0 auto;
padding-left: 1rem;
padding-right: 1rem;
/* Tailwind: container mx-auto px-4 max-w-6xl */
```

---

## Componentes

### Hero

```
┌──────────────────────────────────────┐
│           [Icono: onda de voz]        │
│     "Tu voz tiene el precio justo"    │  ← H1, text-5xl, font-extrabold
│  Asistente de IA por WhatsApp para    │  ← text-slate-600, text-lg
│  que accedas a precios y clima sin   │
│  leer, escribir ni instalar apps     │
│                                        │
│     [Cómo funciona ▼]  (CTA)          │  ← bg-green-700, text-white,
│                                        │     rounded-xl, px-8 py-4,
└──────────────────────────────────────┘      font-semibold
```

CSS Tailwind:
```
<section class="min-h-screen flex flex-col items-center justify-center text-center px-4">
  <div class="mb-8 text-green-700">[SVG icon 64px]</div>
  <h1 class="text-5xl font-extrabold text-slate-900 mb-6 max-w-3xl">
    Tu voz tiene el precio justo
  </h1>
  <p class="text-lg text-slate-600 mb-10 max-w-2xl">
    Asistente de IA por WhatsApp para que accedas a precios agrícolas
    y pronósticos climáticos sin leer, escribir ni instalar aplicaciones.
  </p>
  <a href="#como-funciona" class="bg-green-700 hover:bg-green-600 text-white
    font-semibold rounded-xl px-8 py-4 transition-colors">
    Cómo funciona
  </a>
</section>
```

### FeatureCard

```
┌────────────────────┐
│ [SVG icon, 48px]   │
│                    │
│ Título             │  ← font-semibold, text-xl
│ Descripción corta  │  ← text-slate-600
│ (2 líneas máx)     │
└────────────────────┘
```

CSS Tailwind: `bg-white rounded-2xl p-6 shadow-sm border border-slate-100`

### StatCard

```
┌────────────────────┐
│ 40-60%             │  ← text-4xl, font-bold, text-green-700
│ Pérdida de precio  │  ← text-slate-600
│ por asimetría      │
│ Fuente: INDAP 2024 │  ← text-xs, text-slate-400
└────────────────────┘
```

CSS Tailwind: `bg-white rounded-2xl p-6 text-center shadow-sm`

### TeamCard

```
┌────────────────────┐
│ [Foto circular]    │  ← w-24 h-24 rounded-full, bg-green-100
│ Nombre Apellido    │  ← font-semibold
│ Rol                │  ← text-sm, text-green-700
│ "Fortaleza clave"  │  ← text-slate-600, text-sm, italic
└────────────────────┘
```

### Button

```css
/* Primario (CTA) */
.btn-primary: bg-green-700 text-white font-semibold rounded-xl px-8 py-4
             hover:bg-green-600 transition-colors

/* Secundario */
.btn-secondary: border-2 border-green-700 text-green-700 font-semibold
               rounded-xl px-8 py-4 hover:bg-green-50 transition-colors
```

### Header

```
┌──────────────────────────────────────────────┐
│ [AgroVoz]         Inicio  Cómo funciona ... │
│ (sticky, backdrop-blur, border-b)           │
└──────────────────────────────────────────────┘
```

Mobile: hamburger menu (SVG tres líneas), despliega menú vertical.

---

## Íconos

Usar SVG inline de Lucide (https://lucide.dev/icons/). Copiar el SVG directo, sin dependencia npm.

Íconos necesarios:
- `mic` / `audio-waveform` — voz
- `cloud-sun` — clima
- `banknote` / `trending-up` — precios
- `sprout` / `wheat` — agricultura
- `users` — equipo
- `mail` — contacto
- `menu` / `x` — hamburger menu
- `chevron-down` — scroll indicator
- `check` — bullet points

Tamaños: 24px (inline en texto), 48px (feature cards), 64px (hero icon).

---

## Admin Dashboard (MVP)

- CSS mínimo, utilitario. Tailwind CDN o CSS inline en los templates Jinja2.
- Sidebar izquierdo (escritorio) / top bar (mobile)
- Colores: mismo green-700 como acento, fondo gris claro (#f8fafc)
- Tablas: striped rows, texto monoespaciado para números
- Chart.js desde CDN para gráficos de métricas
- Sin modo oscuro en MVP

---

## Assets

- **Logo**: Texto "AgroVoz" con ícono de onda de voz o brote. SVG simple.
- **Favicon**: SVG 32x32, ícono de onda verde sobre fondo transparente.
- **OG Image**: 1200x630 PNG, generado con `og-image` o manual. Título + tagline + logo.
- **Fotos equipo**: Placeholders cuadrados con iniciales. Reemplazar con fotos reales después.

---

## Prompts de referencia para agentes

Al construir componentes:

```
Creá el componente Hero.astro con:
- Título: "Tu voz tiene el precio justo"
- Subtítulo descriptivo (2 líneas)
- CTA con link a #como-funciona
- Ícono SVG de onda de voz arriba del título
- Tailwind CSS, system-ui, responsive
```

```
Creá FeatureCard.astro que reciba props: icon (SVG string), title, description.
- Ícono a la izquierda en desktop, arriba en mobile
- Max-width 350px
- Sombra sutil, borde redondeado
```
