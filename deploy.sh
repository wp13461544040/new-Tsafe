#!/usr/bin/env bash
#
# 全新 Ubuntu 服务器一键部署（含域名 + 自动 HTTPS）。
#
# 纯 HTTP（用 IP 访问）：
#   curl -fsSL https://raw.githubusercontent.com/wp13461544040/new-Tsafe/main/deploy.sh | sudo bash
#
# 带域名 + 自动申请 SSL 证书：
#   curl -fsSL .../deploy.sh | sudo DOMAIN=julypopo.site ACME_EMAIL=you@mail.com bash
#
# 私有镜像（默认就是私有）需要 GitHub Token：
#   curl -fsSL .../deploy.sh | sudo DOMAIN=julypopo.site GH_TOKEN=ghp_xxx bash
#
# 可选环境变量：
#   DOMAIN          域名。设了就自动申请 Let's Encrypt 证书并强制 HTTPS
#   ACME_EMAIL      证书到期提醒邮箱（强烈建议填）
#   GH_OWNER        GitHub 用户名，默认 wp13461544040
#   GH_TOKEN        拉私有镜像用的 PAT（需 read:packages）
#   HTTP_PORT       HTTP 端口，默认 80（申请证书时必须是 80）
#   HTTPS_PORT      HTTPS 端口，默认 443
#   IMAGE_TAG       镜像标签，默认 latest
#   ADMIN_PASSWORD  初始管理员密码，留空则随机生成
#   INSTALL_DIR     部署目录，默认 /opt/jev-register
#   SKIP_DNS_CHECK  =1 跳过 DNS 预检（CDN/内网场景）

set -euo pipefail

DOMAIN="${DOMAIN:-}"
ACME_EMAIL="${ACME_EMAIL:-}"
GH_OWNER="${GH_OWNER:-wp13461544040}"
GH_REPO="${GH_REPO:-new-Tsafe}"
GH_TOKEN="${GH_TOKEN:-}"
HTTP_PORT="${HTTP_PORT:-80}"
HTTPS_PORT="${HTTPS_PORT:-443}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/jev-register}"
SKIP_DNS_CHECK="${SKIP_DNS_CHECK:-0}"

COMPOSE_URL="https://raw.githubusercontent.com/${GH_OWNER}/${GH_REPO}/main/docker-compose.prod.yml"

info() { echo -e "\033[1;34m==>\033[0m $*"; }
ok()   { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
die()  { echo -e "\033[1;31m[X]\033[0m $*" >&2; exit 1; }

# ── 0. 前置检查 ────────────────────────────────────────────────────────
[[ "${EUID}" -eq 0 ]] || die "需要 root 权限：请用 sudo 执行"

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64|amd64) ;;
  aarch64|arm64)
    # 镜像目前只构建 linux/amd64（多平台要走 QEMU，慢一个量级）。
    # 先说清而不是让 docker pull 报 "no matching manifest"。
    die "检测到 ARM 架构（${ARCH}），但镜像只有 amd64。
    需要在 .github/workflows/docker-publish.yml 的 build-push 步骤加：
        platforms: linux/amd64,linux/arm64
    重新构建后再部署。" ;;
  *) warn "未预期的架构 ${ARCH}，继续尝试" ;;
esac

# 申请证书走 HTTP-01 校验，Let's Encrypt 只会访问 80 端口，改不了。
if [[ -n "${DOMAIN}" && "${HTTP_PORT}" != "80" ]]; then
  die "要申请证书就必须用 80 端口（Let's Encrypt 的 HTTP-01 校验固定访问 80）。
    当前 HTTP_PORT=${HTTP_PORT}。
    要么改回 80，要么去掉 DOMAIN 只用 HTTP。"
fi

# ── 1. 安装 Docker ─────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  ok "Docker 与 compose 插件已就绪：$(docker --version | cut -d, -f1)"
else
  info "安装 Docker（官方脚本，会配好 apt 源与 compose 插件）"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl dnsutils >/dev/null 2>&1 || true

  curl -fsSL https://get.docker.com | sh >/dev/null 2>&1 \
    || die "Docker 安装失败。手动装一次再重跑：curl -fsSL https://get.docker.com | sh"

  systemctl enable --now docker >/dev/null 2>&1 || true
  docker compose version >/dev/null 2>&1 \
    || die "compose 插件缺失。补装：apt-get install -y docker-compose-plugin"
  ok "Docker 安装完成：$(docker --version | cut -d, -f1)"
fi

# ── 2. 域名解析预检 ────────────────────────────────────────────────────
SERVER_IP="$(curl -fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"

if [[ -n "${DOMAIN}" && "${SKIP_DNS_CHECK}" != "1" ]]; then
  info "检查 ${DOMAIN} 的解析"
  command -v dig >/dev/null 2>&1 || apt-get install -y -qq dnsutils >/dev/null 2>&1 || true

  if command -v dig >/dev/null 2>&1; then
    RESOLVED="$(dig +short A "${DOMAIN}" @1.1.1.1 2>/dev/null | tail -1 || true)"
  else
    RESOLVED="$(getent hosts "${DOMAIN}" 2>/dev/null | awk '{print $1}' | tail -1 || true)"
  fi

  if [[ -z "${RESOLVED}" ]]; then
    # 🔴 解析没生效就申请证书 = 必然失败，而且会消耗 Let's Encrypt 的失败配额
    #    （每小时 5 次），撞了之后要等一小时才能重试。所以这里直接拦住。
    die "${DOMAIN} 没有解析到任何 IP。
    请先去域名服务商加一条 A 记录：
        类型 A    主机 @（或留空）    值 ${SERVER_IP:-<本机公网IP>}
    DNS 生效通常几分钟到几十分钟，用这条命令确认：
        dig +short A ${DOMAIN}
    确认解析对了再重跑本脚本。
    （用 CDN 或内网自签场景可加 SKIP_DNS_CHECK=1 跳过）"
  fi

  if [[ -n "${SERVER_IP}" && "${RESOLVED}" != "${SERVER_IP}" ]]; then
    warn "${DOMAIN} 解析到 ${RESOLVED}，而本机公网 IP 是 ${SERVER_IP}"
    warn "若你在用 Cloudflare 等 CDN 的代理模式，这是正常的；"
    warn "但 CDN 代理模式下 Let's Encrypt 校验会被 CDN 拦截 ——"
    warn "请先把 DNS 记录切成「仅 DNS」(灰云) 等证书签发成功后再开代理。"
    sleep 3
  else
    ok "${DOMAIN} -> ${RESOLVED}"
  fi
fi

# ── 3. 登录 ghcr（私有镜像必需）─────────────────────────────────────────
if [[ -n "${GH_TOKEN}" ]]; then
  info "登录 ghcr.io"
  echo "${GH_TOKEN}" | docker login ghcr.io -u "${GH_OWNER}" --password-stdin >/dev/null \
    || die "ghcr.io 登录失败：检查 GH_TOKEN 是否有 read:packages 权限"
  ok "已登录 ghcr.io"
else
  warn "未提供 GH_TOKEN —— 仅当镜像已设为 Public 才能拉取"
fi

# ── 4. 准备部署目录 ────────────────────────────────────────────────────
info "部署目录 ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"/{result,exports}
cd "${INSTALL_DIR}"

curl -fsSL "${COMPOSE_URL}" -o docker-compose.yml \
  || die "下载 compose 文件失败：${COMPOSE_URL}
    仓库是私有的话这个 raw 地址也需要认证 —— 手动把文件传上来再重跑。"

cat > .env <<EOF
GH_OWNER=${GH_OWNER}
HTTP_PORT=${HTTP_PORT}
HTTPS_PORT=${HTTPS_PORT}
IMAGE_TAG=${IMAGE_TAG}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
EOF
chmod 600 .env

# ── 5. 生成 Caddyfile ──────────────────────────────────────────────────
# Caddy 的自动 HTTPS：站点地址写成域名，它就会自己申请证书、到期前自动续期，
# 并把 HTTP 请求 301 到 HTTPS。不需要 certbot、不需要 cron。
if [[ -n "${DOMAIN}" ]]; then
  info "配置 ${DOMAIN} 的自动 HTTPS"
  {
    [[ -n "${ACME_EMAIL}" ]] && printf '{\n\temail %s\n}\n\n' "${ACME_EMAIL}"
    cat <<EOF
${DOMAIN} {
	reverse_proxy frontend:80

	# 安全响应头
	header {
		# 强制一年内只走 HTTPS（含子域）
		Strict-Transport-Security "max-age=31536000; includeSubDomains"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "SAMEORIGIN"
		Referrer-Policy "strict-origin-when-cross-origin"
		# 去掉暴露服务器类型的头
		-Server
	}

	encode gzip zstd

	log {
		output file /data/access.log {
			roll_size 10MiB
			roll_keep 3
		}
	}
}
EOF
  } > Caddyfile

  [[ -z "${ACME_EMAIL}" ]] && warn "未设 ACME_EMAIL —— 证书将要到期时收不到提醒邮件"
  ACCESS_URL="https://${DOMAIN}"
else
  info "未设置 DOMAIN，仅启用 HTTP（用 IP 访问）"
  cat > Caddyfile <<'EOF'
# 无域名模式：只监听 HTTP，不申请证书。
# 要启用 HTTPS 请带 DOMAIN=your-domain.com 重跑 deploy.sh。
:80 {
	reverse_proxy frontend:80
	encode gzip zstd
}
EOF
  PORT_SUFFIX=""
  [[ "${HTTP_PORT}" != "80" ]] && PORT_SUFFIX=":${HTTP_PORT}"
  ACCESS_URL="http://${SERVER_IP:-$(hostname -I | awk '{print $1}')}${PORT_SUFFIX}"
fi
ok "配置就绪"

# ── 6. 放通防火墙（在启动之前做，否则证书校验会被挡）──────────────────
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "${HTTP_PORT}/tcp" >/dev/null 2>&1 || true
  [[ -n "${DOMAIN}" ]] && ufw allow "${HTTPS_PORT}/tcp" >/dev/null 2>&1 || true
  ok "ufw 已放通 ${HTTP_PORT}${DOMAIN:+ 与 ${HTTPS_PORT}}"
fi

# ── 7. 拉取并启动 ──────────────────────────────────────────────────────
info "拉取镜像（首次约 200MB）"
if ! docker compose pull 2>&1 | tail -5; then
  die "镜像拉取失败。两种常见原因：
    1) 镜像还是 Private ⇒ 去 GitHub 仓库 → Packages 改成 Public，
       或带 GH_TOKEN 重跑本脚本
    2) Actions 还没构建完 ⇒ 看仓库 Actions 页面，等两个 job 变绿"
fi

info "启动服务"
docker compose up -d

# ── 8. 等后端就绪 ──────────────────────────────────────────────────────
info "等待后端就绪"
for i in $(seq 1 60); do
  if docker compose exec -T backend python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health',timeout=2).status==200 else 1)" \
      >/dev/null 2>&1; then
    ok "后端已就绪（${i}s）"
    break
  fi
  [[ "${i}" -eq 60 ]] && {
    warn "60 秒内未就绪，日志如下："
    docker compose logs --tail 40 backend
    die "启动超时"
  }
  sleep 1
done

# ── 9. 等证书签发 ──────────────────────────────────────────────────────
if [[ -n "${DOMAIN}" ]]; then
  info "等待 Let's Encrypt 签发证书（通常 10-30 秒）"
  CERT_OK=0
  for i in $(seq 1 90); do
    if docker compose exec -T caddy sh -c \
        "ls /data/caddy/certificates/*/${DOMAIN}/${DOMAIN}.crt" >/dev/null 2>&1; then
      CERT_OK=1
      ok "证书已签发（${i}s）"
      break
    fi
    sleep 1
  done

  if [[ "${CERT_OK}" -eq 0 ]]; then
    warn "90 秒内没看到证书。Caddy 会持续重试，先看它的日志："
    docker compose logs --tail 30 caddy
    echo
    warn "最常见的三个原因："
    warn "  1. 80 端口被云厂商安全组挡了（ufw 放通不等于安全组放通）"
    warn "  2. 域名解析还没生效，或指向了别的服务器"
    warn "  3. 用了 Cloudflare 代理模式（橙云）⇒ 先切「仅 DNS」再签发"
    warn "排查后无需重跑脚本，Caddy 自己会重试；也可 docker compose restart caddy"
  fi
fi

# ── 10. 取初始管理员密码 ───────────────────────────────────────────────
# 随机密码**只在首次建库时打印一次**，所以从日志里捞。
# 重复部署（库已存在）时日志里不会有，这是正常的。
CRED="$(docker compose logs backend 2>/dev/null | grep -A3 '初始管理员已创建' | tail -4 || true)"

echo
echo "════════════════════════════════════════════════════════════════"
ok "部署完成"
echo
echo "  管理后台    ${ACCESS_URL}"
echo "  对外提取页  ${ACCESS_URL}/extract   （无需登录，发给用户的就是这个）"
echo
if [[ -n "${ADMIN_PASSWORD}" ]]; then
  echo "  管理员      ${ADMIN_USERNAME:-admin} / （你指定的 ADMIN_PASSWORD）"
elif [[ -n "${CRED}" ]]; then
  echo "  初始管理员凭据（只显示这一次，请立即保存）："
  echo "${CRED}" | sed 's/^/    /'
else
  echo "  管理员      数据库已存在，沿用原有账号"
fi
echo
echo "  部署目录    ${INSTALL_DIR}"
echo "  常用命令    cd ${INSTALL_DIR}"
echo "                docker compose logs -f backend   # 后端日志"
echo "                docker compose logs -f caddy     # 证书/访问日志"
echo "                docker compose pull && docker compose up -d   # 升级"
echo "                docker compose down              # 停止"
echo
if [[ -n "${DOMAIN}" ]]; then
  echo "  证书由 Caddy 自动续期（到期前 30 天），不需要配 cron。"
  echo "  证书存在 caddy-data 卷里 —— docker compose down 不会删，"
  echo "  但 down -v 会（那样重建需要重新申请，注意 Let's Encrypt 每周 5 次限额）。"
  echo
fi
echo "  下一步：登录后到「邮箱配置」加邮箱服务并点「测试连接」，"
echo "          再去「注册任务」跑批。站点配置已内置默认值，不用动。"
echo "════════════════════════════════════════════════════════════════"
