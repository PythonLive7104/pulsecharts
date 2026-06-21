# Build the React/Vite SPA, then serve the static bundle with nginx. nginx also
# reverse-proxies /api and /ws to the backend, so the browser talks to one origin.
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
