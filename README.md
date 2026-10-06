# VPN Route Kit

Linux 上的 VLESS + REALITY 一键部署仓库。可选 sing-box 或 Xray 入口，可选学校 VPN，支持普通出口、名单分流和全部代理流量走 VPN。VPN 不写死 CMU；默认是 **不使用 VPN 的普通节点**。

## 运行方式

支持 Ubuntu/Debian、systemd、x86_64/aarch64。安装版本固定为 sing-box 1.14.2、Xray 26.3.27，从官方 GitHub Release 下载。没有额外的 SHA 校验、审核或批准步骤。首次安装需要能访问 GitHub 和 apt 软件源；端口及云防火墙由你自行开放。

```bash
git clone <你的仓库地址> vpn-route-kit
cd vpn-route-kit
sudo ./entrypoint.sh
```

直接运行即进入中文向导，不需要 `--xxx` 参数。逐项选择操作、私人配置目录、sing-box / Xray、公网地址、端口，以及普通节点 / 名单分流 / 全部代理流量走 VPN。每项都显示默认值，回车采用默认值；再次运行沿用已保存的设置。VPN 可选 CMU、其他学校或已有 SOCKS5，规则可选域名名单或有序规则 JSON。SOCKS5 密码隐藏输入。

只生成文件、不启动服务时，在向导中选择“只生成配置、链接和二维码”。查看产物、登录 VPN、检查连接、停止服务和更新学术名单，也都在同一个菜单里。先完成提问，再开始安装和部署；提问期间 Ctrl+C 可取消。

以下带参数命令保留给自动化和进阶使用，普通使用直接运行上面的入口即可。

默认选择 sing-box；要使用 Xray，加 `--backend xray`。两种后端共用身份和配置来源，是互换方案，不能同时占用同一入口端口。端口被旧服务占用时会说明原因并退出，不自动关闭旧 Xray。

### 普通节点：不需要 VPN、不需要分流

```bash
sudo ./entrypoint.sh setup --server <公网IP> --mode none --backend sing-box
```

只启动入口，没有 VPN 进程，也没有学校登录。`setup` 会安装依赖、下载程序、生成身份/客户端产物、检查配置、启动 systemd 服务；需要 VPN 时接着启动认证和连通检查。先只生成配置可用 `deps`、`install`、`generate`、`check`。

### CMU：学术名单走 Full VPN

```bash
sudo ./entrypoint.sh setup --server <公网IP> --mode rules \
  --vpn-server https://vpn.cmu.edu --vpn-flavor anyconnect --auth-group 'Full VPN'
```

等价的短命令：`sudo ./entrypoint.sh setup --server <公网IP> --profile profiles/cmu.json`。仓库也提供普通节点、通用 AnyConnect 和通用 GlobalProtect 模板；通用模板仍需传入学校官方 `--vpn-server`。命令行参数优先于 profile，profile 优先于已保存设置。

第一次认证会打印学校网页登录地址；在自己的浏览器完成学校登录/MFA。如果提示 `Cookie "acSamlv2Token"`，可按提示用 F12 读取并粘贴到终端，输入隐藏。**账号、密码、Cookie 都不要写入命令行或 GitHub。** 也可以使用下面的本地浏览器助手。

### 其他学校 / 提供商

```bash
sudo ./entrypoint.sh setup --server <公网IP> --mode rules \
  --vpn-server https://<学校官方VPN网关> --vpn-flavor anyconnect --auth-group '<官方登录组>'
```

`--vpn-flavor` 支持 `anyconnect`、`gp`（GlobalProtect）、`fortinet`、`f5`、`pulse`、`nc`。协议、网关和登录组以学校官方指引为准，不根据学校名称猜测。CMU 的 AnyConnect 已实测；**Stanford、Johns Hopkins 等学校尚未实测**。其他协议的网页登录、设备检查及可用出口可能不同。留空登录组时由认证流程选择。

另一个 VPN 若无法由 OpenConnect 连接，可以让该 VPN 运行在独立网络空间，并提供 SOCKS5，再接入本仓库：

```bash
sudo ./entrypoint.sh setup --server <公网IP> --mode rules --vpn-kind socks \
  --upstream-host 127.0.0.1 --upstream-port 1080
```

本仓库不会安装或管理这种外部 VPN。若 SOCKS5 需要认证，在私人 state 目录建立 `upstream-secrets.json`，格式为 `{"username":"...","password":"..."}`，权限 `600`；脚本读取后写入私人服务端配置。此文件禁止上传。外部 SOCKS 是否支持 UDP/DNS 取决于它的实现。

### VPN 接管全部出口

```bash
sudo ./entrypoint.sh setup --server <公网IP> --mode all \
  --vpn-server https://<VPN网关> --vpn-flavor anyconnect
```

`all` 表示 **进入这个代理节点的全部 TCP/UDP 业务流量**，不是整台 VPS 的 SSH、软件更新或其他进程。默认路由保持原样；REALITY 握手目标和 VPN 控制连接也独立。局域网/本机地址仍拒绝访问。

### 自定义名单

`--mode rules --rules /绝对路径/my-domains.txt` 可以把任意域名名单导向 VPN，不限学术用途。

```text
# 域名及其子域名
example.com
# 仅精确域名
full:research.example.org
```

默认是 `rules/academic-domains.txt` 中的出版商/数据库名单，来自社区学术名单并人工收窄；它不是任何学校的订阅清单。开放资源、整所大学及共享 CDN 不会默认全部收录。遇到论文下载/登录跳转时，可能需要补充域名。

下载完整社区候选列表（不自动应用）：

```bash
./entrypoint.sh update-rules
./entrypoint.sh generate --mode rules --rules "$HOME/.local/share/academic-vpn/rules/community-domains.txt"
sudo ./entrypoint.sh deploy --backend sing-box
```

下载时展开 `include:`，排除 Sci-Hub、Z-Library，记录来源 commit。完整名单仍含开放资源和高校域名；可先编辑。出现尚未支持的语法时保留原运行配置并报错。需要多个名单时可合并成一个文本文件。

### 多名单、例外和优先级

`--mode rules --policy /绝对路径/policy.json` 支持按顺序匹配的多组域名，每组可以直连、走 VPN 或阻断；未匹配的默认出口可以是直连或 VPN：

```json
{
  "default": "vpn",
  "rules": [
    {"domains": ["full:login.example.org"], "outbound": "direct"},
    {"domains": ["blocked.example.org"], "outbound": "block"},
    {"domains": ["example.org", "another.example"], "outbound": "vpn"}
  ]
}
```

第一条命中即生效。它替换单名单规则；不需要同时指定 `--rules`。下次生成会保留 policy；用 `--policy none` 恢复单名单分流。`none` 和 `all` 模式暂时忽略已保存 policy。示例见 `rules/policy.example.json`。IP/CIDR、按用户/端口分流可以在生成器中继续扩展，当前只提供有序域名策略。

## 产物与秘密

默认所有运行文件放在 `$HOME/.local/share/academic-vpn`，**不在 Git 仓库内**。使用 sudo 时默认是 `/root/.local/share/academic-vpn`；可以统一通过 `--state /var/lib/vpn-route-kit` 指定。

```text
state/
├── identity.json                 UUID、REALITY 密钥；重复生成沿用身份
├── settings.json                 VPN/出口设置
├── bin/                          可执行文件
├── config/                       两种入口和可选 VPN 服务端配置
├── rules/                        当前域名和规则集
├── units/                        systemd 文件
└── outputs/
    ├── sing-box/
    └── xray/
        ├── share.txt             VLESS 分享链接
        ├── qr.png / qr.svg        分享链接二维码
        ├── client.json            可运行客户端配置，本地监听 127.0.0.1:2080
        ├── server.json            私人服务端配置
        ├── clash-node.yaml        复制到现有 proxies: 列表的节点片段
        └── clash.yaml             可直接导入的完整 Mihomo 配置
```

sing-box/Xray 是内核名称，两套分享链接都使用标准 `vless://`，没有独立的“sing-box 协议”。客户端只连接 VPS，分流在服务端完成。完整 Clash 配置使用 `MATCH` 把客户端全部流量送到该节点，然后服务器按所选模式决定出口；把节点片段加入已有配置时还需加入原来的代理组/规则。需要支持 REALITY/VLESS 的 Clash Meta/Mihomo，老 Clash 不支持。

服务端配置包含私钥；分享链接、二维码、客户端配置和 Clash 配置虽然不含私钥，但包含能使用节点的 UUID，同样属于私人产物。不要把 outputs、state 或现场日志上传。目录 `700`、文件 `600`，`.gitignore` 也排除了常见私人文件。仓库仅提交源代码、公开规则、文档、测试和维护 skill。

查看产物：

```bash
./entrypoint.sh show --backend sing-box
./entrypoint.sh show --backend xray
```

## 浏览器助手：可选免 F12

在**你自己的有图形界面的电脑**上运行，而不是无桌面的 VPS；需要 Python、SSH 密钥或 ssh-agent：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-browser.txt
.venv/bin/playwright install chromium
.venv/bin/python scripts/browser_auth.py \
  --ssh root@<服务器> \
  --entrypoint /root/vpn-route-kit/entrypoint.sh \
  --state /root/.local/share/academic-vpn
```

它通过 SSH 启动远程 `auth`，打开可见 Chromium，你自行完成登录/MFA；检测 AnyConnect SAML Cookie 后经 SSH 加密连接提交，不打印或保存令牌。只支持 Cookie 型 AnyConnect SAML，尚未在本机实测完整图形登录；浏览器被学校限制、特殊设备检查或其他认证模式请用手动方法。该助手不绕过 MFA，也不会把交互登录变成永久无人值守。

sing-box 官方图形客户端/Dashboard 也提供 endpoint 认证管理；它们的实际网页登录能力依赖平台。终端 F12 方式在 CMU 已验证，作为稳定后备保留。

## 维护与迁移

```bash
./entrypoint.sh status
./entrypoint.sh auth                 # 会话到期/重启后重新登录
./entrypoint.sh doctor               # 出口和页面连通；不证明付费全文权限
./entrypoint.sh generate             # 保留身份、模式、规则及本地端口，更新产物
./entrypoint.sh check
sudo ./entrypoint.sh deploy --backend sing-box
```

重新部署只重启入口，保留已登录的 VPN worker。修改 VPN 网关/协议/本地端口后需要执行 `sudo systemctl restart academic-vpn-vpn`，然后 `auth`。VPN 会话和 Cookie 只在进程内，不保存学校密码；服务器重启或会话到期需要再次认证。VPN 故障时，指定走它的流量失败，不自动回退直连，普通出口不受影响。

更换已部署后端：

```bash
sudo ./entrypoint.sh stop --backend sing-box
sudo ./entrypoint.sh deploy --backend xray
```

若切到 `none` 或外部 SOCKS，旧 VPN worker 不会自动删除；可执行 `sudo systemctl disable --now academic-vpn-vpn`。上述命令只管理本仓库命名的服务，不停止系统原有 `xray.service`。

导入已有 Xray 的单用户 VLESS/REALITY 身份：

```bash
./entrypoint.sh generate --state /独立私人目录 --server <公网IP> \
  --import-xray /usr/local/etc/xray/config.json --mode none
```

只导入 UUID、REALITY 参数和端口，不带入原 WARP、路由或多用户设置。已有 state 不会被再次导入覆盖。迁移前可在另一端口生成测试实例，确认客户端兼容，再自行停掉旧入口并部署。

需要回退时停止本仓库入口，再启动原服务；本仓库不修改原配置。保留原配置备份。可复制仓库和私人 state 到新主机（后者用 SSH 等私人渠道），更新公网地址、重新生成、重新认证。

维护指引在 [`skills/academic-vpn/SKILL.md`](skills/academic-vpn/SKILL.md)。验证方法见 `tests/`。官方资料：[sing-box OpenConnect](https://sing-box.sagernet.org/configuration/endpoint/openconnect/)、[sing-box 规则集](https://sing-box.sagernet.org/configuration/rule-set/)、[Xray 路由](https://xtls.github.io/en/config/routing)、[Mihomo VLESS](https://wiki.metacubex.one/en/config/proxies/vless/)、[社区学术名单](https://github.com/v2fly/domain-list-community)。
