"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export type Loaded<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ok"; data: T };

/** GET /api/<path> through the Next proxy. 401 sends the user to the login page. Read-only: GET only. */
export function useApi<T>(path: string): Loaded<T> {
  const [r, setR] = useState<Loaded<T>>({ state: "loading" });
  const router = useRouter();
  useEffect(() => {
    let live = true;
    fetch(`/api${path}`, { credentials: "same-origin" })
      .then(async (res) => {
        if (res.status === 401) {
          router.replace("/login");
          return;
        }
        if (!res.ok) throw new Error(`${path}: ${res.status} ${(await res.text()).slice(0, 200)}`);
        const data = (await res.json()) as T;
        if (live) setR({ state: "ok", data });
      })
      .catch((e: unknown) => live && setR({ state: "error", message: e instanceof Error ? e.message : String(e) }));
    return () => {
      live = false;
    };
  }, [path, router]);
  return r;
}
