# delivery-verifier

**`dv` 在本机验证编码 Agent 的改动，把回归测试、代码检查、独立评审和人工批准绑定到同一份提交，再由你决定是否交付。**

[English](README.md) · [设计与配置参考](docs/design.md)

适用于 Claude Code、Codex，以及能执行本地命令的编码 Agent。需要 Python 3.10+、Git 2.38+ 和 Linux/macOS。运行时仅依赖 Python 标准库，通过本地 Git 完成检查。工具本身无模型调用、账号或遥测。0.1.0 正在开发，项目名称暂定。

## 验证目标：找出绿灯后的遗漏

测试全绿时，改动仍可能带着这些问题：

- Agent 修好了空格处理，新增测试却只写 `assertIsNotNone(split_words("red   green"))`。旧实现也返回列表，所以修复前后都能通过。
- 生产代码留着 `console.log(...)`，或测试里留着 `test.only(...)`。前者带来调试输出，后者可能让其他用例被跳过。
- 实现者用同一个模型、同一份上下文评审自己的改动，重复漏掉同一个问题。
- 你批准后，Agent 又 amend 了提交。原批准对应的是修改前的代码。

`dv` 保留新增测试，临时撤去生产代码的修复，执行测试，再恢复修复并执行同一条命令。两次都通过时，回归证明失败。验证关卡还会重跑仓库检查，并核对评审、批准是否对应当前输入。

## 快速体验：运行本地示例

在源码目录中执行，约一分钟即可看完：

```sh
python examples/demo/run.py
```

系统只有 `python3` 时，用它替换命令中的 `python`，版本需为 3.10 或更高。示例直接读取源码，无需安装或下载。脚本在已忽略的 `.dv/` 目录里创建临时 Git 仓库，结束后删除临时仓库及日志，保留项目文件和原有 Git 配置。

示例将 `text.split(" ")` 改为 `text.split()`，先配上只检查返回值存在的测试，再改成检查具体结果：

```python
self.assertEqual(split_words("red   green"), ["red", "green"])
```

输出包含：

```text
redgreen: FAIL (exit 1)
  red=0, green=0
Hollow test rejected: it passes even with the fix removed.

redgreen: PASS (exit 0)
  red=1, green=0
Meaningful regression proved: red=1, green=0.
...
gate: PASS (exit 0)
...
gate: FAIL (exit 1)
  [FAIL] V5 human approval: ...
```

为了全程离线演示，示例使用明确标注的**模拟评审记录**。实际改动需要另一位评审者检查。最后的交付关卡按预期失败：示例保留人工批准这一步，交给真正负责的人。

## 安装：使用本地源码

```sh
pipx install .
# 或者：
uv tool install .

dv --help
```

这两条命令安装当前源码目录。构建需要 setuptools；离线安装时，构建工具也需已在本地可用。安装后的运行时依赖是 Python 标准库和本地 Git。上面的示例可以直接运行，省去安装步骤。

## 验证层级：逐项保留证据

| 层级 | 证据 | 检查方式 |
| --- | --- | --- |
| V0 改动说明 | 工作项摘要、可选交付文案 | 记录实现者声明的行为，供后续核验。 |
| V1 仓库检查 | 代码检查、静态检查、完整测试套件 | 重新扫描差异，执行全部已配置命令。 |
| V2 回归证明 | 撤去修复后失败、恢复修复后通过 | 保存两次执行和日志；全绿、超时、启动失败均无法通过。 |
| V3 运行验证 | 可选演示、截图、运行日志 | 检查文件存在且非空，按内容绑定到评审；运行结果由评审者核验。 |
| V4 独立评审 | 对应当前提交及输入的评审报告 | 要求通过结论和不同的模型标识；小改动允许显式声明同模型的新上下文，结果显示警告。 |
| V5 人工批准 | 本人对这份改动的决定 | 核对提交、配置、评审、文案及运行证据与批准时一致。 |

`dv gate` 检查就绪状态，要求 V1、V2、V4，并检查已提供的 V3 附件。`dv gate --require-approval` 增加 V5。警告会保留；有理由的测试豁免显示为跳过。

新提交或 amend 会让原证据失效。合并基点、工作项设置、有效配置、交付文案或运行附件改变后，也要重新收集证据。评审改变后需要重新批准。每次运行 gate 都重新检查，磁盘上的旧 `gate.json` 仅供查看。

## 使用流程：验证当前改动

仓库需要已有提交和本地基线分支，例如 `main`；在另一条分支上实现改动。自行准备依赖，并在 `.dv.toml` 中配置仓库真实使用的检查命令：

```toml
[dv]
static_commands = [["python", "-m", "compileall", "-q", "src"]]
test_commands = [["python", "-m", "unittest", "discover", "-s", "tests"]]
```

这里以 Python 为例。请替换为项目实际使用的 lint、格式检查、类型检查及完整测试命令；`compileall` 只检查语法。每条命令用参数数组表示，直接执行。静态检查和完整测试均从仓库根目录启动。

也可以把相同配置放在 `pyproject.toml` 的 `[tool.dv]` 下；`.dv.toml` 的配置优先。

初始化工作项：

```sh
dv init --implementer model-a --base main
```

命令创建 `.dv/item.json`，并在仓库本地 Git 排除文件中加入 `.dv/`。填写摘要和针对这次回归的测试命令，例如：

```json
{
  "schema_version": 1,
  "summary": "Split repeated spaces without empty words",
  "base_ref": "main",
  "kind": "small",
  "implementer": "model-a",
  "status": "preparing",
  "test_command": ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_words.py"],
  "test_cwd": ".",
  "test_files": ["tests/test_words.py"],
  "runtime_files": [],
  "exceptions": {}
}
```

路径均相对于仓库根目录。可选字段 `description_file` 指向已忽略的本地交付文案，例如 `.dv/description.txt`；`runtime_files` 指向已忽略的运行证据文件。多个工作项各用一个独立的忽略目录。

验证前先提交代码、测试和检查配置。`dv` 要求干净的已提交工作树，连未忽略的未跟踪文件也需处理；暂存和提交由你完成。

```sh
dv redgreen .dv/item.json
dv hygiene .dv/item.json
```

请独立评审者检查差异和两份日志，重点核对 red 确实因目标回归而失败。保存其报告后，绑定到评审过的完整提交 ID：

```sh
dv review record .dv/item.json --reviewer model-b \
  --head "$(git rev-parse HEAD)" --verdict pass --file .dv/reviewer-notes.txt
dv gate .dv/item.json
```

评审者由你安排，`dv` 只记录报告。模型标识和上下文隔离均依赖如实声明。小改动可用 `--fresh-context` 声明同模型的新上下文，关卡显示警告；大改动要求不同模型标识。

本人检查并批准这份确切的改动后，再记录决定：

```sh
dv approve .dv/item.json --head "$(git rev-parse HEAD)" --note "Approved this exact change."
dv gate .dv/item.json --require-approval
```

批准备注应对应真实决定。`dv approve` 记录批准，获得本人同意仍由实际流程负责。通过后，发布或推送另行操作。

### 命令索引

| 命令 | 用途 |
| --- | --- |
| `dv init [ITEM] --implementer MODEL [--base REF] [--kind small\|large]` | 创建准备中的工作项。默认路径 `.dv/item.json`、基线 `main`、类型 `small`，保留已有工作项。 |
| `dv redgreen ITEM [--timeout SECONDS]` | 执行并记录回归证明。每个阶段默认超时 1,800 秒。 |
| `dv hygiene ITEM` | 检查已提交差异、提交消息及可选交付文案，写出检查结果。 |
| `dv review record ITEM --reviewer MODEL --head SHA --verdict pass\|fix-needed\|drop --file REPORT [--fresh-context]` | 将现成评审绑定到当前输入。未通过或独立性不足的评审仍会被 gate 拦住。 |
| `dv gate ITEM [--require-approval]` | 重算就绪检查；指定参数后增加人工批准检查。 |
| `dv approve ITEM --head SHA --note TEXT` | 重跑就绪检查，记录本人对确切输入的批准。 |
| `dv hooks install --agent claude-code [--dry-run]` | 预览或安装项目本地 hook。 |
| `dv hooks run stop\|pre-tool-use` | 内部适配入口，从标准输入读取 hook 事件 JSON，通常由已安装的 hook 调用。 |
| `dv --help`、`dv COMMAND --help`、`dv --version` | 查看帮助或版本。 |

工作项命令支持 `--json`。退出码为：`0` 成功，可带警告；`1` 验证失败；`2` 输入无效；`3` 运行错误；`130` 被中断。Hook 适配器通过 JSON 表达拦截决定，正常处理事件时退出码为零。

证据和日志与工作项同目录：`redgreen.json`、两份 red/green 日志、`review.json`、`review.txt`、`approval.json`、`hygiene.json`、`gate.json` 及 `checks/`。日志可能包含命令输出中的敏感信息，应保留在本地。

### 代码检查：关注交付遗漏

默认检查新增源代码注释的理由、生成式署名尾注、调试语句、聚焦测试、缺少有效断言的测试、缺少理由的锁文件变更、未完成标记、杂项文件和空白错误。源代码变更超过 200 行时警告，超过 400 行（`small`）或 900 行（`large`）时失败；测试超过 1,200 行时警告。

统计分别列出源代码、测试、文档、生成文件、锁文件和快照。可识别的 Rust 尾部内联测试计入测试行数。Red/green 会拒绝在 merge base 或 `HEAD` 中包含内联测试的变更 Rust 生产文件，包括内联测试保持原样的情况。支持的证明要求两个版本都只含生产代码，因此测试拆分需先进入所选基线；仅在当前改动中移出新增回归测试仍会被拒绝。

这些规则用于辅助评审，识别范围受语言和语法形式限制。`no_test_reason`、`comment_justification`、`lockfile_reason` 接受明确理由，结果中会保留豁免说明。自定义断言框架可用 `weak_test_justification` 说明断言语义，将弱测试检查改为可见警告，red/green 证明仍需通过。`allowed_prose_lines` 支持按原文整行豁免合法示例和必要署名，注释豁免也匹配完整源代码行。`allowed_code_lines` 可按完整源代码行豁免调试输出和待办注释检查，结果会显示豁免。调试检查先排除字符串和注释，未完成标记仅检查注释。披露要求以目标项目政策为准。详细规则见[检查参考](docs/design.md#hygiene)，覆盖设置见[配置参考](docs/design.md#configuration)。

## Claude Code：按需安装 hook

Hook 集成要求 Claude Code **2.1.310 或更新版本**，用于支持进程失败时拦截交付。安装 `dv`、初始化配置中的工作项后，在项目根目录先预览再安装：

```sh
dv hooks install --agent claude-code --dry-run
dv hooks install --agent claude-code
```

安装器只合并项目 `.claude/settings.json` 中属于自己的条目，保留其余设置。安装和预览仅输出本次 hook、目标路径及状态，已有配置和环境变量值留在文件中。PreToolUse 设置 `onFailure: "block"`，进程异常退出或外层超时均拦截交付。外层超时覆盖全部工作项的检查命令，并预留 120 秒；适配器提前 30 秒中断，以完成清理并返回拒绝结果。修改命令数量、工作项数量或单命令超时后，应重新安装。命令里包含可执行文件和项目的绝对路径；移动目录或更换安装位置后，先删除旧的 `dv hooks run` 条目，再重新安装。共享设置的改动需在验证前提交；机器专用设置可保留在 Git 忽略路径中。最后重启或重新加载 Claude Code 会话，让新 hook 生效。

- **Stop** 为标记为 `ready` 或 `approved` 的工作项重跑关卡。声明就绪却验证失败时拦住结束；`preparing` 或有明确理由的 `blocked` 工作项可以结束。同一次结束流程连续拦截三次后，允许会话结束并保留未解决问题，验证结果维持失败。
- **PreToolUse（Bash）** 对识别到的直接 `git push` 检查交付关卡和已批准的 `HEAD`。使用独立、明确的 `git push REMOTE HEAD:DESTINATION`。不支持的语法、强推和复合命令会被拦截，识别到的 GitHub 直接写操作也会被拦截。Hook 只检查命令。

可在 `.dv.toml` 中配置工作项和结束重试次数：

```toml
[dv.hooks]
items = [".dv/item.json"]
max_stop_blocks = 3
allow_commands = []
```

`allow_commands` 按完整命令字符串豁免检查，并明确显示豁免结果。Hook 用于拦截常见遗漏，安全边界仍需宿主环境负责。任意脚本、别名、子进程、包发布和其他 Agent 的工具均在此拦截范围之外。全局设置和权限保持原样。其他 Agent 可显式执行 `dv gate ITEM --require-approval`。移除集成时，只删除项目设置中已安装的 `dv hooks run` 条目。

## 使用范围：保留人的判断

以下工作由外部流程承担：

- 实现修复、编写测试、调用模型、判断评审质量、核验评审者身份及决定批准。
- 仓库发现、贡献政策调研、Issue 和 PR 管理、远程拉取、推送、发布、部署、CI 轮询及贡献台账。
- 命令沙箱、完整秘密扫描、证据的密码学签名，以及核对测试是否因预期原因而失败。

内部 Git 命令禁用 lazy fetch 和全部传输协议，缺失对象时直接失败，由用户自行准备本地对象。Partial clone 要求 Git 支持 `--no-lazy-fetch`，旧版本需使用完整本地克隆。

配置中的命令拥有当前用户的权限和环境，也可能访问网络，运行前应先检查。Red/green 会原地临时替换变更的生产文件，失败时也会尝试恢复；该流程依赖 POSIX 进程控制，适用于 Linux/macOS。运行期间应避免并发编辑工作树。

恢复前会检查路径冲突：`HEAD` 中已删除、工作区却仍存在的文件或符号链接会阻止操作，Git 忽略项也适用。每次证明开始即使旧证明失效，gate 拒绝未完成尝试及残留恢复计划。Red 和 green 分别使用全新的 Python 字节码缓存路径，保留用户已有缓存。自行覆盖 Python 缓存环境变量的命令及其他构建缓存，需由测试命令保证干净构建。

中断后若留下 `restoration.json`，请保留文件并检查受影响路径，再决定如何恢复。无法捕获的进程终止可能打断清理。详见 [SECURITY.md](SECURITY.md)。

## 参与开发与许可

测试、lint 和贡献范围见 [CONTRIBUTING.md](CONTRIBUTING.md)，变更记录见 [CHANGELOG.md](CHANGELOG.md)。

采用 Apache-2.0，版权归 clean99。详见 [LICENSE](LICENSE)。内置的 Python 3.10 TOML 兼容解析器保留其 [MIT 许可证](src/dv/_vendor/tomllib/LICENSE)和[声明](src/dv/_vendor/NOTICE)。
