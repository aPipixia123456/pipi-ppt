import type { Metadata } from "next";
import "./style.css";

export const metadata: Metadata = {
  title: "Pipi PPT · 把想法变成演示",
  description: "基于 Presenton 的 AI 演示文稿工作台，通过 pipiapi 授权与计费。",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
