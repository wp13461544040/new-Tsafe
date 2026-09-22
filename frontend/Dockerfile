# 构建上下文同样是**项目根目录**（与 backend 保持一致，compose 里只写一个 context）。
FROM node:20-alpine AS builder

WORKDIR /app

# 用 package-lock.json 走 `npm ci`：可复现构建。
# `npm install` 会按 semver 拉到新版本 —— 同一个 tag 的镜像内容可能不同，
# 出问题时无法判断是代码还是依赖变了。
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ── 运行阶段 ──
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
