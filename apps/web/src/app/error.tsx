"use client";

import { ErrorView } from "@/components/ui/ErrorView";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorView reset={reset} digest={error.digest} />;
}
