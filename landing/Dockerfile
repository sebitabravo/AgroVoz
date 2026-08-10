# AgroVoz Landing — build estatico Astro servido por nginx
#
# Los build types nativos de Dokploy no sirven para este caso:
#   - "static" no corre ningun comando de build, solo copia publishDirectory
#     ya existente en el repo (dist/ esta gitignored, correctamente).
#   - "nixpacks" detecto un snapshot de nixpkgs con Node 18.x, EOL y removido
#     del canal — build roto por drift de nix, no del proyecto.
# Dockerfile explicito en su lugar, mismo patron multi-stage que backend/Dockerfile.

FROM oven/bun:1-alpine AS builder
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY . .
RUN bun run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
