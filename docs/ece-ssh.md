# 通过本节点访问 CMU ECE SSH

[ECE-ITS 官方集群说明](https://cmu-enterprise.atlassian.net/wiki/spaces/ITS/pages/2332131370/ECE%2BCommunity%2BCompute%2BClusters)要求连接 CMU VPN。这里使用的链路是：你的电脑 → VLESS 节点 → CMU VPN → ECE 主机。学校账号仍需具备该资源的使用权限。

1. 在服务器运行 `sudo ./entrypoint.sh`，进入“快速调整 VPN”，选择按规则组分流，保持 CMU 组开启并应用。学术组可独立开关。需要时完成学校 VPN 登录。
2. 在自己的电脑启动 sing-box、Xray 或 Mihomo/Clash Meta 客户端，选择本节点。仓库生成的 sing-box/Xray 客户端 SOCKS 端口是 `2080`，完整 Clash 配置的 mixed-port 是 `7890`；其他客户端以实际 SOCKS 端口为准。
3. 在 Mac 的 `~/.ssh/config` 添加下面的配置，把 `你的AndrewID` 换成自己的 ID，端口换成客户端实际端口：

```sshconfig
Host ece
    HostName ece017.ece.local.cmu.edu
    User 你的AndrewID
    ProxyCommand nc -X 5 -x 127.0.0.1:7890 %h %p
    ServerAliveInterval 60
```

4. 在自己的电脑运行 `ssh ece`，按提示使用自己的学校账号认证。VS Code Remote-SSH 也可以选择同一 `ece` 主机配置。

SSH 不会因为浏览器用了系统代理就自动走代理。这里显式使用 SOCKS5，将主机名传到代理侧，避免在本地解析 `ece.local.cmu.edu` 失败。`nc -X 5 -x` 的语义见 [OpenBSD nc 手册](https://man.openbsd.org/nc)；Linux 需使用支持这些参数的 OpenBSD netcat，Windows 的代理命令需另行配置。

如果把节点加入自己的 Clash 配置，确保 `cmu.edu` 被送往本节点，例如将 `DOMAIN-SUFFIX,cmu.edu,你的节点或代理组名称` 放在通用直连规则之前。本示例使用 SOCKS 传入域名；直接使用内网 IP、只开启浏览器代理，或让 TUN 在本地丢失域名信息，不等同于这条已验证的路径。

当前验证范围：两种 VLESS 服务端均通过现有 CMU VPN 收到 `ece017.ece.local.cmu.edu:22` 的 SSH 握手；未登录学校账号，未验证 GPU 作业或账号权限。其他节点和 GPU 使用方式请以 [ECE Linux 资源指南](https://cmu-enterprise.atlassian.net/wiki/spaces/ITS/pages/3549888534/ECE%2BLinux%2BComputing%2BResources%2BGuide) 为准。

## 直接在这台 Linux 上使用

服务器菜单 9 开启“接管 Linux 本机网络”，且菜单 8 选择按规则分流（CMU 组开启）或全部走 VPN 后，在 Linux 上可以直接执行 `ssh 你的AndrewID@ece005.ece.local.cmu.edu`，无需 SOCKS ProxyCommand。它使用该 Linux 上的 TUN 和 DNS；不影响 Mac 上仍需自己的代理设置。测试时 `ece005` 可用，节点可达性会变化，可根据学校列表更换主机。
