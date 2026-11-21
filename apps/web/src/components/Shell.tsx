"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useApi } from "@/lib/api";
import styles from "./Shell.module.scss";

const NAV = [
  ["/", "Overview"],
  ["/portfolio", "Portfolio"],
  ["/transactions", "Transactions & Imports"],
  ["/data-quality", "Data Quality & Sources"],
  ["/risk", "Risk Monitor"],
  ["/report", "Monthly Report"],
] as const;

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const health = useApi<{ dataset: string; status: string }>("/health");
  const dataset = health.state === "ok" ? health.data.dataset : "…";
  return (
    <>
      <a href="#main" className={styles.skip}>Skip to content</a>
      <header className={styles.header}>
        <strong>ArthaShreni</strong>
        <span className={styles.dataset} data-dataset={dataset}>{dataset} data</span>
        <span className={styles.ro}>Read-only. Nothing here places or changes a trade.</span>
      </header>
      <nav aria-label="Main" className={styles.nav}>
        <ul>
          {NAV.map(([href, label]) => (
            <li key={href}>
              <Link href={href} aria-current={path === href ? "page" : undefined}>{label}</Link>
            </li>
          ))}
        </ul>
      </nav>
      <main id="main" className={styles.main}>{children}</main>
    </>
  );
}
