# delivery-verifier

**`dv prove` 检查回归测试能否在撤去修复后失败、恢复修复后实际执行并通过。可选的交付关卡把评审和批准记录绑定到确切的提交。**

面向需要交付编码 Agent 所写改动的开发者。在本机运行，适用于能执行命令的编码 Agent。

[English](README.md) · [设计与配置参考](docs/design.md)

![离线演示：拒绝空洞测试，证明有效回归，缺少人工批准时保持交付阻塞。](docs/assets/demo.gif)

*动图节选虚构示例的真实 CLI 输出，调整停留时间方便阅读。示例使用模拟评审记录，保留人工批准这一步。*

## 快速开始：一条命令验证回归

**当前入口仅适用于候选源码。** 截至 2026-10-26，公开 `main` 位于 `aeec479965619bb5325cbccfcd7e94fb1e962ce8`，`dv prove` 仍在本地 `first-usable-version` 分支。公开可访问的候选提交或发布包仍是发布前置条件。下列命令需要这份候选源码，直接克隆公开 `main` 得到的是旧功能。

拿到包含 `src/dv/prove.py` 的候选工作区后，从其根目录开始。准备带 `venv` 和 `pip` 的 Python 3.10–3.13、Git 2.38+ 及 Linux/macOS。部分 Linux 发行版需单独安装 venv 系统包，例如 `python3-venv`。构建需要 setuptools >=68，pip 通常会下载它；离线安装需提前准备构建依赖。以下命令使用虚拟环境：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
dv prove --help
```

激活环境后，`dv` 会加入当前 shell 的 `PATH`。切到自己的项目时保持环境激活；新开 shell 时重新激活同一环境。

进入**自己的项目仓库**，在基于本地 `main` 的分支上提交修复和回归测试，然后运行：

```sh
dv prove --test "python -m unittest discover -s tests -p test_words.py -v"
```

直接传入测试命令即可，工作项和手动编辑 JSON 均可省去。Red 在 `main` 与 `HEAD` 的合并基点执行，并保留 head 的测试；green 在另一份 detached worktree 中执行 `HEAD`。通过条件是 red 在测试体断言处失败，green 提供完整报告且至少一个测试体通过。空洞断言、运行或导入错误、green 漏跑及全跳过都会被拒绝。Node TAP 的文件级成功会从测试体通过数中扣除。Node 20 的 TAP 将空文件和以绝对路径命名的顶层测试输出为相同形态，因此这类测试名也按保守规则排除；请使用描述行为的测试名。

用 `--base REF --head REF` 指定已提交版本，`--test-file checks/regression.py` 补充自定义纯测试文件，`--json` 输出机器可读结果。其他受支持的命令示例：

```sh
dv prove --test "python -m pytest --tb=long tests/test_words.py"
dv prove --test "node --test --test-reporter=tap tests/words.test.cjs"
```

差异检查通过 `--hygiene` 开启；评审检查通过 `--require-review .dv/item.json` 开启，并读取下文工作项流程中已有的匹配评审。默认只执行指定的回归命令。完整测试套件、交付批准和旧 gate/hook 规则仍由工作项流程负责。

**执行范围是已提交快照。** 暂存、未暂存、未跟踪和已忽略的本地文件留在原处，执行目录排除这些文件。配置读取已提交的 head。测试子进程会清除继承的 `GIT_*` 环境变量，让普通 Git 命令定位到执行 worktree，保留调用者的仓库和索引。每次尝试在 Git 管理目录的 `dv/proofs/` 下保存独立的 `proof.json`、`red.log` 和 `green.log`，命令结束后清理本次执行目录及缓存。CLI 会打印证据目录。失败和中断保留失败状态；遗留恢复记录需要按所列路径检查，避免全仓 worktree prune。

**当前边界：** 0.1.0 正在开发，发布及安装验收仍待完成。支持 unittest、pytest 和 Node TAP；Jest/Vitest、原生 Windows、submodule/LFS 还原及自动安装依赖暂不支持。命令经 POSIX 参数切分后直接执行，shell 展开和管道需由调用者另行处理。依赖由用户准备；指向其他工作区的 editable install 会破坏源码隔离。Worktree 只隔离执行目录，**安全沙箱需另行准备**，命令仍拥有当前用户的文件和网络权限。日志保存在本机，可能包含敏感信息。证明通过说明所选测试区分了两份实现，完整覆盖和断言语义仍需人工判断。评审身份依赖如实声明。参见[可复跑手册](docs/handbook/green-tests.md)、[实现与验收状态](docs/design/first-usable-version.md)和[安全边界](SECURITY.md)。

源码离线示例仍可通过 `python3 examples/demo/run.py` 运行，演示独立的工作项交付流程，并使用模拟评审记录。

## 示例：检查连续空格的修复

修复将 `text.split(" ")` 改为 `text.split()`。空洞测试只检查返回值存在：

```python
self.assertIsNotNone(split_words("red   green"))
```

两份实现都返回列表，所以测试都会通过，`dv redgreen` 拒绝这份证明。把断言换成具体结果：

```python
self.assertEqual(split_words("red   green"), ["red", "green"])
```

现在，撤去修复时测试在断言处失败，恢复修复后通过：

```text
redgreen: FAIL (exit 1)
  red=0, green=0
Hollow test rejected: it passes even with the fix removed.

redgreen: PASS (exit 0)
  red=1, green=0
Meaningful regression proved: red=1, green=0.
```

完整示例还会检查代码差异，并用**模拟评审记录**演示就绪关卡。最后的交付关卡因缺少人工批准而按预期失败。真实改动需要独立评审者检查，并由本人作出批准决定。

关卡会重跑已配置的仓库检查，标出聚焦测试和调试语句等问题。提交或评审输入变化后，原证据失效。

## 同类工具：检查方式的区别

[mutmut](https://github.com/boxed/mutmut) 面向 Python，[StrykerJS](https://github.com/stryker-mutator/stryker-js) 面向 JavaScript；它们生成代码变异，检查测试套件能否发现变化。`dv` 的范围更窄：将本次生产代码改动恢复到 Git 合并基点，保留新增测试，再运行指定的回归命令。变异生成和变异分数在当前范围之外。

[pre-commit](https://github.com/pre-commit/pre-commit) 管理多语言 Git hook，可以运行检查，也可以接入自定义回归脚本。`dv` 提供约定好的 red/green 流程，并将证据记录绑定到提交及评审输入。已有 lint、测试和评审工具继续使用，评审者由你安排。

## 安装：使用本地源码

上面的虚拟环境命令只依赖 Python 自带工具。若选择独立工具环境，请先按官方指引[安装 pipx 并配置 PATH](https://pipx.pypa.io/latest/how-to/install-pipx.html)，或[安装 uv](https://docs.astral.sh/uv/getting-started/installation/)。分别运行 `pipx ensurepath` 或 `uv tool update-shell` 后打开新 shell，让安装后的 `dv` 可从 PATH 找到。然后在候选源码目录执行：

```sh
pipx install .
# 或者：
uv tool install .

dv --help
```

这两条命令安装当前源码目录。构建需要 setuptools；离线安装时，构建工具也需已在本地可用。安装后的运行时依赖是 Python 标准库和本地 Git。上面的示例可以直接运行，省去安装步骤。

源码分发包包含示例、完整测试和引用文档。Wheel 将这些资源按源码目录结构安装到 `<environment>/share/delivery-verifier`。在可写环境中进入该目录，使用环境自身的 Python 执行 `python examples/demo/run.py` 或 `PYTHONPATH=src python -m unittest discover -s tests -v`。这些资源作为本地示例保留，避免占用顶层测试包名。

使用 `uv build` 构建分发包。构建后端将 sdist 所有者统一为 `0/root`，归档时间取 `SOURCE_DATE_EPOCH`，默认值为 UTC 2000-01-01。发布检查见[可复现构建](CONTRIBUTING.md#reproducible-builds)。

## 验证层级：逐项保留证据

| 层级 | 证据 | 检查方式 |
| --- | --- | --- |
| V0 改动说明 | 工作项摘要、可选交付文案 | 记录实现者声明的行为，供后续核验。 |
| V1 仓库检查 | 代码检查、静态检查、完整测试套件 | 重新扫描差异，执行全部已配置命令。 |
| V2 回归证明 | 撤去修复后失败、恢复修复后通过 | 保存两次执行和日志；red 只接受断言失败，拒绝运行器错误、全绿、超时和启动失败。 |
| V3 运行验证 | 可选演示、截图、运行日志 | 检查文件存在且非空，按内容绑定到评审；运行结果由评审者核验。 |
| V4 独立评审 | 对应当前提交及输入的评审报告 | 要求通过结论和不同的模型标识；小改动允许显式声明同模型的新上下文，结果显示警告。 |
| V5 人工批准 | 本人对这份改动的决定 | 核对提交、配置、评审、文案及运行证据与批准时一致。 |

`dv gate` 检查就绪状态，要求 V1、V2、V4，并检查已提供的 V3 附件。`dv gate --require-approval` 增加 V5。警告会保留；有理由的测试豁免显示为跳过。

新提交或 amend 会让原证据失效。证据还绑定当前安装的检查器 Python 源码 SHA-256 摘要，涵盖版本定义和内置解析器。升级或修改检查器后，red/green、评审和批准均需重做；缺少此绑定的旧记录也需重新生成。合并基点、工作项设置、有效配置、交付文案或运行附件改变后，也要重新收集证据。评审改变后需要重新批准。每次运行 gate 都重新检查，磁盘上的旧 `gate.json` 仅供查看。

## 可选流程：工作项与交付关卡

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

### 回归命令：按运行器核对断言

`test_runner` 默认为 `auto`，识别直接调用的 `python -m unittest`、`python -m pytest` / `pytest` 和 `node --test`。两个阶段使用相同的测试配置。直接调用 Python 运行器时，`dv` 会接入内置的阶段探针：unittest 使用启动器，pytest 使用插件，继续沿用命令指定的解释器及已安装的运行器。探针将 `DV-RUNNER-PHASES` 记录写入日志，运行时依赖保持不变。Red 必须退出 1，并输出完整的断言失败报告：

| 运行器 | Red 输出要求 |
| --- | --- |
| Python unittest | `FAILED (failures=N)` 中失败数大于零、错误数为零，每份失败堆栈均为 `AssertionError`。探针需确认每项失败发生在测试方法执行期间；`setUp`、`tearDown`、`addCleanup` 和 `addAsyncCleanup` 失败均会被拒绝。 |
| pytest | 保留默认或完整堆栈（`--tb=long`）及汇总，失败数大于零，每项失败终止于 `AssertionError`，并有 call 阶段证据。排除 setup/teardown 阶段、用例收集错误及 unittest 生命周期失败，包括 pytest 将 unittest 生命周期失败标成 `FAILED` 的情况。short/line/隐藏堆栈格式暂不支持。 |
| Node.js test runner | 使用 `node --test --test-reporter=tap`，`# fail` 大于零、cancelled 为零，根本失败均报告 `failureType: 'testCodeFailure'` 和 `code: 'ERR_ASSERTION'`。`subtestsFailed` 父项需有失败子项；汇总计入父测试，排除 suite 容器。 |

包装命令需显式指定 `test_runner`。Python 包装命令还需接入内置探针：unittest 用 `python -m dv._runner_probe`，pytest 用 `python -m pytest -p dv._runner_probe`，对应解释器中需能导入 `dv`。阶段记录缺失、不完整或有歧义时，回归证明失败。静态检查和完整测试沿用原命令。

对 unittest，`dv` 将测试方法执行期间视为测试主体，包括方法调用的辅助函数和子测试，排除 `setUp`/`tearDown`、对应的异步方法及清理回调。对 pytest 测试函数，边界是 **call 阶段**，涵盖调用测试函数期间运行的全部代码，包括通过 `request.getfixturevalue()` 动态请求的 fixture，以及 `conftest.py` 中的 `pytest_runtest_call` 等 call 阶段 hook。这两处的断言失败同样计入测试主体失败，即使断言位于测试函数自身语句之外。Pytest 的 setup/teardown 阶段和用例收集错误均被排除；由 pytest 执行的 unittest 用例仍按测试方法边界判断。评审者应阅读 red 日志，核对断言来源及其是否证明了目标回归。

Jest/Vitest、裸脚本、隐藏堆栈等暂不支持的报告格式会验证失败。静态检查和完整测试命令保持原有自由度。`red assertion` 单列断言验证结果：即使 green 通过，导入、运行、用例收集或 setup/teardown 阶段错误也会被拒绝。Gate 会重新解析已保存的日志。日志依赖本地命令如实输出。

路径均相对于仓库根目录。可选字段 `description_file` 指向已忽略的本地交付文案，例如 `.dv/description.txt`；`runtime_files` 指向已忽略的运行证据文件。多个工作项各用一个独立的忽略目录。

工作项验证前先提交代码、测试和检查配置。旧 `redgreen` 与 `gate` 流程要求干净的已提交工作树，未忽略的未跟踪文件也需处理；暂存和提交由你完成。独立的 `prove` 命令保留本地未提交改动，仅执行已提交快照。

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
