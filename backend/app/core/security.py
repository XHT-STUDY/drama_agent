"""集中安全工具（I-03）。

把散落在各模块的输入卫生 / 输出转义 / 密钥脱敏逻辑收敛到一处，
作为「新增代码的安全默认」：
- `escape_html`：Markdown/HTML 输出转义（防 <script> 注入）；
- `sanitize_filename_part`：文件名净化（防路径分隔符 / 控制字符）；
- `assert_safe_key`：存储键防路径穿越校验；
- `mask_secret` / `truncate_content`：日志脱敏（logging.RedactFilter 复用）。

模块边界：纯函数，无 DB / 网络 / LLM 依赖。storage / logging / exporters 复用本模块。
"""

from __future__ import annotations

import re

# 需脱敏的密钥/令牌模式。每项为 (正则, 替换串)，
# 正则首组捕获字段前缀（如 api_key= / bearer ），替换保留前缀、掩蔽值本身。
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI 风格 API Key：保留 sk- 前缀，掩蔽后续值
    (re.compile(r"(sk-)[A-Za-z0-9_\-]{6,}"), r"\1***"),
    # api_key / apikey 字段赋值
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;&\"']+"), r"\1***"),
    # Authorization: Bearer 授权头
    (re.compile(r"(?i)(authorization\s*[=:]\s*)bearer\s+[A-Za-z0-9._~+/=\-]+"), r"\1***"),
    # 裸 Bearer 令牌
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{6,}"), r"\1***"),
    # access_token / token 字段赋值
    (re.compile(r"(?i)(access[_-]?token\s*[=:]\s*)[^\s,;&\"']+"), r"\1***"),
]

# HTML 转义映射（& 先转，避免二次转义）
_HTML_ESCAPES: dict[str, str] = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
}


def escape_html(text: object) -> str:
    """HTML 转义（I-03）。

    把可能由用户/LLM 提供的文本中的 HTML 特殊字符转义为实体，
    使导出 Markdown / 前端渲染中的 `<script>` 等以纯文本显示，
    而不是被 HTML 解析器当作标签执行。`&` 先转，避免重复转义。
    """
    if text is None:
        return ""
    return "".join(_HTML_ESCAPES.get(ch, ch) for ch in str(text))


def mask_secret(text: str, *, max_len: int | None = None) -> str:
    """掩蔽文本中的密钥/令牌，可选截断超长内容（I-02/I-03）。

    覆盖 sk-* API Key、api_key 字段、Bearer 令牌与 Authorization 头；
    保留字段名前缀（如 api_key=），只掩蔽值本身。非字符串输入原样返回。
    """
    if not isinstance(text, str) or not text:
        return text
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    if max_len is not None and len(text) > max_len:
        text = truncate_content(text, max_len)
    return text


def truncate_content(text: str, max_len: int) -> str:
    """截断超长文本，并附加明确截断标记。

    用于日志 / 预览等「不得全量落盘或全量展示」的出口，
    避免把完整 Prompt / 完整脚本泄漏到日志采集系统。
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…（已截断）"


def sanitize_filename_part(name: str, *, max_len: int = 40) -> str:
    """净化文件名片段：路径分隔符 / 控制字符 → `_`，截断到 max_len。

    用于导出文件名与上传文件名等「进文件系统」的字符串，
    杜绝 `/`、`\\`、空字节等路径注入。
    """
    if not isinstance(name, str):
        return ""
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", name)
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "_", cleaned)
    return cleaned[:max_len]


# 存储键必须是「纯文件名」：不含路径分隔符、不含 `..`、不含空字节
_SAFE_STORAGE_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def assert_safe_key(key: str) -> None:
    """校验存储键合法性，防路径穿越。

    Raises:
        ValueError: key 含路径分隔符 / `..` / 空字节 / 非法字符时
    """
    if not isinstance(key, str) or not _SAFE_STORAGE_KEY_RE.match(key):
        raise ValueError(f"非法的存储键: {key!r}")
