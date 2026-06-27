# Fase 04: Landing Page — Astro + Tailwind

**Objetivo**: Crear landing page estática para AgroVoz. No es para los agricultores,
sino para INDAP, PRODESAL, jurados Crea INACAP, y credibilidad institucional.
**Duración estimada**: 5 tareas
**Dependencias**: Fase 03 completada (backend funcionando)
**Archivos de contexto requeridos**:
- `AGENTS.md`
- `docs/ARCHITECTURE.md`

---

## Tareas

### T4.1: Inicializar proyecto Astro

- [ ] `cd landing && bun create astro@latest . --template basics --yes --skip-houston`
- [ ] Instalar Tailwind CSS 4: `bun astro add tailwind`
- [ ] Configurar `astro.config.mjs`:
  - Output: `static`
  - Site: `https://agrovoz.cl`
  - Integraciones: tailwind
- [ ] Estructura base:
  - `src/pages/index.astro` — landing principal
  - `src/layouts/Base.astro` — layout base (HTML, meta tags)
  - `src/components/` — componentes
- Archivos a crear: proyecto Astro inicializado

### T4.2: Layout y componentes base

- [ ] Crear `src/layouts/Base.astro`:
  - Meta tags: title, description, og:image, favicon
  - SEO: "AgroVoz — Asistente de Voz para la Agricultura Familiar Campesina"
  - CSS global con Tailwind
  - Sin Google Fonts, usar system-ui
- [ ] Crear `src/components/Header.astro`:
  - Logo (texto: "AgroVoz"), nav (Inicio, Cómo funciona, Equipo, Contacto)
  - Responsive: hamburger menu en mobile
  - Sticky header con backdrop-blur
- [ ] Crear `src/components/Footer.astro`:
  - "AgroVoz © 2026 — INACAP Temuco"
  - Links: Postulación Crea INACAP, GitHub
  - Disclaimer: "Proyecto estudiantil — Desafío Crea INACAP 2026"
- Archivos a crear: `Base.astro`, `Header.astro`, `Footer.astro`

### T4.3: Secciones de la landing

- [ ] `index.astro` — componer layout + secciones:
  - **Hero**: "Tu voz tiene el precio justo" — headline + subheadline + CTA
  - **Problema**: "205.000 agricultores pierden hasta 60% del precio por no tener información"
  - **Solución**: Cómo funciona, 3 pasos visuales (audio → consulta → respuesta)
  - **Stack**: Iconos de WhatsApp, Whisper, ODEPA, OpenWeatherMap — "100% open-source"
  - **Impacto**: Cifras clave (ODS 2, 8, 10, 13)
  - **Equipo**: 3 tarjetas con foto, rol, fortalezas (datos del informe)
  - **Contacto**: Formulario simple (nombre, email, mensaje)
- Archivos a crear: `src/pages/index.astro` (o modificar existente)

### T4.4: Componentes reutilizables

- [ ] Crear `src/components/Hero.astro`:
  - Props: `title`, `subtitle`, `ctaText`, `ctaLink`
- [ ] Crear `src/components/FeatureCard.astro`:
  - Props: `icon`, `title`, `description`
  - Usar íconos SVG inline (Lucide)
- [ ] Crear `src/components/StatCard.astro`:
  - Props: `number`, `label`, `source`
- [ ] Crear `src/components/TeamCard.astro`:
  - Props: `name`, `role`, `description`
- [ ] Crear `src/components/ContactForm.astro`:
  - Form con Netlify/Formspree o solo mailto para MVP
- Archivos a crear: todos los componentes

### T4.5: Build y deploy

- [ ] `bun run build` — debe generar `dist/` sin errores
- [ ] Probar local: `bun run preview`
- [ ] Configurar deploy:
  - Opción A: Dokploy static app (dashboard, conecta repo GitHub, build `cd landing && bun run build`)
  - Opción B: Cloudflare Pages (gratis, CDN global, más simple)
  - Domain: `agrovoz.cl` con HTTPS automatico
- [ ] Agregar target `deploy-landing` en `landing/Makefile` o `package.json`
- [ ] Test visual: acceder a `http://localhost:4321` y verificar todas las secciones
- Archivos a modificar: `docker-compose.yml`

---

## Validación

- Checklist: `docs/validation/04-frontend-checklist.md`
- Comandos:
  ```bash
  cd landing && bun run build
  cd landing && bun run preview  # test visual
  ```

## Output esperado

```
landing/
├── astro.config.mjs
├── tailwind.config.mjs
├── package.json
├── tsconfig.json
├── public/
│   └── favicon.svg
└── src/
    ├── layouts/
    │   └── Base.astro
    ├── pages/
    │   └── index.astro
    └── components/
        ├── Header.astro
        ├── Footer.astro
        ├── Hero.astro
        ├── FeatureCard.astro
        ├── StatCard.astro
        ├── TeamCard.astro
        └── ContactForm.astro
```

---

## Estado de implementación

**Fase completada.** Verificado contra `main` (PR #57 landing, PR #66 migración Astro 7 + Tailwind 4.3). Desviaciones respecto al spec:

- **Astro 7 + Tailwind 4.3**: migrado desde Astro 5 (PR #66). `compressHTML: true`.
- **Componentes extra**: además de los 8 del spec, hay `DemoPhone.astro`, `PlanCard.astro`, `Waveform.astro` (10 componentes total).
- **Sin Google Fonts**: system-ui confirmado.
- **Icons Lucide**: SVG inline confirmado.
- **Build**: `bun run build` genera `dist/` sin errores (391ms, 1 página). Playwright verificó 0 console errors.
