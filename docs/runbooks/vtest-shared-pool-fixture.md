# vtest Shared Pool fixture gate

- 固定 fixture tenant 是 `vtest-shared-pool`，仅由 `deploy-vtest` 在备份/迁移之后、应用容器部署之前幂等初始化；必须保持 `free`、`active` 和 `config.vtest_shared_pool_fixture=true`。
- 初始化使用既有 PostgreSQL 容器连接，在事务和 advisory lock 内执行；未标记的同名租户、schema/audit 缺失或任何 SQL 错误都会 fail closed，不触碰其他 tenant。
- vtest 的两个预签发 Ed25519 平台 JWT 必须同时包含该 tenant、各自 audience 和 `channel:internal` / `fleet:internal` scope。infra 只做 claims preflight，不生成、保存或打印 token；缺 scope 时由环境管理员使用 Vecta 的 `scripts/ops/mint-platform-service-tokens.mjs` 以现有私钥重签发后再部署。
- `mypc`/生产 inventory 不启用 fixture、E2E flag 或该 tenant scope；main 合并不等于生产部署授权。
