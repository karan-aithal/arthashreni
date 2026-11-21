import type { Metadata } from "next";
import { Shell } from "@/components/Shell";
import "@/styles/globals.scss";

export const metadata: Metadata = { title: "ArthaShreni", description: "Read-only investment decision support (local)" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
