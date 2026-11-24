# 离线局域网源码 MCP 服务

**一份 Python 文件，Python 3.8+ 标准库即可运行，无需 pip、npm、Node.js 或互联网。** 服务器只读源码，IDE 通过 `http://服务器IP:端口/mcp` 获取源码证据。服务不执行 shell 命令，也不修改源码。

## 1. 远端 Linux 一键启动

把 `server.py`、`start.sh` 复制到能访问源码的服务器同一目录，执行：

```bash
bash start.sh
```

默认监听 `0.0.0.0:8787`，源码根目录 `/tmpdata/DFT_PLAT/code_source`。目录或端口不同时：

```bash
bash start.sh --root /你的源码目录 --port 8787
```

后台运行：

```bash
nohup bash start.sh --root /你的源码目录 --port 8787 > server.log 2>&1 &
```

检查服务：`curl http://服务器IP:8787/healthz`。服务器需已有 Python 3.8+，服务账号对源码目录有读权限，局域网允许访问该端口。`0.0.0.0` 是监听地址，IDE 要填服务器实际 IP。

## 2. IDE 只配置 IP、端口

在 VS Code 设置中搜索 `sourceMcp`，填写：

```json
{
  "dftIde.sourceMcpIp": "192.168.1.100",
  "dftIde.sourceMcpPort": 8787
}
```

IDE 点击“AI 分析”时自动带入 `source_code` MCP 配置，无需用户编辑 `.mcp.json`。已有 AI 终端需关闭后重新打开，或重启 IDE，让新配置生效。IP 留空表示不启用远端源码服务。

本地 `hisalad` MCP 提供执行日志和推导出的源码路径；远端 `source_code` 提供 `source_roots`、`source_list`、`source_search`、`source_read` 四个工具。AI 通过远端工具搜索源码并引用文件路径、行号，不把远端路径当成本地文件。

默认不启用鉴权，适用于可信局域网。如果需要，可在启动时加 `--token 你的口令`，并在 IDE 的 `dftIde.sourceMcpToken` 填同一口令；也可用服务端环境变量 `SOURCE_MCP_TOKEN`。不需要证书、反向代理或登录服务。

## 3. 打包给用户，预置 IP、端口

用已有 VSIX 制作 portable IDE，预置配置：

```bash
python deploy/package_ide.py --vscode-source D:/tools/VSCode-win32-x64 --output D:/release/HiSalad --vsix D:/release/dft-ide-0.0.1.vsix --source-mcp-ip 192.168.1.100 --source-mcp-port 8787
```

`--vsix` 使用已经构建好的安装包，不运行联网安装命令。用户拿到整个 IDE 目录后无需再填 IP。插件构建、Claude/DMAS 客户端和模型接入仍沿用现有流程；本服务只负责局域网源码读取。

## 4. 生成离线服务器小包

开发机器执行：

```bash
python remote-source-mcp/package_server.py
```

生成 `remote-source-mcp/dist/hisalad-source-mcp.tar.gz`，只含服务器代码、启动脚本和本文。远端执行：

```bash
tar -xzf hisalad-source-mcp.tar.gz
cd hisalad-source-mcp
bash start.sh
```

## 参数与边界

`python3 server.py --help` 查看参数：`--host`（默认 `0.0.0.0`）、`--port`（默认 `8787`）、`--root`（可重复配置多个根目录）、`--token`（可选）。只允许 IP/localhost 方式的 HTTP 访问，不支持浏览器跨域请求。

只支持 MCP Streamable HTTP 的无状态 JSON 响应，支持协议版本 `2025-03-26`、`2025-06-18`、`2025-11-25`，不提供旧式 SSE/stdio。配置目录和工具版本需要与诊断的 `sourcePath` 对应。拒绝目录越界和指向目录外的符号链接；搜索跳过符号链接及 `.git`、`node_modules`、`.venv`、`__pycache__`。源码目录应由可信发布流程维护，服务账号只授予读取权限。

文件上限 8 MiB，只读 UTF-8 文本；每次最多读 400 行、64 Ki 字符，长行可能截断。搜索为字面量匹配，最多返回 200 项，最多扫描 2,000 个文件、10,000 个目录项，并在遍历间检查约 10 秒时间限制。`truncated`/`stopReason` 表示需缩小范围重试；底层文件系统卡顿不受此时间限制。目录分页按文件系统顺序。

本地测试：`python -m unittest discover -s remote-source-mcp -p "test_*.py"`，不需安装依赖。

协议参考：[MCP Streamable HTTP 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。
