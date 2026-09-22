#!/usr/bin/env bash
#
# 全新 Ubuntu 服务器一键部署。
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/wp13461544040/new-Tsafe/main/deploy.sh | sudo bash
#
# 私有镜像（默认就是私有）需要先给个 GitHub Token：
#   curl -fsSL .../deploy.sh | sudo GH_TOKEN=ghp_xxx bash
#
# 可选环境变量：
#   GH_OWNER      GitHub 用户名，默认 wp13461544040
#   GH_TOKEN      拉私有镜像用的 PAT（需 read:packages 权限）
#   HTTP_PORT     对外端口，默认 80
#   IMAGE_TAG     镜像标签，默认 latest
#   ADMIN_PASSWORD  指定初始管理员密码，留空则随机生成
#   INSTALL_DIR   部署目录，默认 /opt/jev-register

set -euo pipefail

GH_OWNER="${GH_OWNER:-wp13461544040}"
GH_REPO="${GH_REPO:-new-Tsafe}"
GH_TOKEN="${GH_TOKEN:-}"
HTTP_PORT="${HTTP_PORT:-80}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/jev-register}"

# ghcr 的镜像名必须全小写，而仓库名带大写字母 ⇒ 这里转一次
REPO_LC="$(echo "${GH_OWNER}/${GH_REPO}" | tr '[:upper:]' '[:lower:]')"
COMPOSE_URL="https://raw.githubusercontent.com/${GH_OWNER}/${GH_REPO}/main/docker-compose.prod.yml"

info()  { echo -e "\033[1;34m==>\033[0m $*"; }
ok()    { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn()  { echo -e "\033[1;33m[!]\033[0m $*"; }
die()   { echo -e "\033[1;31m[X]\033[0m $*" >&2; exit 1; }

# ── 0. 前置检查 ────────────────────────────────────────────────────────
[[ "${EUID}" -eq 0 ]] || die "需要 root 权限：请用 sudo 执行"

ARCH="$(uname -m)"
case "${ARCH}" in
  x86_64|amd64) ;;
  aarch64|arm64)
    # 镜像目前只构建 linux/amd64（多平台构建要走 QEMU，慢一个量级）。
    # ARM 服务器拉下来会报 "no matching manifest"，先说清而不是让它失败。
    die "检测到 ARM 架构（${ARCH}），但镜像只有 amd64。
    需要在 .github/workflows/docker-publish.yml 的 build-push 步骤加：
        platforms: linux/amd64,linux/arm64
    然后重新构建。" ;;
  *) warn "未预期的架构 ${ARCH}，继续尝试" ;;
esac

# ── 1. 安装 Docker ─────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  ok "Docker 与 compose 插件已就绪：$(docker --version | cut -d, -f1)"
else
  info "安装 Docker（用官方脚本，会自动配好 apt 源与 compose 插件）"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl >/dev/null

  curl -fsSL https://get.docker.com | sh >/dev/null 2>&1 \
    || die "Docker 安装失败。手动装一次再重跑：curl -fsSL https://get.docker.com | sh"

  systemctl enable --now docker >/dev/null 2>&1 || true
  docker compose version >/dev/null 2>&1 \
    || die "compose 插件缺失。补装：apt-get install -y docker-compose-plugin"
  ok "Docker 安装完成：$(docker --version | cut -d, -f1)"
fi

# ── 2. 登录 ghcr（私有镜像必需）─────────────────────────────────────────
if [[ -n "${GH_TOKEN}" ]]; then
  info "登录 ghcr.io"
  echo "${GH_TOKEN}" | docker login ghcr.io -u "${GH_OWNER}" --password-stdin >/dev/null \
    || die "ghcr.io 登录失败：检查 GH_TOKEN 是否有 read:packages 权限"
  ok "已登录 ghcr.io"
else
  warn "未提供 GH_TOKEN —— 仅当镜像已设为 Public 才能拉取"
fi

# ── 3. 准备部署目录 ────────────────────────────────────────────────────
info "部署目录 ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"/{result,exports}
cd "${INSTALL_DIR}"

curl -fsSL "${COMPOSE_URL}" -o docker-compose.yml \
  || die "下载 compose 文件失败：${COMPOSE_URL}
    仓库是私有的话，这个 raw 地址也需要认证 —— 手动把文件传上来再重跑。"

# 参数落到 .env，让后续 `docker compose` 命令不用重复传
cat > .env <<EOF
GH_OWNER=${GH_OWNER}
HTTP_PORT=${HTTP_PORT}
IMAGE_TAG=${IMAGE_TAG}
ADMIN_PASSWORD=${ADMIN_PASSWORD}
EOF
chmod 600 .env
ok "配置就绪"

# ── 4. 拉取并启动 ──────────────────────────────────────────────────────
info "拉取镜像（首次约 200MB，按网速可能要几分钟）"
if ! docker compose pull 2>&1 | tail -5; then
  die "镜像拉取失败。两种常见原因：
    1) 镜像还是 Private ⇒ 去 GitHub 仓库 → Packages 改成 Public，
       或带 GH_TOKEN 重跑本脚本
    2) Actions 还没构建完 ⇒ 看仓库 Actions 页面，等两个 job 变绿"
fi

info "启动服务"
docker compose up -d

# ── 5. 等健康检查 ──────────────────────────────────────────────────────
info "等待后端就绪"
for i in $(seq 1 60); do
  if docker compose exec -T backend python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health',timeout=2).status==200 else 1)" \
      >/dev/null 2>&1; then
    ok "后端已就绪（${i}s）"
    break
  fi
  [[ "${i}" -eq 60 ]] && {
    warn "60 秒内未就绪，打印日志便于排查："
    docker compose logs --tail 40 backend
    die "启动超时"
  }
  sleep 1
done

# ── 6. 放通防火墙 ──────────────────────────────────────────────────────
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "${HTTP_PORT}/tcp" >/dev/null 2>&1 && ok "ufw 已放通 ${HTTP_PORT}/tcp"
fi

# ── 7. 取初始管理员密码 ────────────────────────────────────────────────
# 随机密码**只在首次建库时打印一次**，所以从日志里捞。
# 重复部署（库已存在）时日志里不会有，这是正常的。
CRED="$(docker compose logs backend 2>/dev/null | grep -A3 '初始管理员已创建' | tail -4 || true)"

IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
PORT_SUFFIX=""
[[ "${HTTP_PORT}" != "80" ]] && PORT_SUFFIX=":${HTTP_PORT}"

echo
echo "════════════════════════════════════════════════════════════════"
ok "部署完成"
echo
echo "  管理后台    http://${IP}${PORT_SUFFIX}"
echo "  对外提取页  http://${IP}${PORT_SUFFIX}/extract   （无需登录）"
echo
if [[ -n "${ADMIN_PASSWORD}" ]]; then
  echo "  管理员      admin / （你指定的 ADMIN_PASSWORD）"
elif [[ -n "${CRED}" ]]; then
  echo "  初始管理员凭据（只显示这一次，请立即保存）："
  echo "${CRED}" | sed 's/^/    /'
else
  echo "  管理员      数据库已存在，沿用原有账号"
  echo "              忘记密码可执行：cd ${INSTALL_DIR} && docker compose down &&"
  echo "              docker volume rm \$(docker compose config --volumes | head -1) && sudo bash deploy.sh"
fi
echo
echo "  部署目录    ${INSTALL_DIR}"
echo "  常用命令    cd ${INSTALL_DIR}"
echo "                docker compose logs -f backend    # 看日志"
echo "                docker compose pull && docker compose up -d   # 升级"
echo "                docker compose down               # 停止"
echo
echo "  下一步：登录后到「邮箱配置」页添加邮箱服务，再去「注册任务」跑批。"
echo "════════════════════════════════════════════════════════════════"
