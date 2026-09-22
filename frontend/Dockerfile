# 构建上下文同样是**项目根目录**（与 backend 保持一致，compose 里只写一个 context）。
#
# 🔴 Node 版本要与生成 package-lock.json 的 npm 大版本对齐：
#    lock 文件由 npm 11 生成，而 node:20-alpine 自带 npm 10 ——
#    `npm ci` 对 lock 的校验比 `npm install` 严格，跨大版本会直接 exit 1。
#    （2026-09-22 实测：node:20 构建失败，换 24 通过。）
FROM node:24-alpine AS builder

WORKDIR /app

# 用 package-lock.json 走 `npm ci`：可复现构建。
# `npm install` 会按 semver 拉到新版本 —— 同一个 tag 的镜像内容可能不同，
# 出问题时无法判断是代码还是依赖变了。
COPY frontend/package*.json ./
# 关掉 audit/fund：它们要额外发网络请求，在 CI 里只会拖慢并增加失败面。
RUN npm ci --no-audit --no-fund

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
