"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function Login() {
  const [error, setError] = useState("");
  const router = useRouter();
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: f.get("username"), password: f.get("password") }),
    });
    if (res.ok) router.push("/");
    else setError(res.status === 401 ? "Wrong username or password." : `Login failed (${res.status}).`);
  }
  return (
    <form onSubmit={submit} aria-labelledby="t">
      <h1 id="t">Sign in</h1>
      <p><label>Username <input name="username" autoComplete="username" required /></label></p>
      <p><label>Password <input name="password" type="password" autoComplete="current-password" required /></label></p>
      {error && <p role="alert">{error}</p>}
      <button type="submit">Sign in</button>
    </form>
  );
}
