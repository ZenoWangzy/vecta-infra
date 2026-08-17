# Retired: Nexus Repository 3.94 出站 HTTP/HTTPS 代理配置研究

> Status: retired 2026-08-17 with the integration deployment control plane.
> Retained as historical research only; the retired System HTTP automation is
> not an active Ansible or workflow entry.

- 研究日期：2026-08-11
- 目标版本：Nexus Repository Community Edition `3.94.0-12`
- 背景：vtest 的 `vecta-docker-remote` 直连 `registry-1.docker.io` 报 `Network unreachable`，随后进入 `AUTO_BLOCKED`；已知现有 HTTP proxy 能访问同一 manifest。该背景由任务提供，本研究未登录 vtest 独立复验。
- 来源边界：仅使用 Sonatype 官方帮助、官方 Support、`sonatype/nexus-public` 的 `release-3.94.0-12` 源码。

## 结论摘要

1. **已确认：应在 Nexus 自身的 `Settings → System → HTTP` 配置出站代理。** 这是 Nexus proxy repository 外连远端仓库的全局 HTTP client 配置；不应把带凭据的 `HTTP_PROXY`/`HTTPS_PROXY` 注入 Nexus Docker 容器。Sonatype 当前文档明确把这一页作为外连公共仓库的代理入口；3.91 发布说明还明确要求相关代理行为改用 Nexus UI，而不是 `nexus.vmoptions` 的 JVM system properties，并指出因此不再需要因代理配置而重启。[HTTP Request and Proxy Settings](https://help.sonatype.com/en/http-request-and-proxy-settings.html)；[3.91 release notes, NEXUS-51152](https://help.sonatype.com/en/sonatype-nexus-repository-3-91-0-release-notes.html)（访问：2026-08-11）
2. **已确认：配置持久化在 Nexus 配置数据库，并在保存后热生效。** 3.94 源码的 `HttpClientManagerImpl.setConfiguration()` 先 `store.save(model)`，再发布 `HttpClientConfigurationChangedEvent`；各 repository 的 `HttpClientFacetImpl` 收到事件后关闭并重建 HTTP client。无需重启 Nexus。[HttpClientManagerImpl.java L187-L197](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-core/src/main/java/org/sonatype/nexus/internal/httpclient/HttpClientManagerImpl.java#L187-L197)；[HttpClientFacetImpl.java L244-L246](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-repository-view/src/main/java/org/sonatype/nexus/repository/httpclient/internal/HttpClientFacetImpl.java#L244-L246)；[HttpClientConfigurationDAO.xml L19-L43](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-core/src/main/resources/org/sonatype/nexus/internal/httpclient/HttpClientConfigurationDAO.xml#L19-L43)（访问：2026-08-11）
3. **已确认：3.94 没有用于写入全局 HTTP System Settings 的公开、稳定 REST/OpenAPI。** 官方定义的可集成 API 以实例的 `/service/rest/swagger.json` 为准；3.94 的全局 HTTP 页面实际调用内部 ExtDirect action `coreui_HttpSettings.read/update`，而不是 JAX-RS/OpenAPI resource。因此 Core UI API 可被技术性调用，但不能视为稳定公开契约。[Automation / official OpenAPI](https://help.sonatype.com/en/automation.html)；[HttpSettingsComponent.java L56-L62, L78-L124](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-coreui-plugin/src/main/java/org/sonatype/nexus/coreui/HttpSettingsComponent.java#L56-L124)；[ExtDirectServlet.java L72-L88](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/api/nexus-extdirect/src/main/java/org/sonatype/nexus/extdirect/internal/ExtDirectServlet.java#L72-L88)（访问：2026-08-11）
4. **已确认：公开 REST 可以安全自动化 repository 配置和 cache invalidation。** `vecta-docker-remote` 的 `blocked`、`autoBlock`、`negativeCache.enabled/timeToLive` 属于 repository 配置；负缓存可通过 `POST /service/rest/v1/repositories/vecta-docker-remote/invalidate-cache` 清理。该动作同时失效 proxy/component metadata cache 和 not-found cache，并不是“解除 auto-block”的同义词。[Repositories API](https://help.sonatype.com/en/repositories-api.html)；[Repository Actions](https://help.sonatype.com/en/repository-actions.html)；[RepositoriesApiResource.java L79-L93](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-repository-services/src/main/java/org/sonatype/nexus/repository/rest/api/RepositoriesApiResource.java#L79-L93)（访问：2026-08-11）

## 1. 官方入口与持久化语义

### 已确认

管理员在 `Settings → System → HTTP` 配置全局出站行为；需要 `nx-settings-read`/`nx-settings-update` 对应的 `nexus:settings:read/update` 权限。官方页面把它定义为 Nexus Repository 发往 remote repository 的 HTTP(S) 请求设置，包含 timeout、retry、HTTP proxy、HTTPS proxy、authentication 和 excluded hosts。[HTTP Request and Proxy Settings](https://help.sonatype.com/en/http-request-and-proxy-settings.html)；[Privileges](https://help.sonatype.com/en/privileges.html)（访问：2026-08-11）

3.94 Core UI 的实际配置对象和方法是：

- ExtDirect action：`coreui_HttpSettings`
- 读取方法：`read`
- 写入方法：`update`
- servlet mount：`/service/extdirect/*`
- 写入权限：`nexus:settings:update`；读取权限：`nexus:settings:read`

证据见 [HttpSettingsComponent.java](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-coreui-plugin/src/main/java/org/sonatype/nexus/coreui/HttpSettingsComponent.java#L56-L136) 与 [ExtDirectServlet.java](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/api/nexus-extdirect/src/main/java/org/sonatype/nexus/extdirect/internal/ExtDirectServlet.java#L72-L88)（访问：2026-08-11）。

保存时不是修改容器环境或 JVM 启动参数，而是写入 Nexus 的单例 `http_client_configuration` 配置记录并发布运行时变更事件。[HttpClientConfigurationDAO.xml](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-core/src/main/resources/org/sonatype/nexus/internal/httpclient/HttpClientConfigurationDAO.xml#L19-L43)；[HttpClientManagerImpl.java](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-core/src/main/java/org/sonatype/nexus/internal/httpclient/HttpClientManagerImpl.java#L187-L208)（访问：2026-08-11）。

### 不应采用的方式

- 不把 `HTTP_PROXY=http://user:password@host:port`、`HTTPS_PROXY=...` 写入 Compose、systemd unit 或 `docker_container.env`。这样会让凭据进入 Git/IaC、inventory 展开结果，且可通过 `docker inspect` 读取。
- 不把全局代理写成 `nexus.vmoptions` 的 `-Dhttp.proxy*`/`-Dhttps.proxy*` 作为当前方案。3.94 原生 HTTP client 有自己的持久化配置、凭据加密和热更新路径；Sonatype 3.91 的相关变更也明确从 JVM properties 转向 UI 配置。[3.91 release notes](https://help.sonatype.com/en/sonatype-nexus-repository-3-91-0-release-notes.html)（访问：2026-08-11）。

以上“不要把凭据放进容器 env”是基于 Docker/Ansible 暴露面的安全推断；“应走 Nexus System HTTP”是 Sonatype 已确认路径。

## 2. 字段、语义与配置入口

3.94 的 Core UI exchange object 字段由 `HttpSettingsXO` 明确定义。[HttpSettingsXO.java L28-L80](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-coreui-plugin/src/main/java/org/sonatype/nexus/coreui/HttpSettingsXO.java#L28-L80)（访问：2026-08-11）。

| UI 含义 | 3.94 Core UI 字段 | 约束/说明 |
|---|---|---|
| Enable HTTP proxy | `httpEnabled` | `true/false` |
| HTTP Proxy Host | `httpHost` | 只填 IP/DNS host，不带 `http://` |
| HTTP Proxy Port | `httpPort` | `1..65535` |
| Enable HTTP Authentication | `httpAuthEnabled` | Basic 用户名密码，或额外填写 NTLM host/domain |
| HTTP username/password | `httpAuthUsername`, `httpAuthPassword` | password 写入后返回 placeholder，不返回明文 |
| Enable HTTPS proxy | `httpsEnabled` | `true/false` |
| HTTPS Proxy Host/Port | `httpsHost`, `httpsPort` | host 不带 `https://`；port 为 `1..65535` |
| HTTPS username/password | `httpsAuthUsername`, `httpsAuthPassword` | 同上；另有 `httpsAuthNtlmHost/domain` |
| Hosts to exclude | `nonProxyHosts` | 字符串集合；每项一个 Java `http.nonProxyHosts` 风格 wildcard，例如 `localhost`、`127.*`、`*.corp.example`，不要在单项里用 `|` 拼接 |

官方 UI 字段说明与 wildcard 示例见 [HTTP Request and Proxy Settings](https://help.sonatype.com/en/http-request-and-proxy-settings.html)；3.94 的 exchange object 与校验见 [HttpSettingsXO.java](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-coreui-plugin/src/main/java/org/sonatype/nexus/coreui/HttpSettingsXO.java#L28-L80) 和 [CoreApi.java L47-L112](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-core/src/main/java/org/sonatype/nexus/CoreApi.java#L47-L112)（访问：2026-08-11）。

**3.94 版本细节：**只配置 HTTP proxy 时，内部 route planner 同时将它用于 `HTTP` 与 `HTTPS` 目标；如果又配置 HTTPS proxy，则 HTTPS 项覆盖前者。因此一个传统的 CONNECT forward proxy 通常只需 HTTP proxy host/port；若组织明确提供独立 HTTPS proxy，再设置 HTTPS override。[ConfigurationCustomizer.java L208-L230](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-httpclient/src/main/java/org/sonatype/nexus/httpclient/config/ConfigurationCustomizer.java#L208-L230)（访问：2026-08-11）。这是 3.94 源码确认的实现细节，不应假设未来版本永久不变。

## 3. 凭据如何避免出现在 inspect、日志和 Git

### Sonatype 已确认的保护

- Core UI 写入新 password 时调用 `SecretsService.encryptMaven(...)`；再次读取时返回 password placeholder，并在更新时复用旧 secret，而不是回传明文。[HttpSettingsComponent.java L224-L240](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-coreui-plugin/src/main/java/org/sonatype/nexus/coreui/HttpSettingsComponent.java#L224-L240)（访问：2026-08-11）。
- Nexus audit 记录 proxy host、port、authentication type、username，但显式省略 password。[HttpClientAuditor.java L79-L112](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-core/src/main/java/org/sonatype/nexus/internal/httpclient/HttpClientAuditor.java#L79-L112)（访问：2026-08-11）。
- Sonatype 说明 Nexus 以可逆加密保存 sensitive configuration，并建议替换默认 encryption key；仅“已加密”不等于默认 key 足够安全。[Re-encryption in Nexus Repository](https://help.sonatype.com/en/re-encryption-in-nexus-repository.html)（访问：2026-08-11）。

### Ansible 最小安全约束（操作建议/推断）

1. proxy password 只来自 Ansible Vault 或运行时 secret manager；Git 中只存 variable name，绝不存值。
2. 所有携带 Nexus 登录凭据或 proxy password 的 `uri`/调用任务设置 `no_log: true`；不要用 `debug: var=` 输出完整 request/result。
3. 通过 HTTPS 访问 Nexus 管理端；不要把 password 放在 URL、shell command line、container environment 或临时 world-readable 文件。
4. 若使用 Ansible controller 的环境变量临时注入 secret，必须只存在 controller 进程环境，不能传入 Nexus container。优先直接由 Vault 变量构造 HTTPS request body。
5. 不开启会记录 HTTP request body 的反向代理/debug logging。Nexus 正常 audit 会省略 password，但外层抓包、body logging 与 Ansible verbose 输出不受 Nexus 的遮罩保证。
6. 使用专用自动化账号，只授予 `nx-settings-read`、`nx-settings-update` 以及目标 repository 的必要 read/edit 权限，不使用长期 `admin` 密码。[Privileges](https://help.sonatype.com/en/privileges.html)（访问：2026-08-11）。

## 4. 是否重启、如何处理 auto-block 和 negative cache

### 配置变更无需重启

已确认：System HTTP save 发布 `HttpClientConfigurationChangedEvent`，每个 proxy repository 的 HTTP client 立即 close/recreate；新 client 初始状态是 `READY`（repository online 且未手工 blocked）。因此本次代理修复不需要重启容器，保存本身也会清掉旧 client 的内存态 auto-block，下一次未命中缓存的请求会实际验证新出站链路。[HttpClientFacetImpl.java L244-L246](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-repository-view/src/main/java/org/sonatype/nexus/repository/httpclient/internal/HttpClientFacetImpl.java#L244-L246)；[BlockingHttpClient.java L105-L125](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-repository-view/src/main/java/org/sonatype/nexus/repository/httpclient/internal/BlockingHttpClient.java#L105-L125)（访问：2026-08-11）。

### Auto-block

- `AUTO_BLOCKED_UNAVAILABLE` 表示开启了 `autoBlock` 且远端被判定不可达/无响应；Nexus 会周期性重试，远端恢复后自动解除。[Configurable Repository Fields](https://help.sonatype.com/en/configurable-repository-fields.html)；[Sonatype Support: Remote Auto Blocked and Unavailable](https://support.sonatype.com/hc/en-us/articles/23276928301971-Repository-A-proxy-repository-is-not-working-status-says-Online-Remote-Auto-Blocked-and-Unavailable)（访问：2026-08-11）。
- 不建议为了绕过故障永久关闭 auto-block；它用于避免慢/坏上游拖垮请求线程。修复代理后，System HTTP save 会重建 client；随后通过 Nexus 请求一个未缓存的 Docker manifest，成功响应会把状态更新为 `AVAILABLE`。[BlockingHttpClient.java L198-L244](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-repository-view/src/main/java/org/sonatype/nexus/repository/httpclient/internal/BlockingHttpClient.java#L198-L244)（访问：2026-08-11）。
- 若业务明确要求关闭自动阻断，使用公开 Docker proxy repository `PUT` API 保留完整现有配置，仅把 `httpClient.autoBlock` 设为 `false`、`httpClient.blocked` 保持 `false`；不要调用内部状态接口。字段定义见 [HttpClientAttributes.java L25-L60](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-repository-services/src/main/java/org/sonatype/nexus/repository/rest/api/model/HttpClientAttributes.java#L25-L60)（访问：2026-08-11）。

### Negative cache

- `negativeCache.enabled/timeToLive` 只缓存上游的 “not found” 结果，与网络 auto-block 是两个机制。[Configurable Repository Fields](https://help.sonatype.com/en/configurable-repository-fields.html)；[NegativeCacheAttributes.java](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-repository-services/src/main/java/org/sonatype/nexus/repository/rest/api/model/NegativeCacheAttributes.java#L25-L65)（访问：2026-08-11）。
- 代理修复后执行一次：

  ```http
  POST /service/rest/v1/repositories/vecta-docker-remote/invalidate-cache
  ```

  该公开 API 会清理 proxy cache 标记和 negative/not-found cache；下次请求会重新查询上游，但不会删除已经缓存的 blob。[Repository Actions](https://help.sonatype.com/en/repository-actions.html)；[RepositoryCacheInvalidationService.java L89-L98](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-repository-services/src/main/java/org/sonatype/nexus/repository/cache/RepositoryCacheInvalidationService.java#L89-L98)（访问：2026-08-11）。

### 最小验证顺序

1. 读取 System HTTP 设置，确认 enabled/host/port/no-proxy 与预期一致；password 只应显示 placeholder。
2. `POST .../vecta-docker-remote/invalidate-cache`，期望 `204`。
3. 经 Nexus 的 `vecta-docker-remote` 请求一个确定存在、此前未缓存的 Docker Hub manifest/tag；仅在 Nexus 宿主机上 `curl` 上游不算产品级验证。
4. 在 UI 的 repository status 或 `nexus.log` 确认状态从 `READY/AUTO_BLOCKED_UNAVAILABLE` 变为 `AVAILABLE`，且没有 `407 Proxy Authentication Required`、TLS trust、DNS 或 `Network unreachable`。
5. 再次请求同一 manifest，确认 Nexus 客户端链路成功；必要时用访问日志确认请求确实经过目标 Nexus repository。

## 5. 官方 API 稳定性与 Ansible 最小安全流程

### 已确认的 API 边界

- **稳定/公开：**实例 `/service/rest/swagger.json` 中的 REST/OpenAPI；包括 repository GET/PUT 和 `invalidate-cache`。官方说明 beta endpoints 也受支持。[Automation](https://help.sonatype.com/en/automation.html)；[Repositories API](https://help.sonatype.com/en/repositories-api.html)（访问：2026-08-11）。
- **不是公开 REST 契约：**`/service/extdirect` 的 `coreui_HttpSettings.read/update`。它是官方源码中的 UI backend，但没有出现在官方 OpenAPI；字段名与调用协议应按 exact version 固定，升级时可能变化。[HttpSettingsComponent.java](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-coreui-plugin/src/main/java/org/sonatype/nexus/coreui/HttpSettingsComponent.java#L56-L136)（访问：2026-08-11）。
- **不推荐作为本次最小方案：**Groovy Script REST。3.94 的 `CoreApi` 确实公开了 `httpProxyWithBasicAuth`、`httpsProxyWithBasicAuth`、`nonProxyHosts` 等 script methods，但 Groovy script creation 从 3.21.2 起默认禁用；临时开启需要修改 `nexus.properties` 并前后重启，且 Sonatype 因安全与前向兼容建议优先使用 public REST。[CoreApi.java](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-core/src/main/java/org/sonatype/nexus/CoreApi.java#L47-L112)；[Script API](https://help.sonatype.com/en/script-api.html)；[Database Options: Groovy scripting is not recommended](https://help.sonatype.com/en/database-options.html)（访问：2026-08-11）。

### 推荐的 Ansible 流程

**支持边界优先（推荐）：**

1. Ansible 只做 preflight：确认 Nexus exact version、备份/读取现有 repository 配置、确认 `/service/rest/swagger.json` 的公开端点。
2. 由有 `nx-settings-update` 权限的操作者在 UI 一次性保存 System HTTP proxy；凭据来自受控 password manager，不落 Git/容器 env。
3. Ansible 用公开 API 执行 `invalidate-cache`、repository GET，并跑经 Nexus 的 manifest smoke test。
4. 记录非敏感变更证据：version、host、port、no-proxy、HTTP 状态、manifest digest、时间；不记录 username/password/request body。

**若必须无人值守：版本锁定的例外流程（推断，非 Sonatype 稳定 API 保证）：**

1. 明确 pin `3.94.0-12`；若版本不匹配立即 fail，不做“兼容”猜测。
2. 先调用 ExtDirect `coreui_HttpSettings.read` 保存当前非敏感字段；验证返回 schema 至少包含预期字段。
3. 从 Ansible Vault/secret manager 取 proxy password，通过 HTTPS 调用 `coreui_HttpSettings.update`；整项 `no_log: true`，不将 body 写临时文件。
4. read-after-write 校验 enabled/host/port/no-proxy；password 必须为 placeholder。随后公开 REST invalidate cache，再跑未缓存 manifest E2E。
5. 任一步失败即用 pre-read 配置回滚。若本次替换了旧 password，旧 secret 已可能被 Nexus 删除，回滚所需旧 password 必须也能从 secret manager 重新取得；不能依赖 read 返回明文。[HttpSettingsComponent.java L236-L253](https://github.com/sonatype/nexus-public/blob/release-3.94.0-12/public/common/components/nexus-coreui-plugin/src/main/java/org/sonatype/nexus/coreui/HttpSettingsComponent.java#L236-L253)（访问：2026-08-11）。
6. 每次 Nexus 升级都重新检查该版本的 `/service/rest/swagger.json` 与 `HttpSettingsComponent/HttpSettingsXO`；若未来出现公开 HTTP settings REST，立即删除 ExtDirect 自动化，切换公开 API。

这条无人值守路径的“最小”含义是：不打开 Groovy scripting、不修改 JVM/container proxy env、不重启 Nexus，只对 exact-version 的 UI backend 做一个受控 read/update，并把稳定公开 API 用于后续 cache 与 repository 操作。

## 已确认、版本相关不确定、推断

### 已确认

- System HTTP 是 Nexus 管理 proxy repository 出站 HTTP(S) 的官方入口。
- 3.94.0-12 的字段、ExtDirect action/method、数据库持久化、secret encryption、audit password omission、热重建 HTTP client 均有 tag-pinned 官方源码证据。
- auto-block 会周期探测并自动恢复；negative cache 可用公开 `invalidate-cache` API 清理。
- 官方自动化契约以实例 Swagger/OpenAPI 为准；global HTTP settings 不在 3.94 的公开 REST resource 中。

### 版本相关不确定

- `coreui_HttpSettings`、ExtDirect payload 形状和 HTTP/HTTPS fallback 是 3.94.0-12 实现，不承诺跨版本稳定。
- Sonatype 在线帮助是滚动更新文档；本文用 3.94 tag 源码约束了关键结论，但上线前仍应下载目标实例自己的 `/service/rest/swagger.json`。
- secret 虽经 Nexus 加密保存，实际强度取决于部署是否替换默认 encryption key；本文未核查 vtest 的 key 配置。

### 推断/未验证

- 建议的 Ansible Vault、`no_log`、version gate、read-before-write/rollback 是依据上述 API/安全边界形成的操作设计，不是 Sonatype 提供的 Ansible role。
- 未在 vtest 实际调用 ExtDirect、公开 REST 或 Docker manifest smoke test；因此当前 vtest 的最终代理连通、认证方式、TLS trust 与 no-proxy 列表仍为待验证状态。
