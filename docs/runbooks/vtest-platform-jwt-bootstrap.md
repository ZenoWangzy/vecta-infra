# vtest 平台 JWT bootstrap 交接

## 当前边界

`_deploy-vtest-job.yml` 的 `workflow_call.secrets` 暂时保留四个旧平台 JWT
输入，均为 `required: false`。这些输入只为 `pull_request_target` 的 startup
workflow 校验提供声明，不进入 job `env`、step、Ansible 或容器；vtest
deploy 每次运行都在 infra reusable workflow 内生成一次性平台 JWT。

## 证据与移除条件

- Vecta startup runs `31614583502`、`31616996956`、`31617601252` 均无 jobs，
  因此不能把它们当作 deploy 成功证据。
- Vecta PR `#454` 合并后，重新触发一次 startup workflow，确认 caller 不再
  映射旧输入，并完成一次有 jobs 的 deploy/verify 验证。
- 验证完成后立即删除 reusable workflow 中的四个旧声明，同时删除对应的
  bootstrap contract test；不得恢复旧 token 转发或在声明中填入任何值。

生产行为不变；本交接不包含任何 secret 值。
