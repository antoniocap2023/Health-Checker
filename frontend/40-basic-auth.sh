#!/bin/sh
# Runs at container start (nginx:alpine executes everything in /docker-entrypoint.d/
# before launching nginx). It turns the BASIC_AUTH_* env vars into an nginx auth
# config — or writes an empty one when they're absent, so auth is simply OFF.
#
# Why env-driven: the password is a secret. On AWS it's injected from SSM Parameter
# Store; locally we leave it unset so `docker compose up` needs no password.
set -e

AUTH_INC=/etc/nginx/conf.d/auth.inc

if [ -n "$BASIC_AUTH_USER" ] && [ -n "$BASIC_AUTH_PASSWORD" ]; then
  # htpasswd (-n print, -b take password as arg, -B bcrypt) writes "user:hash".
  htpasswd -nbB "$BASIC_AUTH_USER" "$BASIC_AUTH_PASSWORD" > /etc/nginx/.htpasswd
  printf 'auth_basic "Restricted";\nauth_basic_user_file /etc/nginx/.htpasswd;\n' > "$AUTH_INC"
  echo "[basic-auth] ENABLED for user '$BASIC_AUTH_USER'"
else
  # Empty include = no auth (local dev / when no password is configured).
  : > "$AUTH_INC"
  echo "[basic-auth] disabled (set BASIC_AUTH_USER and BASIC_AUTH_PASSWORD to enable)"
fi
