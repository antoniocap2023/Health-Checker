# syntax=docker/dockerfile:1
# ============================================================================
# Frontend image — TWO stages:
#   1) "build": use Node to compile the React app into static files (HTML/JS/CSS).
#   2) final:   use nginx to SERVE those static files and reverse-proxy /api.
# Only the final stage becomes the shipped image, so Node and node_modules (big,
# build-only) are discarded. This is a "multi-stage build": small, clean result.
# ============================================================================

# ---- Stage 1: build --------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# Copy ONLY the manifests first, then install — same cached-layer trick as the
# backend: dependencies reinstall only when package.json / lock file change, not
# on every source edit. `npm ci` installs the EXACT versions from the lock file
# (reproducible), unlike `npm install` which can drift.
COPY package*.json ./
RUN npm ci

# Now the source, then produce the optimized static bundle in /app/dist.
COPY . .
RUN npm run build

# ---- Stage 2: serve --------------------------------------------------------
# Start fresh from a tiny nginx image; nothing from Node comes along except the
# built files we explicitly copy over.
FROM nginx:alpine

# apache2-utils provides `htpasswd`, used by the startup script to build the Basic
# Auth password file from env vars.
RUN apk add --no-cache apache2-utils

# The compiled site goes where nginx serves from by default.
COPY --from=build /app/dist /usr/share/nginx/html
# Our server config (static serving + the /api reverse proxy) replaces the default.
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Startup hook: nginx:alpine runs everything in /docker-entrypoint.d/ before starting
# nginx. This one enables Basic Auth when BASIC_AUTH_* env vars are present.
COPY 40-basic-auth.sh /docker-entrypoint.d/40-basic-auth.sh
RUN chmod +x /docker-entrypoint.d/40-basic-auth.sh

EXPOSE 80
# The base nginx image already starts nginx in the foreground, so no CMD needed.
