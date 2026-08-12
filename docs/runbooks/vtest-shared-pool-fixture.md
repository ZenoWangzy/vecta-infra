# vtest Shared Pool fixture gate

- 固定 fixture tenant 是 `vtest-shared-pool`，仅由 `deploy-vtest` 在备份/迁移之后、应用容器部署之前幂等初始化；必须保持 `free`、`active` 和 `config.vtest_shared_pool_fixture=true`。
- 初始化使用既有 PostgreSQL 容器连接，在事务和 advisory lock 内执行；未标记的同名租户、`audit_log.tenant_id`/`FORCE ROW LEVEL SECURITY` 缺失或任何 SQL 错误都会 fail closed，不触碰其他 tenant。
- 每次 vtest deploy 在同一个 runner shell 内根据数据库当前 employee tenant 集合生成一次性 Ed25519 key pair 和三个 JWT（channel、fleet、fruit），并额外加入固定 fixture tenant。私钥只存在 mint 进程内；公钥和 token 仅以内存环境变量传给 Ansible，GitHub mask 后随进程退出销毁，不写 output/env/artifact，也不依赖旧长期 token secret。
- Ansible preflight 用同一公钥验证三个真实签名，并检查 issuer、audience、scope、`tid`、`tenant_ids`、`iat`、`exp` 和 fixture scope；失败即不部署。切换前会 quiesce、快照并停放 A2A/Fleet/Channel/ Fruit 容器，所有消费者从同一 bundle 重建；健康检查和 smoke 通过后才清理旧容器，失败则删除新容器并恢复完整旧集合。
- `mypc`/生产 inventory 不启用 fixture、E2E flag 或该 tenant scope；main 合并不等于生产部署授权。
