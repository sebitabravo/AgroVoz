# AgroVoz — Sistema de Diseño
> Referencia de paleta, tipografía, componentes y animaciones usados en la landing page.
> Pégalo en cualquier contexto nuevo para mantener consistencia visual.

---

## 🎨 Paleta de colores (CSS variables)

```css
--bg:           #FBFAF7   /* fondo global — crema casi blanco */
--ink:          #1c1e1a   /* texto principal — negro verdoso */
--muted:        #7e827a   /* texto secundario / labels */
--line:         #e8e7e0   /* bordes y separadores */
--surface:      #ffffff   /* tarjetas, formularios, módulos */
--accent:       #4f7d5a   /* verde principal — CTAs, íconos, onda */
--accent-deep:  #3c6446   /* verde oscuro — hover, highlights, ODS */
--accent-soft:  #edf2ed   /* verde muy claro — fondos de sección / chips activos */
```

### Uso por contexto
| Rol | Variable |
|---|---|
| Fondo de página | `--bg` |
| Fondo de tarjetas / nav | `--surface` |
| Texto body | `--ink` |
| Labels, meta, placeholders | `--muted` |
| Separadores / bordes | `--line` |
| Botones primarios, acento | `--accent` |
| Hover, títulos de acento, links | `--accent-deep` |
| Fondos alternativos de sección, chips seleccionados | `--accent-soft` |

---

## 🔤 Tipografía

- **Familia:** `Manrope` (Google Fonts) + fallback `system-ui, -apple-system, sans-serif`
- **Import:** `https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap`
- **Rendering:** `-webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility`

### Escala tipográfica
| Rol | Tamaño | Peso | Letter-spacing |
|---|---|---|---|
| Hero H1 | `clamp(46px, 8.6vw, 128px)` | 800 | `-.035em` |
| H2 de sección | `clamp(32px, 4.4vw, 58px)` | 800 | `-.025em` |
| H3 tarjeta | `21px` | 700 | `-.01em` |
| Eyebrow (label) | `13px` | 700 | `.16em` uppercase |
| Body | `clamp(16px, 1.5vw, 19px)` | 450 | — |
| Body tarjeta | `15px` | — | — |
| Meta / caption | `13–14px` | 500 | `.01–.04em` |
| Número estadística | `clamp(40px, 4.6vw, 62px)` | 800 | `-.03em` |
| Copyright / disclaimer | `12–12.5px` | — | — |

---

## 📐 Layout

- **Max-width container:** `1160px` centrado con `padding: 0 32px`
- **Sección padding:** `clamp(80px, 12vh, 150px) 0`
- **Gap de grilla:** `24px` (tarjetas), `60px` (columnas mayores)
- **Border-radius tarjetas:** `16–20px`
- **Border-radius pills (CTAs, chips):** `999px`
- **Border-radius teléfono mockup:** `46px`

### Sistema de grid
```css
/* 3 columnas auto-adaptables (tecnología, pasos, equipo) */
grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));

/* Stats 4 columnas separadas por borde */
grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
background: var(--line);   /* gap como borde con 1px entre celdas */
gap: 1px;

/* Selector de plan 2×2 */
grid-template-columns: 1fr 1fr;
gap: 8px;
```

---

## 🧩 Componentes clave

### Eyebrow (etiqueta de sección)
```html
<span style="font-size:13px;letter-spacing:.16em;text-transform:uppercase;font-weight:700;color:var(--accent-deep)">
  El problema
</span>
```

### Tarjeta estándar
```html
<div style="background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:32px">
  <!-- contenido -->
</div>
```

### Tarjeta sobre --accent-soft
```html
<div style="background:var(--bg);border-radius:20px;padding:34px">
  <!-- en secciones con background: var(--accent-soft) -->
</div>
```

### Botón primario
```html
<a href="#" style="background:var(--accent);color:#fff;font-size:15.5px;font-weight:600;padding:14px 26px;border-radius:999px;text-decoration:none">
  CTA
</a>
```

### Botón secundario
```html
<a href="#" style="background:var(--surface);border:1px solid var(--line);color:var(--ink);font-size:15.5px;font-weight:600;padding:14px 26px;border-radius:999px;text-decoration:none">
  CTA secundario
</a>
```

### Chip/selector activo
```css
background: var(--accent-soft);
border-color: var(--accent);
color: var(--accent-deep);
```

### Logo / onda de marca
```html
<span style="display:inline-flex;align-items:flex-end;gap:2px;height:18px">
  <span style="width:3px;height:8px;border-radius:2px;background:var(--accent)"></span>
  <span style="width:3px;height:16px;border-radius:2px;background:var(--accent)"></span>
  <span style="width:3px;height:11px;border-radius:2px;background:var(--accent)"></span>
</span>
```

### Header sticky
```css
position: sticky; top: 0; z-index: 50;
background: rgba(251,250,247,.72);
backdrop-filter: saturate(160%) blur(14px);
border-bottom: 1px solid var(--line);
height: 68px;
```

### Stat con conteo animado
```html
<!-- El JS lo anima con count-up al entrar en viewport -->
<div data-count="205000" style="font-size:clamp(40px,4.6vw,62px);font-weight:800">205.000</div>
<!-- Atributos: data-count, data-decimals="1", data-prefix="+$", data-suffix="%" -->
```

---

## ✨ Animaciones

### Reveal al scroll (se re-dispara siempre)
```html
<!-- Agregar data-reveal al elemento. Opcional: data-reveal-delay="120ms" -->
<div data-reveal data-reveal-delay="0ms">...</div>
```
```css
/* Estado inicial (JS lo setea) */
opacity: 0;
transform: translateY(28px);
transition: opacity .9s cubic-bezier(.16,.8,.3,1), transform .9s cubic-bezier(.16,.8,.3,1);
```
- **Observer threshold:** `0` con `rootMargin: 0px 0px -8% 0px`
- **Comportamiento:** muestra al entrar, oculta al salir → re-anima siempre

### Onda animada (keyframe)
```css
@keyframes wf {
  0%,100% { transform: scaleY(.32) }
  50%      { transform: scaleY(1)   }
}
/* Aplicar con animation-delay escalonado por barra */
animation: wf 1.3s <delay> ease-in-out infinite;
```

### Count-up (cifras)
- Duración: `1500ms`
- Easing: `1 - (1-p)³` (ease-out cúbico)
- Formato: `Intl.NumberFormat('es-CL')`
- Re-dispara cada vez que el elemento sale y vuelve a entrar

### Parallax sutil
```js
// Se aplica a elementos con data-parallax="0.12"
const center = (rect.top + rect.height/2) - (window.innerHeight/2);
el.style.transform = `translateY(${center * factor * 0.35}px)`;
```

---

## 🌐 i18n ES/EN

- Cualquier elemento con `data-i18n="clave"` es traducible
- Inputs con `data-i18n-ph="clave"` traducen el `placeholder`
- La clave EN se registra en un objeto `this.EN = { 'clave': 'traducción' }`
- Toggle via botones `#lang-es` / `#lang-en`
- Persiste en `localStorage` con key `agrovoz_lang`

---

## 🗂 Secciones de la landing (en orden)

| ID | Contenido |
|---|---|
| `#top` / hero | Headline gigante + onda de marca + CTAs |
| `#problema` | Stats 4 columnas con count-up |
| `#como` | 3 tarjetas numeradas (01/02/03) |
| `#demo` | Grid texto + mockup WhatsApp con cascada de mensajes |
| `#planes` | 3 tarjetas de pricing |
| `#stack` | 6 tarjetas de tecnología (grilla 3+3) |
| `#impacto` | 3 stats grandes + fila ODS |
| `#equipo` | 3 tarjetas con iniciales avatar |
| `#contacto` | Grid texto + formulario con selector de plan |
| footer | Una fila: logo·tagline / links / copyright |

---

## 🧠 Principios de diseño

1. **Casi monocromático** — un solo acento verde, fondo crema, sin gradientes
2. **Tipografía como jerarquía** — el tamaño hace el trabajo, no el color
3. **Espacio como diseño** — márgenes generosos, secciones bien separadas
4. **Sin emojis** — la marca comunica con forma y tipografía
5. **Inline styles únicamente** — no hay clases CSS globales; cada estilo es explícito
6. **Animaciones útiles** — revelan contenido, no distraen

---

## ⚙️ Props / Tweaks disponibles

| Prop | Tipo | Default | Descripción |
|---|---|---|---|
| `accentColor` | color | `#4f7d5a` | Color de acento global |
| `defaultLang` | enum `es\|en` | `es` | Idioma inicial |
| `revealAnimations` | boolean | `true` | Activar/desactivar reveals |
