# VPN Route Kit

Linux 上的 VLESS + REALITY 一键部署仓库。可选 sing-box 或 Xray 入口，可选学校 VPN，支持普通出口、独立规则组分流和全部代理流量走 VPN。VPN 不写死 CMU；默认是 **不使用 VPN 的普通节点**。

## 运行方式

支持 Ubuntu/Debian、systemd、x86_64/aarch64。安装版本固定为 sing-box 1.14.2、Xray 26.3.27，从官方 GitHub Release 下载。没有额外的 SHA 校验、审核或批准步骤。首次安装需要能访问 GitHub 和 apt 软件源；端口及云防火墙由你自行开放。

```bash
git clone <你的仓库地址> vpn-route-kit
cd vpn-route-kit
sudo ./entrypoint.sh
```

直接运行即进入中文向导，不需要 `--xxx` 参数。逐项选择操作、私人配置目录、sing-box / Xray、公网地址、端口，以及普通节点 / 名单分流 / 全部代理流量走 VPN。每项都显示默认值，回车采用默认值；再次运行沿用已保存的设置。VPN 可选 CMU、其他学校或已有 SOCKS5，规则可选域名名单或有序规则 JSON。SOCKS5 密码隐藏输入。

只生成文件、不启动服务时，在向导中选择“只生成配置、链接和二维码”。查看产物、登录 VPN、检查连接、停止服务和更新学术名单，也都在同一个菜单里。先完成提问，再开始安装和部署；提问期间 Ctrl+C 可取消。

### 日常调整：总开关 + 独立规则组

已安装后，运行 `sudo ./entrypoint.sh`，选择 **8. 快速调整 VPN**（已安装时默认选中）。先选择 VPN 关闭、按规则组分流或全部代理流量走 VPN。按规则组分流时，出现独立开关：

```text
1. [开] 学术网站 / 当前域名名单
2. [开] CMU：cmu.edu 及其所有子域名
0. 应用当前选择（回车）
```

输入编号即可反转对应开关，可以连续切换多个，最后回车一次性应用。默认两个组都开启；第一次升级旧配置时也会展示这两个默认值，回车应用后生效。分组选择保存到私人 `settings.json`，关闭 VPN 或临时切成全部代理后，再恢复分流仍保留原来的各组开关。所有组都关闭时，未被自定义有序规则匹配的流量走普通出口；要停止内置 VPN 进程，请用总开关“关闭 VPN”。

域名组按并集匹配：命中任一开启的组就走 VPN。例如 `cmu.edu` 同时覆盖根域名、`www.cmu.edu` 和 `ece017.ece.local.cmu.edu`，不匹配 `notcmu.edu`。关闭一个组不会强制直连其他开启组中也包含的域名。自定义学术名单不要混入整校域名，可保持两个开关职责清晰。

无需重走地址、端口、后端等安装问题，也不会下载程序或安装依赖。节点身份和客户端连接参数沿用，客户端无需重新导入。首次启用 VPN 且没有连接设置时才补问提供商；会话过期或关闭后重连时可能需要学校登录。配置未变则直接返回；实际切换会重启代理入口，现有连接可能短暂中断，配置未变的 VPN 会话继续使用。

这些开关不指定 VPN 提供商，统一使用已选出口；使用其他学校 VPN 时，可以关闭 CMU 组。规则组在 `rules/groups.json` 注册，新增组只需一个独立定义：`name`、默认开关 `default`，以及 `domains` 域名数组或 `file` 名单路径（相对 `rules/`）。界面和两种后端自动读取，无需增加组合模式。新组通常设 `default: false`，由用户主动开启。内置学术组的 `$academic` 指向用户选定的名单。

更换提供商、修改地址/端口或更换名单文件时，使用“安装或更新节点”。通过本节点访问 ECE SSH / VS Code 的具体步骤见 [ECE 连接指南](docs/ece-ssh.md)。

以下带参数命令保留给自动化和进阶使用，普通使用直接运行上面的入口即可。

默认选择 sing-box；要使用 Xray，加 `--backend xray`。两种后端共用身份和配置来源，是互换方案，不能同时占用同一入口端口。端口被旧服务占用时会说明原因并退出，不自动关闭旧 Xray。

### Linux 本机网络：代理与整机接管切换

本节控制**运行脚本的这台 Linux**，不是 Mac，也不改变客户端的 Clash 模式。运行：

```bash
sudo /root/academic-vpn-kit/entrypoint.sh
```

选择 **9. Linux 本机网络**，再选择：

| 选择 | Linux 普通程序的行为 |
|---|---|
| 仅代理 | 恢复原 DNS 和普通出口；现有 VLESS 节点继续按自己的规则工作 |
| 本机分流 | 无需设置代理，学术 / CMU 等开启的规则组走 VPN，其他流量走普通出口 |
| 本机全局 | 无需设置代理，本机 TCP/UDP 流量走 VPN，包括通过域名访问学校内网 |

它复用已登录的 sing-box/OpenConnect worker，新建独立的 `academic-vpn-host.service` 和 TUN 接口；切换不需要重启学校登录会话。需要先安装节点并启用、登录内置 VPN。本机接管当前支持 Linux/systemd + 内置 OpenConnect，需有 `/dev/net/tun`、`iproute2`、`nftables`、`curl`；外部 SOCKS 的推送 DNS 无法自动获取，暂不支持此菜单。

VPN 控制连接、当前 SSH 管理来源、物理网卡直连网段及本仓库 sing-box/Xray 进程保留普通出口，避免回环或断开管理连接。此处的“全局”指这些例外之外的 TCP/UDP；ICMP（例如 ping）不通过 SOCKS 隧道，不能用 ping 判断该模式是否正常。已有入站服务连接由 Linux conntrack 保留其返回路径。

本机分流沿用现有域名组及自定义有序规则。菜单 8 修改规则时，会暂时停止本机 TUN，完成代理部署和认证后再按新的规则恢复原本机档位；在菜单 8 关闭 VPN，则本机接管也关闭。全部代理出口（菜单 8）和本机全局（菜单 9）是两个独立控制项。

DNS 会临时切换到 TUN 内的解析器，A/AAAA 使用 FakeIP 保留域名，VPN worker 最终使用学校 DNS 解析 VPN 目标；本机分流的其他目标使用公共 DNS。自带 DoH、旧 DNS 缓存或直接使用 IP 的程序，在域名分流模式下不能可靠匹配域名组，整机全局不依赖域名匹配。应用缓存的 FakeIP 在关闭 TUN 后可能需要重新连接或重启该应用。

正常关闭、启动失败或 TUN 进程异常退出时恢复 `/etc/resolv.conf` 原文件或符号链接，并清理本服务的路由/nftables。本机接管不会自动开机启动；DNS 恢复服务会在重启后恢复上次意外中断的配置。重启后先完成学校登录，再从菜单 9 开启。VPN 会话或 worker 单独断开时，仍运行的 TUN 不把应走 VPN 的流量回退直连；**若 TUN 服务本身退出，会恢复普通网络，此功能不是永久断网保护开关。**

紧急恢复普通网络：

```bash
sudo systemctl stop academic-vpn-host.service
```

所有私人 TUN 配置、DNS 备份和状态位于原 state 下的 `host/`，不进入仓库。若 DNS 被其他软件同时修改，恢复程序会保留其修改和备份并报告冲突，不强行覆盖。

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

`all` 表示 **进入这个代理节点的全部 TCP/UDP 业务流量**，不是整台 VPS 的 SSH、软件更新或其他进程。默认路由保持原样；REALITY 握手目标和 VPN 控制连接也独立。直接指定局域网/本机 IP 仍拒绝访问；匹配 VPN 的域名由 VPN 侧解析，可连接学校内网地址。

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

优先级为：自定义有序规则（第一条命中即生效）→ 开启的域名组 → 私有 IP 地址拦截 → policy 默认出口。有序规则可以覆盖组，例如阻断某个 CMU 子域名；关闭域名组不会覆盖有序规则中显式指定的 VPN 出口。下次生成会保留 policy；用 `--policy none` 恢复单名单分流。`none` 和 `all` 模式暂时忽略已保存 policy 和组开关，回到分流模式后恢复。示例见 `rules/policy.example.json`。IP/CIDR、按用户/端口分流可以在生成器中继续扩展，当前只提供有序域名策略。

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

重新部署会重启入口；VPN 配置相同时保留已登录的 worker，网关/协议/本地端口变化时自动重启 worker，然后需要 `auth`。VPN 会话和 Cookie 只在进程内，不保存学校密码；服务器重启或会话到期需要再次认证。VPN 故障时，指定走它的流量失败，不自动回退直连，普通出口不受影响。

更换已部署后端：

```bash
sudo ./entrypoint.sh stop --backend sing-box
sudo ./entrypoint.sh deploy --backend xray
```

切到 `none` 或外部 SOCKS 时，会自动停止并禁用本仓库旧 VPN worker。上述命令只管理本仓库命名的服务，不停止系统原有 `xray.service`。

导入已有 Xray 的单用户 VLESS/REALITY 身份：

```bash
./entrypoint.sh generate --state /独立私人目录 --server <公网IP> \
  --import-xray /usr/local/etc/xray/config.json --mode none
```

只导入 UUID、REALITY 参数和端口，不带入原 WARP、路由或多用户设置。已有 state 不会被再次导入覆盖。迁移前可在另一端口生成测试实例，确认客户端兼容，再自行停掉旧入口并部署。

需要回退时停止本仓库入口，再启动原服务；本仓库不修改原配置。保留原配置备份。可复制仓库和私人 state 到新主机（后者用 SSH 等私人渠道），更新公网地址、重新生成、重新认证。

维护指引在 [`skills/academic-vpn/SKILL.md`](skills/academic-vpn/SKILL.md)。验证方法见 `tests/`。官方资料：[sing-box OpenConnect](https://sing-box.sagernet.org/configuration/endpoint/openconnect/)、[sing-box 规则集](https://sing-box.sagernet.org/configuration/rule-set/)、[Xray 路由](https://xtls.github.io/en/config/routing)、[Mihomo VLESS](https://wiki.metacubex.one/en/config/proxies/vless/)、[社区学术名单](https://github.com/v2fly/domain-list-community)。
