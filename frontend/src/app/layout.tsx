import type { Metadata } from "next";
import Link from "next/link";

import { QueryProvider } from "@/lib/query-client";

import "./globals.css";

export const metadata: Metadata = {
  title: "DramaAgent — 短剧创作工作台",
  description: "面向中文短剧创作的对话型 Agent 系统",
};

/** 侧边栏导航条目 */
const navItems = [
  { href: "/projects", label: "项目列表", icon: "📁" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="h-full">
      <body className="flex h-full bg-gray-50">
        <QueryProvider>
          {/* 侧边栏 */}
          <aside className="flex w-56 shrink-0 flex-col border-r border-gray-200 bg-white">
            <div className="flex h-14 items-center gap-2 border-b border-gray-100 px-4">
              <span className="text-lg">🎬</span>
              <Link href="/" className="text-base font-bold text-gray-800">
                DramaAgent
              </Link>
            </div>
            <nav className="flex-1 space-y-0.5 px-3 py-4">
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-gray-600 hover:bg-gray-100 hover:text-gray-900 transition-colors"
                >
                  <span>{item.icon}</span>
                  <span>{item.label}</span>
                </Link>
              ))}
            </nav>
            <div className="border-t border-gray-100 px-4 py-3">
              <p className="text-xs text-gray-400">DramaAgent v0.1.0</p>
            </div>
          </aside>

          {/* 主内容区 */}
          <main className="flex-1 overflow-auto">
            <div className="mx-auto max-w-5xl px-6 py-8">
              {children}
            </div>
          </main>
        </QueryProvider>
      </body>
    </html>
  );
}
