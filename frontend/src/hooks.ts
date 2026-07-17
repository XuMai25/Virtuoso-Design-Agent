import { useEffect, useState } from "react";
import { api } from "./api";

export interface LoadState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useApi<T>(path: string, refreshKey = 0): LoadState<T> {
  const [state, setState] = useState<LoadState<T>>({
    data: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    setState((current) => ({ ...current, loading: true, error: null }));
    api<T>(path, { signal: controller.signal })
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState({
          data: null,
          loading: false,
          error: error instanceof Error ? error.message : "未知错误",
        });
      });
    return () => controller.abort();
  }, [path, refreshKey]);

  return state;
}
