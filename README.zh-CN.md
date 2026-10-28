# delivery-verifier

**证明这条回归测试确实能抓到旧 bug。**

保留新增测试，先运行旧代码，再运行修复后的代码：

```sh
dv prove --test "<command>"
```

直接传入 unittest、pytest、node:test（TAP）或 Vitest **3.2.x 或精确版本 4.1.12** 命令。

**GitHub Action 摘要**：由 Action 适配器在本地读取[下方合成示例](#合成示例验证连续空格)的证明结果生成，托管 CI 运行另行验证。

```text
dv prove: PASS

- Red: pass (exit 1, normal)
- Green: pass (exit 0, normal)

Assertion-only red failure and executed green pass.
```

[安装](#快速开始一条命令验证回归) · [GitHub Actions](#github-actions) · [真实项目示例](#真实项目phoenix-回归证明) · [English](README.md)

## 快速开始：一条命令验证回归

准备带 `venv` 和 `pip` 的 Python 3.10–3.13、Git 2.38+ 及 Linux/macOS。部分 Linux 发行版需单独安装 venv 系统包，例如 `python3-venv`。

用 [pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html) 安装 **GitHub Release v0.3.1 的 wheel**：

```sh
pipx install https://github.com/clean99/delivery-verifier/releases/download/v0.3.1/delivery_verifier-0.3.1-py3-none-any.whl
pipx ensurepath
```

运行 `pipx ensurepath` 后打开新 shell，再检查安装结果：

```sh
dv --version
dv prove --help
```

uv、Python 虚拟环境和 Git tag 的安装命令见[安装方式](#安装方式选择发布包或源码)。下列命令从 GitHub 获取发布包或源码。

进入**自己的项目仓库**，在基于本地 `main` 的分支上提交修复和回归测试，然后运行：

```sh
dv prove --test "python -m unittest discover -s tests -p test_words.py -v"
```

直接传入测试命令即可，工作项和手动编辑 JSON 均可省去。Red 在 `main` 与 `HEAD` 的合并基点执行，并保留 head 的测试；green 在另一份 detached worktree 中执行 `HEAD`。通过条件是 red 在测试体断言处失败，green 提供完整报告且至少一个测试体通过。空洞断言、运行或导入错误、green 漏跑及全跳过都会被拒绝。Node TAP 的文件级成功会从测试体通过数中扣除。Node 20 的 TAP 将空文件和以绝对路径命名的顶层测试输出为相同形态，因此这类测试名也按保守规则排除；请使用描述行为的测试名。

用 `--base REF --head REF` 指定已提交版本，`--test-file checks/regression.py` 补充自定义纯测试文件，`--json` 输出机器可读结果。其他受支持的命令示例：

```sh
dv prove --test "python -m pytest --tb=long tests/test_words.py"
dv prove --test "node --test --test-reporter=tap tests/words.test.cjs"
dv prove --test "vitest run tests/words.test.js"
```

**Vitest 证明范围：** PASS 表示所选测试检测到了本次改动，业务模块是否修复仍需审查。插件读取而 Vite 未报告的文件也计入待验证改动，详见[运行器契约](#回归命令按运行器核对断言)。

## 合成示例：验证连续空格

这个教学示例将 `text.split(" ")` 改为 `text.split()`，断言连续空格应得到 `["red", "green"]`。它使用 Python 自带的 unittest，无需安装项目依赖。

<details>
<summary>在新目录中复现</summary>

按上文安装 dv，保持环境激活，将 `<example>` 替换为新目录。Git 提交使用你配置的作者身份。

```sh
git init -b main "<example>"
cd "<example>"
printf 'def split_words(text):\n    return text.split(" ")\n' > words.py
git add words.py
git commit -m "Handle single spaces"
git switch -c fix-spaces

printf 'def split_words(text):\n    return text.split()\n' > words.py
mkdir tests
cat > tests/test_words.py <<'PY'
import unittest
from words import split_words


class WordTests(unittest.TestCase):
    def test_repeated_spaces(self):
        self.assertEqual(split_words("red   green"), ["red", "green"])
PY
git add words.py tests/test_words.py
git commit -m "Split repeated spaces"
dv prove --test "python -m unittest discover -s tests -p test_words.py -v"
```

</details>

以下节选来自 macOS 上使用 **v0.3.0 发布 wheel**、Python **3.12.13** 的实际输出：

```text
prove: PASS
[PASS] red assertion: unittest: red failed at assertions with no runner errors
[PASS] green execution: unittest: complete=True, tests={'executed': 1, 'passed': 1, 'failed': 0, 'skipped': 0}; requires a normal exit 0 and at least one passed test body, with no errors
```

Red 退出码为 1，断言报错为 `AssertionError: Lists differ: ['red', '', '', 'green'] != ['red', 'green']`；green 退出码为 0，一个测试通过。CLI 打印的证据目录保存 `proof.json`、`red.log` 和 `green.log`。首屏摘要也来自这次运行。

## 真实项目：Phoenix 回归证明

**真实上游修复的回归证明已通过。** [Phoenix #17270](https://github.com/Arize-ai/phoenix/pull/17270) 由 clean99 提交，2026-10-26 合入，修复保存的 prompt 转成 SDK 参数时丢失 `extra_body` 的问题。新增 pytest 方法覆盖 metadata、采样参数覆盖和空值处理。

2026-10-28 在 macOS、Python **3.12.13** 上复跑，使用从源码安装的 dv **0.3.1**，代码对应 `60b309e41bcd7e75fac876063e27b303782ef016`。`dv prove` **退出码为 0**，实际输出如下，仅替换本机证据路径：

```text
prove: PASS
HEAD deb0731a4606b77c786d86953301ecca39916829 base abbfa7bd3ecc5c82cd17c112713f972b0052db4e
Committed snapshots only; not a security sandbox or delivery approval.
Evidence: <evidence-dir>
hygiene: not_requested
review: not_requested
[PASS] red assertion: pytest: red failed at assertions with no runner errors
[PASS] green execution: pytest: complete=True, tests={'executed': 6, 'passed': 6, 'failed': 0, 'skipped': 0, 'xpassed': 0, 'xfailed': 0, 'errors': 0}; requires a normal exit 0 and at least one passed test body, with no errors
```

在父提交 `abbfa7bd` 上，保留的测试在 `assert kwargs.get("extra_body") == expected_extra_body` 处失败，原因是旧代码丢弃保存的 `extra_body`，使 metadata 和采样参数覆盖丢失；red 退出码为 **1**，**3 个失败、3 个通过**。
在合并提交 `deb0731a` 上，green 退出码为 **0**，**6 个通过**。

两个阶段各有 **2 条警告**，来自未使用的 asyncio 配置，green 执行记录完整并获接受。已跟踪的测试辅助符号链接和上游文件均保持原样。

<details>
<summary>复现这次上游回归证明</summary>

将 `<clone>` 和 `<venv>` 替换为两个独立的新目录。下方命令固定 Phoenix 合并提交及其父提交，并安装 v0.3.1 发布 wheel；项目依赖仅需 Python 客户端和所选测试模块的依赖。实测使用相同的包和全新虚拟环境，安装 dv 时将 wheel URL 换成了上文对应的本地源码。

```sh
git clone --no-checkout --depth 1 https://github.com/Arize-ai/phoenix.git "<clone>"
git -C "<clone>" fetch --depth 2 origin deb0731a4606b77c786d86953301ecca39916829
git -C "<clone>" checkout --detach deb0731a4606b77c786d86953301ecca39916829

python3.12 -m venv "<venv>"
. "<venv>/bin/activate"
python -m pip install --index-url https://pypi.org/simple \
  https://github.com/clean99/delivery-verifier/releases/download/v0.3.1/delivery_verifier-0.3.1-py3-none-any.whl \
  "<clone>/packages/phoenix-client" pytest==8.4.2 deepdiff==8.6.2 Faker==33.3.1
cd "<clone>"
unset PYTHONPATH
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
dv prove \
  --base abbfa7bd3ecc5c82cd17c112713f972b0052db4e \
  --head deb0731a4606b77c786d86953301ecca39916829 \
  --test "python -m pytest -c packages/phoenix-client/pyproject.toml -o pythonpath=src --tb=long packages/phoenix-client/tests/canary/sdk/anthropic/test_messages.py::TestToChatMessagesAndKwargs::test_extra_body_is_preserved"
```

`-c` 选择客户端配置，`-o pythonpath=src` 让测试从各阶段快照导入源码。该转换测试无需服务或模型请求，因此关闭插件自动加载，相应的 asyncio 配置警告保留在日志中。dv 打印的证据目录包含 `proof.json`、`red.log` 和 `green.log`，其中保留了获接受的 green 执行记录。

其余候选的运行器超出当前支持范围：Phoenix [#17253](https://github.com/Arize-ai/phoenix/pull/17253)／[#17246](https://github.com/Arize-ai/phoenix/pull/17246) 和 assistant-ui [#10111](https://github.com/assistant-ui/assistant-ui/pull/10111) 声明使用 Vitest 5；React Hook Form [#13950](https://github.com/react-hook-form/react-hook-form/pull/13950) 使用 Jest。这些结论来自配置检查。上游克隆保留其许可证和声明，本仓库仅提供复现命令。

</details>

## GitHub Actions

在 checkout 和依赖安装后加入 action。下例使用 pytest，将检出的 PR 合并提交与 PR 目标分支的合并基点作比较：

```yaml
name: Regression proof
on: [pull_request]
permissions:
  contents: read
jobs:
  prove:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          fetch-depth: 0
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0
        with:
          python-version: '3.12'
      - run: python -m pip install pytest==8.4.2
      - uses: clean99/delivery-verifier@v0.3.1
        with:
          test: python -m pytest --tb=long tests/test_words.py
```

项目已在提交的依赖清单和锁文件中配置 Vitest 3.2.x 时，可使用：

```yaml
name: Regression proof
on: [pull_request]
permissions:
  contents: read
jobs:
  prove:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          fetch-depth: 0
      - uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0
        with:
          node-version: '20'
      - run: npm ci
      - uses: clean99/delivery-verifier@v0.3.1
        with:
          test: npx vitest run tests/words.test.js
```

需要最严格的版本锁定时，将 action 固定到评审过的完整 commit SHA。

| 输入 | 默认值 | 含义 |
| --- | --- | --- |
| `test` | 必填 | 直接调用受支持的运行器，按参数数组解析，保留原有 shell 展开限制。 |
| `base` | `pull_request` 的目标分支，其他事件使用仓库默认分支 | 从 `origin` 拉取的分支，例如 `main`、`release/topic` 或 `refs/heads/main`。短名称始终按 `refs/heads/` 解析，同名 tag 不影响选择。输入范围仅限分支，排除 tag、commit ID 和远程跟踪别名。证明基线为该分支与检出 `HEAD` 的合并基点，分支缺失时失败。 |
| `version` | `local` | 用 pipx 从 action 自身的源码目录安装，与固定的 action commit 一致。显式填写 `0.3.1` 或 `v0.3.1` 等发布版本时，改为安装对应的 GitHub Release wheel。 |
| `timeout` | `1800` | 每个 red/green 阶段的超时秒数，取正整数。 |
| `working-directory` | `.` | Git 仓库根目录，相对于 `GITHUB_WORKSPACE`，也可传绝对路径。测试从此根目录运行，测试路径按仓库根目录填写。 |

Action 单独准备 Python 3.12 和 pipx 环境，保留测试命令原有的 Python 和 PATH。项目依赖需提前安装。浅克隆会验证失败，并提示设置 `fetch-depth: 0`；拉取操作只在 action 适配层执行。默认分支上的 push 若要验证之前的改动，应将 `base` 设为指向较早提交的分支。

输出为 `ok`（`true` 或 `false`）、`verdict`（`PASS` 或 `FAIL`）和 `evidence-path`（证据目录绝对路径）。Job summary 显示结论、red/green 状态和原因。每次调用上传独立命名的 `dv-proof-*` artifact，保留七天，包含 `proof.json`、阶段日志及适配层诊断。早期失败保留已有诊断。失败或未完成的证明在尝试上传后使 action 失败；应将其设为必需检查，保持默认的失败行为。

Linux/macOS 上沿用现有运行器契约和证明范围。Worktree 只隔离执行目录，上传日志可能包含敏感信息。PR 代码应在一次性的托管 runner 上运行，权限设为只读，并与 secrets 隔离；`pull_request_target` 不适合执行不受信任的 PR 代码。启用前检查测试命令和 artifact 可见范围。完整测试、评审和批准继续单独执行。

## 回归证明：执行范围与边界

差异检查通过 `--hygiene` 开启；评审检查通过 `--require-review .dv/item.json` 开启，并读取下文工作项流程中已有的匹配评审。默认只执行指定的回归命令。完整测试套件、交付批准和旧 gate/hook 规则仍由工作项流程负责。

**执行范围是已提交快照。** 暂存、未暂存、未跟踪和已忽略的本地源码留在原处，执行目录只取已提交源码。Node TAP 和 Vitest 证明会把已安装的 Node 依赖复制到每阶段的独立副本；Python 证明保持原有依赖处理方式。验证策略（policy）读取已提交的 head。测试子进程会清除继承的 `GIT_*` 环境变量，让普通 Git 命令定位到执行 worktree，保留调用者的仓库和索引。执行 worktree 位于调用者仓库及其 `.git` 目录之外。每次尝试在 Git 管理目录的 `dv/proofs/` 下保存独立的 `proof.json`、`red.log` 和 `green.log`，命令结束后清理本次执行目录及缓存。CLI 会打印证据目录。失败和中断保留失败状态；遗留恢复记录需要按所列路径检查，避免全仓 worktree prune。

**当前边界：** 支持 unittest、pytest、Node TAP 和 Vitest 3.2.x、4.1.12；Jest、原生 Windows、submodule/LFS 还原及自动安装依赖暂不支持。Vitest 限用默认 runner 和 forks/threads pool，排除浏览器模式、自定义 runner/pool、全局 setup/teardown。扩展 fixture 在 3.2.x 中继续排除；4.1.12 支持正常完成的 test/file/worker fixture。命令经 POSIX 参数切分后直接执行，shell 展开和管道需由调用者另行处理。依赖由用户准备；指向其他工作区的 editable install 会破坏源码隔离。Worktree 只隔离执行目录，**安全沙箱需另行准备**，命令仍拥有当前用户的文件和网络权限。日志保存在本机，可能包含敏感信息。证明通过说明所选测试区分了两份实现，完整覆盖和断言语义仍需人工判断。评审身份依赖如实声明。参见[可复跑手册](docs/handbook/green-tests.md)、[实现与验收状态](docs/design/first-usable-version.md)和[安全边界](SECURITY.md)。

依赖优先使用 APFS clone 或 Linux reflink；不支持时先检查空间再完整复制，大型依赖树会更慢。

## 回归命令：按运行器核对断言

工作项流程的 `test_runner` 默认为 `auto`，识别直接调用的 `python -m unittest`、`python -m pytest` / `pytest` 和 `node --test`。**Vitest 仅通过 `dv prove` 支持**，识别 `vitest`、`npx vitest`、`pnpm vitest` 及 `pnpm exec vitest`。工作项的 `redgreen` 和对应证据 gate 会拒绝 Vitest，并提示使用 `dv prove`。两个阶段使用相同的测试命令。直接调用 Python 运行器时，`dv` 会接入内置的阶段探针：unittest 使用启动器，pytest 使用插件，继续沿用命令指定的解释器及已安装的运行器。探针将 `DV-RUNNER-PHASES` 记录写入日志，运行时依赖保持不变。Red 必须退出 1，并输出完整的断言失败报告：

Pytest 的 green 判定以探针读取的报告对象为准，允许通过的测试带有 warning，也支持 `-qq` 和 `-p no:terminal`。XPASS 和错误数必须为零。探针将本轮报告写到 checkout 外的临时文件，同时通过继承的管道发送原始内容的摘要。`dv` 校验摘要后将日志记录与执行阶段绑定，测试打印、缺失或随后被改写的报告均按证据异常处理。探针与 `conftest.py`、pytest 插件仍共享同一受信任进程，改写 pytest 报告对象或直接修改探针的代码，不在这条保证的范围内。Red 仍需保留下表要求的堆栈和汇总。

| 运行器 | Red 输出要求 |
| --- | --- |
| Python unittest | `FAILED (failures=N)` 中失败数大于零、错误数为零，每份失败堆栈均为 `AssertionError`。探针需确认每项失败发生在测试方法执行期间；`setUp`、`tearDown`、`addCleanup` 和 `addAsyncCleanup` 失败均会被拒绝。 |
| pytest | 保留默认或完整堆栈（`--tb=long`）及汇总，失败数大于零，每项失败终止于 `AssertionError`，并有 call 阶段证据。排除 setup/teardown 阶段、用例收集错误及 unittest 生命周期失败，包括 pytest 将 unittest 生命周期失败标成 `FAILED` 的情况。short/line/隐藏堆栈格式暂不支持。 |
| Node.js test runner | 使用 `node --test --test-reporter=tap`，`# fail` 大于零、cancelled 为零，根本失败均报告 `failureType: 'testCodeFailure'` 和 `code: 'ERR_ASSERTION'`。`subtestsFailed` 父项需有失败子项；汇总计入父测试，排除 suite 容器。 |
| Vitest 3.2.x / 4.1.12（仅 `prove`） | 内置 JSON reporter 与测试体探针共同提供证据。失败数大于零，最终错误与测试体错误一致，来源为真实 Chai/Node 断言实例或探针观察到的 Vitest 快照不匹配。改名的运行时错误、普通对象、文件或 suite 错误、生命周期错误、退出阶段错误、重试及预期失败测试均会被拒绝。 |

Vitest 省略 `run` 时也只运行一次；显式 watch 模式及 reporter/output 覆盖参数会被拒绝。`-t watch`、`--testNamePattern dev` 等参数值按测试过滤条件处理。`dv` 在回归证明期间关闭 coverage，保留默认的可读报告。Reporter 检查根配置及所有 project 后，接入默认 runner 的内置子类；全局 setup/teardown 继续排除。版本检查接受 Vitest 3.2.x 和精确版本 4.1.12，其他 4.x 与所有 5.x 版本继续拒绝。3.2.x 基线覆盖 Vite 6.3.5、6.4.5 和 7.3.7。4.1.12 已在 Node 20.20、22.23、24.16 上验证 Vite 6.4.5、7.3.7、8.3.5，依赖分别由 npm 和隔离布局的 pnpm 安装。Vite 版本由项目选择。

4.1.12 适配先校验固定源码指纹，再仅修改阶段独占的依赖副本。探针在 fixture 解析完成后观察原始测试函数，每个执行的测试须完整进入并结束一次。Fixture setup/teardown、hook、断言次数检查和脱离测试体的异步错误均按测试体外错误处理。关闭后的错误集合和报告输出前的最终错误集合必须为空，worker teardown 错误也计入检查。配置中的 `onUnhandledError` 回调及 `aroundEach`/`aroundAll` hook 会使证明失败，源码指纹变化同样拒绝。超时、取消和待完成断言仍由 Vitest 原有包装层处理；异步断言需在测试函数内等待完成。

Vitest 命令行只接受以下参数：

| 用途 | 支持的参数 |
| --- | --- |
| 运行和全局 API | `run`、`--run`、`--globals`，后两项为无值开关 |
| 测试选择 | 位置参数形式的文件过滤条件、`-t` / `--testNamePattern`、`--project` |
| Worker 数量 | `--maxWorkers`；`--minWorkers` 仅供 3.2.x 使用，4.1.12 拒绝两种传值写法 |
| 仓库内路径 | `-c` / `--config`、`-r` / `--root`、`--dir` |

带值选项支持独立参数和 `--option=value` 写法。其他参数在执行任一阶段前直接拒绝，包括 `--diff`、`--workspace`、watch 和 reporter 覆盖参数。路径必须位于仓库内，并改指向执行副本；测试名正则和 project 名保持字符串原值。

`dv` 要求 Vite/Vitest 报告的测试输入在 red、green 中保持一致，包括配置文件、配置依赖、setup 文件、workspace 文件及可观察到的导入依赖，以及当前 mode、envDir 对应的 `.env`、`.env.local`、`.env.<mode>`、`.env.<mode>.local`。两阶段分别加载各自提交中的配置。仓库内外的输入都记录原始字节哈希和存在状态。仓库内使用相对路径，外部文件使用路径哈希和文件名标识，主目录与绝对路径前缀替换为占位符。PR 导致这些阶段输入变化、新增或删除时，证明直接失败。

报告还对每个 project 解析后的完整测试配置，以及 Vite 环境、define 和模块解析设置按顶层字段计算哈希。执行目录内的路径以带类型的片段统一处理，覆盖 `viteDefine` 中带引号或转义的字符串，以及 RegExp 源码中的路径。普通文本、define 键、正则 flags 和 `lastIndex` 均保留。Setup 导入依赖从 Vite 各环境的模块图采集。规范化时排除探针 reporter/runner 路径、缓存目录、API 端口和自动生成的 API／探针标识。报告只保存哈希，差异提示只列顶层字段名。配置值使用时间、随机数或变化中的进程状态计算时，两阶段结果不同就会使证明失败；无法规范化的配置对象同样拒绝。Vite 模块解析设置中的函数也会被拒绝，包括 alias 的函数式和对象式 `customResolver` 回调：函数源码相同时，闭包仍可能指向不同模块。请改用声明式 alias。

检查范围是 Vite/Vitest 报告的输入和最终解析出的配置，`dv` 保留程序原有的文件读取行为。插件或其他代码通过 `fs` 等方式读取、而 Vite 未报告的文件，计入本次待验证改动。例如，插件读取变更后的 `offset.dat` 并用它改写测试时，PASS 表示测试检测到了该文件改动；即使业务模块保持不变，也可能得到 PASS。文件读取结果若改变了最终解析配置，配置检查仍会拒绝这次证明。

所有 runner 都从 head 保留 `tests/`、`test/`、`__tests__/`、`test-utils/`、`test_utils/`、`fixtures/`、`__mocks__/` 内的辅助文件及各级 `conftest.py`，并删除仅在 base 中存在的辅助文件。只改辅助文件不足以构成业务代码差异。导出期望值的 `helper.js` 等测试辅助代码应放在这些路径下；导入关系同时覆盖业务模块，因此文件用途按路径约定识别。

支持内联快照和标准外部快照。`dv` 设置 `CI=true`，清除 `UPDATE_SNAPSHOT`，在 3.2.x 中强制 `--update=false`，在 4.1.12 中强制 `--update=none`，并拦截快照保存与删除操作。缺失快照或尝试写入都会让证明失败；触发 Vitest 重写的过时快照条目需先清理。Red 保留 head 的 `.snap`、`__snapshots__` 文件及测试。每次快照读取都要与 head 的原始文件字节及存在状态一致，包括 `toMatchFileSnapshot` 和自定义快照路径。同一测试在 red、green 中读取的仓库相对路径和哈希必须完全一致，实际消费的条目还要按顺序比对文件、条目键及期望序列化内容的哈希。使用快照的测试要求完整名称在同一文件、同一 project 内唯一，名称包含所属 suite。同名测试会使证明失败，跳过的同名项也计入检查。参数化测试应把用例值写进名称，例如 `test.each([0, 1])('value %s', ...)`。条目仍按顺序绑定；切换条目或只在一个阶段消费条目都会让证明失败，提示会指出具体测试和条目。探针依据真实 matcher 的返回结果识别快照不匹配，并在 soft assertion 序列化前判断断言类型。仅把错误改名为 `AssertionError` 会被拒绝。4.1.12 将 `SnapshotClient.match` 的返回值绑定到 matcher 创建的具体 Error，覆盖属性不匹配和原始文件快照的异步路径。4.1.12 会拒绝 domain/polling 快照（`matchDomain`、`processDomainSnapshot`）、直接调用 `SnapshotState.match`／`probeExpectedSnapshot`、直接读取 `_snapshotData`／`_initialData` 缓存，以及绕过绑定的自定义快照 helper。捕获这些访问抛出的异常，或随后跳过测试，证明仍会失败。

最终 JSON 在进程 exit 事件中同步写入，再以 `DV-VITEST-REPORT` 嵌入带哈希的日志。报告要求资源关闭完成、退出码一致，每个执行文件和测试均携带本次探针标识。晚到的异常或拒绝、退出阶段诊断及资源关闭失败均会使报告失效；exit listener 写入后发生的错误也会被拦截。Green 至少有一个测试实际执行并通过，且没有失败或运行器错误。`prove` 校验两份 Vitest 日志后再判定结果。

先安装好测试依赖；`npx` 会增加 `--no-install`。所有 runner 的 proof worktree 都位于仓库和 `.git` 之外。`prove` 复制根目录及包内的 `node_modules`，把依赖和 workspace 链接改指向执行副本。复制会占用额外磁盘，但缓存不会经链接写回调用者的依赖。`node_modules` 中的已提交文件保持提交时的内容，其余依赖补入同一目录。Node runner 的仓库内启动器和明确的路径参数会改指向执行副本，包括 Vitest 文件过滤条件及 config/root/dir 路径；存在歧义时拒绝执行，并提示改用仓库相对路径。测试名正则、project 名等字符串参数保留原样，即使内容恰好与路径相同。Python 命令保留所选解释器和参数，支持仓库内虚拟环境及激活后的 PATH 启动器。全局工具安装保持外部路径，其自身缓存仍可能被写入。探针按次放在执行 checkout 之外的临时 Node 依赖目录，保持执行目录的 Git 状态干净，Git 排除规则和 Vite 文件访问设置保持原样。示例通过 `--globals` 使用 Vitest API：

```sh
python3 examples/vitest/run.py --vitest /path/to/tooling/node_modules/.bin/vitest
```

包装命令需显式指定 `test_runner`。Python 包装命令还需接入内置探针：unittest 用 `python -m dv._runner_probe`，pytest 用 `python -m pytest -p dv._runner_probe`，对应解释器中需能导入 `dv`。阶段记录缺失、不完整或有歧义时，回归证明失败。静态检查和完整测试沿用原命令。

对 unittest，`dv` 将测试方法执行期间视为测试主体，包括方法调用的辅助函数和子测试，排除 `setUp`/`tearDown`、对应的异步方法及清理回调。对 pytest 测试函数，边界是 **call 阶段**，涵盖调用测试函数期间运行的全部代码，包括通过 `request.getfixturevalue()` 动态请求的 fixture，以及 `conftest.py` 中的 `pytest_runtest_call` 等 call 阶段 hook。这两处的断言失败同样计入测试主体失败，即使断言位于测试函数自身语句之外。Pytest 的 setup/teardown 阶段和用例收集错误均被排除；由 pytest 执行的 unittest 用例仍按测试方法边界判断。评审者应阅读 red 日志，核对断言来源及其是否证明了目标回归。

Jest、裸脚本、隐藏堆栈等暂不支持的报告格式会验证失败。静态检查和完整测试命令保持原有自由度。`red assertion` 单列断言验证结果：即使 green 通过，导入、运行、用例收集或 setup/teardown 阶段错误也会被拒绝。Gate 会重新解析已保存的日志。日志依赖本地命令如实输出。

## 同类工具：检查方式的区别

[mutmut](https://github.com/boxed/mutmut) 面向 Python，[StrykerJS](https://github.com/stryker-mutator/stryker-js) 面向 JavaScript；它们生成代码变异，检查测试套件能否发现变化。`dv` 的范围更窄：将本次生产代码改动恢复到 Git 合并基点，保留新增测试，再运行指定的回归命令。变异生成和变异分数在当前范围之外。

[pre-commit](https://github.com/pre-commit/pre-commit) 管理多语言 Git hook，可以运行检查，也可以接入自定义回归脚本。`dv` 提供约定好的 red/green 流程，并将证据记录绑定到提交及评审输入。已有 lint、测试和评审工具继续使用，评审者由你安排。

## 安装方式：选择发布包或源码

### uv：安装发布包

按官方指引[安装 uv](https://docs.astral.sh/uv/getting-started/installation/)，再安装同一份 wheel：

```sh
uv tool install https://github.com/clean99/delivery-verifier/releases/download/v0.3.1/delivery_verifier-0.3.1-py3-none-any.whl
uv tool update-shell
```

更新 PATH 后打开新 shell，运行 `dv --version`。

### Python 虚拟环境：使用 pip

这种方式只需 Python 自带的 `venv` 和 `pip`：

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install https://github.com/clean99/delivery-verifier/releases/download/v0.3.1/delivery_verifier-0.3.1-py3-none-any.whl
dv --version
dv prove --help
```

激活环境后，`dv` 会加入当前 shell 的 `PATH`。切到自己的项目时保持环境激活；新开 shell 时重新激活同一环境。

### Git tag：安装固定版本

通过 v0.3.1 tag 安装对应源码：

```sh
pipx install "delivery-verifier @ git+https://github.com/clean99/delivery-verifier.git@v0.3.1"
```

Release 也提供源码归档 [`delivery_verifier-0.3.1.tar.gz`](https://github.com/clean99/delivery-verifier/releases/download/v0.3.1/delivery_verifier-0.3.1.tar.gz)。源码安装在构建时需要 setuptools >=68，安装器通常会下载它；离线安装需提前准备构建工具。Wheel 可直接安装。安装后的运行时依赖是 Python 标准库和本地 Git。

### 本地源码：用于开发贡献

在源码根目录运行 `pipx install .` 或 `uv tool install .`。Editable 开发环境的配置见[贡献指南](CONTRIBUTING.md#local-setup)。示例也可直接从源码运行。

源码分发包包含示例、完整测试和引用文档。Wheel 将这些资源按源码目录结构安装到 `<environment>/share/delivery-verifier`。在可写环境中进入该目录，使用环境自身的 Python 执行 `python examples/demo/run.py` 或 `PYTHONPATH=src python -m unittest discover -s tests -v`。这些资源作为本地示例保留，避免占用顶层测试包名。

使用 `uv build` 构建分发包。构建后端将 sdist 所有者统一为 `0/root`，归档时间取 `SOURCE_DATE_EPOCH`，默认值为 UTC 2000-01-01。发布检查见[可复现构建](CONTRIBUTING.md#reproducible-builds)。

## 可选流程：工作项与交付关卡

需要把仓库检查、评审和人工批准绑定到提交时，再启用此流程。单独使用 `dv prove` 可直接跳过这些配置。

### 验证层级：逐项保留证据

| 层级 | 证据 | 检查方式 |
| --- | --- | --- |
| V0 改动说明 | 工作项摘要、可选交付文案 | 记录实现者声明的行为，供后续核验。 |
| V1 仓库检查 | 代码检查、静态检查、完整测试套件 | 重新扫描差异，执行全部已配置命令。 |
| V2 回归证明 | 撤去修复后失败、恢复修复后通过 | 保存两次执行和日志；red 只接受断言失败，拒绝运行器错误、全绿、超时和启动失败。green 回执检查（XPASS、错误数、探针绑定的报告）只适用于 `dv prove`。 |
| V3 运行验证 | 可选演示、截图、运行日志 | 检查文件存在且非空，按内容绑定到评审；运行结果由评审者核验。 |
| V4 独立评审 | 对应当前提交及输入的评审报告 | 要求通过结论和不同的模型标识；小改动允许显式声明同模型的新上下文，结果显示警告。 |
| V5 人工批准 | 本人对这份改动的决定 | 核对提交、配置、评审、文案及运行证据与批准时一致。 |

`dv gate` 检查就绪状态，要求 V1、V2、V4，并检查已提供的 V3 附件。`dv gate --require-approval` 增加 V5。警告会保留；有理由的测试豁免显示为跳过。

新提交或 amend 会让原证据失效。证据还绑定当前安装的检查器 Python 与内置 JavaScript 源码 SHA-256 摘要，涵盖版本定义和解析器。升级或修改检查器后，red/green、评审和批准均需重做；缺少此绑定的旧记录也需重新生成。合并基点、工作项设置、有效配置、交付文案或运行附件改变后，也要重新收集证据。评审改变后需要重新批准。每次运行 gate 都重新检查，磁盘上的旧 `gate.json` 仅供查看。

### 合成交付关卡演示

![离线演示：拒绝空洞测试，证明有效回归，缺少人工批准时保持交付阻塞。](docs/assets/demo.gif)

*动图节选虚构示例的真实 CLI 输出，调整停留时间方便阅读。示例使用模拟评审记录，保留人工批准这一步。*

源码离线示例仍可通过 `python3 examples/demo/run.py` 运行，演示独立的工作项交付流程，并使用模拟评审记录。

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

### 工作项配置

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

配置中的命令拥有当前用户的权限和环境，也可能访问网络，运行前应先检查。旧命令 `dv redgreen` 会原地临时替换变更的生产文件，失败时也会尝试恢复；该流程依赖 POSIX 进程控制，适用于 Linux/macOS。运行期间应避免并发编辑工作树。

恢复前会检查路径冲突：`HEAD` 中已删除、工作区却仍存在的文件或符号链接会阻止操作，Git 忽略项也适用。每次证明开始即使旧证明失效，gate 拒绝未完成尝试及残留恢复计划。Red 和 green 分别使用全新的 Python 字节码缓存路径，保留用户已有缓存。自行覆盖 Python 缓存环境变量的命令及其他构建缓存，需由测试命令保证干净构建。

中断后若留下 `restoration.json`，请保留文件并检查受影响路径，再决定如何恢复。无法捕获的进程终止可能打断清理。

`dv prove` 在调用者仓库外的 detached worktree 中运行，并清理自己创建的 worktree 和缓存。每个 Vitest 阶段把 `TMPDIR`、`TEMP`、`TMP` 指向专用临时目录，SSR 转换产物也写入其中；成功、失败或超时后清理该目录，其他临时目录保持原样。详见 [SECURITY.md](SECURITY.md)。

## 参与开发与许可

测试、lint 和贡献范围见 [CONTRIBUTING.md](CONTRIBUTING.md)，变更记录见 [CHANGELOG.md](CHANGELOG.md)。

采用 Apache-2.0，版权归 clean99。详见 [LICENSE](LICENSE)。内置的 Python 3.10 TOML 兼容解析器保留其 [MIT 许可证](src/dv/_vendor/tomllib/LICENSE)和[声明](src/dv/_vendor/NOTICE)。
