FROM node:26.1.0-alpine3.23 AS dependencies
WORKDIR /opt/forge
COPY package.json package-lock.json ./
RUN npm ci

FROM dependencies AS dev
COPY . .
EXPOSE 8080
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM dependencies AS build
COPY . .
RUN npm run build

FROM build AS test
RUN node --version && npm --version && npm run typecheck && npm test && npm run build && npm audit

FROM nginx:1.30.2-alpine AS production
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /opt/forge/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
