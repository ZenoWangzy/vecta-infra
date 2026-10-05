# /chat/ 首屏公共资源（issue 2573）

故障链：浏览器冷启动约 200 个资源请求，公共 JS/CSS 每次经 VPS → WireGuard → mypc。双端 timing 显示内层响应亚秒，隧道侧可达 20–60 秒并有 502；启动 config 尚未发起，系统 prompt 与工具执行不在这段关键路径。

先建立公共缓存父目录：`sudo mkdir -p /var/cache/nginx`；nginx 初始化缓存路径并设置 worker 权限。`deploy/open-webui/vps-chat-assets-cache.conf` 安装到 VPS `/etc/nginx/conf.d/vecta-chat-assets-cache.conf`；location 文件安装到 `/etc/nginx/snippets/vecta-chat-assets-location.conf`，只在生产域名 TLS server 中 include。先备份当前文件并做 after-md5 CAS 回滚，`nginx -t` 后 reload，禁止覆盖整个在途 vhost。

只缓存列明目录的公共 JS/CSS；上游请求不含 Cookie、Authorization 或任何浏览器身份头。HTML、JSON、API、附件、聊天与 socket 均不匹配；响应 MIME 不符或带身份 Set-Cookie 不入缓存。当前内层 nginx 对匿名静态请求仍发历史 `vecta_cache_reset_0929b` 固定标记，精确匹配这一非身份标记可缓存；任何其他 cookie 仍拒绝缓存。Set-Cookie 与 Clear-Site-Data 都不向静态资源客户端重放。缓存保存未压缩正文，VPS 明确 gzip `text/javascript`、`application/javascript`、`text/css`，避免前端 MIME 与系统 nginx 默认值不同导致传输量翻倍。缓存最多 256MiB，服务端 TTL 5 分钟，过期资源立即返回旧的公开正文并后台刷新；刷新错误继续返回已有公开正文。新构建的 hash 路径天然换 key；同路径热补丁上线后清理这份公共缓存再预热，避免 5 分钟旧资源。

上线与每次 frontend 构建后，从无身份的 gateway 取当前已重写的 index 和动态 import 图，顺序预热资源，不能复制历史前端文件。浏览器 cache-disabled 冷启动验收需包含首屏可见且 composer 可输入；缓存冷 MISS 仍受隧道性能影响，不把 HIT 宣称为隧道修复。留存三次 trace、瀑布、截图与双端 timing；只读验收，不提交消息。

检查：`node --test scripts/chat-entry-assets.test.mjs`；生产 `nginx -t`，不同身份头读同一公共 chunk 应 HIT 且字节一致，API 应无 X-Vecta-Asset-Cache。预算：预热后冷浏览器 UI P75 <5s、P95 <8s；任何一次 >12s 仍需调查。
