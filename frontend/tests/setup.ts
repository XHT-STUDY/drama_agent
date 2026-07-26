/** Vitest 全局设置 (H-01).
 *
 * 注：MVP 阶段使用标准断言，不依赖 @testing-library/jest-dom 扩展。
 * 后续阶段可通过 pnpm workspace 配置解决 hoisting 问题后添加。
 */

// ============================================================
// EventSource mock（jsdom 不支持原生 EventSource）
// ============================================================

class MockEventSource {
  static CONNECTING = 0 as const;
  static OPEN = 1 as const;
  static CLOSED = 2 as const;

  readyState: number = MockEventSource.CONNECTING;
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((msg: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private _listeners: Map<string, Array<(e: MessageEvent) => void>> = new Map();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (e: MessageEvent) => void): void {
    const arr = this._listeners.get(type) || [];
    arr.push(listener);
    this._listeners.set(type, arr);
  }

  close(): void {
    this.readyState = MockEventSource.CLOSED;
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).EventSource = MockEventSource;
