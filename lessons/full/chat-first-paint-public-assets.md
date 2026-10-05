# V-NEXT：首屏公共资源不重复跨慢隧道

规则入口：[V-NEXT](../RULES.md#v-next)。issue 2573。

三轮认证冷启动为 63.9 秒、超过 110 秒且 JS 502、30.2 秒。config 到第 58 秒才发起。内层 nginx 对同一 chunk 371ms，VPS 20.3 秒；隧道 TCP 大量重传。不是模型 prompt 或工具执行阻塞 UI。

VPS 用原生 nginx cache 承载列明公共 JS/CSS，移除全部浏览器身份头，只缓存 MIME 正确且无身份 Set-Cookie 的 200 响应。精确处理现网固定的历史清缓存标记，公共响应不重放 cookie/Clear-Site-Data；未知 cookie 仍拒绝缓存。缓存未压缩正文，VPS gzip 必须包含 text/javascript。HTML、API、聊天、附件、socket 不进入缓存。只缓存 5 分钟，热补丁需清理公共缓存再预热；冷 MISS 仍暴露隧道性能，不能称已修隧道。

Guard：`node --test scripts/chat-entry-assets.test.mjs`。真实 nginx 回归：`node --test scripts/chat-entry-assets-cache.test.mjs`，在有 nginx 的主机运行隔离实例。禁用 cache 时 1 秒慢上游击穿 250ms 首屏预算；开启时命中不再访问上游，两个 API 身份保持隔离，伪 JS 路径上的 HTML 不缓存。实际 UI 还必须采集 trace/瀑布，检查截图并报告每轮时间。
