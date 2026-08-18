# Retired: vtest 平台 JWT bootstrap 交接

> Status: retired 2026-08-17. Historical handoff only; the vtest workflow,
> bootstrap, and control-plane entries were removed. Do not use this document
> as a deployment entry.

## 退役合并顺序

1. VectA hotfix 必须先合并，发布一个已经删除全部 infra workflow caller 的
   状态。
2. 确认该 caller-free 状态后，`vecta-infra` 的永久删除提交才能合并。

当前 dirty worktree 或未发布分支中仍存在 caller，不构成恢复兼容 workflow、
wrapper、fallback 或 runner 的理由。

## 生产镜像构建边界

保留的 production image build 只能从 `vecta-infra` main 人工触发，输入必须是
当前 VectA main HEAD 的完整 SHA，并经过 production environment 审批。成功 run
只构成独立的 exact-SHA image-build 证据，不构成合并、部署或生产健康证据，也不
改变上面的退役合并顺序。

## 已完成

- Vecta PR `#454` 已合并到 `develop`（`910ca553`）。
- 合并后的 Vecta 验证 run `31621054632` 已完成有 jobs 的 startup、deploy 和
  verify 检查；caller 不再映射旧的 bootstrap 输入。
- 因此已删除退役 workflow 中仅用于过渡的四个可选声明，并删除对应的 bootstrap
  contract test。vtest deploy path is retired; do not restore old
  token forwarding or replace it with a compatibility wrapper. No secret value
  is retained here.

## 事故记录

Vecta startup runs `31614583502`、`31616996956`、`31617601252` 均无 jobs，不能
作为 deploy 成功证据；这也是先完成 caller 合并并取得有 jobs 验证、再移除过渡
声明的原因。

生产行为不变；本交接不包含任何 secret 值。
