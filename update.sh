#!/usr/bin/env bash
#
# 服务器更新（拉新镜像 + 重启），**更新前自动备份数据**。
#
# 用法（在服务器上）：
#   curl -fsSL https://raw.githubusercontent.com/wp13461544040/new-Tsafe/main/update.sh | sudo bash
#
# 私有镜像且本机凭据已过期时：
#   curl -fsSL .../update.sh | sudo GH_TOKEN=ghp_xxx bash
#
# 可选环境变量：
#   INSTALL_DIR   部署目录，默认 /opt/jev-register
#   IMAGE_TAG     要更新到的标签，默认沿用 .env 里的（通常是 latest）
#   GH_TOKEN      私有镜像拉取凭据（read:packages）
#   GH_OWNER      GitHub 用户名，默认 wp13461544040
#   KEEP_BACKUPS  保留几份备份，默认 10
#   SKIP_BACKUP   =1 跳过备份（**不建议**，见下面说明）
#
# 🔴 为什么必须备份：
#    后端容器启动时会自动跑 run_migrations()。其中 operation_logs 的迁移是
#    「建新表 + 复制数据 + 删旧表 + 改名」—— SQLite 不支持 ALTER TABLE 去掉
#    NOT NULL，只能这么做，而这个过程**不可逆**。迁移中断或新版本有问题时，
#    没有备份就回不到原状态。
#    备份内容是整个 data 目录，不只是 admin.db —— .secret_key 也在里面，
#    丢了它所有人的登录态会失效（JWT 签名密钥变了）。

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/jev-register}"
GH_OWNER="${GH_OWNER:-wp13461544040}"
GH_TOKEN="${GH_TOKEN:-}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
SKIP_BACKUP="${SKIP_BACKUP:-0}"

info() { echo -e "\033[1;34m==>\033[0m $*"; }
ok()   { echo -e "\033[1;32m[OK]\033[0m $*"; }
warn() { echo -e "\033[1;33m[!]\033[0m $*"; }
die()  { echo -e "\033[1;31m[X]\033[0m $*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "需要 root 权限：请用 sudo 执行"
[[ -d "${INSTALL_DIR}" ]] || die "找不到部署目录 ${INSTALL_DIR}
    若部署在别处：INSTALL_DIR=/your/path sudo bash update.sh"

cd "${INSTALL_DIR}"
[[ -f docker-compose.yml ]] || die "${INSTALL_DIR} 下没有 docker-compose.yml —— 这里不像部署目录"

# 🔴 备份阶段会先停 backend。若之后任何一步失败（登录过期、拉取失败、
#    甚至脚本被 Ctrl-C），服务会**停在那里不起来** —— 用户以为只是更新失败，
#    实际是站点已经下线了。这个陷阱保证异常退出时把服务拉回来。
BACKEND_STOPPED=0
restore_on_abort() {
  local rc=$?
  if [[ "${rc}" -ne 0 && "${BACKEND_STOPPED}" == "1" ]]; then
    echo
    warn "更新中断，正在把后端恢复到运行状态"
    docker compose start backend >/dev/null 2>&1 || true
    docker compose ps 2>/dev/null || true
  fi
  exit "${rc}"
}
trap restore_on_abort EXIT INT TERM

# ── 1. 记录当前版本（回滚要用）────────────────────────────────────────
# 记 image ID 而不是 tag：latest 会被新镜像顶掉，记 tag 等于没记。
# 用 docker inspect 容器而不是 `docker compose images -q` —— 后者的 -q
# 在部分 compose 版本里不输出 image ID（甚至不支持），拿不到就没法回滚。
# 容器名在 compose 里用 container_name 固定过，可以直接引用。
OLD_BACKEND="$(docker inspect --format '{{.Image}}' jev-backend 2>/dev/null || true)"
OLD_FRONTEND="$(docker inspect --format '{{.Image}}' jev-frontend 2>/dev/null || true)"

info "当前版本"
echo "    backend  ${OLD_BACKEND:-<未运行>}"
echo "    frontend ${OLD_FRONTEND:-<未运行>}"

# ── 2. 备份数据 ────────────────────────────────────────────────────────
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="${INSTALL_DIR}/backups/data-${TS}"

if [[ "${SKIP_BACKUP}" == "1" ]]; then
  warn "已跳过备份（SKIP_BACKUP=1）—— 迁移不可逆，出问题无法回退"
else
  info "备份数据到 backups/data-${TS}"
  mkdir -p "${INSTALL_DIR}/backups"

  # 先停后端再拷：SQLite 在写入过程中被复制会拿到不一致的快照
  # （WAL 里的事务没落盘）。停几秒换一个可用的备份，值得。
  docker compose stop backend >/dev/null 2>&1 || true
  BACKEND_STOPPED=1

  if docker compose cp backend:/app/backend/data "${BACKUP_DIR}" 2>/dev/null; then
    DB_FILE="$(find "${BACKUP_DIR}" -name 'admin.db' -print -quit 2>/dev/null || true)"
    if [[ -n "${DB_FILE}" ]]; then
      ok "已备份 $(du -h "${DB_FILE}" | cut -f1) 的数据库 + 密钥文件"
    else
      warn "备份目录里没找到 admin.db —— 可能是首次部署还没建库"
    fi
  else
    docker compose start backend >/dev/null 2>&1 || true
    die "备份失败。容器可能不存在，先确认服务状态：docker compose ps
    确实要跳过备份才继续：SKIP_BACKUP=1 sudo bash update.sh"
  fi
fi

# ── 3. 登录 ghcr（私有镜像）────────────────────────────────────────────
if [[ -n "${GH_TOKEN}" ]]; then
  info "登录 ghcr.io"
  echo "${GH_TOKEN}" | docker login ghcr.io -u "${GH_OWNER}" --password-stdin >/dev/null \
    || die "ghcr.io 登录失败：检查 GH_TOKEN 是否有 read:packages 权限"
  ok "已登录"
fi

# ── 4. 更新 compose 文件 ───────────────────────────────────────────────
# compose 文件本身也可能变（加了卷、改了健康检查）。只 pull 镜像不更新它，
# 新镜像依赖的配置就缺了，表现为容器起不来或行为不对。
COMPOSE_URL="https://raw.githubusercontent.com/${GH_OWNER}/new-Tsafe/main/docker-compose.prod.yml"
info "更新 compose 文件"
if curl -fsSL "${COMPOSE_URL}" -o docker-compose.yml.new 2>/dev/null; then
  if cmp -s docker-compose.yml docker-compose.yml.new; then
    rm -f docker-compose.yml.new
    ok "compose 文件无变化"
  else
    cp docker-compose.yml "docker-compose.yml.bak-${TS}"
    mv docker-compose.yml.new docker-compose.yml
    ok "compose 文件已更新（旧版存为 docker-compose.yml.bak-${TS}）"
  fi
else
  rm -f docker-compose.yml.new
  warn "下载 compose 文件失败（仓库私有时 raw 地址也需认证），沿用本地版本"
fi

# ── 5. 拉新镜像 ────────────────────────────────────────────────────────
# 先 pull 再 up：pull 失败时现有服务**不受影响**，只是没更新。
# 反过来（先 down 再 pull）失败就变成服务中断。
#
# 🔴 不要写成 `docker compose pull | grep ... | tail`：脚本开了 pipefail，
#    而 grep 没匹配到内容时返回 1 ⇒ 拉取明明成功却被判成失败并中止。
#    这里把输出落到临时文件，退出码单独判断。
info "拉取新镜像"
PULL_LOG="$(mktemp)"
if ! docker compose pull >"${PULL_LOG}" 2>&1; then
  tail -20 "${PULL_LOG}"
  rm -f "${PULL_LOG}"
  docker compose start backend >/dev/null 2>&1 || true
  die "镜像拉取失败。常见原因：
    1) GitHub Actions 还没构建完 ⇒ 看仓库 Actions 页面，等 job 变绿
    2) 镜像是 Private 且本机凭据过期 ⇒ GH_TOKEN=ghp_xxx 重跑
    现有服务未受影响，仍在运行旧版本。"
fi
tail -6 "${PULL_LOG}" | sed 's/^/    /'
rm -f "${PULL_LOG}"
ok "镜像已拉取"

# ── 6. 重启服务 ────────────────────────────────────────────────────────
# 不做「已是最新就跳过」的判断：`up -d` 本身幂等，镜像没变时不会重建容器。
# 少一处版本比较逻辑，就少一处判断错导致的「该更新却没更新」。
info "应用新版本（容器启动时会自动跑数据库迁移）"
docker compose up -d
# 服务已重新起来，之后的失败不再需要「恢复停止的后端」这种兜底
BACKEND_STOPPED=0

# ── 7. 健康检查 ────────────────────────────────────────────────────────
info "等待后端就绪"
HEALTHY=0
for i in $(seq 1 90); do
  if docker compose exec -T backend python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health',timeout=2).status==200 else 1)" \
      >/dev/null 2>&1; then
    HEALTHY=1
    ok "后端已就绪（${i}s）"
    break
  fi
  sleep 1
done

if [[ "${HEALTHY}" -eq 0 ]]; then
  echo
  warn "90 秒内后端未就绪。日志："
  docker compose logs --tail 50 backend
  echo
  # 🔴 刻意**不自动回滚**：恢复数据库会覆盖新版本可能已经写入的数据，
  #    该不该覆盖只有人能判断。这里把回滚命令给全，由你决定。
  echo "════════════════════════════════════════════════════════════════"
  warn "回滚步骤（按需执行）："
  echo
  echo "  # 1) 退回旧镜像"
  if [[ -n "${OLD_BACKEND}" ]]; then
    echo "  docker tag ${OLD_BACKEND} rollback-backend:prev"
    echo "  # 编辑 docker-compose.yml，把 backend 的 image 改成 rollback-backend:prev"
  else
    echo "  # 未记录到旧镜像 ID（更新前服务可能没在运行）"
  fi
  echo
  echo "  # 2) 恢复数据（会覆盖当前数据，确认后再做）"
  if [[ "${SKIP_BACKUP}" != "1" ]]; then
    echo "  cd ${INSTALL_DIR}"
    echo "  docker compose stop backend"
    echo "  docker compose cp ${BACKUP_DIR}/. backend:/app/backend/data/"
    echo "  docker compose up -d"
  else
    echo "  # 本次跳过了备份，没有可恢复的数据"
  fi
  echo "════════════════════════════════════════════════════════════════"
  die "更新未成功，服务可能不可用"
fi

# ── 8. 清理与收尾 ──────────────────────────────────────────────────────
# 只删悬空镜像（被新版本顶掉的旧层）。不用 -a，那会删掉所有未被容器
# 引用的镜像，包括你刚 tag 出来准备回滚的那个。
info "清理旧镜像层"
FREED="$(docker image prune -f 2>/dev/null | grep 'Total reclaimed' || echo '无可清理')"
echo "    ${FREED}"

if [[ "${SKIP_BACKUP}" != "1" ]]; then
  info "清理旧备份（保留最近 ${KEEP_BACKUPS} 份）"
  cd "${INSTALL_DIR}/backups"
  ls -1dt data-* 2>/dev/null | tail -n "+$((KEEP_BACKUPS + 1))" | while read -r old; do
    rm -rf "${old}" && echo "    已删 ${old}"
  done
  cd "${INSTALL_DIR}"
fi

echo
echo "════════════════════════════════════════════════════════════════"
ok "更新完成"
echo
docker compose ps
echo
echo "  本次备份    backups/data-${TS}"
echo "  查看日志    cd ${INSTALL_DIR} && docker compose logs -f backend"
echo
echo "  新功能：账号池巡检（验活）"
echo "    · 系统设置 → 账号巡检：打开开关并设间隔（默认关闭）"
echo "    · 账号池页面：可手动触发、看每个号的巡检结论"
echo "    · 探测不消耗账号额度"
echo "════════════════════════════════════════════════════════════════"
