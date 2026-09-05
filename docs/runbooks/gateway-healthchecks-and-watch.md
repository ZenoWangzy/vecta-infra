# 网关 healthcheck、日志轮转与「谁在看生产」（票 106）

`openclaw-channel-gateway` 与 `openclaw-fleet-gateway` 在 2026-09-05 之前的实测状态：
`restart=unless-stopped`、`RestartCount=0`、**`Config.Healthcheck=null`**、log driver `json-file` 且
`LogConfig.Config={}`（不轮转）。主机上没有 prometheus / grafana / alertmanager / uptime-kuma /
autoheal / watchtower，也没有任何 `docker events` 监听。一次数据库抖动让网关反复重启，审批通知链断掉，
**不会有任何东西发现**。`/healthz` 在两个网关上都会翻 503（fleet：postgres/redis/litellm/fruitV4；
channel：WeCom 回调过期），而在此之前没有任何东西读它。

本文交付三样东西，都不引入新的监控栈：

| 产物 | 位置 | 作用 |
|---|---|---|
| `deploy/gateways/compose.healthchecks.yml` | 本仓 → 生产 `-f` 链 | 两个网关的 healthcheck（打自己的 `/healthz`）+ `json-file` 50m×5 轮转 |
| `scripts/ops/gateway-watch.sh` | 本仓 → 主机 cron，每 5 分钟 | 容器转 unhealthy 或 RestartCount 增长 → 一条 `proactive_outbox` 管理员告警行 |
| `scripts/test_gateway_healthcheck_contract.py` | 本仓，PR 上自动跑 | 断言上面两样都还在、且合并语义没变 |

## 一、为什么是新加一个 overlay，而不是改现有的那个

`openclaw-enterprise` 这个 compose project 由一条不断增长的 `-f` 链拼出来，**这条链的唯一真源是运行中容器的
`com.docker.compose.project.config_files` 标签**：

```bash
ssh mypc "docker inspect openclaw-fleet-gateway \
  --format '{{index .Config.Labels \"com.docker.compose.project.config_files\"}}' | tr ',' '\n' | nl"
```

2026-09-05 读出 **24** 个文件，第 1 个是 `/data/ocee/migration-compose.config.yml`（2026-08-15 从活容器快照还原
出来的基础文件），最后一个是 `/data/ocee/releases/main-a1cc5129b45e/compose.images.yml`。**这 24 个文件没有一个
提交在任何仓库里**，每个 `releases/*` overlay 都是在自己那次部署窗口里写出来的。

**它们的内容不是「只有 `image:`」。** 2026-09-05 对 `/data/ocee/releases` 下 29 个目录、28 个 compose 文件逐个数过：

| 设了哪些键 | 个数 | 例子 |
|---|---|---|
| 只有 `image:` | 19 | `main-a1cc5129b45e/compose.images.yml` |
| `image:` + `environment:`（fleet-gateway 的 `HERMES_RUNTIME_IMAGE_VERSION`） | 2 | `ca150-5e56973941cf…/compose.images.yml`、`main-631554ab6022-20260829T103832Z/compose.images.yml`——**两个都在活链里** |
| 只有 `environment:` | 3 | `main-050566c21bac`／`main-940b61aa262d`／`main-5fa7a5cd5dcf` 的 `compose.hermes-runtime.yml`，三个都在活链里 |
| `image:` + `environment:` + `extra_hosts:` + `mem_limit:` | 4 | `promote-6a1e9973…/compose.override.yml`（在活链里）等 |

所以本文的 overlay 是链上**新增的、常驻的**一员：

- 改最后那个 release overlay 会重写 #854 记录中「已部署」的那份产物，而且下一次部署窗口一追加新 overlay，
  healthcheck 就又没了；
- 本文件排在链上哪个位置都无所谓，但**理由不是「release overlay 只设 `image:`」**（上表已证伪），而是更窄的一条、直接查过的事实：
  `grep -rlE '^\s*(healthcheck|logging):' --include='compose.*.yml' /data/ocee/releases` 在 2026-09-05 **一个都没匹配到**，
  即今天在场的 overlay 没有一个写本文件设的这两个键，而 compose 是按键合并的。
  **将来某次窗口若给 release overlay 加上 `healthcheck:` 或 `logging:`，而它在 `-f` 上排在本文件之后，就会盖掉本文件。**
  别默认这条永远成立——加 overlay 前重跑上面那条 grep；
- 回滚仍然是这条链自己的回滚方式：把这一个 `-f` 去掉。

**从今往后每一次网关部署都必须把这个 `-f` 带上。** 判据是部署后重新读一次上面那条 `config_files` 标签，
里面要有 `compose.healthchecks.yml`。

## 二、装 overlay（需要一个部署窗口，指挥者安排）

`healthcheck` 与 `logging` 都是创建容器时的选项，改它们要重建容器，因此这一步不能在窗口外做。

```bash
# 1. 复制到主机（放在 releases/ 之外，因为它不属于任何一次 release）
scp deploy/gateways/compose.healthchecks.yml \
    mypc:/data/ocee/releases/gateway-healthchecks/compose.healthchecks.yml

# 2. 用活标签重建当前 -f 链，追加本文件，先只做 config -q
ssh mypc
CHAIN=$(docker inspect openclaw-fleet-gateway \
  --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' \
  | tr ',' '\n' | sed 's,^, -f ,' | tr -d '\n')
docker compose -p openclaw-enterprise $CHAIN \
  -f /data/ocee/releases/gateway-healthchecks/compose.healthchecks.yml config -q

# 3. 逐个重建，--no-deps 是强制的（litellm 无法被项目接管，
#    openclaw-ceo / openclaw-employee 处于 created 状态且不得启动）；--remove-orphans 禁用
docker compose -p openclaw-enterprise $CHAIN \
  -f /data/ocee/releases/gateway-healthchecks/compose.healthchecks.yml \
  up -d --no-deps fleet-gateway
docker compose -p openclaw-enterprise $CHAIN \
  -f /data/ocee/releases/gateway-healthchecks/compose.healthchecks.yml \
  up -d --no-deps channel-gateway
```

通过判据（三条都要）：

```bash
docker inspect openclaw-fleet-gateway openclaw-channel-gateway \
  --format '{{.Name}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}NONE{{end}} log={{.HostConfig.LogConfig.Config}}'
# 期望：两行都是 health=healthy（起后约 30–60s）、log=map[max-file:5 max-size:50m]
docker inspect openclaw-fleet-gateway \
  --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' | tr ',' '\n' | grep -c healthchecks
# 期望：1
```

中止条件：`config -q` 非零；重建后 `/healthz` 不是 200；`config_files` 里没有 healthchecks 那一行。
**回滚**：去掉那一个 `-f`，重新 `up -d --no-deps <service>`。

## 三、装 cron

```bash
scp scripts/ops/gateway-watch.sh mypc:/data/ocee/scripts/ops/gateway-watch.sh
ssh mypc "chmod 755 /data/ocee/scripts/ops/gateway-watch.sh && mkdir -p /var/lib/gateway-watch"
# 追加到 /etc/crontab：
# */5 * * * * root /data/ocee/scripts/ops/gateway-watch.sh >> /var/log/gateway-watch.log 2>&1
```

**注意：现有三条 cron 不在同一个文件里。** `ip-monitor.sh`（20 分钟）与 `ip-daily-report.sh`（每天 01:00）在
`/etc/crontab`；`channel-recent-conversations.sh`（5 分钟）在 **root 的用户 crontab**
（`/var/spool/cron/crontabs/root`，用 `crontab -l` 才看得到，`cat /etc/crontab` 看不到）。
本文把 gateway-watch 装在 `/etc/crontab`，所以查全量要读两个地方。

第一次运行只建立基线（`/var/lib/gateway-watch/state`：每个运行中容器一行 `名字 健康 重启次数`），
唯一可能立即发条告警的情况是当时已经有容器是 `unhealthy`。

两个信号都是**边沿触发**，这就是去重的全部：容器**转入** unhealthy 才告警，不是每 5 分钟重复告警；
RestartCount **比上一轮大**才告警。状态没变就一行都不写。插入失败时**不推进 state**，下一轮重试。

告警行的形状与 fleet-gateway `enqueueAdminAlertIfNeeded` 写的完全一致
（`instance_id='admin'`、`trigger_type='system_alert'`、`channel='web'`、`channel_uid=NULL`、`max_attempts=6`），
因此走的是已有的 delivery worker，不新增告警通道。

手工验一次。**不要拿活网关来做这件事**——`gateway-watch.sh` 看的是 `docker ps` 的**全部**容器，
不是只看两个网关，所以一个用完即弃的容器就足以驱动两个信号，而 `docker kill openclaw-channel-gateway`
会真的中断生产上的 WeCom 回调。用一次性容器：

```bash
ssh mypc '
docker run -d --name gateway-watch-probe --restart unless-stopped \
  --health-cmd "exit 1" --health-interval 5s --health-retries 1 --health-start-period 1s \
  alpine:3 sh -c "while :; do sleep 30; done"
sleep 20                                   # 等它自己转 unhealthy
/data/ocee/scripts/ops/gateway-watch.sh    # 第一轮：建基线并报 unhealthy
docker kill gateway-watch-probe            # unless-stopped 拉回来，RestartCount +1
sleep 10
/data/ocee/scripts/ops/gateway-watch.sh    # 第二轮：报 RestartCount 增长
docker rm -f gateway-watch-probe           # 收尾，别把它留在 ps 里
'
ssh mypc "docker exec -i openclaw-postgres psql -U openclaw_poc -d openclaw_poc -c \
  \"SELECT created_at, left(prompt, 80) FROM proactive_outbox \
    WHERE instance_id='admin' AND trigger_type='system_alert' ORDER BY created_at DESC LIMIT 3\""
```

（探针容器删掉之后，它在 `/var/lib/gateway-watch/state` 里留下的那一行会在下一轮自然消失：
脚本每轮用当前 `docker ps` 全量覆写 state，不做删除告警。）

## 四、谁在看生产

这是完整清单。清单之外没有别的东西在看。

| 看什么 | 谁在看 | 频率 | 看不见什么 |
|---|---|---|---|
| 两个网关的进程活着且依赖可达（`/healthz`：fleet 的 postgres/redis/litellm/fruitV4，channel 的 WeCom 回调新鲜度） | 本文的 compose `healthcheck`（30s 间隔、10s 超时、3 次重试、30s start_period） | 30s | 只把状态写进 `State.Health`；**docker 不会因为 unhealthy 而重启容器**，主机上没有 autoheal |
| 容器转 unhealthy、容器 RestartCount 增长 | `scripts/ops/gateway-watch.sh` → `proactive_outbox` 管理员告警 | 5 分钟 | 只看**运行中**的容器；容器被删掉或 compose 之外停掉的，它看不到；延迟最长 5 分钟 |
| 可达性漂移与化石清扫爆炸半径（应用层） | 票 95：reconciler 周期末尾写同一条 `proactive_outbox` 系统告警 | 见票 95 | 未合并前为 0 —— 今天没有任何东西在看 |
| 公网 IP 变化 | `/usr/local/bin/ip-monitor.sh`（`/etc/crontab`） | 20 分钟 | 与容器健康无关 |
| IP 日报 | `/usr/local/bin/ip-daily-report.sh`（`/etc/crontab`） | 每天 01:00 | 与容器健康无关 |
| 近 30 分钟用户对话导出（只读） | `/data/ocee/docs/ops/channel-recent-conversations.sh`（**root 用户 crontab**，不在 `/etc/crontab`） | 5 分钟 | 只写 `/var/log/ocee-conversations.log`，**不告警任何人** |

仍然没有人在看的（本票不修，记在这里免得被当成已覆盖）：

- 磁盘、内存、CPU 与 `/tmp` 占满；
- 74 个运行容器里其余 60 多个仍然**没有 healthcheck**，`gateway-watch.sh` 只能报它们的重启计数；
- 日志轮转只加在两个网关上，其余容器仍是 `json-file {}`；
- 告警本身的投递失败（管理员行投不出去时，fleet-gateway 明确不再二次告警以避免递归）。

## 五、`test_gateway_healthcheck_contract.py` **没有**覆盖什么

这张表存在的理由：闸绿只等于表里没列的那些被验过了，不等于脚本整体被验过。

| 没覆盖的 | 为什么没覆盖 | 后果 |
|---|---|---|
| `docker ps` 失败（脚本 `exit 1`） | `run_watch` 的 fake docker 恒 `exit 0` | 这条分支从未被执行过 |
| `docker inspect` 失败（脚本 `exit 1`） | 同上 | 同上 |
| INSERT 失败（`exit 1` 且**不推进 state**，下一轮重试） | fake psql 是 `tee`，不会失败 | 「投不出去就重试」这个设计点没有可执行证据 |
| 同一轮里两个信号同时发生（既转 unhealthy 又 RestartCount 增长 → 一条 INSERT 带两行） | 每个 fixture 只动一个信号 | 两行拼接的形状没被验过 |

窗口里的手工验证（第三节）走的是真 docker，能覆盖上表第四行；前三行仍然只有代码审读。
