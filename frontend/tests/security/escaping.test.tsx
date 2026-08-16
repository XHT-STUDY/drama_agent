/** 前端转义回归测试（I-03）。
 *
 * 覆盖：
 * - escapeHtml 五类特殊字符转义（& 先转防二次转义）；
 * - buildExportMarkdown 中剧本对白 / 设定字段含 `<script>` 时
 *   以纯文本实体输出，不出现可执行标签；
 * - 序列化器结构性 Markdown 语法不受转义影响。
 *
 * 纯函数测试，无 DOM / 网络依赖。
 */

import { describe, it, expect } from "vitest";

import { buildExportMarkdown, escapeHtml, type ExportData } from "@/lib/export";

describe("escapeHtml", () => {
  it("转义 <script> 为纯文本实体", () => {
    expect(escapeHtml("<script>alert(1)</script>")).toBe(
      "&lt;script&gt;alert(1)&lt;/script&gt;",
    );
  });

  it("& 先转，避免重复转义", () => {
    expect(escapeHtml("a & b < c > d")).toBe("a &amp; b &lt; c &gt; d");
    expect(escapeHtml("&lt;")).toBe("&amp;lt;");
  });

  it("引号转义", () => {
    expect(escapeHtml('"')).toBe("&quot;");
    expect(escapeHtml("'")).toBe("&#39;");
  });

  it("空值与数字", () => {
    expect(escapeHtml(null)).toBe("");
    expect(escapeHtml(undefined)).toBe("");
    expect(escapeHtml(123)).toBe("123");
  });
});

describe("buildExportMarkdown 内容转义", () => {
  const data: ExportData = {
    projectTitle: "测试项目",
    storyBible: null,
    outline: null,
    scripts: [
      {
        episode_number: 1,
        title: "第1集",
        opening_hook: "开场",
        ending_hook: "悬念",
        plain_text: "",
        word_count: 100,
        dialogue_ratio: 0.5,
        referenced_outline_artifact_id: "art-1",
        scenes: [
          {
            scene_number: 1,
            location: "天台",
            time_of_day: "夜",
            characters: ["主角"],
            action: "风吹过",
            dialogue: [
              { speaker: "主角", text: "<script>alert('xss')</script>" },
            ],
          },
        ],
      },
    ],
    evaluations: [],
    revisions: [],
  };

  it("剧本含 <script> 渲染为纯文本", () => {
    const md = buildExportMarkdown({
      projectTitle: data.projectTitle,
      exportedAt: "2026-08-16T00:00:00",
      data,
      kinds: ["script"],
    });
    expect(md).toContain("&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;");
    expect(md).not.toContain("<script>");
    expect(md).not.toContain("alert('xss')");
  });

  it("结构 Markdown 语法不受影响", () => {
    const md = buildExportMarkdown({
      projectTitle: data.projectTitle,
      exportedAt: "2026-08-16T00:00:00",
      data,
      kinds: ["script"],
    });
    expect(md).toContain("# 测试项目 — 内容导出");
    expect(md).toContain("# 第 1 集剧本：第1集");
    expect(md).toContain("## 第 1 场：天台（夜）");
    expect(md).toContain("- 主角：");
  });

  it("项目名含 HTML 时转义", () => {
    const md = buildExportMarkdown({
      projectTitle: "<b>剧名</b>",
      exportedAt: "2026-08-16T00:00:00",
      data,
      kinds: ["script"],
    });
    expect(md).toContain("# &lt;b&gt;剧名&lt;/b&gt; — 内容导出");
    expect(md).not.toContain("<b>剧名</b>");
  });
});
