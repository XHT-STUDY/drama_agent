import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "DramaAgent",
  description: "面向中文短剧创作的对话型 Agent 系统",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
