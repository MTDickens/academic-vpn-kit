# 内置规则组

`groups.json` 注册彼此独立的开关。所有开启的组取并集；关闭一个组不会覆盖其他组或自定义有序规则。Google、SheerID 默认关闭，向导菜单 8 可分别开启，使用已选择的 VPN 出口。它们同时适用于 sing-box、Xray 和 Linux 本机接管。

## Google

`google-domains.txt` 来自 [v2fly/domain-list-community](https://github.com/v2fly/domain-list-community/blob/master/data/google) 的 Google 组及递归引用的子组。文件头记录来源版本和包含的子组，许可证保存在 `domain-list-community-LICENSE`。

收录搜索、Gmail、Drive、YouTube、Gemini、Google Cloud、Firebase、Android、Play 等产品以及广告、统计、托管服务的域名。保留 `full:` 的精确匹配语义，包含所有地域/广告属性；两条 Play CDN 正则已由同组的完整域名后缀覆盖，无需重复添加。未知规则语法会提示维护者处理。

这是社区域名快照，不保证覆盖未来产品、所有第三方登录/付款资源、产品绑定的自定义域名或仅使用 IP 的连接。不会把 `.com`、`.app`、`.dev` 或共享 CDN 整体纳入。客户端必须将请求送到本节点并保留目标域名，服务器才能分流；Clash 的本地 DIRECT 规则不会因服务器新增组而改变。

普通用户无需另行下载这份名单。维护者更新时运行：

```bash
git clone --depth 1 https://github.com/v2fly/domain-list-community.git /tmp/domain-list-community
python3 scripts/import_google_domains.py /tmp/domain-list-community
```

已有该临时目录时先更新它或换一个新目录。导入后提交名单和许可证。已部署节点更新名单后，使用菜单 1 重新生成并部署，或通过菜单 8 修改开关后应用；菜单 7 只更新学术名单。

## SheerID

独立匹配 `sheerid.com` 及全部子域名，含 `services.sheerid.com`、`verify.sheerid.com`、`my.sheerid.com`。域名依据 [SheerID 官方 API 文档](https://developer.sheerid.com/api-quickstart) 和 [验证页面](https://verify.sheerid.com/)。

第三方商家、学校登录、共享 CDN 依然遵循各自规则，不会因开启 SheerID 而整体加入。需要 Google 时单独开启 Google 组。
