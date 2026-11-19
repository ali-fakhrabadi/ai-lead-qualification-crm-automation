# syntax=docker/dockerfile:1

# ---- build: type-check and bundle the single-page app ------------------------------------------
FROM node:22.23.5-alpine3.24@sha256:1314fb30ec37592d5845e5a160d522c0e680972aa534549940b8d73bde88481a AS build
# npm swaps the registry host in package-lock.json for this one, so a mirror can be used; the
# lock file's integrity hashes are checked either way.
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund --ignore-scripts --registry="$NPM_REGISTRY"
COPY index.html tsconfig.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npm run build

# ---- runtime: static files behind an unprivileged nginx ----------------------------------------
FROM nginxinc/nginx-unprivileged:1.30.6-alpine3.24@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03 AS runtime
# Pick up security fixes released after the base image was built: Alpine packages, and nginx
# itself from nginx.org's signed repository (the image pins its version; 1.30.7 fixes
# CVE-2026-121810 and CVE-2026-113425 found by Trivy in 1.30.6). The dynamic modules are not
# used by this configuration and are removed rather than kept at the old version.
ARG NGINX_FIXED=1.30.7-r1
USER root
RUN apk upgrade --no-cache \
 && apk del --no-cache nginx-module-geoip nginx-module-image-filter nginx-module-njs nginx-module-xslt \
 && apk add --no-cache -X https://nginx.org/packages/alpine/v3.24/main "nginx=${NGINX_FIXED}" \
 && rm -f /etc/nginx/*.apk-new \
 && nginx -t
USER 101
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
