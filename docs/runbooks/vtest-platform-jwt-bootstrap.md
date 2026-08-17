# Retired: vtest 平台 JWT bootstrap 交接

> Status: retired 2026-08-17. Historical handoff only; the vtest workflow,
> bootstrap, and reusable control-plane entries were removed. Do not use this
> document as a deployment entry.

## 已完成

- Vecta PR `#454` 已合并到 `develop`（`910ca553`）。
- 合并后的 Vecta 验证 run `31621054632` 已完成有 jobs 的 startup、deploy 和
  verify 检查；caller 不再映射旧的 bootstrap 输入。
- 因此已删除 reusable workflow 中仅用于过渡的四个可选声明，并删除对应的
  bootstrap contract test。vtest deploy path is retired; do not restore old
  token forwarding or replace it with a compatibility wrapper. No secret value
  is retained here.

## 事故记录

Vecta startup runs `31614583502`、`31616996956`、`31617601252` 均无 jobs，不能
作为 deploy 成功证据；这也是先完成 caller 合并并取得有 jobs 验证、再移除过渡
声明的原因。

生产行为不变；本交接不包含任何 secret 值。
