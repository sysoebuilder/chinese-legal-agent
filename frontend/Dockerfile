# Docker 官方在 ECR Public 发布的基础镜像。
FROM public.ecr.aws/docker/library/node:24-alpine AS build
ENV RAYON_NUM_THREADS=2
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM public.ecr.aws/docker/library/nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
