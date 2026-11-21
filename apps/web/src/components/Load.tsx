import type { Loaded } from "@/lib/api";

/** Renders loading and error states; an API failure is shown, never hidden behind empty numbers. */
export function Load<T>({ r, children }: { r: Loaded<T>; children: (d: T) => React.ReactNode }) {
  if (r.state === "loading") return <p role="status">Loading…</p>;
  if (r.state === "error") return <p role="alert">Could not load: {r.message}</p>;
  return <>{children(r.data)}</>;
}

/** Waits for several loads, then renders with all of them. */
export function LoadAll<A, B = never, C = never, D = never>({
  a, b, c, d, children,
}: {
  a: Loaded<A>; b?: Loaded<B>; c?: Loaded<C>; d?: Loaded<D>;
  children: (a: A, b: B, c: C, d: D) => React.ReactNode;
}) {
  const all = [a, b, c, d].filter((x) => x !== undefined) as Loaded<unknown>[];
  const bad = all.find((x) => x.state === "error");
  if (bad && bad.state === "error") return <p role="alert">Could not load: {bad.message}</p>;
  if (all.some((x) => x.state === "loading")) return <p role="status">Loading…</p>;
  const v = (x: Loaded<unknown> | undefined) => (x?.state === "ok" ? x.data : undefined);
  return <>{children(v(a) as A, v(b) as B, v(c) as C, v(d) as D)}</>;
}
