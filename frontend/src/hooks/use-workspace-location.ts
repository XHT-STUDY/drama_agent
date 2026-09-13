"use client";

/** useWorkspaceLocation — 工作台 URL 导航事实（W1-02）。
 *
 * URL 是可恢复的导航事实（刷新/后退/分享都回到同一份稿件与工具面板），
 * TanStack Query 只是服务端事实缓存；Artifact 内容不落 localStorage。
 *
 * 固定字段：
 * - artifact=<uuid>       正在阅读的确切 Artifact（切稿 push——后退回到上一篇）
 * - scene=<正整数>        剧本内只读场景锚点（replace）
 * - conversation=<uuid>   当前会话（replace——恢复事实，不是浏览历史）
 * - panel=read|evaluation|diff|exports|sources  工具面板（replace）
 * - compare=<uuid>        Diff 基线 Artifact（replace）
 * - run=<uuid>            聚焦展示进度的 Run（replace；缺省由工作台
 *                         从焦点计划派生）
 *
 * 语义（W1-02 卡）：首次加载未带 artifact 时由工作台选最新可读作品并
 * replace 到确切 ID；消息/任务事件不改阅读选择。非法值清除对应字段
 * 并提示——绝不"猜测最新稿"替代用户输入。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

export type WorkspacePanel = "read" | "evaluation" | "diff" | "exports" | "sources";

const PANELS: readonly WorkspacePanel[] = [
  "read",
  "evaluation",
  "diff",
  "exports",
  "sources",
];

// 通用 UUID 形状即可：未知/失效 ID 由服务端 404 兜底（画布有局部重试），
// 这里只挡明显不是 ID 的脏值
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export interface WorkspaceLocation {
  artifactId: string | null;
  scene: number | null;
  conversationId: string | null;
  panel: WorkspacePanel;
  compareId: string | null;
  runId: string | null;
  /** 非法参数的清理提示：粘性——URL 清理后仍显示，直到 clearNotice() */
  notice: string | null;
  clearNotice: () => void;
  /** 切稿（浏览语义：push，回退回到上一篇）；重置 scene/compare，panel 可指定 */
  openArtifact: (artifactId: string, panel?: WorkspacePanel) => void;
  /** 无历史记录地设定当前稿件（首载默认选稿/非法值清理用） */
  replaceArtifact: (artifactId: string | null, panel?: WorkspacePanel) => void;
  setPanel: (panel: WorkspacePanel) => void;
  setScene: (scene: number | null) => void;
  setCompare: (compareId: string | null) => void;
  setConversation: (conversationId: string | null) => void;
  setRun: (runId: string | null) => void;
}

function buildUrl(
  pathname: string,
  base: URLSearchParams,
  patch: Record<string, string | null>,
): string {
  const params = new URLSearchParams(base.toString());
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === "") params.delete(key);
    else params.set(key, value);
  }
  const qs = params.toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

export function useWorkspaceLocation(): WorkspaceLocation {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const raw = useMemo(() => {
    const get = (key: string) => searchParams.get(key);
    return {
      artifact: get("artifact"),
      scene: get("scene"),
      conversation: get("conversation"),
      panel: get("panel"),
      compare: get("compare"),
      run: get("run"),
    };
  }, [searchParams]);

  // 校验：非法值不进入状态（渲染层拿到 null），并生成清理提示
  const artifactValid = raw.artifact !== null && UUID_RE.test(raw.artifact);
  const compareValid = raw.compare !== null && UUID_RE.test(raw.compare);
  const runValid = raw.run !== null && UUID_RE.test(raw.run);
  const conversationValid =
    raw.conversation !== null && UUID_RE.test(raw.conversation);
  const sceneParsed = raw.scene !== null ? Number.parseInt(raw.scene, 10) : null;
  // 缺省（null）= 未选场景，合法；提供时必须是正整数字面量
  const sceneValid =
    raw.scene === null ||
    (Number.isInteger(sceneParsed) &&
      (sceneParsed as number) >= 1 &&
      String(sceneParsed) === raw.scene);
  const panelValid = raw.panel === null || PANELS.includes(raw.panel as WorkspacePanel);

  const invalidKeys: string[] = [];
  if (!artifactValid && raw.artifact !== null) invalidKeys.push("artifact");
  if (!compareValid && raw.compare !== null) invalidKeys.push("compare");
  if (!runValid && raw.run !== null) invalidKeys.push("run");
  if (!conversationValid && raw.conversation !== null) invalidKeys.push("conversation");
  if (!sceneValid) invalidKeys.push("scene");
  if (!panelValid) invalidKeys.push("panel");
  const notice =
    invalidKeys.length > 0
      ? `链接中的 ${invalidKeys.join("、")} 参数无效，已清除对应选择。`
      : null;
  // effect 依赖按值稳定（invalidKeys 派生自 searchParams，每次渲染是新数组）
  const invalidKey = invalidKeys.join(",");
  // 粘性提示：URL 清理（replace）会让派生 notice 归 null——若直接派生，
  // 用户永远看不到"已清除"的告知；存入 state 直到显式关闭
  const [stickyNotice, setStickyNotice] = useState<string | null>(null);
  useEffect(() => {
    if (notice) setStickyNotice(notice);
  }, [notice]);
  const clearNotice = useCallback(() => setStickyNotice(null), []);

  // 非法参数从 URL 清除（replace——不给历史记录添垃圾）
  const navigate = useCallback(
    (
      patch: Record<string, string | null>,
      mode: "push" | "replace" = "replace",
    ) => {
      const url = buildUrl(pathname, searchParams, patch);
      if (mode === "push") router.push(url);
      else router.replace(url);
    },
    [pathname, router, searchParams],
  );

  // 非法参数从 URL 清除（replace——不给历史记录添垃圾）。effect 驱动：
  // 渲染期触发导航会与 React 状态更新互相追赶
  useEffect(() => {
    if (!invalidKey) return;
    const patch: Record<string, string | null> = {};
    for (const key of invalidKey.split(",")) patch[key] = null;
    navigate(patch, "replace");
  }, [navigate, invalidKey]);

  const openArtifact = useCallback(
    (artifactId: string, panel: WorkspacePanel = "read") => {
      navigate({ artifact: artifactId, scene: null, compare: null, panel }, "push");
    },
    [navigate],
  );

  const replaceArtifact = useCallback(
    (artifactId: string | null, panel: WorkspacePanel = "read") => {
      navigate({ artifact: artifactId, scene: null, compare: null, panel }, "replace");
    },
    [navigate],
  );

  const setPanel = useCallback(
    (panel: WorkspacePanel) => navigate({ panel }),
    [navigate],
  );
  const setScene = useCallback(
    (scene: number | null) => navigate({ scene: scene === null ? null : String(scene) }),
    [navigate],
  );
  const setCompare = useCallback(
    (compareId: string | null) => navigate({ compare: compareId }),
    [navigate],
  );
  const setConversation = useCallback(
    (conversationId: string | null) => navigate({ conversation: conversationId }),
    [navigate],
  );
  const setRun = useCallback(
    (runId: string | null) => navigate({ run: runId }),
    [navigate],
  );

  return {
    artifactId: artifactValid ? raw.artifact : null,
    scene: sceneValid ? (sceneParsed as number | null) : null,
    conversationId: conversationValid ? raw.conversation : null,
    panel: panelValid ? ((raw.panel as WorkspacePanel) ?? "read") : "read",
    compareId: compareValid ? raw.compare : null,
    runId: runValid ? raw.run : null,
    notice: stickyNotice,
    clearNotice,
    openArtifact,
    replaceArtifact,
    setPanel,
    setScene,
    setCompare,
    setConversation,
    setRun,
  };
}
