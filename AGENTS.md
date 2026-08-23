# Codex 项目契约

## 代理不变量

- `codexcomp` 是 Codex CLI 与上游 Responses API 之间的本地环回代理，地址为 `127.0.0.1:8787`。通过顶层 `openai_base_url` key 配置它，绝不用 `[model_providers]` 条目。
- bind 只能保持环回。按原样转发 `Authorization`；绝不检查、记录或持久化它。
- 上游连接永远是无状态的 SSE POST、每轮重放完整 input：绝不向上游转发 `previous_response_id` 链式引用，也绝不直连上游 WebSocket；绝不新增一个独立的 round 1 零 reasoning 重试——round 1 的零 reasoning 是合法的完整回答（见 `fold.py` ~369-374 与 `test_round1_zero_reasoning`）。
- 干净的上游轮次内容忠实：正常 reasoning→output 形状下不丢失、不重排任何 output item，终止状态原样保留，且不额外插入上游轮次——但每个下游事件都带代理自有的 `sequence_number` 与重新编号的 `output_index`，非 reasoning items 先缓冲、待轮次结束确认干净才释放（干净与否只有到用时才知道），且每个终止事件都带 `metadata.proxy_rounds`/`metadata.proxy_billed_usage`。`fold()` 拥有终止事件：上游 EOF、流错误和续写打开错误变为 `response.incomplete`；被拒绝的首轮变为 `response.failed`。绝不静默丢失输出或伪造完成响应。

## 聚焦验证与 eval

- 改 `fold.py` 前运行 `uv run python test_fold.py`。改 `server.py` 的 WebSocket 路径前运行 `uv run python test_ws.py`。二者都是以 `ALL PASS` 结束的 assert 脚本；本仓没有 pytest、lint 或 typecheck 套件。
- 仅在明确有意时运行 `uv run codexcomp-eval` 或 `uv run codexcomp-sudoku-eval`：每次都会调用 Codex 并消耗真实 tokens 与 quota。

## 文档与发布

- Issue tracker 契约见 `docs/agents/issue-tracker.md`。
- 对用户可见的行为，让 `README.md` 与 `README.zh-CN.md` 保持一致。保留 neteroster/CodexCont 的机制致谢，并让 `LICENSE` 保持纯 MIT 文本。
- 发布时先在候选提交中步进包版本，再合并并推送 `master`；必须等该**同一 SHA** 的普通 CI 全绿后，才推送匹配的带注解 `v*` tag（命令：`git tag -a vX.Y.Z -m "Release vX.Y.Z"`）。tag 一旦出现在远端就不移动或复用；失败修复使用下一个 patch。`v0.1.0` 至 `v0.3.8` 是 2026-08-17 该门禁上线前创建的轻量 tag，是既成事实，绝不补修或重新打 tag；annotated-tag 门禁从下一个发布版本起适用。Release 会再次复用共享 CI，并校验 tag、包版本和 master 祖先关系，然后发布 PyPI；之后创建 GitHub Release。systemd unit 绝不自行更新：只有明确存在活跃的本地 uv-tool/service 部署时，才以 `uv tool upgrade codexcomp` 和 `systemctl --user restart codexcomp` 收尾；否则跳过这些命令，不能臆测存在部署。
