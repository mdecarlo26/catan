# Frontend image: multi-stage build.
#   1. deps  - install node_modules (cached unless package*.json changes)
#   2. build - `npm run build` -> static bundle in /app/dist
#   3. runtime - nginx serving that bundle, proxying /api and /ws to
#      the backend container (see nginx.conf)
# Built from ./frontend as the build context (see docker-compose.yml).
# docker-compose.dev.yml targets the "deps" stage directly and runs
# `vite dev` against a bind-mounted src instead of building this image.

# --- deps ---------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# --- build ---------------------------------------------------------------
FROM deps AS build

# Base URLs the built JS uses to reach the backend. Defaults are
# same-origin relative paths: nginx.conf (prod) proxies /api and /ws
# to the backend container, so the browser never needs an absolute
# backend URL baked into the bundle. docker-compose.dev.yml overrides
# these instead (as container env vars at `vite dev` time), since the
# Vite dev server and the backend run on different localhost ports
# there.
ARG VITE_API_URL=/api
ARG VITE_WS_URL=/ws
ENV VITE_API_URL=$VITE_API_URL \
    VITE_WS_URL=$VITE_WS_URL

# Copy the rest of the source. .dockerignore excludes node_modules/
# and dist/ so this never risks a host-platform node_modules
# shadowing the one `npm ci` just installed above, and doesn't
# invalidate on build output.
COPY . .
RUN npm run build

# --- runtime ---------------------------------------------------------------
FROM nginx:alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
