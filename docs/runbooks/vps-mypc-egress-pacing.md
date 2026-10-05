# VPS → mypc 出口整形（VectA issue 2578）

2026-10-05 12:00Z owner 裁决：包丢失是独立缺陷，实测 eth0 CAKE 4250kbit 消除了下行缺失并改善冷启动，批准立即持久化。剩余首屏预算由 VectA #2580、#2593 跟进，不阻断本配置。网络验收与首屏预算分开记录。

## 测量与范围

两端本地落盘外层 WireGuard snap96，结束后取回，以 receiver_index/counter 匹配，kernel drops0。VPS → mypc 合成 UDP，每档3秒：1/2/3/4/5Mbit/s missing0/292、0/584、0/875、0/1167、0/1458；10Mbit/s1349/2917（46.246%）；20Mbit/s4360/5833（74.747%）。高档接收量约5.5Mbit/s。未区分 provider policer 和下游浅缓冲，不修改 MTU/MSS/sysctl。

wg1 CAKE4250kbit（含 overhead76 复测）仍有外层缺失；必须测加密后 eth0 出口。eth0 全出口 CAKE4250kbit（最高已测 clean5 ×0.85）：原 hop-probe exit1、下行1908/4847 → exit0、下行0/4825，反向0/6998，全部抓包零drop。12公开静态GET全200，2.426–3.336s；其旧3s采集子阈值exit1，outer-loss检查整体exit0。

20 次既有 zeno/界宸 9223 租约、禁缓存/SW bypass：FP P75/P95 1.236/1.464s，FCP4.552/4.820s，composer6.327/9.071s（max9.854s），预算FAIL；前任8.364/10.737s。零HTTP5xx/JSexception/60s卡屏，20个undefined-image abort留证。API没有edge cache；本轮config均200，但第5/9/13/17次TTFB仍为2.991/1.906/2.641/4.327s（issue2580仍需分段）；不能把全部残余耗时归入网络。

全出口限速包括公网 HTTPS、SSH 和隧道；安装后须验证两端 SSH、公开聊天入口、配对零丢包与服务 restart。服务无容器重启、nginx变更、私钥或业务数据。前轮临时整形已回滚，eth0原mq+两个fq_codel的JSON完全一致；本轮从合并 main 安装，wg1保持noqueue，不对wg1整形。

## 安装与验收

只用于 `vecta-vps`：eth0 原生默认mq+两fq_codel，`net.core.default_qdisc=fq_codel`。先读最新 mypc COORD，在变更前后追加记录；备份 `tc -s/-j qdisc show dev eth0`。其他机器或不同qdisc不能套用回滚。

从**已合并**的本配置安装 `/etc/systemd/system/vecta-vps-egress.service`，`systemd-analyze verify`，然后 `systemctl daemon-reload`、`systemctl enable --now vecta-vps-egress.service`。每次现场验证先设5分钟自动回滚；通过前保持回滚有效。

```bash
sudo systemctl restart vecta-vps-egress.service
sudo systemctl is-active vecta-vps-egress.service
sudo tc qdisc show dev eth0
python3 /home/gerald/.vecta-agent/night/hand-2578-artifacts/hop-probe.py
```

restart 后必须仍是CAKE4250Kbit且配对outer-loss为0，抓包kernel drops0；比较qdisc配置时忽略内核自动分配的handle与累计统计。两端SSH与公开聊天入口通过后，取消自己的回滚timer。随后同一session10次冷测，记录FP/FCP/composer P75/P95，确认安装配置下的表现；5秒预算不是本次部署门。现有wg1 upstream始终保留。只关闭自己创建的 browser targets、只停自己的 PID/timer；不碰客户请求payload、NAS或产品容器。

回滚：`systemctl disable --now vecta-vps-egress.service`；ExecStop 删除本服务的root qdisc，恢复内核默认mq/fq_codel。临时实验也可 `tc qdisc del dev eth0 root`，再与保存JSON比较。`tc qdisc replace root mq`会分配新handle，不能继续使用旧`:1/:2`父标识，不能声称与原始配置完全相等。

本轮 private report/artifacts：`/home/gerald/.vecta-agent/night/hand-2578b.report`、`hand-2578b-artifacts`；paired green样本在前任artifact目录`probe-1791194029`。VPS回滚备份`/data/ocee/backups/hand-2578b-eth-qdisc-20261005/ROLLBACK`（已修正、实跑exit0）。前轮未做持久化服务restart验收；本轮验收记录见 `/home/gerald/.vecta-agent/night/hand-2578c.report`。
