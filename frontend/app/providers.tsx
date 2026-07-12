"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ApiError, SessionExpiredError } from "@/lib/api/errors";

/** Don't retry auth/session failures or 4xx client errors — only transient ones. */
function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof SessionExpiredError) return false;
  if (error instanceof ApiError && error.status < 500) return false;
  return failureCount < 2;
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5_000,
            retry: shouldRetry,
            refetchOnWindowFocus: false,
          },
          mutations: {
            retry: false,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
